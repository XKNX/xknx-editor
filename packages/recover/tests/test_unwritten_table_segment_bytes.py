"""A hidden setting stored in the code segment of a BCU2 group object table: "Value", shown
when "Mode" is 1, in the second byte of a union whose first member "Level" is shown when
"Mode" is 0. A full download writes nothing of that segment's data (only the table, and the
settings it shows), so where "Value" is hidden its byte keeps what the device held before
(not the 01h of the segment's data). A result that hides it fits whatever is there; one that
shows it still reads its value.
"""

from __future__ import annotations

import base64

import pytest

from xknxeditor.download import GroupCommunication, build_image
from xknxeditor.download.image import written_octets
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-00FA-01-0000"
_ADDRESSES = f"{_APP}_AS-0116"
_ASSOCIATIONS = f"{_APP}_AS-011E"
_OBJECTS = f"{_APP}_AS-0130"
_MODE = f"{_APP}_P-1_R-1"
_VALUE = f"{_APP}_UP-2_R-2"
_LEVEL = f"{_APP}_UP-3_R-3"
# Count 2, the RAM flags table pointer, two records (value pointer, flags, type), the bytes
# "Level", "Value" and "Mode" are stored at.
_OBJECT_SEED = "02d0004f00014f00010100"
_LAYOUT = {_ADDRESSES: (0x0116, 8), _ASSOCIATIONS: (0x011E, 7), _OBJECTS: (0x0130, 11)}


def _data(hex_octets: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_octets)).decode()


def _application() -> Application:
    flags = 'ReadFlag="Enabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"'
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="250" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-0021" Name="Settings by the objects"
        LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="true" MinEtsVersion="4.0">
      <Static>
        <Code>
          <AbsoluteSegment Id="{_ADDRESSES}" Address="278" Size="8"><Data>{_data("0100000000000000")}</Data></AbsoluteSegment>
          <AbsoluteSegment Id="{_ASSOCIATIONS}" Address="286" Size="7"><Data>{_data("00000000000000")}</Data></AbsoluteSegment>
          <AbsoluteSegment Id="{_OBJECTS}" Address="304" Size="11"><Data>{_data(_OBJECT_SEED)}</Data></AbsoluteSegment>
        </Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-M" Name="M"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="Off" Value="0" Id="{_APP}_PT-M_EN-0" />
            <Enumeration Text="On" Value="1" Id="{_APP}_PT-M_EN-1" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{_APP}_PT-V" Name="V"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-M" Text="Mode" Value="0">
            <Memory CodeSegment="{_OBJECTS}" Offset="10" BitOffset="0" />
          </Parameter>
          <Union SizeInBit="16">
            <Memory CodeSegment="{_OBJECTS}" Offset="8" BitOffset="0" />
            <Parameter Id="{_APP}_UP-3" Name="Level" ParameterType="{_APP}_PT-V" Offset="0" BitOffset="0" Text="Level" Value="1" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-2" Name="Value" ParameterType="{_APP}_PT-V" Offset="1" BitOffset="0" Text="Value" Value="1" />
          </Union>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_VALUE}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_LEVEL}" RefId="{_APP}_UP-3" />
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
            <LdCtrlAbsSegment LsmIdx="3" SegType="0" Address="304" Size="11" Access="51" MemType="3" SegFlags="0" />
            <LdCtrlTaskCtrl2 LsmIdx="3" Callback="0" Address="304" Seg0="0" Seg1="0" />
            <LdCtrlLoadCompleted LsmIdx="3" />
            <LdCtrlRestart />
            <LdCtrlDisconnect />
          </LoadProcedure>
        </LoadProcedures>
        <Options DownloadInvisibleParameters="None" />
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_MODE}" />
            <choose ParamRefId="{_MODE}">
              <when test="0">
                <ParameterRefRef RefId="{_LEVEL}" />
              </when>
              <when test="1">
                <ParameterRefRef RefId="{_VALUE}" />
              </when>
            </choose>
            <ComObjectRefRef RefId="{_APP}_O-0_R-1" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    return parse_application_xml(xml.encode(), "M-00FA")[0]


@pytest.fixture(scope="module")
def application() -> Application:
    return _application()


def _downloaded(
    application: Application, values: dict[str, str], held: int
) -> dict[str, bytes]:
    """The code segments a full download without links leaves in a device that held the
    octet ``held`` everywhere before."""
    image = build_image(
        application,
        parameter_values=values,
        group_communication=GroupCommunication(device_address=0x1101, links=()),
    )
    segments: dict[str, bytes] = {}
    for segment_id, (base, size) in _LAYOUT.items():
        data = bytearray([held]) * size
        for address, run in (
            image.masked_writes(base, size, include_initialized=True) or []
        ):
            data[address - base : address - base + len(run)] = run
        segments[segment_id] = bytes(data)
    return segments


def test_the_download_writes_no_data_of_the_segment_where_the_setting_is_hidden(
    application: Application,
) -> None:
    hidden = seed_dynamic_ui(application, {_MODE: "0"})
    shown = seed_dynamic_ui(application, {_MODE: "1", _VALUE: "1"})
    assert hidden is not None
    assert shown is not None
    # The table's records and the settings shown are written.
    assert written_octets(application, hidden)[_OBJECTS] == b"\xff" * 9 + b"\x00\xff"
    assert written_octets(application, shown)[_OBJECTS] == b"\xff" * 8 + b"\x00\xff\xff"


@pytest.mark.parametrize("held", [0x00, 0x01, 0x5A])
def test_a_hidden_setting_fits_whatever_the_device_held_there(
    application: Application, held: int
) -> None:
    device = _downloaded(application, {_MODE: "0"}, held)
    assert device[_OBJECTS][9] == held
    recovered = recover_parameters(application, device)
    assert recovered.values[_MODE] == "0"
    assert recovered.complete
    assert recovered.differing_bytes == 0


def test_a_shown_setting_there_is_read(application: Application) -> None:
    device = _downloaded(application, {_MODE: "1", _VALUE: "0"}, 0x5A)
    recovered = recover_parameters(application, device)
    assert recovered.complete
    assert recovered.values[_MODE] == "1"
    assert recovered.values[_VALUE] == "0"


def test_a_byte_the_download_writes_still_differs(application: Application) -> None:
    device = _downloaded(application, {_MODE: "0"}, 0x00)
    objects = bytearray(device[_OBJECTS])
    # "Mode" holds no value of its type: it stays at its default, and its byte differs.
    objects[10] = 0x07
    recovered = recover_parameters(application, {**device, _OBJECTS: bytes(objects)})
    assert not recovered.complete
    assert recovered.differing_bytes == 1
