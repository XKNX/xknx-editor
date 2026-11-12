"""A switch the device keeps no copy of, above a stored selector, is switched off where what
the selector shows is not on the device.

"Outputs" (no memory, on by default) shows "Mode" (byte 0). A choose on "Mode" beside it, which
renders only while "Mode" is shown, shows "Delay" (byte 1, 10 to 200) for 0. Switched off, "Outputs" leaves both bytes as the segment has them, 0 and 4: with its
default, "Delay" reads 4, no value of it, and the result writes its default there. Only
"Outputs", above the stored "Mode", takes it back.
"""

from __future__ import annotations

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-00F9-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_OUTPUTS = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-2_R-2"
_DELAY = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="249" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Switch above a stored selector"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="2"><Data>AAQ=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B">
            <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-M" Name="M">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Plain" Value="0" Id="{_APP}_PT-M_EN-0" />
              <Enumeration Text="Other" Value="1" Id="{_APP}_PT-M_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="10" maxInclusive="200" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Outputs" ParameterType="{_APP}_PT-B" Text="Outputs" Value="1" />
          <Parameter Id="{_APP}_P-2" Name="Mode" ParameterType="{_APP}_PT-M" Text="Mode" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
          <Parameter Id="{_APP}_P-3" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="116">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_OUTPUTS}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_MODE}" RefId="{_APP}_P-2" />
          <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-3" />
        </ParameterRefs>
      </Static>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_OUTPUTS}" />
            <choose ParamRefId="{_OUTPUTS}">
              <when test="1">
                <ParameterRefRef RefId="{_MODE}" />
              </when>
            </choose>
          </ParameterBlock>
          <choose ParamRefId="{_MODE}">
            <when test="0">
              <ParameterBlock Id="{_APP}_PB-2" Text="Output">
                <ParameterRefRef RefId="{_DELAY}" />
              </ParameterBlock>
            </when>
          </choose>
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


def test_the_switch_is_off_where_the_device_lacks_what_it_shows() -> None:
    segments = _segments({_OUTPUTS: "0"})
    assert segments[_SEGMENT] == bytes([0, 4])

    for effort in (RecoveryEffort.FAST, RecoveryEffort.MEDIUM):
        recovered = recover_parameters(_application(), segments, effort=effort)
        assert recovered.values[_OUTPUTS] == "0"
        assert recovered.complete


def test_the_switch_stays_on_where_the_device_holds_what_it_shows() -> None:
    segments = _segments({_MODE: "0", _DELAY: "77"})
    assert segments[_SEGMENT] == bytes([0, 77])

    recovered = recover_parameters(_application(), segments)
    assert recovered.values.get(_OUTPUTS, "1") == "1"
    assert recovered.values.get(_MODE, "0") == "0"
    assert recovered.values[_DELAY] == "77"
    assert recovered.complete
