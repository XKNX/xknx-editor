"""The download of a Generic Plug-in device, as each version of the plug-in makes it.

The application and the description are small, made-up documents in the product data's and
the plug-in's XML dialects: no manufacturer product data is used.
"""

import asyncio
from dataclasses import replace

import pytest

from packages.download.tests.conftest import FakeDevice
from packages.download.tests.test_prepared import master
from xknxeditor.download.conditions import (
    ConditionalRead,
    ConditionalWrite,
    PropertyAlternatives,
)
from xknxeditor.download.download import prepared_controls
from xknxeditor.download.image import GroupCommunication
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem
from xknxeditor.prod import parse_application_xml
from xknxeditor.vendor.siemens_generic import (
    find_sources,
    parse_settings,
    plugin_version,
)
from xknxeditor.vendor.siemens_generic.configuration import (
    default_config,
    downloaded_config,
    edit,
)
from xknxeditor.vendor.siemens_generic.description import Description
from xknxeditor.vendor.siemens_generic.image import compile_image
from xknxeditor.vendor.siemens_generic.prepare import prepare_image, validate_profile
from xknxeditor.vendor.siemens_generic.recover import (
    DeviceImage,
    decode_device,
    image_of,
    read_device_image,
)

MAPPING = {0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: 4, 6: 0xFFFF}

