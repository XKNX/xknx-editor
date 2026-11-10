"""A union member whose bytes hold the value of a later member on the same bits, and no value
of its own, takes the value that shows that member.

"Role" (values 0, 1, 2) and "Type" (values 2, 3, 4) share byte 0. "Role" 2 shows "Type", which
is written after it over all of its bits; "Role" 0 shows "Delay" (byte 1). With "Role" 2 and
"Type" 3, the byte holds 3: no value of "Role", so the memory alone does not show "Type".
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F7-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_ROLE = f"{_APP}_UP-1_R-1"
_TYPE = f"{_APP}_UP-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="247" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Member with a later value"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-R" Name="R">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Master" Value="0" Id="{_APP}_PT-R_EN-0" />
              <Enumeration Text="Alone" Value="1" Id="{_APP}_PT-R_EN-1" />
              <Enumeration Text="Slave" Value="2" Id="{_APP}_PT-R_EN-2" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-T" Name="T">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="A" Value="2" Id="{_APP}_PT-T_EN-2" />
              <Enumeration Text="B" Value="3" Id="{_APP}_PT-T_EN-3" />
              <Enumeration Text="C" Value="4" Id="{_APP}_PT-T_EN-4" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-1" Name="Role" ParameterType="{_APP}_PT-R" Offset="0" BitOffset="0" Text="Role" Value="0" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-2" Name="Type" ParameterType="{_APP}_PT-T" Offset="0" BitOffset="0" Text="Type" Value="2" />
          </Union>
          <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_ROLE}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_TYPE}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_ROLE}" />
            <choose ParamRefId="{_ROLE}">
              <when test="0">
                <ParameterRefRef RefId="{_DELAY}" />
              </when>
              <when test="2">
                <ParameterRefRef RefId="{_TYPE}" />
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _segments(values: dict[str, str]) -> dict[str, bytes]:
    device = _application().dynamic_ui()
    assert device is not None
    for ref_id, value in values.items():
        device.set_parameter_ref(ref_id, value)
    return device.encode_to_memory()


def test_the_member_takes_the_value_showing_the_later_member() -> None:
    segments = _segments({_ROLE: "2", _TYPE: "3"})
    assert segments[_SEGMENT][0] == 3

    recovered = recover_parameters(_application(), segments)
    assert recovered.values[_ROLE] == "2"
    assert recovered.values[_TYPE] == "3"
    assert recovered.complete


def test_a_value_of_both_members_is_read_as_the_first() -> None:
    segments = _segments({_ROLE: "2", _TYPE: "2"})

    recovered = recover_parameters(_application(), segments)
    assert recovered.values[_ROLE] == "2"
    assert recovered.values[_TYPE] == "2"
    assert recovered.complete


def test_a_value_of_neither_member_stays_unknown() -> None:
    segments = _segments({_DELAY: "7"})
    segments[_SEGMENT] = bytes([9, 7])

    recovered = recover_parameters(_application(), segments)
    assert recovered.values.get(_ROLE, "0") != "2"
    assert recovered.values[_DELAY] == "7"
    assert not recovered.complete


def test_the_own_value_is_read_as_it_is() -> None:
    segments = _segments({_ROLE: "0", _DELAY: "42"})

    recovered = recover_parameters(_application(), segments)
    assert recovered.values.get(_ROLE, "0") == "0"
    assert recovered.values[_DELAY] == "42"
    assert recovered.complete


# "Level" (0 to 100) takes byte 0; "Flag", written after it, its first bit, and "Code", shown
# only with "Advanced" on (byte 1), all of it. With "Level" 50 and "Flag" on, the byte holds
# 0xB2: no value of "Level" read as it is.
_PART_APP = "M-00FA_A-00FB-01-0000"
_PART_SEGMENT = f"{_PART_APP}_AS-0000"
_LEVEL = f"{_PART_APP}_UP-1_R-1"
_FLAG = f"{_PART_APP}_UP-2_R-2"
_CODE = f"{_PART_APP}_UP-3_R-3"
_ADVANCED = f"{_PART_APP}_P-4_R-4"

_PART_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_PART_APP}" ApplicationNumber="251" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Member written over in part"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_PART_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_PART_APP}_PT-L" Name="L"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="100" /></ParameterType>
          <ParameterType Id="{_PART_APP}_PT-F" Name="F">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
          <ParameterType Id="{_PART_APP}_PT-C" Name="C"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
          <ParameterType Id="{_PART_APP}_PT-A" Name="A">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Off" Value="0" Id="{_PART_APP}_PT-A_EN-0" />
              <Enumeration Text="On" Value="1" Id="{_PART_APP}_PT-A_EN-1" />
            </TypeRestriction>
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_PART_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_PART_APP}_UP-1" Name="Level" ParameterType="{_PART_APP}_PT-L" Offset="0" BitOffset="0" Text="Level" Value="20" DefaultUnionParameter="true" />
            <Parameter Id="{_PART_APP}_UP-2" Name="Flag" ParameterType="{_PART_APP}_PT-F" Offset="0" BitOffset="0" Text="Flag" Value="0" />
            <Parameter Id="{_PART_APP}_UP-3" Name="Code" ParameterType="{_PART_APP}_PT-C" Offset="0" BitOffset="0" Text="Code" Value="0" />
          </Union>
          <Parameter Id="{_PART_APP}_P-4" Name="Advanced" ParameterType="{_PART_APP}_PT-A" Text="Advanced" Value="0">
            <Memory CodeSegment="{_PART_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_LEVEL}" RefId="{_PART_APP}_UP-1" />
          <ParameterRef Id="{_FLAG}" RefId="{_PART_APP}_UP-2" />
          <ParameterRef Id="{_CODE}" RefId="{_PART_APP}_UP-3" />
          <ParameterRef Id="{_ADVANCED}" RefId="{_PART_APP}_P-4" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_PART_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_ADVANCED}" />
            <choose ParamRefId="{_ADVANCED}">
              <when test="0">
                <ParameterRefRef RefId="{_LEVEL}" />
                <ParameterRefRef RefId="{_FLAG}" />
              </when>
              <when test="1">
                <ParameterRefRef RefId="{_CODE}" />
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _part_application() -> Application:
    return parse_application_xml(_PART_XML.encode(), "M-00FA")[0]


def test_a_member_written_over_in_part_takes_the_value_its_other_bits_hold() -> None:
    device = _part_application().dynamic_ui()
    assert device is not None
    device.set_parameter_ref(_LEVEL, "50")
    device.set_parameter_ref(_FLAG, "1")
    segments = device.encode_to_memory()
    assert segments[_PART_SEGMENT] == bytes([0xB2, 0])

    recovered = recover_parameters(_part_application(), segments)
    assert recovered.values[_LEVEL] == "50"
    assert recovered.values[_FLAG] == "1"
    assert recovered.complete
