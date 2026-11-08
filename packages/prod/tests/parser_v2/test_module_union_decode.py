"""Decoding a module instance's union: the member its ParameterRef makes active is read, also
when the instance has no value for it yet (a device read back, seeded with what was decoded)."""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml

_APP = "M-00FA_A-00F1-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
# The instance's refs.
_I = f"{_MD}_M-1_MI-1_"
_SWITCH = f"{_I}P-1_R-1"
_FIRST = f"{_I}UP-2_R-2"
_SECOND = f"{_I}UP-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="241" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Module union"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Switch" ParameterType="{_APP}_PT-N" Text="Switch" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Union SizeInBit="8">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
                <Parameter Id="{_MD}_UP-2" Name="First" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="First" Value="0" DefaultUnionParameter="true" />
                <Parameter Id="{_MD}_UP-3" Name="Second" ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="Second" Value="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_UP-2_R-2" RefId="{_MD}_UP-2" />
              <ParameterRef Id="{_MD}_UP-3_R-3" RefId="{_MD}_UP-3" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="0"><ParameterRefRef RefId="{_MD}_UP-2_R-2" /></when>
                <when test="1"><ParameterRefRef RefId="{_MD}_UP-3_R-3" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General" /></ChannelIndependentBlock>
        <Module Id="{_MD}_M-1" RefId="{_MD}" Name="Channel 1">
          <NumericArg RefId="{_MD}_A-1" Value="0" />
        </Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_active_member_of_a_module_union_is_decoded_without_a_value() -> None:
    application = _application()
    device = application.dynamic_ui()
    assert device is not None
    device.set_parameter_ref(_SWITCH, "1")
    device.set_parameter_ref(_SECOND, "7")
    segments = device.encode_to_memory()
    assert segments[_SEGMENT] == b"\x07\x01"

    # Read back: the switch is known, the member it shows has no value yet.
    read = application.dynamic_ui()
    assert read is not None
    read.set_parameter_ref(_SWITCH, "1")
    decoded = read.decode_module_parameters(segments, {})
    assert decoded[_SECOND] == "7"