APPLICATION = b"""<?xml version="1.0"?>
<KNX xmlns="http://knx.org/xml/project/14"><ManufacturerData><Manufacturer RefId="M-0001">
<ApplicationPrograms><ApplicationProgram Id="M-0001_A-9821-01-0000"
 ApplicationNumber="38945" ApplicationVersion="1" ProgramType="ApplicationProgram"
 MaskVersion="MV-07B0" Name="Demo" LoadProcedureStyle="MergedProcedure" PeiType="0"
 DefaultLanguage="en-US" DynamicTableManagement="false" Linkable="false"><Static>
 <Code>
  <RelativeSegment Id="M-0001_A-9821-01-0000_RS-01-00000" Offset="0" Size="65536"
   LoadStateMachine="1"/>
  <RelativeSegment Id="M-0001_A-9821-01-0000_RS-02-00000" Offset="0" Size="65536"
   LoadStateMachine="2"/>
 </Code>
 <ComObjectTable><ComObject Id="M-0001_A-9821-01-0000_O-0" Name="Switch" Text="Switch"
  Number="0" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled"
  CommunicationFlag="Enabled" TransmitFlag="Disabled" UpdateFlag="Disabled"
  ReadOnInitFlag="Disabled" Priority="Low"/></ComObjectTable>
 <ComObjectRefs><ComObjectRef Id="M-0001_A-9821-01-0000_O-0_R-1"
  RefId="M-0001_A-9821-01-0000_O-0"/></ComObjectRefs>
 <LoadProcedures><LoadProcedure MergeId="1">
  <LdCtrlRelSegment LsmIdx="1" Size="65536" Mode="0" Fill="0"/>
  <LdCtrlRelSegment LsmIdx="2" Size="65536" Mode="0" Fill="0"/>
 </LoadProcedure></LoadProcedures>
</Static></ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _description(
    version: tuple[int, int, int] | None,
    *,
    schema: str = "GenericPlugIn_4.xsd",
    segment: str = '<CodeSegment Id="s" Address="0x5000" AllocLength="64"/>',
    hardware: str = '<HwType Data="000000000052"/>',
    root: str = 'HardwareType="000000000051"',
    application_ids: str = "",
) -> Description:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationProgram Id="id-0001_9821_01" {root}
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:noNamespaceSchemaLocation="{schema}"><Static>
 <DeviceDownloadCompatibility><HardwareTypes>{hardware}</HardwareTypes>
 {application_ids}</DeviceDownloadCompatibility>
 <CodeSegments>{segment}</CodeSegments>
 <ParameterTypes>
  <ParameterType Id="t_yn"><TypeRestriction Size="8">
   <Enumeration Id="e_no" Name="yn_No" Text="No" Value="0"/>
   <Enumeration Id="e_yes" Name="yn_Yes" Text="Yes" Value="1"/>
  </TypeRestriction></ParameterType>
  <ParameterType Id="t_byte"><TypeNumber Size="8"/></ParameterType>
  <ParameterType Id="t_name"><TypeText Size="80"/></ParameterType>
 </ParameterTypes>
 <Parameters>
  <Parameter Id="p_on" RefId="t_yn" Value="yn_No"><Memory CodeSegment="s" Offset="0"/></Parameter>
  <Parameter Id="p_keep" RefId="t_yn" Value="yn_Yes"><Memory CodeSegment="s" Offset="1"/></Parameter>
  <Parameter Id="p_scene" RefId="t_byte" Value="7" DownloadIfParameterRefId="r_keep">
   <Memory CodeSegment="s" Offset="2"/></Parameter>
  <Parameter Id="p_name" RefId="t_name" Value="Hall"><Memory CodeSegment="s" Offset="8"/></Parameter>
  <Parameter Id="p_level" RefId="t_byte" Value="1"><Memory CodeSegment="s" Offset="24"/></Parameter>
 </Parameters>
 <ParameterRefs>
  <ParameterRef Id="r_on" RefId="p_on"/>
  <ParameterRef Id="r_keep" RefId="p_keep"/>
  <ParameterRef Id="r_scene" RefId="p_scene"/>
  <ParameterRef Id="r_name" RefId="p_name"/>
  <ParameterRef Id="r_level" RefId="p_level"/>
 </ParameterRefs>
 <ComObjectTable><ComObject Id="o_switch" Number="0" Text="Switch" Size="1 Bit"
  CommunicationFlag="Enabled" WriteFlag="Enabled"/></ComObjectTable>
 <ComObjectRefs><ComObjectRef Id="or_switch" RefId="o_switch"/></ComObjectRefs>
 </Static><Dynamic>
 <ChannelIndependentBlock><ParameterBlock Id="b" Text="General">
  <ParameterRefRef RefId="r_on"/>
  <ParameterRefRef RefId="r_keep"/>
  <ParameterRefRef RefId="r_name"/>
  <ParameterRefRef RefId="r_level"/>
  <choose ParamRefId="r_on"><when test="1"><ParameterRefRef RefId="r_scene"/></when></choose>
  <ComObjectRefRef RefId="or_switch"/>
 </ParameterBlock></ChannelIndependentBlock>
 </Dynamic></ApplicationProgram>""".encode()
    setup = (
        "M-0001_BG-982101-"
        + (
            "PlugInSetup.5FV{}.5F{}.5F{}.2Emsi".format(*version)
            if version is not None
            else "Readme.2Etxt"
        ),
    )
    sources = find_sources(
        ("M-0001_BG-982101-0001.5F9821.5F01.5FGenericPlugInAll.2Exml", *setup),
        lambda ref: xml if ref.endswith("All.2Exml") else None,
    )
    assert sources is not None
    return sources.description


def _application():
    return parse_application_xml(APPLICATION, "M-0001")[0]


def _prepared(description: Description, data: bytes | None = None):
    settings = parse_settings(data or default_config(description))
    assert settings is not None
    compiled = compile_image(description, settings)
    communication = GroupCommunication(
        device_address=0x1101,
        links=(),
        group_object_descriptors={0: (0xD7, 0)},
    )
    app = _application()
    prepared = prepare_image(app, description, compiled, {}, communication)
    return compiled, prepared, prepared_controls(app, prepared, master())


