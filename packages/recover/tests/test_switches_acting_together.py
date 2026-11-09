"""Settings the device keeps no copy of that decide together whether a channel is there.

Three shown checkboxes "Logic 1-4", "Logic 5-8" and "Logic 9-12" (no memory, initially on)
each show the stored function of their group; a calculation shows the channel they are in
when any of them is on, and the channel stores a hidden setting with a fixed value (or a
shown one with a single choice, which the segment's own bytes are no value of). With all three
off, the device holds nothing of the channel (the segment's own bytes). Switching off one of
them alone changes nothing the device holds: only all of them together give its memory.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0009-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_GROUPS = [f"{_APP}_P-{n}_R-{n}" for n in (1, 2, 3)]
_CHANNEL = f"{_APP}_P-4_R-4"
_SIZE = f"{_APP}_P-5_R-5"
_FUNCTIONS = [f"{_APP}_P-{n}_R-{n}" for n in (6, 7, 8)]


def _group(number: int) -> str:
    return (
        f'<Parameter Id="{_APP}_P-{number}" Name="Group{number}" ParameterType="{_APP}_PT-Check"'
        f' Text="Logic {number}" Value="1" />'
    )


def _function(number: int, offset: int) -> str:
    return (
        f'<Parameter Id="{_APP}_P-{number}" Name="Function{number}" ParameterType="{_APP}_PT-Byte"'
        f' Text="Function" Value="0">'
        f'<Memory CodeSegment="{_SEGMENT}" Offset="{offset}" BitOffset="0" /></Parameter>'
    )


def _refs() -> str:
    return "".join(
        f'<ParameterRef Id="{_APP}_P-{n}_R-{n}" RefId="{_APP}_P-{n}" />'
        for n in range(1, 9)
    )


def _blocks() -> str:
    return "".join(
        f'<choose ParamRefId="{group}"><when test="1">'
        f'<ParameterBlock Id="{_APP}_PB-{n + 3}" Text="Logic {n + 1}">'
        f'<ParameterRefRef RefId="{function}" /></ParameterBlock></when></choose>'
        for n, (group, function) in enumerate(zip(_GROUPS, _FUNCTIONS, strict=True))
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="9" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Switches acting together"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="5"><Data>//////8=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-Check" Name="Check">
                <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Byte" Name="Byte">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Size" Name="Size">
                <TypeRestriction Base="Value" SizeInBit="16">
                  <Enumeration Text="60" Value="60" Id="{_APP}_PT-Size_EN-60" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Word" Name="Word">
                <TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              {_group(1)}
              {_group(2)}
              {_group(3)}
              <Parameter Id="{_APP}_P-4" Name="Channel" ParameterType="{_APP}_PT-Byte" Text="" Access="None" Value="1" />
              <Parameter Id="{_APP}_P-5" Name="Size" ParameterType="{_APP}_PT-Word" Text="Size" Access="None" Value="60">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
              </Parameter>
              {_function(6, 2)}
              {_function(7, 3)}
              {_function(8, 4)}
            </Parameters>
            <ParameterRefs>{_refs()}</ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_APP}_PC-1" Name="Channel" Language="JavaScript">
                <RLTransformation>//Not implemented</RLTransformation>
                <LRTransformation>c = 0;
if (a == 1 || b == 1 || d == 1) {{
  c = 1;
}}</LRTransformation>
                <LParameters>
                  <ParameterRefRef RefId="{_GROUPS[0]}" AliasName="a" />
                  <ParameterRefRef RefId="{_GROUPS[1]}" AliasName="b" />
                  <ParameterRefRef RefId="{_GROUPS[2]}" AliasName="d" />
                </LParameters>
                <RParameters><ParameterRefRef RefId="{_CHANNEL}" AliasName="c" /></RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_GROUPS[0]}" />
                <ParameterRefRef RefId="{_GROUPS[1]}" />
                <ParameterRefRef RefId="{_GROUPS[2]}" />
                <ParameterRefRef RefId="{_CHANNEL}" />
              </ParameterBlock>
            </ChannelIndependentBlock>
            <choose ParamRefId="{_CHANNEL}">
              <when test="1">
                <Channel Id="{_APP}_CH-1" Name="Logic" Text="Logic" Number="1">
                  <ParameterBlock Id="{_APP}_PB-2" Text="General">
                    <ParameterRefRef RefId="{_SIZE}" />
                  </ParameterBlock>
                  {_blocks()}
                </Channel>
              </when>
            </choose>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module", params=["hidden", "shown"])
def application(request: pytest.FixtureRequest) -> Application:
    xml = _XML
    if request.param == "shown":
        xml = xml.replace(
            f'ParameterType="{_APP}_PT-Word" Text="Size" Access="None"',
            f'ParameterType="{_APP}_PT-Size" Text="Size"',
        )
    return parse_application_xml(xml.encode(), "M-00FA")[0]


def _device(application: Application, groups: tuple[str, str, str]) -> dict[str, bytes]:
    ui = application.dynamic_ui()
    assert ui is not None
    for group, value in zip(_GROUPS, groups, strict=True):
        ui.edit_parameter(group, value)
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


def test_the_channel_shows_while_any_switch_is_on(application: Application) -> None:
    assert _device(application, ("0", "0", "0"))[_SEGMENT] == b"\xff" * 5
    assert _device(application, ("0", "1", "0"))[_SEGMENT] == b"\x00\x3c\xff\x00\xff"


@pytest.mark.parametrize("effort", [RecoveryEffort.FAST, RecoveryEffort.MEDIUM])
def test_switches_off_together_are_found_together(
    application: Application, effort: RecoveryEffort
) -> None:
    segments = _device(application, ("0", "0", "0"))
    recovered = recover_parameters(application, segments, effort=effort)
    assert recovered.complete
    assert [recovered.values.get(group, "1") for group in _GROUPS] == ["0", "0", "0"]
    again = seed_dynamic_ui(application, recovered.values)
    assert again is not None
    assert again.encode_to_memory()[_SEGMENT] == segments[_SEGMENT]


@pytest.mark.parametrize(
    "groups", [("1", "1", "1"), ("0", "1", "0"), ("1", "0", "0"), ("0", "1", "1")]
)
def test_a_channel_that_is_there_is_kept(
    application: Application, groups: tuple[str, str, str]
) -> None:
    segments = _device(application, groups)
    recovered = recover_parameters(application, segments, effort=RecoveryEffort.FAST)
    assert recovered.complete
    again = seed_dynamic_ui(application, recovered.values)
    assert again is not None
    assert again.encode_to_memory()[_SEGMENT] == segments[_SEGMENT]
