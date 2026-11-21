"""A function the device keeps no copy of, told by a hidden marker the product fixes per choice,
that adds a channel (a module instance) whose own mode the device keeps no copy of either.

With the marker at its fixed value only the right function fits the marker's byte, but alone
it differs in all of the new channel's bytes: the channel's mode still has its default, which
leaves the channel's stored setting inactive and its own marker (fixed as well) where the
setting is. The channel's own mode is corrected behind the
function, and the two together fit the device.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import (
    RecoveredParameters,
    RecoveryEffort,
    _fixed_values,  # pyright: ignore[reportPrivateUsage]
    _hidden_fixed,  # pyright: ignore[reportPrivateUsage]
    _refine_fixed,  # pyright: ignore[reportPrivateUsage]
    _shadow_candidates,  # pyright: ignore[reportPrivateUsage]
    recover_parameters,
)
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0D02-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_INPUT = f"{_APP}_P-1_R-1"
_CHANNEL = f"{_MD}_M-1_MI-1"
_MODE = f"{_CHANNEL}_P-1_R-1"
_TIME = f"{_CHANNEL}_P-2_R-2"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="246" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Marker and new channel"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-Input" Name="Input"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="Input 1" Value="0" Id="{_APP}_PT-Input_EN-0" />
            <Enumeration Text="Disabling function" Value="8" Id="{_APP}_PT-Input_EN-8" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{_APP}_PT-Mode" Name="Mode"><TypeRestriction Base="Value" SizeInBit="8">
            <Enumeration Text="Switch" Value="0" Id="{_APP}_PT-Mode_EN-0" />
            <Enumeration Text="Shutter" Value="1" Id="{_APP}_PT-Mode_EN-1" />
          </TypeRestriction></ParameterType>
          <ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
          <ParameterType Id="{_APP}_PT-T" Name="T"><TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Input" ParameterType="{_APP}_PT-Input" Text="Input behaves like" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Marker" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_INPUT}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_APP}_P-2_R-20" RefId="{_APP}_P-2" Value="0" />
          <ParameterRef Id="{_APP}_P-2_R-28" RefId="{_APP}_P-2" Value="7" />
        </ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Mode" ParameterType="{_APP}_PT-Mode" Text="Mode" Value="0" />
              <Parameter Id="{_MD}_P-2" Name="Time" ParameterType="{_APP}_PT-T" Text="Time" Value="50000">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Parameter Id="{_MD}_P-3" Name="Kind" ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_P-2_R-2" RefId="{_MD}_P-2" />
              <ParameterRef Id="{_MD}_P-3_R-3" RefId="{_MD}_P-3" Value="1" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="0"><ParameterRefRef RefId="{_MD}_P-3_R-3" /></when>
                <when test="1"><ParameterRefRef RefId="{_MD}_P-2_R-2" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_INPUT}" />
            <choose ParamRefId="{_INPUT}">
              <when test="0"><ParameterRefRef RefId="{_APP}_P-2_R-20" /></when>
              <when test="8">
                <ParameterRefRef RefId="{_APP}_P-2_R-28" />
                <Module Id="{_MD}_M-1" RefId="{_MD}"><NumericArg RefId="{_MD}_A-1" Value="1" /></Module>
              </when>
            </choose>
          </ParameterBlock>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, values: dict[str, str]) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, values)
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_channel_writes_its_time_behind_the_marker(
    application: Application,
) -> None:
    memory = _device(application, {_INPUT: "8", _MODE: "1"})
    assert memory[_SEGMENT] == bytes([7, 0xC3, 0x50])


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_the_function_and_the_mode_of_the_channel_it_adds_are_found(
    application: Application, effort: RecoveryEffort
) -> None:
    memory = _device(application, {_INPUT: "8", _MODE: "1"})
    recovered = recover_parameters(application, memory, effort=effort)
    assert recovered.complete
    assert recovered.values[_INPUT] == "8"
    assert recovered.values.get(_MODE) == "1"
    assert recovered.values.get(_TIME) == "50000"


@pytest.mark.parametrize("scale", [1, None])
def test_the_fixed_markers_correct_the_function_with_the_mode_of_its_channel(
    application: Application, scale: int | None
) -> None:
    # A result that fits only by reading the device's marker into the first function's
    # reference: with the marker at its fixed value the function is corrected, and the
    # channel it adds only fits with its own mode corrected behind it.
    memory = _device(application, {_INPUT: "8", _MODE: "1"})
    ui = application.dynamic_ui()
    assert ui is not None
    result = _refine_fixed(
        application,
        memory,
        None,
        None,
        set(),
        {},
        RecoveredParameters(values={_INPUT: "0", f"{_APP}_P-2_R-20": "7"}, unknown=[]),
        _fixed_values(application, ui),
        scale,
        None,
    )
    assert result.complete
    assert result.values[_INPUT] == "8"
    assert result.values.get(_MODE) == "1"


def test_the_hidden_settings_and_shadow_candidates_are_kept_per_program(
    application: Application,
) -> None:
    # Asked with every UI a search judges: computed once per program (and set of hidden
    # refs, compared by its refs), not again for each new set of the same refs.
    ui = application.dynamic_ui()
    assert ui is not None
    hidden = _hidden_fixed(application.program, ui)
    assert _hidden_fixed(application.program, ui) is hidden
    assert {f"{_APP}_P-2_R-20", f"{_APP}_P-2_R-28"} <= hidden
    candidates = _shadow_candidates(ui, frozenset(hidden))
    assert _shadow_candidates(ui, frozenset(set(hidden))) is candidates
