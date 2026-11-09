"""An HSV colour setting: the device holds the hue, saturation and value octets the colour
converts to. Several colours convert to the same octets, so the recovery gives back one of
them, which writes the device's octets again: the result fits the device.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0007-01-0000"
_COLOUR = f"{_APP}_P-1_R-1"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="HSV colour"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-C" Name="Colour">
                <TypeColor Space="HSV" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Colour" ParameterType="{_APP}_PT-C" Text="Scene colour" Value="#000000">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_COLOUR}" RefId="{_APP}_P-1" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_COLOUR}" />
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


def _device(application: Application, colour: str) -> dict[str, bytes]:
    ui = seed_dynamic_ui(application, {_COLOUR: colour})
    assert ui is not None
    return {key: bytes(data) for key, data in ui.encode_to_memory().items()}


@pytest.mark.parametrize("colour", ["#0029C8", "#02B87E", "#E6B180", "#FFFFFF"])
def test_the_colour_read_writes_the_octets_of_the_device(
    application: Application, colour: str
) -> None:
    device = _device(application, colour)
    recovered = recover_parameters(application, device)
    assert recovered.complete
    assert recovered.unknown == []
    assert _device(application, recovered.values[_COLOUR]) == device
