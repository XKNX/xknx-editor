"""Hidden settings stored in the group communication tables of a BCU2 application: "Flags"
in the flag byte of group object 0, "Associations" in the count of the association table.
A full download writes the tables over them: the flag byte from the object (its
communication bit from the links), the association table with a slot per object. What
the settings encode never reaches the device, so a result is not told apart by those bits.
A bit of the flag byte the table keeps from the segment (bit 5) is still the setting's.

"Delay", shown, is stored behind the group object table.
"""

from __future__ import annotations

import base64

from xknxeditor.download import GroupCommunication, build_image, group_table_bits
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F9-01-0000"
_ADDRESSES = f"{_APP}_AS-0116"
_ASSOCIATIONS = f"{_APP}_AS-011E"
_OBJECTS = f"{_APP}_AS-0130"
_FLAGS = f"{_APP}_P-1_R-1"
_COUNT = f"{_APP}_P-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"
# Count 2, the RAM flags table pointer, two records (value pointer, flags, type) and the
# byte "Delay" is stored at, with a spare one.
_OBJECT_SEED = "02d0004f00014f000500"
_LAYOUT = {_ADDRESSES: (0x0116, 8), _ASSOCIATIONS: (0x011E, 7), _OBJECTS: (0x0130, 10)}


def _data(hex_octets: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_octets)).decode()


def _application(mask: str = "MV-0021") -> Application:
    """The application, for the device mask ``mask``."""
    flags = 'ReadFlag="Enabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"'
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="249" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="{mask}" Name="Settings in tables"
        LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="true" MinEtsVersion="4.0">
      <Static>
        <Code>
          <AbsoluteSegment Id="{_ADDRESSES}" Address="278" Size="8"><Data>{_data("0100000000000000")}</Data></AbsoluteSegment>
          <AbsoluteSegment Id="{_ASSOCIATIONS}" Address="286" Size="7"><Data>{_data("00000000000000")}</Data></AbsoluteSegment>
          <AbsoluteSegment Id="{_OBJECTS}" Address="304" Size="10"><Data>{_data(_OBJECT_SEED)}</Data></AbsoluteSegment>
        </Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-F" Name="F"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="17" Value="23" Id="{_APP}_PT-F_EN-23" />
            <Enumeration Text="4F" Value="79" Id="{_APP}_PT-F_EN-79" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{_APP}_PT-Z" Name="Z"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="Zero" Value="0" Id="{_APP}_PT-Z_EN-0" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{_APP}_PT-D" Name="D"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="60" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Flags" ParameterType="{_APP}_PT-F" Text="" Access="None" Value="79">
            <Memory CodeSegment="{_OBJECTS}" Offset="3" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-2" Name="Associations" ParameterType="{_APP}_PT-Z" Text="" Access="None" Value="0">
            <Memory CodeSegment="{_ASSOCIATIONS}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-D" Text="Delay" Value="5">
            <Memory CodeSegment="{_OBJECTS}" Offset="8" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_FLAGS}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_COUNT}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
        </ParameterRefs>
        <ComObjectTable CodeSegment="{_OBJECTS}" Offset="0">
          <ComObject Id="{_APP}_O-0" Name="Switch" Text="Switch" Number="0" FunctionText="" ObjectSize="1 Bit" Priority="Low" {flags} />
          <ComObject Id="{_APP}_O-1" Name="Status" Text="Status" Number="1" FunctionText="" ObjectSize="1 Bit" Priority="Low" {flags} />
        </ComObjectTable>
        <ComObjectRefs>
          <ComObjectRef Id="{_APP}_O-0_R-1" RefId="{_APP}_O-0" />
          <ComObjectRef Id="{_APP}_O-1_R-2" RefId="{_APP}_O-1" />
        </ComObjectRefs>
        <AddressTable CodeSegment="{_ADDRESSES}" Offset="0" MaxEntries="2" />
        <AssociationTable CodeSegment="{_ASSOCIATIONS}" Offset="0" MaxEntries="3" />
        <LoadProcedures>
          <LoadProcedure>
            <LdCtrlConnect />
            <LdCtrlUnload LsmIdx="1" />
            <LdCtrlUnload LsmIdx="2" />
            <LdCtrlUnload LsmIdx="3" />
            <LdCtrlLoad LsmIdx="1" />
            <LdCtrlAbsSegment LsmIdx="1" SegType="0" Address="278" Size="8" Access="51" MemType="3" SegFlags="128" />
            <LdCtrlLoadCompleted LsmIdx="1" />
            <LdCtrlLoad LsmIdx="2" />
            <LdCtrlAbsSegment LsmIdx="2" SegType="0" Address="286" Size="7" Access="51" MemType="3" SegFlags="128" />
            <LdCtrlLoadCompleted LsmIdx="2" />
            <LdCtrlLoad LsmIdx="3" />
            <LdCtrlAbsSegment LsmIdx="3" SegType="0" Address="304" Size="10" Access="51" MemType="3" SegFlags="0" />
            <LdCtrlTaskCtrl2 LsmIdx="3" Callback="0" Address="304" Seg0="0" Seg1="0" />
            <LdCtrlLoadCompleted LsmIdx="3" />
            <LdCtrlRestart />
            <LdCtrlDisconnect />
          </LoadProcedure>
        </LoadProcedures>
        <Options />
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_DELAY}" />
            <ParameterRefRef RefId="{_FLAGS}" />
            <ParameterRefRef RefId="{_COUNT}" />
            <ComObjectRefRef RefId="{_APP}_O-0_R-1" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def _downloaded(application: Application, delay: str = "7") -> dict[str, bytes]:
    """The code segments as a full download without links leaves them in the device."""
    image = build_image(
        application,
        parameter_values={_DELAY: delay},
        group_communication=GroupCommunication(device_address=0x1101, links=()),
    )
    segments: dict[str, bytes] = {}
    for segment_id, (base, size) in _LAYOUT.items():
        data = bytearray(size)
        for address, run in (
            image.masked_writes(base, size, include_initialized=True) or []
        ):
            data[address - base : address - base + len(run)] = run
        segments[segment_id] = bytes(data)
    return segments


