"""Two settings the device keeps no copy of, each choosing a hidden setting with a fixed value, whose
fixed values share one byte (two channels' "switch command" bits): correcting one selector alone
leaves the byte differing until the other one is corrected too, so it must still count as better."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0006-01-0000"
_FIRST = f"{_APP}_P-1_R-1"
_SECOND = f"{_APP}_P-2_R-2"


def _channel(selector: str, parameter: int) -> str:
    """A selector showing one of two refs of a hidden parameter: fixed 2 when on, 0 when off."""
    return (
        f'<ParameterRefRef RefId="{selector}" />'
        f'<choose ParamRefId="{selector}">'
        f'<when test="1"><ParameterRefRef RefId="{_APP}_P-{parameter}_R-{parameter}2" /></when>'
        f'<when test="0"><ParameterRefRef RefId="{_APP}_P-{parameter}_R-{parameter}0" /></when>'
        "</choose>"
    )


def _hidden(parameter: int, bit_offset: int) -> str:
    return (
        f'<Parameter Id="{_APP}_P-{parameter}" Name="Command{parameter}"'
        f' ParameterType="{_APP}_PT-Command" Text="" Access="None" Value="3">'
        f'<Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="{bit_offset}" />'
        "</Parameter>"
    )


def _refs(parameter: int) -> str:
    return (
        f'<ParameterRef Id="{_APP}_P-{parameter}_R-{parameter}2" RefId="{_APP}_P-{parameter}" Value="2" />'
        f'<ParameterRef Id="{_APP}_P-{parameter}_R-{parameter}0" RefId="{_APP}_P-{parameter}" Value="0" />'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared fixed byte"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-Switch" Name="Switch">
                <TypeRestriction Base="Value" SizeInBit="1">
                  <Enumeration Text="Off" Value="0" Id="{_APP}_PT-Switch_EN-0" />
                  <Enumeration Text="On" Value="1" Id="{_APP}_PT-Switch_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Command" Name="Command">
                <TypeNumber SizeInBit="2" Type="unsignedInt" minInclusive="0" maxInclusive="3" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Timer1" ParameterType="{_APP}_PT-Switch" Text="Timer 1" Value="1" />
              <Parameter Id="{_APP}_P-2" Name="Timer2" ParameterType="{_APP}_PT-Switch" Text="Timer 2" Value="1" />
              {_hidden(3, 0)}
              {_hidden(4, 4)}
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_FIRST}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_SECOND}" RefId="{_APP}_P-2" />
              {_refs(3)}
              {_refs(4)}
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                {_channel(_FIRST, 3)}
                {_channel(_SECOND, 4)}
              </ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
@pytest.mark.parametrize(("first", "second"), [("0", "0"), ("0", "1"), ("1", "0")])
def test_selectors_whose_fixed_values_share_a_byte_are_recovered(
    application: Application, effort: RecoveryEffort, first: str, second: str
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_FIRST, first)
    ui.set_parameter_ref(_SECOND, second)
    segments = ui.encode_to_memory()
    recovered = recover_parameters(application, segments, effort=effort)
    assert recovered.complete
    seeded = seed_dynamic_ui(application, recovered.values)
    assert seeded is not None
    assert (seeded.get_value(_FIRST), seeded.get_value(_SECOND)) == (first, second)
    assert seeded.encode_to_memory() == segments