def _names(controls: list[object]) -> list[str]:
    """The controls as short names, the writes of the parameter memory as one."""
    names: list[str] = []
    for control in controls:
        name = type(control).__name__.removeprefix("LdCtrl")
        if isinstance(control, LdCtrlWriteMem) or (
            isinstance(control, LdCtrlWriteRelMem) and control.obj_idx == 4
        ):
            name = "Parameters"
        elif isinstance(control, LdCtrlRelSegment):
            name = f"RelSegment{control.lsm_idx}"
        elif isinstance(control, LdCtrlCompareProp):
            name = f"Compare{control.obj_idx}/{control.prop_id}"
        if not names or names[-1] != name or name != "Parameters":
            names.append(name)
    return names


def test_the_plugin_version_comes_from_its_setup_in_the_baggage():
    assert plugin_version(
        [
            "M-0001_BG-982003-0001.5F9820.5F03.5Fen.2DUS.2Exml",
            "X-PlugInSetup.5FV2.5F0.5F37.2Emsi",
        ]
    ) == (2, 0, 37)
    assert plugin_version(["SiemensPlugInSetup_V1_2_7.msi"]) == (1, 2, 7)
    assert plugin_version(["M-0001_BG-982101-0001.5F9821.5F01.5Fen.2DUS.2Exml"]) is None
    assert _description((1, 2, 16)).plugin_version == (1, 2, 16)
    assert _description(None).plugin_version is None


def test_a_first_plugin_takes_the_application_s_own_hardware_type():
    d = _description((1, 2, 16), schema="GenericPlugIn_1.xsd", hardware="")
    assert d.hardware_types == ()
    assert d.compatible_hardware == (bytes.fromhex("000000000051"),)
    validate_profile(_application(), d)
    later = _description((2, 0, 37))
    assert later.compatible_hardware == (bytes.fromhex("000000000052"),)
    without = _description((2, 0, 37), hardware="")
    with pytest.raises(ValueError, match="hardware compatibility"):
        validate_profile(_application(), without)
    with pytest.raises(ValueError, match="hardware compatibility"):
        validate_profile(_application(), _description((1, 2, 7), root=""))


def test_an_unknown_first_plugin_download_is_refused():
    with pytest.raises(ValueError, match=r"version 1\.2\.12"):
        validate_profile(_application(), _description((1, 2, 12)))


def test_plugin_1_2_7_loads_the_tables_before_the_application():
    d = _description((1, 2, 7), schema="GenericPlugIn_1.xsd", hardware="")
    _compiled, _prepared_download, controls = _prepared(d)
    names = _names(controls)
    application = names.index("RelSegment4")
    assert names.index("RelSegment3") < application < names.index("Parameters")
    assert names[:2] == ["Compare4/13", "PropertyAlternatives"]
    assert isinstance(controls[2], ConditionalRead)
    assert (controls[2].read.obj_idx, controls[2].read.prop_id) == (6, 203)
    # The tables are checked as loaded before the application is loaded again.
    assert names[application - 4 : application] == [
        "Compare1/5",
        "Compare2/5",
        "Compare3/5",
        "Load",
    ]
    assert names.count("RelSegment4") == 1
    assert not any(isinstance(c, ConditionalWrite) for c in controls)
    flags = [c.inline_data for c in controls if isinstance(c, LdCtrlWriteProp)]
    assert flags[-1] == b"\x00"
    # The device's application of the same number from the product's version on.
    check = controls[0]
    assert isinstance(check, LdCtrlCompareProp)
    assert check.range == f"[{0x0001982101},{0x00019821FF}]"