def test_the_tables_write_the_flags_and_the_slots() -> None:
    application = _application()
    ui = application.dynamic_ui()
    assert ui is not None
    bits = group_table_bits(application, ui)
    # Records of objects 0 and 1: the value pointer is the segment's, the flag byte the
    # object's but for bit 5; the shown object's size is the type byte's low six bits.
    assert [bits.get(0x0130 + offset, 0) for offset in range(10)] == [
        0,
        0,
        0,
        0xDF,
        0x3F,
        0,
        0xDF,
        0,
        0,
        0,
    ]
    # The count and a slot per group object.
    assert [bits.get(0x011E + offset, 0) for offset in range(7)] == [0xFF] * 5 + [0, 0]


def test_the_download_writes_the_tables_over_the_settings() -> None:
    segments = _downloaded(_application())
    # Unlinked: no communication bit, and two unused slots.
    assert segments[_OBJECTS][3] == 0x5B
    assert segments[_ASSOCIATIONS][:5] == bytes.fromhex("02fe00fe01")


def test_a_device_the_download_wrote_is_reproduced() -> None:
    recovered = recover_parameters(_application(), _downloaded(_application()))
    assert recovered.values[_DELAY] == "7"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_a_bit_the_table_keeps_from_the_segment_still_differs() -> None:
    segments = _downloaded(_application())
    objects = bytearray(segments[_OBJECTS])
    objects[3] |= 0x20
    recovered = recover_parameters(
        _application(), {**segments, _OBJECTS: bytes(objects)}
    )
    assert not recovered.complete
    assert recovered.differing_bytes == 1


def test_tables_outside_the_memory_write_no_bits() -> None:
    # System 300 keeps its group tables apart from the code segments.
    application = _application("MV-0300")
    ui = application.dynamic_ui()
    assert ui is not None
    assert group_table_bits(application, ui) == {}
