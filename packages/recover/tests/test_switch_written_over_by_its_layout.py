"""A switch whose value shows a hidden union member written after it over all of its bits
keeps the value read: in the layout it chooses, the memory holds the hidden member's value.

"Channel" (byte 0, first bit) shows "Staircase" (byte 1, second bit). "Staircase" on shows the
hidden "Init A" and "Init B", each with a fixed value over a whole byte of those two and written
after them, and "Time"; off, it shows "Other" in the place of "Time". Read with the defaults,
"Channel" is on; with "Channel" on, "Staircase" is on; with "Staircase" on, both switches are
written over, and with their defaults the layout would go back to the start.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F5-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_CHANNEL = f"{_APP}_UP-1_R-1"
_STAIRCASE = f"{_APP}_UP-2_R-2"
_TIME = f"{_APP}_P-3_R-3"
_OTHER = f"{_APP}_P-4_R-4"
_INIT_A = f"{_APP}_UP-9_R-9"
_INIT_B = f"{_APP}_UP-10_R-10"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="245" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Switch written over"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeRestriction Base="Value" SizeInBit="1">
              <Enumeration Text="Off" Value="0" Id="{_APP}_PT-B_EN-0" />
              <Enumeration Text="On" Value="1" Id="{_APP}_PT-B_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-9" Name="InitA" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="" Access="None" Value="128" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-1" Name="Channel" ParameterType="{_APP}_PT-B" Offset="0" BitOffset="0" Text="Channel" Value="0" />
          </Union>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
            <Parameter Id="{_APP}_UP-10" Name="InitB" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="" Access="None" Value="64" DefaultUnionParameter="true" />
            <Parameter Id="{_APP}_UP-2" Name="Staircase" ParameterType="{_APP}_PT-B" Offset="0" BitOffset="1" Text="Staircase" Value="0" />
          </Union>
          <Parameter Id="{_APP}_P-3" Name="Time" ParameterType="{_APP}_PT-N" Text="Time" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-4" Name="Other" ParameterType="{_APP}_PT-N" Text="Other" Value="20">
            <Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_CHANNEL}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_STAIRCASE}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_TIME}" RefId="{_APP}_P-3" />
          <ParameterRef Id="{_OTHER}" RefId="{_APP}_P-4" />
          <ParameterRef Id="{_INIT_A}" RefId="{_APP}_UP-9" />
          <ParameterRef Id="{_INIT_B}" RefId="{_APP}_UP-10" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_CHANNEL}" />
            <choose ParamRefId="{_CHANNEL}">
              <when test="1">
                <ParameterRefRef RefId="{_STAIRCASE}" />
                <choose ParamRefId="{_STAIRCASE}">
                  <when test="1">
                    <ParameterRefRef RefId="{_INIT_A}" />
                    <ParameterRefRef RefId="{_INIT_B}" />
                    <ParameterRefRef RefId="{_TIME}" />
                  </when>
                  <when test="0">
                    <ParameterRefRef RefId="{_OTHER}" />
                  </when>
                </choose>
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


def test_the_switches_keep_the_values_read() -> None:
    application = _application()
    device = application.dynamic_ui()
    assert device is not None
    device.set_parameter_ref(_CHANNEL, "1")
    device.set_parameter_ref(_STAIRCASE, "1")
    device.set_parameter_ref(_TIME, "77")
    segments = device.encode_to_memory()
    # "Init A" and "Init B" wrote over both switches, with the bits the switches have.
    assert segments[_SEGMENT] == bytes([0x80, 0x40, 77])

    recovered = recover_parameters(application, segments)
    assert recovered.values[_CHANNEL] == "1"
    assert recovered.values[_STAIRCASE] == "1"
    assert recovered.values[_TIME] == "77"
    assert recovered.complete


def test_a_switch_off_is_read_as_off() -> None:
    application = _application()
    device = application.dynamic_ui()
    assert device is not None
    device.set_parameter_ref(_CHANNEL, "1")
    device.set_parameter_ref(_OTHER, "33")
    segments = device.encode_to_memory()
    assert segments[_SEGMENT] == bytes([0x80, 0x00, 33])

    recovered = recover_parameters(application, segments)
    assert recovered.values[_CHANNEL] == "1"
    assert recovered.values.get(_STAIRCASE, "0") == "0"
    assert recovered.values[_OTHER] == "33"
    assert recovered.complete
