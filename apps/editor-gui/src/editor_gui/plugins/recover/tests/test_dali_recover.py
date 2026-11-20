"""DALI readback through snapshots, project assembly and exported artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from editor_gui.device import Device
from editor_gui.plugin_blob_readers.dali_backup import e64pro
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.recover import dali
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.dali.configuration import compile_configuration, inputs_from_xml
from xknxeditor.dali.connection import DaliCommissioningError
from xknxeditor.dali.profiles import PROFILES
from xknxeditor.dali.properties import PropertyType as P
from xknxeditor.dali.properties import element_size_for, property_id_for
from xknxeditor.dali.restore import prepare_restore
from xknxeditor.dali.structs import EcgProp, EcgStatic, TemplateProp
from xknxeditor.prod import parse_application_xml
from xknxeditor.proj import export_knxproj, import_knxproj
from xknxeditor.recover import (
    RecoveredDevice,
    RecoveredParameters,
    devices_from_snapshot,
    snapshots_json,
)


def _application(app_id, count=1, *, sensors=None):
    modules, uses = [], []
    layouts = [
        (
            "GRP",
            "DCA",
            1,
            [
                ("CC-ColourControlType", "1"),
                ("CC-RGBInputTypeU", "0"),
                ("CC-IsSlave", "1"),
                ("AssignMasterGroup", "5"),
                ("Description", "Recovered Parameter"),
            ],
        ),
        ("ECG", "DCE", 13, [("ColourControlType", "6")]),
    ]
    if sensors is not None:
        layouts += [
            (
                "MD",
                "DCM",
                2,
                [
                    ("WithBrightness", str(sensors)),
                    ("NumberofInstancesM1", "2"),
                    ("NumberofInstancesB1", "3"),
                    ("WithConstantLightControl", "2"),
                    ("CLBrightnessCorrectionType", "1"),
                    ("CurrentConsumption", "10"),
                ],
            ),
            ("GI", "DCG", 3, [("TypeOfInputSignal", "4")]),
        ]
    for channel in range(count):
        for kind, prefix, number, params in layouts:
            mid = f"{app_id}_MD-{len(modules) + 1}"
            module_name = f"Module{kind}" + (f"_CH{channel + 1}" if count == 2 else "")
            parameters = "".join(
                f'<Parameter Id="{mid}_P-{i}" Name="{prefix}1-{name}" Text="{name}" ParameterType="{app_id}_PT-{2 if name == "Description" else 1}" Value="{value}" />'
                for i, (name, value) in enumerate(params, 1)
            )
            refs = "".join(
                f'<ParameterRef Id="{mid}_P-{i}_R-{i}" RefId="{mid}_P-{i}" />'
                for i in range(1, len(params) + 1)
            )
            dynamic = "".join(
                f'<ParameterRefRef RefId="{mid}_P-{i}_R-{i}" />'
                for i in range(1, len(params) + 1)
            )
            modules.append(f'''<ModuleDef Id="{mid}" Name="{module_name}">
              <Arguments><Argument Id="{mid}_A-1" Name="NUMBER" Type="Numeric" /></Arguments>
              <Static><Parameters>{parameters}</Parameters><ParameterRefs>{refs}</ParameterRefs></Static>
              <Dynamic><ParameterBlock Id="{mid}_PB-1" Text="Settings">{dynamic}</ParameterBlock></Dynamic>
            </ModuleDef>''')
            uses.append(
                f'<Module Id="{mid}_M-1" RefId="{mid}"><NumericArg RefId="{mid}_A-1" Value="{number}" /></Module>'
            )
    buttons = (
        [("NumberOfButton", "2"), ("NumberOfInstances", "3")]
        if sensors is not None
        else []
    )
    extra_parameters = "".join(
        f'<Parameter Id="{app_id}_P-{i}" Name="DPB1-{name}" Text="{name}" ParameterType="{app_id}_PT-1" Value="{value}" />'
        for i, (name, value) in enumerate(buttons, 2)
    )
    extra_refs = "".join(
        f'<ParameterRef Id="{app_id}_P-{i}_R-{i}" RefId="{app_id}_P-{i}" />'
        for i in range(2, 2 + len(buttons))
    )
    extra_dynamic = "".join(
        f'<ParameterRefRef RefId="{app_id}_P-{i}_R-{i}" />'
        for i in range(2, 2 + len(buttons))
    )
    xml = f'''<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData>
      <Manufacturer RefId="{app_id.split("_")[0]}"><ApplicationPrograms>
      <ApplicationProgram Id="{app_id}" Name="DALI" ApplicationNumber="1" ApplicationVersion="1" ProgramType="ApplicationProgram" MaskVersion="MV-07B0" LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US" DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
        <Static><ParameterTypes><ParameterType Id="{app_id}_PT-1" Name="Byte"><TypeNumber Type="unsignedInt" SizeInBit="8" minInclusive="0" maxInclusive="255" /></ParameterType><ParameterType Id="{app_id}_PT-2" Name="Description"><TypeText SizeInBit="480" /></ParameterType></ParameterTypes>
          <Parameters><Parameter Id="{app_id}_P-1" Name="DEV-Number_of_DaliChannels" Text="Channels" ParameterType="{app_id}_PT-1" Value="{count}" />{extra_parameters}</Parameters>
          <ParameterRefs><ParameterRef Id="{app_id}_P-1_R-1" RefId="{app_id}_P-1" />{extra_refs}</ParameterRefs>
        </Static><ModuleDefs>{"".join(modules)}</ModuleDefs>
        <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app_id}_PB-1" Text="Buttons">{extra_dynamic}</ParameterBlock>{"".join(uses)}</ChannelIndependentBlock></Dynamic>
      </ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>'''
    return parse_application_xml(xml.encode(), app_id.split("_")[0])[0]


class _Programmer:
    def __init__(self, profile, count=1, *, no_map=False):
        self.profile = profile
        self.reads = []
        self.tables = {}
        self.no_map = no_map
        for channel in range(count):
            base = profile.object_index_base + channel
            default = EcgProp(0, 0, 255, 255)
            props = {3: EcgProp(8, 8, 16, 12), 7: EcgProp(8, 2, 0, 2)}
            values = {
                P.EVG_PROP: b"".join(props.get(i, default).encode() for i in range(64)),
                P.EVG_STATUS: bytes(5 * 64),
                P.EVG_STATIC: EcgStatic(0xABCDEF, 10, 2700, 6500).encode() * 64,
                P.GROUP_DYN: bytes(6 * 16),
                P.GROUP_STATUS: bytes(element_size_for(P.GROUP_STATUS, profile) * 16),
                P.GROUP_FAILURE: bytes(6 * 16),
                P.SCENE_ASSIGN: bytes([7] * 16),
                P.SCENE_KNX_MAP: bytes(range(20, 36)),
                P.SCENE_GROUP_VALUE: bytes((0, 0, 255, 0x34, 0x12, 0, 0, 0))
                + bytes((1, 28, 51 + channel * 51, 1, 2, 3, 4, 0))
                + b"\xff" * 8,
                P.COLOUR_C_ASSIGN: b"\x01\x00" + bytes(8) + bytes(10 * 15),
                P.COLOUR_C_EVENT: TemplateProp(
                    0, 4, b"\xb8\x0b\x00\x00", 7, 31, 6, 30, 1, True
                ).encode()
                + b"\xff" * 10,
            }
            if profile.supports_extended_templates:
                values |= {
                    P.EFFECT_MODE: bytes([1] + [0] * 15),
                    P.EFFECT_STEP: bytes((0, 0, 255, 0x10, 0x0E, 0, 0, 0, 2, 7))
                    + b"\xff" * 10,
                    P.INPUT_TABLE: bytes(6 * 3)
                    + bytes.fromhex("123456036400")
                    + bytes(6 * 60),
                    P.INSTANCE_TABLE: bytes(8 * 100)
                    + bytes(
                        (
                            3,
                            4,
                            7,
                            10,
                            0,
                            1,
                            0x76,
                            0,
                            3,
                            1,
                            8,
                            1,
                            1,
                            0x11,
                            0x22,
                            0,
                            3,
                            2,
                            9,
                            8,
                            2,
                            0,
                            1,
                            0,
                        )
                    ),
                    P.INPUT_STATUS: bytes(5 * 103),
                    P.GTIN: (1234567890123).to_bytes(6, "big") * 128,
                    P.CONSTANT_LIGHT: bytes.fromhex("3300640055CC03E80320") * 8,
                    P.ECG_NAME: b"Gear".ljust(20, b"\0") * 64,
                    P.GROUP_NAME: b"Living".ljust(20, b"\0") * 16,
                    P.INPUT_NAME: b"Sensor".ljust(20, b"\0") * 8,
                }
            for prop, data in values.items():
                self.tables[base, property_id_for(prop, profile)] = (
                    element_size_for(prop, profile),
                    data,
                )

    def property_chunk_size(self, size):
        return min(15, 30 // size)

    async def read_property(self, object_index, property_id, *, count=1, start_index=1):
        self.reads.append((object_index, property_id, start_index, count))
        if object_index == 0 and property_id == 25:
            return bytes((4, 0, 1))
        if property_id == P.SCENE_KNX_MAP and self.no_map:
            return b""
        size, data = self.tables[object_index, property_id]
        if start_index == 0:
            return (len(data) // size).to_bytes(2, "big")
        return data[(start_index - 1) * size : (start_index - 1 + count) * size]

    async def write_property(self, *args, **kwargs):
        pytest.fail("Recovery must not write to the bus")


def _recovered(app, capture):
    return RecoveredDevice(
        "1.1.50",
        app.id,
        0x1132,
        [],
        [],
        {},
        RecoveredParameters({}, []),
        addin_data=capture,
    )


def _project(tmp_path, monkeypatch, app):
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    return project


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.key)
async def test_bus_snapshot_apply_undo_reopen_and_export(
    tmp_path, monkeypatch, profile
):
    count = 2 if profile.key == "mdt" else 1
    app = _application(profile.app_id_prefixes[0] + "-40-0000", count)
    programmer = _Programmer(profile, count, no_map=profile.key == "mdt")
    reader = dali.addin_reader(app)
    assert reader is not None
    captured = await reader(programmer)
    recovered = devices_from_snapshot(snapshots_json([_recovered(app, captured)]))[0]
    assert recovered.addin_data == captured
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
        names = [
            profile.backup_blob_base + (f".{ch}" if ch else "") for ch in range(count)
        ]
        blobs = [project.device_binary_data(device, name) for name in names]
        for channel, payload in enumerate(blobs):
            root = ET.fromstring(payload)
            assert root.find("devecgs/DevECG[@index='3']").get("short-address") == "12"
            assert root.find("ecgs/ECG[@index='12']").get("short-address") == "3"
            assert root.find("ecgs/ECG[@index='12']").get("flags") == "4"
            assert (
                root.find("scenes/Scene[@index='0']/SceneItem").get("color") == "4660"
            )
            second = root.find("scenes/Scene[@index='1']/SceneItem")
            assert (
                second.get("color-type"),
                second.get("color"),
                second.get("value"),
            ) == ("8", "1;2;3;4", str(20 + channel * 20))
            assert root.find("scenes/Scene[@index='0']").get("knxScene") == (
                "1" if profile.key == "mdt" else "20"
            )
            template = root.find("templates/Tpl/Commands/TplCmd")
            assert (
                template.get("value"),
                template.get("time"),
                template.get("fadeIn"),
            ) == ("3000", "06:30", "CFadeIn5")
            config = compile_configuration(payload)
            assert config.scenes[0].values[0].color == b"\x34\x12\x00\x00"
            if profile.supports_extended_templates:
                assert root.get("type") == "e64pro"
                assert root.find("groups/Group").get("description") == "Living"
                restored = prepare_restore(payload)
                assert (
                    restored.properties
                    == programmer.tables[profile.object_index_base, P.EVG_PROP][1]
                )
                assert (
                    restored.static
                    == programmer.tables[profile.object_index_base, P.EVG_STATIC][1]
                )
                assert (
                    restored.gtin[: 64 * 6]
                    == programmer.tables[profile.object_index_base, P.GTIN][1][: 64 * 6]
                )
                assert (
                    restored.calibration
                    == programmer.tables[profile.object_index_base, P.CONSTANT_LIGHT][1]
                )
                assert root.find("effects/Effect/EffectStep").get("color") == "3600"
                assert root.find("mds/MdInput").get("nop") == "6"
                assert (
                    root.find("mds/MdInput/Instances/EtsInstance[@index='6']").get(
                        "ref-idx"
                    )
                    == "100"
                )
                assert (
                    root.find("pbs/BtnInput/Instances/EtsInstance[@index='3']").get(
                        "ref-idx"
                    )
                    == "101"
                )
                assert (
                    root.find("gis/GenInput/Instances/EtsInstance").get("ref-idx")
                    == "102"
                )
                assert inputs_from_xml(root)[300:309] == bytes(
                    (0, 1, 0x76, 1, 0x11, 0x22, 2, 0, 1)
                )
                e64pro.import_configuration(
                    e64pro.export_configuration(payload), "1.1.50", ""
                )
        assert project.undo()
        assert project.cursor == cursor
        assert project.find_device_by_address("1.1.50") is None
        assert project.redo()
        project.close()
        project.open(tmp_path / "project.xknx")
        device = project.find_device_by_address("1.1.50")
        assert [project.device_binary_data(device, name) for name in names] == blobs
        export_knxproj(project.working_path, tmp_path / "recovered.knxproj")
        project.close()
        import_knxproj(tmp_path / "recovered.knxproj", tmp_path / "roundtrip.xknx")
        project.open(tmp_path / "roundtrip.xknx")
        device = project.find_device_by_address("1.1.50")
        assert [project.device_binary_data(device, name) for name in names] == blobs
    finally:
        project.close()


async def test_truncated_readback_is_not_applied(tmp_path, monkeypatch):
    profile = PROFILES[2]
    app = _application(profile.app_id_prefixes[0] + "-40-0000")
    programmer = _Programmer(profile)
    programmer.tables[7, P.EVG_PROP] = (4, bytes(4))
    reader = dali.addin_reader(app)
    with pytest.raises(DaliCommissioningError, match="short read"):
        await reader(programmer)
    capture = await reader(_Programmer(profile))
    rows = json.loads(capture[app.id]["dali-recovery.json"])
    rows[0]["scenes"].pop()
    capture[app.id]["dali-recovery.json"] = json.dumps(rows).encode()
    project = _project(tmp_path, monkeypatch, app)
    entry = RecoverEntry(
        "1.1.50",
        0x07B0,
        application=app,
        product_ref_id="P-1",
        hardware2program_ref_id="HP-1",
        recovered=_recovered(app, capture),
    )
    try:
        cursor = project.cursor
        assert not RecoverService(SimpleNamespace(project=project))._add_recovered(
            entry, {}
        )
        assert "Incomplete recovered DALI" in entry.error
        assert project.cursor == cursor
        assert project.find_device_by_address("1.1.50") is None
    finally:
        project.close()


@pytest.mark.parametrize(
    "field",
    [
        "state",
        "scenes",
        "templates",
        "effects",
        "inputs",
        "gtin",
        "calibration",
        "names",
    ],
)
async def test_partial_snapshot_is_rejected_before_persistence(field):
    profile = PROFILES[2]
    app = _application(profile.app_id_prefixes[0] + "-40-0000")
    capture = await dali.addin_reader(app)(_Programmer(profile))
    rows = json.loads(capture[app.id]["dali-recovery.json"])
    del rows[0][field]
    capture[app.id]["dali-recovery.json"] = json.dumps(rows).encode()
    device = Device(node_id=1, name="DALI", app=app, individual_address="1.1.50")
    with pytest.raises(ValueError, match="Incomplete recovered DALI configuration"):
        dali.apply_recovered(SimpleNamespace(), device, _recovered(app, capture))


@pytest.mark.parametrize("names", [{}, {"ecgs": [], "groups": [], "mds": []}])
async def test_incomplete_names_are_rejected_before_persistence(names):
    profile = PROFILES[2]
    app = _application(profile.app_id_prefixes[0] + "-40-0000")
    capture = await dali.addin_reader(app)(_Programmer(profile))
    rows = json.loads(capture[app.id]["dali-recovery.json"])
    rows[0]["names"] = names
    capture[app.id]["dali-recovery.json"] = json.dumps(rows).encode()
    device = Device(node_id=1, name="DALI", app=app, individual_address="1.1.50")
    with pytest.raises(ValueError, match="Incomplete recovered DALI descriptions"):
        dali.apply_recovered(SimpleNamespace(), device, _recovered(app, capture))


async def test_empty_bus_name_replaces_parameter_description():
    profile = PROFILES[2]
    app = _application(profile.app_id_prefixes[0] + "-40-0000")
    programmer = _Programmer(profile)
    programmer.tables[profile.object_index_base, P.GROUP_NAME] = (10, bytes(16 * 20))
    capture = await dali.addin_reader(app)(programmer)
    saved = []
    project = SimpleNamespace(persist_dali_backup=lambda *args: saved.append(args[2]))
    device = Device(node_id=1, name="DALI", app=app, individual_address="1.1.50")
    dali.apply_recovered(project, device, _recovered(app, capture))
    assert ET.fromstring(saved[0]).find("groups/Group").get("description") == ""


async def test_recovery_service_selects_dali_reader_before_connection_closes(
    tmp_path, monkeypatch
):
    from editor_gui.plugins.recover import service as service_module

    profile = PROFILES[2]
    app = _application(profile.app_id_prefixes[0] + "-40-0000")
    programmer = _Programmer(profile)
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
    entry = RecoverEntry("1.1.50", 0x07B0, application=app)
    try:
        await service._recover([entry])
        assert entry.error is None
        assert entry.recovered.addin_data == captured[0]
        assert {obj for obj, _, _, _ in programmer.reads} == {0, 7}
    finally:
        project.close()


async def test_incomplete_snapshot_cannot_overwrite_an_existing_project_device(
    tmp_path, monkeypatch
):
    profile = PROFILES[2]
    app = _application(profile.app_id_prefixes[0] + "-40-0000")
    reader = dali.addin_reader(app)
    capture = await reader(_Programmer(profile))
    project = _project(tmp_path, monkeypatch, app)
    service = RecoverService(SimpleNamespace(project=project))
    entry = RecoverEntry(
        "1.1.50",
        0x07B0,
        application=app,
        product_ref_id="P-1",
        hardware2program_ref_id="HP-1",
        recovered=_recovered(app, capture),
    )
    try:
        assert service._add_recovered(entry, {})
        original = project.find_device_by_address("1.1.50")
        saved = project.device_binary_data(original, profile.backup_blob_base)
        cursor = project.cursor
        invalid = _recovered(app, {app.id: {"dali-recovery.json": b"[]"}})
        entry.recovered = invalid
        assert not service._add_recovered(entry, {}, replace=original.node_id)
        assert project.cursor == cursor
        current = project.find_device_by_address("1.1.50")
        assert current.node_id == original.node_id
        assert project.device_binary_data(current, profile.backup_blob_base) == saved
    finally:
        project.close()


def test_non_dali_does_not_select_a_reader():
    assert dali.addin_reader(SimpleNamespace(id="M-00FA_A-A401-01-0000")) is None
    assert dali.addin_reader(SimpleNamespace(id="M-0001_A-9837-01-0000")) is None


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize(
    "sensors,presence,brightness", [(1, 2, 3), (2, 2, 0), (3, 0, 3)]
)
def test_parameter_derived_input_layouts(count, sensors, presence, brightness):
    app = _application("M-00EF_A-DA11-22-0000", count, sensors=sensors)
    device = Device(node_id=1, name="DALI", app=app, individual_address="1.1.50")
    for channel in range(count):
        root = ET.Element("device", type="e64pro")
        dali._logical_parameters(root, device, channel)
        motion = root.find("mds/MdInput[@index='1']")
        assert (
            motion.get("wclc"),
            motion.get("wclbct"),
            motion.get("consumption"),
        ) == (
            "2",
            "1",
            "10",
        )
        group = root.find("groups/Group[@index='0']")
        assert (group.get("cmode"), group.get("couple")) == ("1", "5")
        assert (motion.get("nop"), motion.get("nob"), motion.get("sensors-avail")) == (
            str(presence),
            str(brightness),
            str(sensors),
        )
        assert [row.get("type") for row in motion.findall("Instances/EtsInstance")] == [
            "MotionSensor"
        ] * presence + ["BrightnessSensor"] * brightness
        generic = root.find("gis/GenInput[@index='2']")
        assert generic.get("type") == "4"
        assert generic.find("Instances/EtsInstance").get("type") == "GenericSensor"
        button = root.find("pbs/BtnInput[@index='0']")
        assert (button.get("fold"), button.get("noi")) == ("2", "3")
        assert len(button.findall("Instances/EtsInstance")) == 12


def test_real_mdt_application_resolves_two_channels_and_scoped_color_overrides():
    root = next(p for p in Path(__file__).parents if (p / "packages/dali").exists())
    source = (
        root
        / ".references/mdt_dali_64/knxprod_x/M-0083/M-0083_A-0154-40-0F69-O00EF.xml"
    )
    if not source.exists():
        pytest.skip("Optional MDT application is not installed")
    app = parse_application_xml(source.read_bytes(), "M-0083")[0]
    assert dali.channel_count(app) == 2
    device = Device(node_id=1, name="DALI", app=app, individual_address="1.1.50")
    ui = device.dynamic_ui
    instances = ui.get_module_instances()
    first = next(
        instance for instance, definition in instances if definition.endswith("_MD-1")
    )
    second = next(
        instance for instance, definition in instances if definition.endswith("_MD-2")
    )
    for instance, value in ((first, "1"), (second, "3")):
        ui.apply_parameter_values(
            {
                ui.find_parameter_ref(
                    "name", "DCA1-CC-ColourControlType", instance
                ): value
            }
        )
    for channel, expected in ((0, "4"), (1, "6")):
        root = ET.Element("device")
        dali._logical_parameters(root, device, channel)
        assert root.find("groups/Group[@index='0']").get("ctype") == expected
