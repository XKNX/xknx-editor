"""Decoding a UI's state with one module instance as another UI evaluated it: what a UI seeded
with the first UI's values and that instance's values of the other decodes, without an
evaluation of its own; also the members of the instances' unions that the application's own
decoding reads (the last instance that writes one gives its value)."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-00F8-01-0000"
_MD = f"{_APP}_MD-1"
_SEGMENT = f"{_APP}_AS-0000"
_GENERAL = f"{_APP}_P-1_R-1"


def _ref(instance: int, ref: str) -> str:
    return f"{_MD}_M-{instance}_MI-1_{ref}"


def _module(instance: int) -> str:
    return (
        f'<Module Id="{_MD}_M-{instance}" RefId="{_MD}" Name="Channel {instance}">'
        f'<NumericArg RefId="{_MD}_A-1" Value="{2 * (instance - 1)}" /></Module>'
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="248" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Channels with a kind"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="7"><Data>AAAAAAAAAA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-Kind" Name="Kind">
            <TypeRestriction Base="Value" SizeInBit="8">
              <Enumeration Text="Low" Value="0" Id="{_APP}_PT-Kind_EN-0" />
              <Enumeration Text="High" Value="1" Id="{_APP}_PT-Kind_EN-1" />
            </TypeRestriction>
          </ParameterType>
          <ParameterType Id="{_APP}_PT-Low" Name="Low"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="9" /></ParameterType>
          <ParameterType Id="{_APP}_PT-High" Name="High"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="10" maxInclusive="99" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="General" ParameterType="{_APP}_PT-Low" Text="General" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="6" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs><ParameterRef Id="{_GENERAL}" RefId="{_APP}_P-1" /></ParameterRefs>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Kind" ParameterType="{_APP}_PT-Kind" Text="Kind" Value="0" />
              <Parameter Id="{_MD}_P-4" Name="Delay" ParameterType="{_APP}_PT-Low" Text="Delay" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
              <Union SizeInBit="8">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
                <Parameter Id="{_MD}_UP-2" Name="Low" ParameterType="{_APP}_PT-Low" Offset="0" BitOffset="0" Text="Low" Value="0" DefaultUnionParameter="true" />
                <Parameter Id="{_MD}_UP-3" Name="High" ParameterType="{_APP}_PT-High" Offset="0" BitOffset="0" Text="High" Value="10" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" />
              <ParameterRef Id="{_MD}_UP-2_R-2" RefId="{_MD}_UP-2" />
              <ParameterRef Id="{_MD}_UP-3_R-3" RefId="{_MD}_UP-3" />
              <ParameterRef Id="{_MD}_P-4_R-4" RefId="{_MD}_P-4" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{_MD}_P-1_R-1" />
              <ParameterRefRef RefId="{_MD}_P-4_R-4" />
              <choose ParamRefId="{_MD}_P-1_R-1">
                <when test="0"><ParameterRefRef RefId="{_MD}_UP-2_R-2" /></when>
                <when test="1"><ParameterRefRef RefId="{_MD}_UP-3_R-3" /></when>
              </choose>
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General"><ParameterRefRef RefId="{_GENERAL}" /></ParameterBlock>
        </ChannelIndependentBlock>
        {_module(1)}{_module(2)}{_module(3)}
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""

_SEGMENTS = {_SEGMENT: bytes([42, 1, 43, 2, 5, 3, 4])}
# Every channel has a value: the instances are placed in the order of the channels.
_VALUES = {
    _GENERAL: "4",
    **{_ref(n, "P-4_R-4"): str(n) for n in (1, 2, 3)},
}


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _ui(application: Application, values: dict[str, str]) -> DynamicUI:
    program, builder = application.resolved()
    seeded = DynamicUI(program, tree_builder=builder, parameter_values=values)
    seeded.ui()
    return seeded


def _decoded(ui: DynamicUI) -> dict[str, str | None]:
    decoded = ui.decode_stored_parameters(_SEGMENTS, {})
    decoded.update(ui.decode_module_parameters(_SEGMENTS, {}))
    return decoded


@pytest.mark.parametrize("instance", [1, 2, 3])
def test_the_decode_is_that_of_a_ui_seeded_with_the_instance_of_the_other(
    application: Application, instance: int
) -> None:
    # This UI shows the second channel's high member, the other one the instance's.
    base = {**_VALUES, _ref(2, "P-1_R-1"): "1"}
    changed = {_ref(instance, "P-1_R-1"): "1"}
    mine = _ui(application, base)
    theirs = _ui(application, {**_VALUES, **changed})

    decoded = mine.decode_parameters_with_instance(
        _SEGMENTS, {}, f"{_MD}_M-{instance}_MI-1", theirs
    )

    assert decoded == _decoded(_ui(application, {**base, **changed}))
    assert decoded != _decoded(mine) or instance == 2


def test_the_application_decoding_reads_the_last_instance_writing_a_member(
    application: Application,
) -> None:
    mine = _ui(application, _VALUES)
    theirs = _ui(application, {**_VALUES, _ref(3, "P-1_R-1"): "1"})

    decoded = mine.decode_parameters_with_instance(
        _SEGMENTS, {}, f"{_MD}_M-1_MI-1", theirs
    )
    assert decoded is not None
    assert f"{_MD}_UP-3" not in decoded

    decoded = mine.decode_parameters_with_instance(
        _SEGMENTS, {}, f"{_MD}_M-3_MI-1", theirs
    )
    assert decoded is not None
    # The third channel's byte, 5, is no value of the high member.
    assert decoded[f"{_MD}_UP-3"] is None
    assert decoded[_ref(3, "UP-3_R-3")] is None


def test_without_the_instance_nothing_is_decoded(application: Application) -> None:
    mine = _ui(application, _VALUES)

    assert (
        mine.decode_parameters_with_instance(_SEGMENTS, {}, f"{_MD}_M-9_MI-1", mine)
        is None
    )


def test_the_states_stay_as_they_are(application: Application) -> None:
    mine = _ui(application, _VALUES)
    theirs = _ui(application, {**_VALUES, _ref(1, "P-1_R-1"): "1"})
    before = (_decoded(mine), _decoded(theirs))

    mine.decode_parameters_with_instance(_SEGMENTS, {}, f"{_MD}_M-1_MI-1", theirs)

    assert (_decoded(mine), _decoded(theirs)) == before
    assert mine.get_module_instances() == theirs.get_module_instances()
