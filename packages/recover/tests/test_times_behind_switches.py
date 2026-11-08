"""Times a calculation downloads behind two switches the device keeps no copy of (an "extra times"
switch showing an "on" switch that shows the time): the time is stored in minutes, which the RL
transformation turns back into the hour. The switches are found from the stored time, though
the first alone shows nothing read, and the hour is computed from the time read whatever order
the values come in."""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0C02-01-0000"
_EXTRA = f"{_APP}_P-1_R-1"
_ON = f"{_APP}_P-2_R-2"
_HOUR = f"{_APP}_P-3_R-3"
_MINUTES = f"{_APP}_P-4_R-4"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="194" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Times behind switches"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-OnOff" Name="OnOff">
                <TypeRestriction Base="Value" SizeInBit="8">
                  <Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
                  <Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Hour" Name="Hour">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="23" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Word" Name="Word">
                <TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Extra" ParameterType="{_APP}_PT-OnOff" Text="Extra times" Value="0" />
              <Parameter Id="{_APP}_P-2" Name="On" ParameterType="{_APP}_PT-OnOff" Text="Switch on" Value="0" />
              <Parameter Id="{_APP}_P-3" Name="Hour" ParameterType="{_APP}_PT-Hour" Text="Hour" Value="6" />
              <Parameter Id="{_APP}_P-4" Name="Minutes" ParameterType="{_APP}_PT-Word" Text="" Access="None" Value="0">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_EXTRA}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_ON}" RefId="{_APP}_P-2" />
              <ParameterRef Id="{_HOUR}" RefId="{_APP}_P-3" />
              <ParameterRef Id="{_MINUTES}" RefId="{_APP}_P-4" />
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_APP}_PC-1" Name="Times" Language="JavaScript">
                <RLTransformation>h = Math.floor(m / 60);</RLTransformation>
                <LRTransformation>m = x == 1 &amp;&amp; o == 1 ? h * 60 : 0;</LRTransformation>
                <LParameters>
                  <ParameterRefRef RefId="{_EXTRA}" AliasName="x" />
                  <ParameterRefRef RefId="{_ON}" AliasName="o" />
                  <ParameterRefRef RefId="{_HOUR}" AliasName="h" />
                </LParameters>
                <RParameters><ParameterRefRef RefId="{_MINUTES}" AliasName="m" /></RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="Times">
                <ParameterRefRef RefId="{_EXTRA}" />
                <choose ParamRefId="{_EXTRA}">
                  <when test="1">
                    <ParameterRefRef RefId="{_ON}" />
                    <choose ParamRefId="{_ON}">
                      <when test="1">
                        <ParameterRefRef RefId="{_HOUR}" />
                        <ParameterRefRef RefId="{_MINUTES}" />
                      </when>
                    </choose>
                  </when>
                </choose>
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
def test_both_switches_and_the_hour_are_found_from_the_stored_time(
    application: Application, effort: RecoveryEffort
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in ((_EXTRA, "1"), (_ON, "1"), (_HOUR, "7")):
        ui.edit_parameter(ref_id, value)
    segments = ui.encode_to_memory()
    assert next(iter(segments.values())) == (420).to_bytes(2, "big")
    recovered = recover_parameters(application, segments, effort=effort)
    assert recovered.complete
    seeded = seed_dynamic_ui(application, recovered.values)
    assert seeded is not None
    assert [seeded.get_value(ref) for ref in (_EXTRA, _ON, _HOUR)] == ["1", "1", "7"]


def test_the_hour_is_computed_from_the_time_read_after_the_switches_guessed(
    application: Application,
) -> None:
    # The switches come first (guessed values go before those read): the calculation still
    # runs from the time read.
    calculated = parameters._with_calculated(  # pyright: ignore[reportPrivateUsage]
        application, {_EXTRA: "1", _ON: "1", _MINUTES: "420"}, None
    )
    assert calculated[_HOUR] == "7"
    assert calculated[_MINUTES] == "420"


def test_a_switch_is_guessed_while_the_time_it_is_computed_from_is_hidden(
    application: Application,
) -> None:
    hidden = seed_dynamic_ui(application, {_EXTRA: "1"})
    shown = seed_dynamic_ui(application, {_EXTRA: "1", _ON: "1"})
    assert hidden is not None
    assert shown is not None
    # Hidden behind the "on" switch, the time is not read: nothing decides the switches.
    assert not parameters._calculated_from_reads(hidden, _EXTRA)  # pyright: ignore[reportPrivateUsage]
    assert not parameters._calculated_from_reads(hidden, _ON)  # pyright: ignore[reportPrivateUsage]
    assert parameters._calculated_from_reads(shown, _ON)  # pyright: ignore[reportPrivateUsage]
