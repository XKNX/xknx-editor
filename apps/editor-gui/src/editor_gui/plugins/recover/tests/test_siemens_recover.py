"""Siemens property recovery through snapshots and portable project artifacts."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from editor_gui.plugin_blob_readers.siemens_dali import download, reader
from editor_gui.plugin_blob_readers.siemens_dali.encoder import (
    encode_date,
    encode_time,
)
from editor_gui.plugins.recover import dali, siemens
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from editor_gui.plugins.recover.tests.test_dali_recover import (
    _application,
    _project,
    _recovered,
)
from xknxeditor.proj import export_knxproj, import_knxproj
from xknxeditor.recover import devices_from_snapshot, snapshots_json

NS = "{http://www.siemens.com/knx/N141_21_Parameters}"
FIXTURE = Path(__file__).parents[5] / "tests/fixtures/siemens_n141_blob_para.dat"


class Programmer:
    def __init__(self, lines=1):
        root = ET.fromstring(
            zlib.decompress(FIXTURE.read_bytes(), -15).decode("utf-16-le")
        )
        self.blob = root.find(".//" + NS + "Blob")
        self.tables = {
            (4, 246): (
                1,
                bytes.fromhex("058B0301040500" if lines == 1 else "058B0101040500"),
            )
        }
        self.calls = []
        for prop in siemens.properties():
            for line in range(lines if prop.per_line else 1):
                container = self.blob.findall(NS + prop.container)[line]
                parents = (
                    container.findall(NS + prop.item) if prop.item else [container]
                )
                data = b""
                for parent in parents:
                    value = parent.findall(NS + prop.leaf)[prop.occurrence].get(
                        "Value", ""
                    )
                    if prop.kind.startswith("enum:"):
                        raw = bytes(
                            [
                                siemens.enum_values(prop.kind.removeprefix("enum:"))[
                                    value
                                ]
                            ]
                        )
                    elif prop.kind == "hex":
                        raw = bytes.fromhex(value)
                    elif prop.kind == "time":
                        raw = encode_time(value)
                    elif prop.kind == "date":
                        raw = encode_date(value)
                    elif prop.kind == "float":
                        raw = struct.pack("!f", float(value))
                    else:
                        raw = int(value).to_bytes(
                            prop.size, "big", signed=prop.kind == "int"
                        )
                    data += raw
                self.tables[prop.object_index + line, prop.pid] = (prop.size, data)
        for line in range(lines):
            self.tables[37 + line, 51] = (1, bytes((5,)) + b"\xff" * 63)
            self.tables[37 + line, 53] = (6, bytes.fromhex("03A3C20175BA") + bytes(378))
            for pid, size in (
                (51, 1),
                (52, 3),
                (53, 8),
                (57, 1),
                (58, 8),
                (70, 1),
                (71, 1),
                (72, 1),
                (73, 1),
                (74, 1),
                (75, 2),
                (76, 2),
                (77, 1),
            ):
                self.tables[35 + line, pid] = (size, bytes(size * 64))
            self.tables[35 + line, 51] = (1, bytes((12 + line,)) + b"\xff" * 63)
            self.tables[35 + line, 53] = (8, b"\x08" + b"\xff" * 7 + bytes(504))
            self.tables[35 + line, 52] = (3, bytes.fromhex("123456") + bytes(189))
            self.tables[20 + line, 60] = (1, bytes((12 + line,)) + b"\xff" * 63)
            self.tables[20 + line, 61] = (1, bytes((3 + line,)) + b"\xff" * 63)
            self.tables[15 + line, 160] = (4, bytes.fromhex("01234567") + bytes(60))
            self.tables[28 + line, 110] = (1, b"\x01" * 8)
            self.tables[28 + line, 111] = (1, b"\x00" * 8)

    def property_chunk_size(self, size):
        return min(15, 9 // size)

    async def read_property(self, object_index, property_id, *, count=1, start_index=1):
        size, data = self.tables[object_index, property_id]
        assert size * count <= 9
        self.calls.append((object_index, property_id, count, start_index))
        return data[(start_index - 1) * size : (start_index - 1 + count) * size]

    async def write_property(self, *args, **kwargs):
        pytest.fail("Siemens recovery must only read")


@pytest.mark.parametrize(
    "app_id,lines", [("M-0001_A-9837-11-449E", 1), ("M-0001_A-9834-11-0000", 2)]
)
async def test_recover_snapshot_undo_reopen_export(
    tmp_path, monkeypatch, app_id, lines
):
    app = _application(app_id)
    programmer = Programmer(lines)
    read = dali.addin_reader(app)
    assert read is not None
    captured = await read(programmer)
    recovered = devices_from_snapshot(snapshots_json([_recovered(app, captured)]))[0]
    project = _project(tmp_path, monkeypatch, app)
    service = RecoverService(SimpleNamespace(project=project))
    entry = RecoverEntry(
        "1.1.50",
        0x07B0,
        application=app,
        product_ref_id="P-1",
        hardware2program_ref_id="HP-1",
        recovered=recovered,
    )
    try:
        cursor = project.cursor
        assert service._add_recovered(entry, {}), entry.error
        device = project.find_device_by_node_id(entry.device_id)
        payload = project.device_binary_data(device, "BLOB_PARA")
        assert reader.read(payload)
        root = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le"))
        blob = root.find(".//" + NS + "Blob")
        for line in range(lines):
            ecg = blob.findall(NS + "GenericEcg")[line].find(NS + "GenericEcgEcg")
            assert ecg.find(NS + "GenericEcgEcgShortAddress").get("Value") == str(
                12 + line
            )
            assert ecg.find(NS + "GenericEcgEcgGroupAddress").get("Value") == str(
                3 + line
            )
            scene = blob.findall(NS + "Scenes")[line].find(NS + "ScenesScene")
            assert scene.find(NS + "ScenesSceneValue").get("Value") == "01234567"
            presence = blob.findall(NS + "Presence")[line].find(NS + "PresenceChannel")
            assert [
                v.get("Value")
                for v in presence.findall(NS + "PresenceChannelLockRecovery")
            ] == ["LOCK_REC_ON", "LOCK_REC_OFF"]
        assert project.undo()
        assert project.cursor == cursor
        assert project.find_device_by_address("1.1.50") is None
        assert project.redo()
        project.close()
        project.open(tmp_path / "project.xknx")
        device = project.find_device_by_address("1.1.50")
        assert project.device_binary_data(device, "BLOB_PARA") == payload
        export_knxproj(project.working_path, tmp_path / "recovered.knxproj")
        project.close()
        import_knxproj(tmp_path / "recovered.knxproj", tmp_path / "roundtrip.xknx")
        project.open(tmp_path / "roundtrip.xknx")
        device = project.find_device_by_address("1.1.50")
        assert project.device_binary_data(device, "BLOB_PARA") == payload
    finally:
        project.close()


async def test_incomplete_table_fails_before_project_apply():
    programmer = Programmer()
    programmer.tables[20, 60] = (1, b"\x01")
    read = siemens.addin_reader(_application("M-0001_A-9837-11-449E"))
    with pytest.raises(ValueError, match="Incomplete Siemens property 20/60"):
        await read(programmer)


async def test_partial_snapshot_and_wrong_family_are_rejected():
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(Programmer())
    data = capture[app.id][siemens._CAPTURE]
    with pytest.raises(ValueError, match="Invalid Siemens DALI capture"):
        siemens.build_blob(data, "M-0001_A-9A1B-03-0000")
    incomplete = json.loads(data)
    del incomplete["tables"]["20/61"]
    with pytest.raises(ValueError, match="Incomplete Siemens DALI property image"):
        siemens.build_blob(json.dumps(incomplete).encode(), app.id)


@pytest.mark.parametrize(
    "addresses", [b"\x40" + b"\xff" * 63, b"\x01\x01" + b"\xff" * 62]
)
async def test_invalid_or_duplicate_short_addresses_fail(addresses):
    programmer = Programmer()
    programmer.tables[20, 60] = (1, addresses)
    with pytest.raises(ValueError, match="Siemens DALI short address"):
        await siemens.addin_reader(_application("M-0001_A-9837-11-449E"))(programmer)


async def test_all_captured_properties_roundtrip_to_the_same_xml_values():
    programmer = Programmer()
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    blob = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le")).find(
        ".//" + NS + "Blob"
    )
    for prop in siemens.properties():
        container = blob.find(NS + prop.container)
        parents = container.findall(NS + prop.item) if prop.item else [container]
        raw = programmer.tables[prop.object_index, prop.pid][1]
        for index, parent in enumerate(parents):
            if prop.container == "ConnectedSensors":
                continue
            value = parent.findall(NS + prop.leaf)[prop.occurrence].get("Value")
            if prop.kind.startswith("enum:"):
                encoded = bytes([siemens.enum_values(prop.kind[5:])[value]])
            elif prop.kind == "hex":
                encoded = bytes.fromhex(value)
            elif prop.kind == "time":
                encoded = encode_time(value)
            elif prop.kind == "date":
                encoded = encode_date(value)
            elif prop.kind == "float":
                encoded = struct.pack("!f", float(value))
            else:
                encoded = int(value).to_bytes(
                    prop.size, "big", signed=prop.kind == "int"
                )
            assert encoded == raw[index * prop.size : (index + 1) * prop.size], (
                prop,
                index,
            )
    connected = blob.find(NS + "ConnectedEcgs").findall(NS + "ConnectedEcgsEcg")[12]
    assert connected.find(NS + "ConnectedEcgsEcgRnd").get("Value") == "123456"
    assert (
        blob.find(NS + "ConnectedEcgs")
        .findall(NS + "ConnectedEcgsEcg")[0]
        .find(NS + "ConnectedEcgsEcgActive")
        .get("Value")
        == "false"
    )
    assert (
        blob.find(
            NS + "GenericEcg/" + NS + "GenericEcgEcg/" + NS + "GenericEcgEcgDevTypes"
        ).get("Value")
        == "08"
    )
    sections = reader.read(payload)
    connected_rows = next(
        s.parameters for s in sections if "Connected ECGs" in s.section
    )
    assert len(connected_rows) == 1


@pytest.mark.parametrize(
    "app_id,lines", [("M-0001_A-9837-11-449E", 1), ("M-0001_A-9834-11-0000", 2)]
)
async def test_recovered_configuration_compiles_to_captured_properties(app_id, lines):
    programmer = Programmer(lines)
    capture = await siemens.addin_reader(_application(app_id))(programmer)
    payload = siemens.build_blob(capture[app_id][siemens._CAPTURE], app_id)
    configuration = download.compile_configuration(payload, app_id)
    assert configuration.lines == lines
    assert len(configuration.writes) == (350 if lines == 1 else 560)
    for write in configuration.writes:
        size, data = programmer.tables[write.odx, write.pid]
        assert len(write.data) == size * write.count
        if write.pid not in (51, 52):
            assert write.data == data, (write.odx, write.pid)
    assert (
        next(w for w in configuration.writes if w.odx == 11 and w.pid == 151).count == 2
    )


async def test_blob_uses_vendor_envelope_and_namespace_prefixes():
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(Programmer())
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    xml = zlib.decompress(payload, -15).decode("utf-16-le")
    assert "<n141_21_ImExport:ImExport xmlns:n141_21_ImExport=" in xml
    assert "<n141:Blob xmlns:n141=" in xml
    root = ET.fromstring(xml)
    assert (
        root.find(
            "{http://www.siemens.com/knx/ImExportSchema.xsd}N141_21/" + NS + "Blob"
        )
        is not None
    )


async def test_sensor_without_project_label_stays_visible():
    programmer = Programmer()
    programmer.tables[28, 60] = (1, b"\x05" + b"\xff" * 7)
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    sections = reader.read(payload)
    rows = [
        row
        for section in sections
        if "Presence channels" in section.section
        for row in section.parameters
    ]
    assert any(row.value == "5" and "ShortAddress" in row.ref_id for row in rows)
    sensors = next(s.parameters for s in sections if "Connected sensors" in s.section)
    assert len(sensors) == 1


@pytest.mark.parametrize("version", (258, 515, 772, 1280))
async def test_unknown_interface_version_is_rejected_before_tables(version):
    programmer = Programmer()
    programmer.tables[4, 246] = (
        1,
        bytes.fromhex("058B0301") + version.to_bytes(2, "big") + b"\x00",
    )
    with pytest.raises(ValueError, match="interface version"):
        await siemens.addin_reader(_application("M-0001_A-9837-11-449E"))(programmer)
    assert {call[:2] for call in programmer.calls} == {(4, 246)}


@pytest.mark.parametrize("variant", ("058B0101", "058B0201", "00000000"))
async def test_mismatched_firmware_variant_is_rejected_before_tables(variant):
    programmer = Programmer()
    programmer.tables[4, 246] = (1, bytes.fromhex(variant + "040500"))
    with pytest.raises(ValueError, match="firmware variant"):
        await siemens.addin_reader(_application("M-0001_A-9837-11-449E"))(programmer)
    assert {call[:2] for call in programmer.calls} == {(4, 246)}


async def test_sensor_records_follow_short_addresses():
    programmer = Programmer()
    programmer.tables[37, 51] = (1, b"\x85" + b"\xff" * 63)
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    root = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le"))
    sensors = root.find(".//" + NS + "ConnectedSensors").findall(
        NS + "ConnectedSensorsSensor"
    )
    assert len(sensors) == 64
    assert sensors[0].find(NS + "ConnectedSensorsSensorActive").get("Value") == "false"
    assert sensors[5].find(NS + "ConnectedSensorsSensorActive").get("Value") == "true"
    assert (
        sensors[5].find(NS + "ConnectedSensorsSensorGtin").get("Value")
        == "03A3C20175BA"
    )


async def test_separate_emergency_converter_keeps_device_types():
    programmer = Programmer()
    programmer.tables[35, 51] = (1, bytes((12, 13)) + b"\xff" * 62)
    programmer.tables[35, 53] = (
        8,
        bytes.fromhex("06FFFFFFFFFFFFFF0106FFFFFFFFFFFF") + bytes(496),
    )
    programmer.tables[24, 76] = (1, bytes((13,)) + b"\xff" * 63)
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    root = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le"))
    assert root.find(".//" + NS + "GenericEcgEcgDevTypes").get("Value") == "0106"


@pytest.mark.parametrize(
    "enabled,group,line_mode,expected",
    [
        ((), 255, 1, ""),
        ((0, 1), 255, 1, "0001"),
        ((8,), 255, 1, "08"),
        ((1, 8), 3, 1, "01"),
        ((8,), 255, 2, ""),
        ((8,), 255, 0, ""),
    ],
)
async def test_missing_physical_cache_preserves_only_proven_configured_types(
    enabled, group, line_mode, expected
):
    programmer = Programmer()
    programmer.tables[35, 51] = (1, b"\xff" * 64)
    programmer.tables[20, 61] = (1, bytes((group,)) + b"\xff" * 63)
    programmer.tables[11, 166] = (1, bytes((line_mode, 0)))
    for number, object_index in ((0, 22), (1, 24), (8, 42)):
        programmer.tables[object_index, 60] = (
            1,
            bytes((int(number in enabled),)) + bytes(63),
        )
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    root = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le"))
    logical = root.find(".//" + NS + "GenericEcgEcg")
    assert logical.find(NS + "GenericEcgEcgDevTypes").get("Value") == expected
    assert logical.find(NS + "GenericEcgEcgCin") is None
    assert all(
        item.find(NS + "ConnectedEcgsEcgActive").get("Value") == "false"
        for item in root.findall(".//" + NS + "ConnectedEcgsEcg")
    )


@pytest.mark.parametrize("conflicting", (False, True))
async def test_group_scene_colour_and_standby_values_follow_assigned_members(
    conflicting,
):
    programmer = Programmer()
    programmer.tables[20, 60] = (1, bytes((12, 13)) + b"\xff" * 62)
    programmer.tables[20, 61] = (1, bytes((3, 3, 4)) + b"\xff" * 61)
    programmer.tables[20, 74] = (1, bytes((26, 51 if conflicting else 26)) + bytes(62))
    programmer.tables[42, 61] = (
        2,
        struct.pack("!HH", 2700, 4000 if conflicting else 2700) + bytes(124),
    )
    programmer.tables[18, 70] = (1, bytes((2, 4 if conflicting else 2)) + b"\xff" * 62)
    programmer.tables[15, 161] = (
        4,
        bytes.fromhex("76543210" if conflicting else "01234567") + bytes(60),
    )
    app = _application("M-0001_A-9837-11-449E")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    root = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le"))
    scene = root.find(".//" + NS + "ScenesScene")
    group_values = scene.findall(NS + "ScenesSceneValueGroup")
    assert len(group_values) == 16
    assert all(value.get("Value") is not None for value in group_values)
    assert group_values[3].get("Value") == ("00000000" if conflicting else "01234567")
    assert scene.findall(NS + "ScenesSceneValue")[1].get("Value") == (
        "76543210" if conflicting else "01234567"
    )
    colour = root.find(".//" + NS + "DevType8group").findall(NS + "DevType8groupGroup")[
        3
    ]
    standby = root.find(".//" + NS + "Minergy").findall(NS + "MinergyGroup")[3]
    if conflicting:
        assert colour.find(NS + "DevType8groupGroupMinColourTemperature") is None
        assert standby.find(NS + "MinergyGroupAsignment") is None
    else:
        assert (
            colour.find(NS + "DevType8groupGroupMinColourTemperature").get("Value")
            == "2700"
        )
        assert standby.find(NS + "MinergyGroupAsignment").get("Value") == "2"
    groups = root.find(".//" + NS + "Group").findall(NS + "GroupGroup")
    assert groups[3].find(NS + "GroupGroupNr").get("Value") == "4"
    assert groups[4].find(NS + "GroupGroupNr").get("Value") == "0"
    if conflicting:
        assert groups[3].find(NS + "GroupGroupMinValue") is None
    else:
        assert groups[3].find(NS + "GroupGroupMinValue").get("Value") == "26"


@pytest.mark.parametrize("conflicting", (False, True))
async def test_scene_catalog_combines_active_scene_numbers_from_both_lines(conflicting):
    programmer = Programmer(2)
    programmer.tables[15, 150] = (1, bytes((0x81, 0x42, 3)) + b"\xff" * 13)
    programmer.tables[16, 150] = (1, bytes((3, 0x84)) + b"\xff" * 14)
    programmer.tables[15, 155] = (1, bytes((1, 2, 3)) + bytes(13))
    programmer.tables[16, 155] = (1, bytes((5 if conflicting else 3, 4)) + bytes(14))
    app = _application("M-0001_A-9834-11-0232")
    capture = await siemens.addin_reader(app)(programmer)
    payload = siemens.build_blob(capture[app.id][siemens._CAPTURE], app.id)
    root = ET.fromstring(zlib.decompress(payload, -15).decode("utf-16-le"))
    scenes = root.find(".//" + NS + "ScenePluginInfo").findall(
        NS + "ScenePluginInfoScene"
    )
    assert len(scenes) == 32
    assert [
        scene.find(NS + "ScenePluginInfoSceneKnxSceneNumber").get("Value")
        for scene in scenes
    ] == ["1", "3", "4"] + ["255"] * 29
    assert (
        scenes[0].find(NS + "ScenePluginInfoSceneFadeTime").get("Value") == "DFT_0_7S"
    )
    assert (
        scenes[2].find(NS + "ScenePluginInfoSceneFadeTime").get("Value") == "DFT_2_0S"
    )
    if conflicting:
        assert scenes[1].find(NS + "ScenePluginInfoSceneFadeTime") is None
    else:
        assert (
            scenes[1].find(NS + "ScenePluginInfoSceneFadeTime").get("Value")
            == "DFT_1_4S"
        )
    assert all(scene.find(NS + "ScenePluginInfoSceneName") is None for scene in scenes)
