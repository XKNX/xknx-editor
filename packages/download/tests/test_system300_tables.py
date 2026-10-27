"""The group communication tables of a System 300 device (mask 0300, property based): the
product's own WriteProp controls write them as properties, with the number of elements of each
table, the group addresses ascending, the links sending first, and each object's flags."""

from __future__ import annotations

from dataclasses import dataclass

from xknx.telegram.apci import PropertyValueWrite

from xknxeditor.download.group_communication import (
    materialize_group_communication_controls,
)
from xknxeditor.download.image import GroupCommunication, build_image
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml

from .conftest import FakeDevice

_APP = "M-00FD_A-4301-10-0000"
_MASK = "MV-0300-01000001000000000000"
_GA_LOW = 0x0802
_GA_HIGH = 0x0901

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FD">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="17153" ApplicationVersion="16"
            ProgramType="ApplicationProgram" MaskVersion="{_MASK}" Name="System 300"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-DPT" Name="DPT">
                <TypeNumber SizeInBit="32" Type="unsignedInt" minInclusive="0" maxInclusive="4294967295" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Object 1 type" ParameterType="{_APP}_PT-DPT" Text="Type" Access="None" Value="65537">
                <Property ObjectType="9" PropertyId="51" Offset="2" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1" />
            </ParameterRefs>
            <ComObjectTable>
              <ComObject Id="{_APP}_O-1" Name="Switch" Text="Switch" Number="1" FunctionText="Switch" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Enabled" DatapointType="DPST-1-1" />
              <ComObject Id="{_APP}_O-3" Name="Status" Text="Status" Number="3" FunctionText="Status" ObjectSize="1 Bit" ReadFlag="Enabled" WriteFlag="Disabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />
            </ComObjectTable>
            <ComObjectRefs>
              <ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" />
              <ComObjectRef Id="{_APP}_O-3_R-3" RefId="{_APP}_O-3" />
            </ComObjectRefs>
            <AddressTable MaxEntries="40" />
            <AssociationTable MaxEntries="40" />
            <LoadProcedures>
              <LoadProcedure>
                <LdCtrlUnload ObjType="1" />
                <LdCtrlUnload ObjType="2" />
                <LdCtrlUnload ObjType="9" />
                <LdCtrlLoad ObjType="1" />
                <LdCtrlWriteProp ObjType="1" PropId="23" Count="0" Verify="false" InlineData="00000000000000" />
                <LdCtrlLoadCompleted ObjType="1" />
                <LdCtrlLoad ObjType="2" />
                <LdCtrlWriteProp ObjType="2" PropId="23" Count="0" Verify="false" InlineData="00000000000000" />
                <LdCtrlLoadCompleted ObjType="2" />
                <LdCtrlLoad ObjType="9" />
                <LdCtrlWriteProp ObjType="9" PropId="51" Count="0" Verify="false" InlineData="00000000000000" AppliesTo="all" />
                <LdCtrlWriteProp ObjType="9" PropId="52" Count="0" Verify="false" InlineData="00000000000000" AppliesTo="all" />
                <LdCtrlLoadCompleted ObjType="9" />
                <LdCtrlRestart />
              </LoadProcedure>
            </LoadProcedures>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="Objects">
                <ParameterRefRef RefId="{_APP}_P-1_R-1" />
              </ParameterBlock>
              <ComObjectRefRef RefId="{_APP}_O-1_R-1" />
              <ComObjectRefRef RefId="{_APP}_O-3_R-3" />
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""

# Object index -> type on the device: the address, association and group object tables.
_OBJECTS = {0: 0, 1: 1, 2: 2, 3: 9}


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FD")[0]


def _image(app: Application):  # type: ignore[no-untyped-def]
    links = [
        GroupObjectLink(f"{_APP}_O-1_R-1", _GA_HIGH, sending=True),
        GroupObjectLink(f"{_APP}_O-1_R-1", _GA_LOW, sending=False),
        GroupObjectLink(f"{_APP}_O-3_R-3", _GA_LOW, sending=False),
    ]
    return build_image(app, group_communication=GroupCommunication(0x1105, links))


def _controls(app: Application, scope: DownloadScope) -> list[object]:
    return materialize_group_communication_controls(
        _image(app),
        resolve_download_controls(app, None, scope=scope),
        {},
        scope,
        property_tables=True,
    )


