"""A selector whose value shows an option stored in one of its own bits reads back with it.

"Source" (a whole byte: internal, external, average) and "Read request" (its first bit) are
members of one union, "Read request" written after "Source"; "Source" external shows "Read
request". The byte then holds both: external with a read request is 0x81, no value of "Source"
alone. Read with "Source" at its default, "Read request" is not shown, so the search has to
find "Source" external from the bits "Read request" leaves to it (a thermostat's source of the
room temperature).
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters

_APP = "M-00FA_A-00F6-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_SOURCE = f"{_APP}_UP-1_R-1"
_REQUEST = f"{_APP}_UP-2_R-2"
_LIMIT = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="246" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Option in a bit"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-Source" Name="Source">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Internal" Value="0" Id="{_APP}_PT-Source_EN-0" />
              <Enumeration Text="External" Value="1" Id="{_APP}_PT-Source_EN-1" />
              <Enumeration Text="Average" Value="2" Id="{_APP}_PT-Source_EN-2" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeRestriction Base="Value" SizeInBit="1">
              <Enumeration Text="No" Value="0" Id="{_APP}_PT-B_EN-0" />
              <Enumeration Text="Yes" Value="1" Id="{_APP}_PT-B_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Union SizeInBit="8">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
            <Parameter Id="{_APP}_UP-2" Name="ReadRequest" ParameterType="{_APP}_PT-B" Offset="0" BitOffset="0" Text="Read request" Value="0" />
            <Parameter Id="{_APP}_UP-1" Name="Source" ParameterType="{_APP}_PT-Source" Offset="0" BitOffset="0" Text="Source" Value="0" />
          </Union>
          <Parameter Id="{_APP}_P-3" Name="Limit" ParameterType="{_APP}_PT-N" Text="Limit" Value="10">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_SOURCE}" RefId="{_APP}_UP-1" />
          <ParameterRef Id="{_REQUEST}" RefId="{_APP}_UP-2" />
          <ParameterRef Id="{_LIMIT}" RefId="{_APP}_P-3" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_SOURCE}" />
            <choose ParamRefId="{_SOURCE}">
              <when test="1">
                <ParameterRefRef RefId="{_REQUEST}" />
              </when>
            </choose>
            <ParameterRefRef RefId="{_LIMIT}" />
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(**values: str) -> dict[str, bytes]:
    device = _application().dynamic_ui()
    assert device is not None
    for ref_id, value in values.items():
        device.set_parameter_ref(ref_id, value)
    return device.encode_to_memory()


def test_the_selector_reads_back_with_the_option_in_its_bit() -> None:
    segments = _device(**{_SOURCE: "1", _REQUEST: "1", _LIMIT: "33"})
    # The read request writes its bit over the source's value.
    assert segments[_SEGMENT] == bytes([0x81, 33])

    recovered = recover_parameters(_application(), segments)
    assert recovered.values[_SOURCE] == "1"
    assert recovered.values[_REQUEST] == "1"
    assert recovered.values[_LIMIT] == "33"
    assert recovered.complete


def test_the_selector_reads_back_with_the_option_off() -> None:
    segments = _device(**{_SOURCE: "1", _LIMIT: "33"})
    assert segments[_SEGMENT] == bytes([0x01, 33])

    recovered = recover_parameters(_application(), segments)
    assert recovered.values[_SOURCE] == "1"
    assert recovered.values.get(_REQUEST, "0") == "0"
    assert recovered.complete


def test_another_value_of_the_selector_reads_as_it_is() -> None:
    segments = _device(**{_SOURCE: "2", _LIMIT: "33"})
    assert segments[_SEGMENT] == bytes([0x02, 33])

    recovered = recover_parameters(_application(), segments)
    assert recovered.values[_SOURCE] == "2"
    assert recovered.complete