def test_later_plugins_load_the_application_around_the_tables():
    for version in ((1, 2, 16), (2, 0, 37), None):
        d = _description(version)
        _compiled, _prepared_download, controls = _prepared(d)
        names = _names(controls)
        assert names.count("RelSegment4") == 2
        assert (
            names.index("RelSegment4")
            < names.index("Parameters")
            < names.index("RelSegment3")
        )
        assert any(isinstance(c, ConditionalWrite) for c in controls)
        flags = [c.inline_data for c in controls if isinstance(c, LdCtrlWriteProp)]
        assert flags[-1] == b"\x10"
    # 1.2.16 takes the application of the same number up to the product's version.
    check = _prepared(_description((1, 2, 16)))[2][0]
    assert isinstance(check, LdCtrlCompareProp)
    assert check.range == f"[{0x0001982100},{0x0001982101}]"
    # A later one the description's application ids, or any application.
    assert not isinstance(_prepared(_description((2, 0, 37)))[2][0], LdCtrlCompareProp)
    ids = '<ApplicationIDs><ApplicationID AppId="00019821**"/></ApplicationIDs>'
    gate = _prepared(_description((2, 0, 37), application_ids=ids))[2][0]
    assert isinstance(gate, PropertyAlternatives)
    assert gate.comparisons[0].mask == b"\xff\xff\xff\xff\0"


def test_a_first_plugin_keeps_the_whole_address_space_in_its_image():
    d = _description((1, 2, 16), schema="GenericPlugIn_1.xsd", hardware="")
    compiled, prepared, _controls = _prepared(d)
    assert (compiled.base, compiled.start) == (0, 0x5000)
    assert compiled.data[0x5000:0x5003] == b"\x00\x01\x07"
    # The download writes the parameter memory alone.
    (memory,) = prepared.image.segments
    assert memory.address == 0x5000 and memory.data[:3] == b"\x00\x01\x07"
    # The stored image is the plug-in's: byte n stands for address n.
    stored = parse_settings(
        downloaded_config(default_config(d), compiled.data, compiled.mask, bytes(8))
    )
    assert stored is not None and stored.memory_image is not None
    assert stored.memory_image[0x5018] == 1
    # And a device read gives the configuration back.
    held = image_of(compiled)
    assert held.base == 0x5000 and len(held.data) == 25
    assert decode_device(d, default_config(d), held) is not None
    later = compile_image(
        _description((2, 0, 37)), parse_settings(default_config(_description(None)))
    )
    assert (later.base, later.start) == (0x5000, 0x5000)


def _relative() -> Description:
    return _description(
        (2, 0, 36),
        segment='<CodeSegment Id="s" Address="0x1000" AllocLength="8192"'
        ' UseRelDownloadSubThisOffset="0"/>',
    )


def test_a_relative_download_writes_from_the_application_s_segment():
    d = _relative()
    assert d.download_offset == 0 and not d.image_unsupported
    compiled, prepared, controls = _prepared(d)
    writes = [
        c for c in controls if isinstance(c, LdCtrlWriteRelMem) and c.obj_idx == 4
    ]
    # Eight octets per write, at the address less the offset.
    assert [(w.offset, w.size) for w in writes] == [
        (0x1000, 3),
        (0x1008, 8),
        (0x1010, 2),
        (0x1018, 1),
    ]
    absolute = _prepared(_description((2, 0, 36)))[2]
    assert [(w.address, w.size) for w in absolute if isinstance(w, LdCtrlWriteMem)] == [
        (0x5000, 3),
        (0x5008, 10),
        (0x5018, 1),
    ]
    assert not prepared.image.segments
    assert not any(isinstance(c, LdCtrlWriteMem) for c in controls)

    fake = FakeDevice(MAPPING, 0x07B0)
    fake.properties[0, 78] = bytes.fromhex("000000000052")
    fake.properties[4, 13] = bytes.fromhex("0001982101")
    fake.property_element_sizes[4, 13] = 5
    fake.property_element_sizes[0, 78] = 6
    fake.properties[6, 205] = b"\0"
    fake.property_element_sizes[6, 205] = 1
    fake.properties[4, 27] = bytes(8)
    fake.property_element_sizes[4, 27] = 8
    fake.table_references = {i: 0x4000 * i for i in range(1, 6)}
    runner = LoadProcedureRunner(
        _application(),
        prepared.image,
        DeviceProgrammer(fake),
        controls=controls,
        object_types=MAPPING,
    )
    asyncio.run(runner.run())
    segment = fake.table_references[4]
    assert bytes(fake.memory.get(segment + 0x1000 + i, 0xAA) for i in range(5)) == (
        b"\x00\x01\x07\xaa\xaa"
    )
    assert bytes(fake.memory.get(segment + 0x1008 + i, 0) for i in range(4)) == b"Hall"
    assert fake.memory.get(segment + 0x1018) == 1
    assert compiled.relative_offset == 0


