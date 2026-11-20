"""Siemens DALI-2 cached discovery recovery and project persistence."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from editor_gui.device import Device
from editor_gui.plugins.recover import siemens_dali2 as dali
from editor_gui.plugins.recover.tests.test_dali_recover import _project, _recovered
from xknxeditor.prod import parse_application_xml
from xknxeditor.proj import export_knxproj, import_knxproj
from xknxeditor.recover import devices_from_snapshot, snapshots_json


def _varint(number):
    result = bytearray()
    while number > 127:
        result.append((number & 127) | 128)
        number >>= 7
    result.append(number)
    return bytes(result)


def _uint(field, number):
    return _varint(field << 3) + _varint(number)


def _bytes(field, data):
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def _images():
    ecg = _uint(2, 7) + _uint(3, 0xABCDEF) + _bytes(5, b"\x06\x08") + _uint(6, 26)
    ecgs = _uint(1, 1) + _bytes(2, ecg)
    instance = _uint(1, 2) + _uint(2, 4) + _uint(3, 12)
    button = _uint(1, 3) + _uint(2, 1) + _uint(3, 1)
    sensor = (
        _uint(3, 0x123456)
        + _uint(4, 2)
        + _uint(5, 2)
        + _bytes(6, instance)
        + _bytes(6, button)
    )
    sensors = _uint(1, 17) + _bytes(2, sensor)
    return ecgs, sensors


class _Programmer:
    def __init__(self, count=1):
        self.reads = []
        self.pointers = {}
        self.memory = {}
        for line in range(count):
            for property_id, image in zip((55, 56), _images(), strict=True):
                address = 0x100 + line * 0x1000 + (property_id - 55) * 0x100
                self.pointers[16 + line, property_id] = address.to_bytes(4, "big")
                self.memory[address, 4] = len(image).to_bytes(4, "little")
                self.memory[address + 4, len(image)] = image

    async def read_property(self, object_index, property_id):
        self.reads.append(("property", object_index, property_id))
        return self.pointers[object_index, property_id]

    async def read_user_memory(self, address, size):
        self.reads.append(("user_memory", address, size))
        return self.memory[address, size]

    async def write_property(self, *args, **kwargs):
        pytest.fail("Recovery must not write to the bus")

    async def write_memory(self, *args, **kwargs):
        pytest.fail("Recovery must not write to the bus")


def _application(twin=False, *, sensor_prefix="BSens", version="30"):
    app_id = f"M-0001_A-9A1{'C' if twin else 'B'}-{version}-33CF"
    parameters = []
    refs = []
    for line in range(2 if twin else 1):
        for name, value in {
            "ShortAddress": 0,
            "Rnd": 0x123456,
            "ChannelIdx": 4,
            "InstanceNumber": 2,
        }.items():
            pid = f"{app_id}_P-{len(parameters) + 1}"
            parameters.append(
                f'<Parameter Id="{pid}" Name="DaliLine.{line}.{sensor_prefix}Mapping.0.{name}" Text="{name}" ParameterType="{app_id}_PT-1" Value="{value}" />'
            )
            refs.append(f'<ParameterRef Id="{pid}_R-1" RefId="{pid}" />')
    xml = f'''<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData>
      <Manufacturer RefId="M-0001"><ApplicationPrograms>
      <ApplicationProgram Id="{app_id}" Name="DALI-2" ApplicationNumber="39452" ApplicationVersion="{int(version, 16)}" ProgramType="ApplicationProgram" MaskVersion="MV-07B0" LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US" DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
        <Static><ParameterTypes><ParameterType Id="{app_id}_PT-1" Name="Number"><TypeNumber Type="unsignedInt" SizeInBit="32" minInclusive="0" maxInclusive="4294967295" /></ParameterType></ParameterTypes>
          <Parameters>{"".join(parameters)}</Parameters><ParameterRefs>{"".join(refs)}</ParameterRefs>
        </Static><Dynamic><ChannelIndependentBlock /></Dynamic>
      </ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>'''
    return parse_application_xml(xml.encode(), "M-0001")[0]


@pytest.mark.parametrize("twin", [False, True])
@pytest.mark.parametrize("version", ["01", "30"])
async def test_cached_discovery_snapshot_undo_and_project_export(
    tmp_path, monkeypatch, twin, version
):
    app = _application(twin, version=version)
    programmer = _Programmer(2 if twin else 1)
    captured = await dali.addin_reader(app)(programmer)
    recovered = devices_from_snapshot(snapshots_json([_recovered(app, captured)]))[0]
    assert recovered.addin_data == captured
    project = _project(tmp_path, monkeypatch, app)
    try:
        device_id = project.add_device("P-1", "HP-1", "Gateway", app)
        device = project.find_device_by_node_id(device_id)
        cursor = project.cursor
        dali.apply_recovered(project, device, recovered)
        device = project.find_device_by_node_id(device_id)
        payload = project.device_binary_data(device, "Dali2DcaBinaryData")
        result = json.loads(payload)
        for line in range(2 if twin else 1):
            assert result[f"RuntimeDataEcgLine{line + 1}"]["7"] == {
                "EcgName": "",
                "MinDimmValue": "10",
                "TypeOfEcgList": [6, 8],
                "SearchFlags": 0,
            }
            sensors = result[f"RuntimeDataSensorLine{line + 1}"]
            assert sensors["LineIndex"] == line
            sensor = sensors["FoundSensorsWithInstancesSortedByShortAddress"]["0"]
            assert sensor["Random"] == 0x123456
            assert sensor["Version"] == 2
            assert sensor["HasUnlinkedInstances"]
            instance = sensor["ListOfInstances"]["2"]
            assert instance["PhysicalSensorNumber"] == 64
            assert instance["LinkedToSensorNumber"] == 5
            assert instance["InstanceType"] == {"InstanceNumber": 4}
            assert instance["InstanceResolution"] == 12
            assert sensor["ListOfInstances"]["3"]["LinkedToSensorNumber"] == 255
        assert len(programmer.reads) == (16 if twin else 8)
        assert project.undo()
        assert project.cursor == cursor
        assert (
            project.device_binary_data(
                project.find_device_by_node_id(device_id), "Dali2DcaBinaryData"
            )
            is None
        )
        assert project.redo()
        project.close()
        project.open(tmp_path / "project.xknx")
        current = project.find_device_by_node_id(device_id)
        assert project.device_binary_data(current, "Dali2DcaBinaryData") == payload
        export_knxproj(project.working_path, tmp_path / "siemens.knxproj")
        project.close()
        import_knxproj(tmp_path / "siemens.knxproj", tmp_path / "roundtrip.xknx")
        project.open(tmp_path / "roundtrip.xknx")
        assert len(project.devices) == 1
        assert (
            project.device_binary_data(project.devices[0], "Dali2DcaBinaryData")
            == payload
        )
    finally:
        project.close()


async def test_unavailable_cache_does_not_create_empty_device_lists(caplog):
    app = _application()
    programmer = _Programmer()
    programmer.pointers = dict.fromkeys(programmer.pointers, bytes(4))
    capture = await dali.addin_reader(app)(programmer)
    device = Device(node_id=1, name="DALI-2", app=app, individual_address="1.1.50")
    dali.apply_recovered(SimpleNamespace(), device, _recovered(app, capture))
    assert all(row[0] == "property" for row in programmer.reads)
    assert "discovery cache unavailable" in caplog.text


@pytest.mark.parametrize(
    "failure", ["pointer", "address", "size", "empty", "overflow", "data", "changed"]
)
async def test_bad_bus_read_fails_before_snapshot(failure):
    app = _application()
    programmer = _Programmer()
    if failure == "pointer":
        programmer.pointers[16, 55] = b"\x01"
    elif failure == "address":
        programmer.pointers[16, 55] = (0x100000).to_bytes(4, "big")
    elif failure == "size":
        programmer.memory[0x100, 4] = b"\x01"
    elif failure == "empty":
        programmer.memory[0x100, 4] = bytes(4)
    elif failure == "overflow":
        programmer.memory[0x100, 4] = (0x100000).to_bytes(4, "little")
    elif failure == "data":
        image = _images()[0]
        programmer.memory[0x104, len(image)] = image[:-1]
    else:
        original = programmer.read_user_memory

        async def read_memory(address, size):
            programmer.pointers[16, 55] = (0x200).to_bytes(4, "big")
            return await original(address, size)

        programmer.read_user_memory = read_memory
    with pytest.raises(ValueError, match="Siemens DALI-2"):
        await dali.addin_reader(app)(programmer)


@pytest.mark.parametrize(
    "data", [b"\x80", b"\x08" + b"\xff" * 10, b"\0", b"\x12\x05\x01", b"\x0f"]
)
def test_malformed_wire_data_is_rejected(data):
    with pytest.raises(ValueError, match="Siemens DALI-2"):
        dali._ecg_data(data)


@pytest.mark.parametrize(
    "data",
    [
        _uint(3, 2),
        _uint(3, 3) + _bytes(2, _uint(2, 1)),
        _bytes(2, _uint(2, 64)),
        _bytes(2, _uint(2, 1)) * 2,
        _bytes(2, _uint(2, 1) + _uint(1, 256)),
        _bytes(2, _uint(2, 1) + b"\x35" + bytes(4)),
    ],
)
def test_failed_duplicate_or_invalid_discovery_is_rejected(data):
    with pytest.raises(ValueError, match="Siemens DALI-2"):
        dali._ecg_data(data)


def test_unknown_fields_and_packed_and_unpacked_device_types():
    row = _uint(5, 6) + _bytes(5, bytes((8, 1)))
    data = _bytes(2, row) + _uint(99, 1) + b"\xa5\x06" + bytes(4)
    assert dali._ecg_data(data)["0"]["TypeOfEcgList"] == [6, 8, 1]
    assert dali._ecg_data(_uint(3, 3)) == {}


def test_sensor_declared_count_duplicates_and_invalid_instance_are_tolerated(caplog):
    instance = _uint(1, 2)
    for sensor in (
        _uint(5, 2) + _bytes(6, instance),
        _uint(5, 2) + _bytes(6, instance) * 2,
        _uint(5, 2) + _bytes(6, instance) + _bytes(6, _uint(1, 255)),
    ):
        result = dali._sensor_data(_bytes(2, sensor), 0)
        assert list(
            result["FoundSensorsWithInstancesSortedByShortAddress"]["0"][
                "ListOfInstances"
            ]
        ) == ["2"]
    assert "Ignoring invalid Siemens DALI-2 sensor instance" in caplog.text


@pytest.mark.parametrize(
    "level,percent", [(0, "0"), (2, "1"), (26, "10"), (254, "100"), (255, "100")]
)
def test_minimum_dimming_value_is_rounded(level, percent):
    assert dali._ecg_data(_bytes(2, _uint(6, level)))["0"]["MinDimmValue"] == percent


@pytest.mark.parametrize("prefix,kind", [("BSens", 4), ("PSens", 3), ("Key", 1)])
def test_each_input_mapping_family_is_linked(prefix, kind):
    app = _application(sensor_prefix=prefix)
    device = Device(node_id=1, name="DALI-2", app=app, individual_address="1.1.50")
    instance = _uint(1, 2) + _uint(2, kind)
    sensor = _uint(3, 0x123456) + _bytes(6, instance)
    data = dali._sensor_data(_bytes(2, sensor), 0)
    dali._link_sensors(data, device, 0)
    found = data["FoundSensorsWithInstancesSortedByShortAddress"]["0"]
    assert found["ListOfInstances"]["2"]["LinkedToSensorNumber"] == 5
    assert not found["HasUnlinkedInstances"]


@pytest.mark.parametrize(
    "app_id,expected",
    [
        ("M-0001_A-9A1B-02-ABCD", True),
        ("M-0001_A-9A1C-03-ABCD", True),
        ("m-0001_a-9a1c-30-abcd", True),
        ("M-0001_A-9A1C-01-33CF", True),
        ("M-0001_A-9A1B-01-ABCD", True),
        ("M-0001_A-9A1C-00-ABCD", False),
        ("M-0001_A-9A1C0-30-ABCD", False),
        ("M-0002_A-9A1C-30-ABCD", False),
        ("M-0001_A-9837-11-ABCD", False),
        (None, False),
    ],
)
def test_only_verified_application_versions_are_supported(app_id, expected):
    assert dali.supports(app_id) is expected


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"[]",
        b"[{}]",
        b"[null]",
        b'[{"ecgs":4,"sensors":null}]',
        b'[{"ecgs":"x","sensors":null}]',
        b'[{"ecgs":"0","sensors":null}]',
    ],
)
def test_invalid_snapshot_fails_before_project_write(payload):
    app = _application()
    device = Device(node_id=1, name="DALI-2", app=app, individual_address="1.1.50")
    recovered = _recovered(app, {app.id: {"siemens-dali2-recovery.json": payload}})
    with pytest.raises(ValueError, match="Siemens DALI-2"):
        dali.apply_recovered(SimpleNamespace(), device, recovered)


@pytest.mark.parametrize("version", ["01", "30"])
async def test_service_uses_dali2_reader_and_rolls_back_malformed_replacement(
    tmp_path, monkeypatch, version
):
    from editor_gui.plugins.recover import service as service_module
    from editor_gui.plugins.recover.service import RecoverEntry, RecoverService

    app = _application(True, version=version)
    programmer = _Programmer(2)
    captured = []

    async def recover(_xknx, address, application, *, addin_read, **kwargs):
        result = await addin_read(programmer)
        captured.append(result)
        return _recovered(application, result)

    monkeypatch.setattr(service_module, "recover_device_at", recover)
    monkeypatch.setattr(service_module, "_SETTLE_SECONDS", 0)
    project = _project(tmp_path, monkeypatch, app)
    api = SimpleNamespace(
        project=project,
        connection=SimpleNamespace(
            xknx=object(), interface_max_apdu_length=None, master=None
        ),
    )
    service = RecoverService(api)
    monkeypatch.setattr(service, "_factory_image", lambda entry: None)
    entry = RecoverEntry(
        "1.1.50",
        0x07B0,
        application=app,
        product_ref_id="P-1",
        hardware2program_ref_id="HP-1",
    )
    try:
        await service._recover([entry])
        assert entry.error is None
        assert entry.recovered.addin_data == captured[0]
        cursor = project.cursor
        assert service._add_recovered(entry, {}), entry.error
        original = project.find_device_by_address("1.1.50")
        payload = project.device_binary_data(original, "Dali2DcaBinaryData")
        assert "RuntimeDataEcgLine2" in json.loads(payload)
        assert project.undo()
        assert project.cursor == cursor
        assert project.find_device_by_address("1.1.50") is None
        assert project.redo()
        cursor = project.cursor
        entry.recovered = _recovered(
            app, {app.id: {"siemens-dali2-recovery.json": b"[]"}}
        )
        assert not service._add_recovered(entry, {}, replace=original.node_id)
        assert project.cursor == cursor
        current = project.find_device_by_address("1.1.50")
        assert current.node_id == original.node_id
        assert project.device_binary_data(current, "Dali2DcaBinaryData") == payload
    finally:
        project.close()
