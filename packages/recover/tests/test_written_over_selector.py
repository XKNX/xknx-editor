"""A union member a later active member writes over in part is searched like a setting the
device keeps no copy of: the memory holds several of its values alike.

"Type" (3 bits) shares its union with the hidden "Shadow" (3 bits, one bit further), which is
written after it: only the first bit of "Type" survives, so 0, 2 and 3 read the same. "Type" 2
shows "Delay" and its group object; the memory of "Delay" and the linked object tell it apart.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F3-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_TYPE = f"{_APP}_UP-1_R-1"
_SHADOW = f"{_APP}_UP-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="243" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Written over"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-T" Name="T">
            <TypeRestriction Base="Value" SizeInBit="3">
              <Enumeration Text="None" Value="0" Id="{_APP}_PT-T_EN-0" />
              <Enumeration Text="Normal" Value="2" Id="{_APP}_PT-T_EN-2" />
              <Enumeration Text="Inverted" Value="3" Id="{_APP}_PT-T_EN-3" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-1" Name="Type" ParameterType="{_APP}_PT-T" Offset="0" BitOffset="0" Text="Type" Value="0" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-2" Name="Shadow" ParameterType="{_APP}_PT-T" Offset="0" BitOffset="1" Text="Shadow" Access="None" Value="0" />
          </Union>
          <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_TYPE}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_SHADOW}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
        </ParameterRefs>
        <ComObjectTable>
          <ComObject Id="{_APP}_O-1" Name="O1" Text="Alarm" Number="1" FunctionText="" ObjectSize="1 Bit"
              ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled"
              UpdateFlag="Disabled" ReadOnInitFlag="Disabled" DatapointType="DPST-1-1" />
        </ComObjectTable>
        <ComObjectRefs><ComObjectRef Id="{_APP}_O-1_R-1" RefId="{_APP}_O-1" /></ComObjectRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_TYPE}" />
            <ParameterRefRef RefId="{_SHADOW}" />
            <choose ParamRefId="{_TYPE}">
              <when test="2">
                <ParameterRefRef RefId="{_DELAY}" />
                <ComObjectRefRef RefId="{_APP}_O-1_R-1" />
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


def test_the_value_behind_the_bits_left_is_found() -> None:
    application = _application()
    device = application.dynamic_ui()
    assert device is not None
    device.set_parameter_ref(_TYPE, "2")
    device.set_parameter_ref(_DELAY, "99")
    segments = device.encode_to_memory()
    # "Shadow" wrote over all but the first bit of "Type".
    assert segments[_SEGMENT] == bytes([0, 99])

    recovered = recover_parameters(application, segments, linked_objects={1})
    assert recovered.values[_TYPE] == "2"
    assert recovered.values[_DELAY] == "99"
    assert recovered.complete


def test_without_evidence_the_first_fitting_value_stays() -> None:
    # Nothing wrote "Delay": "Type" did not show it.
    recovered = recover_parameters(_application(), {_SEGMENT: bytes([0, 0])})
    assert recovered.values[_TYPE] == "0"
    assert recovered.complete
