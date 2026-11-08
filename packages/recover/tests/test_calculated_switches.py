"""Settings shown behind a switch a calculation computes from a stored value.

A channel switch the device keeps no copy of is computed from a setting read from the memory;
the channel shows a hidden value a calculation without RL transformation computes from a
setting of the channel the device keeps no copy of either, which is found from it. That
setting shows further stored settings, directly or through another switch the same calculation
computes. The values read behind the computed switch are inputs of the calculations too, and
what the calculation computes from the setting found follows it.
"""

from __future__ import annotations

from typing import Any

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-0C01-01-0000"
_ENABLE = f"{_APP}_P-1_R-1"
_SWITCH = f"{_APP}_P-2_R-2"
_FUNCTION = f"{_APP}_P-3_R-3"
_CODE = f"{_APP}_P-4_R-4"
_DELAY = f"{_APP}_P-5_R-5"
_CONDITION = f"{_APP}_P-6_R-6"
_EXTRA = f"{_APP}_P-7_R-7"


def _memory(number: int, offset: int, access: str = "") -> str:
    return (
        f'<Parameter Id="{_APP}_P-{number}" Name="P{number}"'
        f' ParameterType="{_APP}_PT-Byte" Text="P{number}"{access} Value="0">'
        f'<Memory CodeSegment="{_APP}_AS-0000" Offset="{offset}" BitOffset="0" />'
        "</Parameter>"
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="193" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Calculated switches"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-OnOff" Name="OnOff">
                <TypeRestriction Base="Value" SizeInBit="8">
                  <Enumeration Text="Off" Value="0" Id="{_APP}_PT-OnOff_EN-0" />
                  <Enumeration Text="On" Value="1" Id="{_APP}_PT-OnOff_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Byte" Name="Byte">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Enable" ParameterType="{_APP}_PT-OnOff" Text="Enable" Value="0">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-2" Name="Switch" ParameterType="{_APP}_PT-OnOff" Text="" Access="None" Value="0" />
              <Parameter Id="{_APP}_P-3" Name="Function" ParameterType="{_APP}_PT-OnOff" Text="Function" Value="0" />
              {_memory(4, 1, ' Access="None"')}
              {_memory(5, 2)}
              <Parameter Id="{_APP}_P-6" Name="Condition" ParameterType="{_APP}_PT-OnOff" Text="" Access="None" Value="0" />
              {_memory(7, 3)}
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_ENABLE}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-2" />
              <ParameterRef Id="{_FUNCTION}" RefId="{_APP}_P-3" />
              <ParameterRef Id="{_CODE}" RefId="{_APP}_P-4" />
              <ParameterRef Id="{_DELAY}" RefId="{_APP}_P-5" />
              <ParameterRef Id="{_CONDITION}" RefId="{_APP}_P-6" />
              <ParameterRef Id="{_EXTRA}" RefId="{_APP}_P-7" />
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_APP}_PC-1" Name="Switch" Language="JavaScript">
                <LRTransformation>sw = en;</LRTransformation>
                <LParameters><ParameterRefRef RefId="{_ENABLE}" AliasName="en" /></LParameters>
                <RParameters><ParameterRefRef RefId="{_SWITCH}" AliasName="sw" /></RParameters>
              </ParameterCalculation>
              <ParameterCalculation Id="{_APP}_PC-2" Name="Function" Language="JavaScript">
                <LRTransformation>c = f == 1 ? 77 : 255; k = f;</LRTransformation>
                <LParameters><ParameterRefRef RefId="{_FUNCTION}" AliasName="f" /></LParameters>
                <RParameters>
                  <ParameterRefRef RefId="{_CODE}" AliasName="c" />
                  <ParameterRefRef RefId="{_CONDITION}" AliasName="k" />
                </RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_ENABLE}" />
                <ParameterRefRef RefId="{_SWITCH}" />
                <choose ParamRefId="{_SWITCH}">
                  <when test="1">
                    <ParameterRefRef RefId="{_FUNCTION}" />
                    <ParameterRefRef RefId="{_CODE}" />
                    <ParameterRefRef RefId="{_CONDITION}" />
                    <choose ParamRefId="{_FUNCTION}">
                      <when test="1"><ParameterRefRef RefId="{_DELAY}" /></when>
                    </choose>
                    <choose ParamRefId="{_CONDITION}">
                      <when test="1"><ParameterRefRef RefId="{_EXTRA}" /></when>
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


@pytest.fixture
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, *values: tuple[str, str]) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values:
        ui.edit_parameter(ref_id, value)
    return ui.encode_to_memory()


@pytest.fixture
def segments(application: Application) -> dict[str, bytes]:
    segments = _device(
        application, (_ENABLE, "1"), (_FUNCTION, "1"), (_DELAY, "42"), (_EXTRA, "9")
    )
    assert next(iter(segments.values())) == bytes([1, 77, 42, 9])
    return segments


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_the_settings_behind_a_calculated_switch_are_read(
    application: Application, segments: dict[str, bytes], effort: RecoveryEffort
) -> None:
    recovered = recover_parameters(application, segments, effort=effort)
    assert recovered.complete
    assert recovered.values[_FUNCTION] == "1"
    # Shown by the setting found from the code behind the computed switch.
    assert recovered.values[_DELAY] == "42"
    # Shown by the switch the calculation computes from the setting found.
    assert recovered.values[_CONDITION] == "1"
    assert recovered.values[_EXTRA] == "9"


def test_a_value_read_behind_a_computed_switch_is_an_input_of_the_calculations(
    application: Application, segments: dict[str, bytes]
) -> None:
    calculated = parameters._with_calculated(  # pyright: ignore[reportPrivateUsage]
        application, {_ENABLE: "1", _CODE: "77"}, None
    )
    assert calculated[_SWITCH] == "1"
    assert calculated[_FUNCTION] == "1"
    assert calculated[_CONDITION] == "1"
    # The values read are kept.
    assert calculated[_CODE] == "77"


def test_a_value_read_behind_no_computed_switch_does_not_run_them_again(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The channel stays hidden: its code is not read, nothing is computed from it.
    calls: list[int] = []
    recalculate = DynamicUI.recalculate

    def counted(self: DynamicUI, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return recalculate(self, *args, **kwargs)

    monkeypatch.setattr(DynamicUI, "recalculate", counted)
    calculated = parameters._with_calculated(  # pyright: ignore[reportPrivateUsage]
        application, {_ENABLE: "0", _CODE: "77"}, None
    )
    assert calls == [1]
    assert _FUNCTION not in calculated
