"""A colour of a v10/v11 application (a text with a TypeColor pattern) is three octets."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

APP = "M-00FA_A-0003-01-0000"
PATTERN = "^#[0-9a-fA-F]{6}$(?# TypeColor:{space})"


def _xml(namespace: int, space: str) -> bytes:
    pattern = PATTERN.replace("{space}", space)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/{namespace}" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{APP}" ApplicationNumber="3" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-0705" Name="Colour"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="4.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{APP}_AS-4000" Address="16384" Size="4" />
            </Code>
            <ParameterTypes>
              <ParameterType Id="{APP}_PT-C" Name="TypeColor">
                <TypeText SizeInBit="56" Pattern="{pattern}" />
              </ParameterType>
              <ParameterType Id="{APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{APP}_P-1" Name="RGB" ParameterType="{APP}_PT-C" Text="RGB" Value="#FF8000">
                <Memory CodeSegment="{APP}_AS-4000" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{APP}_P-2" Name="White" ParameterType="{APP}_PT-N" Text="White" Value="7">
                <Memory CodeSegment="{APP}_AS-4000" Offset="3" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{APP}_P-1_R-1" RefId="{APP}_P-1" />
              <ParameterRef Id="{APP}_P-2_R-2" RefId="{APP}_P-2" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{APP}_PB-1" Text="Main">
                <ParameterRefRef RefId="{APP}_P-1_R-1" />
                <ParameterRefRef RefId="{APP}_P-2_R-2" />
              </ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>""".encode()


def _memory(namespace: int, space: str) -> str:
    program = parse_application_xml(_xml(namespace, space), "M-00FA")[0].program
    ui = DynamicUI(program, tree_builder=DynamicTreeBuilder(program))
    ui.ui()
    ((encoded, _written),) = ui.encode_to_memory_masked().values()
    return encoded.hex(" ")


@pytest.mark.parametrize("namespace", [10, 11])
@pytest.mark.parametrize(
    ("space", "data"),
    [
        ("RGB", "ff 80 00 07"),
        # #FF8000 as hue, saturation and value.
        ("HSV", "15 ff ff 07"),
    ],
)
def test_colour_is_three_octets_before_the_next_parameter(
    namespace: int, space: str, data: str
) -> None:
    assert _memory(namespace, space) == data