@dataclass
class _Written:
    """The elements written to one property, joined over its frames."""

    count: int = 0
    data: bytes = b""


def _writes(device: FakeDevice) -> dict[tuple[int, int], _Written]:
    written: dict[tuple[int, int], _Written] = {}
    frames = sorted(
        (w for w in device.sent if isinstance(w, PropertyValueWrite)),
        key=lambda w: (w.object_index, w.property_id, w.start_index),
    )
    for w in frames:
        entry = written.setdefault((w.object_index, w.property_id), _Written())
        entry.count += w.count
        entry.data += w.data
    return written


async def _download(
    scope: DownloadScope, *, association_width: int = 2
) -> dict[tuple[int, int], _Written]:
    app = _application()
    device = FakeDevice(object_types=_OBJECTS, descriptor=0x0300)
    device.property_element_sizes.update(
        {(1, 23): 2, (2, 23): association_width, (3, 51): 6}
    )
    # What the device holds before: its own octets in every group object element.
    device.properties[(3, 51)] = b"\xee" * 18
    programmer = DeviceProgrammer(device)
    programmer.skip_property_verify_probe()
    await LoadProcedureRunner(
        app,
        _image(app),
        programmer,
        controls=resolve_download_controls(app, None, scope=scope),
        scope=scope,
    ).run()
    return _writes(device)


def test_the_tables_are_built_from_the_links() -> None:
    tables = _image(_application()).property_tables
    assert tables is not None
    assert tables.address_data == bytes.fromhex("0802 0901")
    # Sending first, then by object number; each link names its address by its element.
    assert tables.association_data(2) == bytes.fromhex("0201 0101 0103")
    assert tables.association_data(4) == bytes.fromhex("00020001 00010001 00010003")
    # Objects 1..3, with object 2 a hole; bit 5 (read on init) is left out.
    assert len(tables.object_flags) == 3
    assert tables.object_flags[0] & 0x20 == 0
    assert tables.object_flags[0] & 0x5C == 0x54  # communication, write, transmit
    assert tables.object_flags[1] == 0x03


def test_each_table_write_gets_its_number_of_elements() -> None:
    app = _application()
    writes = {
        (c.obj_type, c.prop_id): c.count
        for c in _controls(app, DownloadScope.FULL)
        if isinstance(c, LdCtrlWriteProp)
    }
    assert writes == {(1, 23): 2, (2, 23): 3, (9, 51): 3, (9, 52): 3}
    # A group communication download leaves the references to the parameters.
    grp = {
        (c.obj_type, c.prop_id)
        for c in _controls(app, DownloadScope.GROUP_COMMUNICATION)
        if isinstance(c, LdCtrlWriteProp)
    }
    assert grp == {(1, 23), (2, 23), (9, 51)}
    # A parameters download writes no table, and no empty table write is left either.
    assert not [
        c
        for c in _controls(app, DownloadScope.PARAMETERS)
        if isinstance(c, LdCtrlWriteProp) and c.count == 0
    ]


async def test_a_full_download_writes_the_tables() -> None:
    writes = await _download(DownloadScope.FULL)
    assert writes[(1, 23)].data == bytes.fromhex("0802 0901")
    assert writes[(1, 23)].count == 2
    assert writes[(2, 23)].data == bytes.fromhex("0201 0101 0103")
    objects = writes[(3, 51)]
    assert objects.count == 3
    # Object 1: its flags, and its type from the parameter (65537 = 0x00010001).
    assert objects.data[1] & 0x5C == 0x54
    assert objects.data[2:6] == bytes.fromhex("00010001")
    # Object 2 is a hole.
    assert objects.data[6:12] == bytes.fromhex("000300000000")


async def test_four_octet_associations_follow_the_device() -> None:
    writes = await _download(DownloadScope.FULL, association_width=4)
    assert writes[(2, 23)].data == bytes.fromhex("00020001 00010001 00010003")


async def test_a_group_communication_download_keeps_the_device_bytes() -> None:
    writes = await _download(DownloadScope.GROUP_COMMUNICATION)
    # Only the flags are written; the device's other octets stay.
    objects = writes[(3, 51)].data
    assert objects[0] == 0xEE
    assert objects[1] & 0x5C == 0x54
    assert objects[2:6] == b"\xee" * 4
    assert objects[7] == 0x03
    assert (3, 52) not in writes