def test_a_relative_device_read_reads_from_the_application_s_segment():
    d = _relative()
    compiled = compile_image(d, parse_settings(edit(d, default_config(d), "r_on", "1")))
    held = image_of(compiled)

    class Reader:
        def __init__(self) -> None:
            self.reads: list[tuple[int, int]] = []

        async def read_memory(self, address: int, count: int, *, user: bool = False):
            self.reads.append((address, count))
            return bytes(memory.get(a, 0) for a in range(address, address + count))

        async def read_property(self, index: int, pid: int, **_: object) -> bytes:
            return (0x2000).to_bytes(4, "big") if pid == 7 else bytes(8)

    memory = {0x2000 + 0x1000 + i: b for i, b in enumerate(held.data)}
    reader = Reader()
    image = asyncio.run(read_device_image(reader, d))
    assert reader.reads == [(0x3000, 25)]
    assert image.data == held.data


@pytest.mark.parametrize(
    ("version", "keep", "on", "locked"),
    [
        ((2, 0, 36), "0", "0", True),  # every setting of the tree, also a hidden one
        ((2, 0, 36), "0", "1", True),
        ((2, 0, 36), "1", "1", False),
        ((2, 0, 11), "0", "1", False),  # only a ParameterRef's own condition
        ((1, 2, 16), "0", "1", False),  # no condition at all
    ],
)
def test_a_setting_whose_download_condition_is_0_is_not_written(
    version, keep, on, locked
):
    d = _description(version)
    data = edit(d, default_config(d), "r_keep", keep)
    if on == "1":
        data = edit(d, data, "r_on", on)
    compiled = compile_image(d, parse_settings(data))
    offset = 0x5002 - compiled.base
    assert compiled.mask[offset] == (0 if locked else 0xFF)
    assert compiled.data[offset] == 7
    # A device read keeps the setting's configured value: the memory does not hold it.
    held = image_of(compiled)
    device = DeviceImage(
        held.base, held.data[:2] + b"\x63" + held.data[3:], held.segment_info
    )
    recovered = parse_settings(decode_device(d, data, device))
    assert recovered is not None
    assert recovered.values.get("r_scene", "7") == ("7" if locked else "99")


def test_a_relative_offset_before_the_memory_is_refused():
    d = replace(_relative(), relative_offset=0x2000)
    with pytest.raises(ValueError, match="relative download offset"):
        compile_image(d, parse_settings(default_config(d)))


def test_a_condition_read_runs_only_on_a_loaded_application():
    d = _description((1, 2, 7), schema="GenericPlugIn_1.xsd", hardware="")
    controls = _prepared(d)[2]
    for loaded in (False, True):
        fake = FakeDevice(MAPPING, 0x07B0)
        fake.load_states[4] = LoadState.LOADED if loaded else LoadState.UNLOADED
        fake.properties[6, 203] = b"\x05"
        fake.property_element_sizes[6, 203] = 1
        runner = LoadProcedureRunner(
            _application(),
            _prepared(d)[1].image,
            DeviceProgrammer(fake),
            controls=[controls[2]],
            object_types=MAPPING,
        )
        asyncio.run(runner.run())
        assert ((6, 203, 1) in runner.property_reads) == loaded
        assert not runner.state_mutated


def test_the_last_controls_complete_the_application():
    controls = _prepared(_description((1, 2, 7), hardware=""))[2]
    completed = [c.lsm_idx for c in controls if isinstance(c, LdCtrlLoadCompleted)]
    assert completed[-1] == 4