def test_the_descriptor_completes_a_plain_system_300_mask() -> None:
    from xknxeditor.download.merge import device_mask_version

    xml = _XML.replace(f'MaskVersion="{_MASK}"', 'MaskVersion="MV-0300"').replace(
        "<LoadProcedure>",
        '<LoadProcedure><LdCtrlCompareProp ObjIdx="0" PropId="72" '
        'InlineData="01000001000000000000" />',
    )
    app = parse_application_xml(xml.encode(), "M-00FD")[0]
    assert app.program.mask_version == "MV-0300"
    assert device_mask_version(app) == _MASK
    assert _image(app).property_tables is not None


_LEGACY = "M-00FD_A-9001-10-0000"
_LEGACY_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FD">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_LEGACY}" ApplicationNumber="36865" ApplicationVersion="16"
            ProgramType="ApplicationProgram" MaskVersion="MV-0300-01000000000000000000"
            Name="System 300 memory" LoadProcedureStyle="ProductProcedure" PeiType="0"
            DefaultLanguage="en-US" DynamicTableManagement="false" Linkable="false"
            MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_LEGACY}_AS-0100" Address="256" Size="23"><Data>AQIDBAUGBwgJCgsMDQ4PEBESExQVFhc=</Data></AbsoluteSegment>
              <AbsoluteSegment Id="{_LEGACY}_AS-0124" Address="292" Size="8"><Data>AgAAAAAAAAA=</Data></AbsoluteSegment>
            </Code>
            <ComObjectTable CodeSegment="{_LEGACY}_AS-0124" Offset="0">
              <ComObject Id="{_LEGACY}_O-1" Name="Switch" Text="Switch" Number="1" FunctionText="Switch" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />
            </ComObjectTable>
            <ComObjectRefs>
              <ComObjectRef Id="{_LEGACY}_O-1_R-1" RefId="{_LEGACY}_O-1" />
            </ComObjectRefs>
            <AddressTable MaxEntries="2" />
            <AssociationTable MaxEntries="2" />
            <LoadProcedures>
              <LoadProcedure>
                <LdCtrlUnload LsmIdx="1" />
                <LdCtrlUnload LsmIdx="4" />
                <LdCtrlLoad LsmIdx="1" />
                <LdCtrlAbsSegment LsmIdx="1" SegType="0" Address="278" Size="8" Access="51" MemType="2" SegFlags="129" />
                <LdCtrlLoadCompleted LsmIdx="1" />
                <LdCtrlLoad LsmIdx="4" />
                <LdCtrlAbsSegment LsmIdx="4" SegType="0" Address="256" Size="23" Access="51" MemType="3" SegFlags="131" />
                <LdCtrlLoadCompleted LsmIdx="4" />
                <LdCtrlTaskSegment LsmIdx="5" Address="292" />
                <LdCtrlRestart />
              </LoadProcedure>
            </LoadProcedures>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ComObjectRefRef RefId="{_LEGACY}_O-1_R-1" />
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


async def test_a_memory_configuration_writes_only_its_eeprom_segment() -> None:
    from xknx.telegram.apci import MemoryWrite

    from xknxeditor.download.load_state import LoadState

    app = parse_application_xml(_LEGACY_XML.encode(), "M-00FD")[0]
    links = [
        GroupObjectLink(f"{_LEGACY}_O-1_R-1", _GA_HIGH, sending=True),
        GroupObjectLink(f"{_LEGACY}_O-1_R-1", _GA_LOW, sending=False),
    ]
    image = build_image(app, group_communication=GroupCommunication(0x1105, links))
    device = FakeDevice(descriptor=0x0300)
    programmer = DeviceProgrammer(device)
    programmer.skip_property_verify_probe()
    await LoadProcedureRunner(
        app,
        image,
        programmer,
        controls=resolve_download_controls(app, None, scope=DownloadScope.FULL),
        scope=DownloadScope.FULL,
    ).run()
    written = {
        w.address + i
        for w in device.sent
        if isinstance(w, MemoryWrite)
        for i in range(len(w.data))
    }
    # The EEPROM segment, and in it the count of the group address table (1 + 2).
    assert written == set(range(256, 279))
    assert device.memory[278] == 3
    # The RAM segment of the table is only allocated; LSM 5 is not announced.
    assert device.load_states[1] == LoadState.LOADED
    assert 5 not in device.load_states
