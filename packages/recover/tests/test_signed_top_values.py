"""A signed 8-bit setting whose range reaches past the signed values of its size (-127..128):
the download writes 128 as 0x80, which the read gives back as 128, not as -128 (no value of
the type)."""

from __future__ import annotations

import pytest

from xknxeditor.download.image import build_image
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-0012-01-0000"
_SEGMENT = f"{_APP}_AS-0380"
_OFFSET = f"{_APP}_P-1_R-1"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="18" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Signed top values"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="896" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-S" Name="S">
            <TypeNumber SizeInBit="8" Type="signedInt" minInclusive="-127" maxInclusive="128" />
          </ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="Offset" ParameterType="{_APP}_PT-S" Text="Offset" Value="0">
            <Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" />
          </Parameter>
        </Parameters>
        <ParameterRefs><ParameterRef Id="{_OFFSET}" RefId="{_APP}_P-1" /></ParameterRefs>
      </Static>
      <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
        <ParameterRefRef RefId="{_OFFSET}" />
      </ParameterBlock></ChannelIndependentBlock></Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, value: str) -> dict[str, bytes]:
    """The segment a full download of the setting ``value`` leaves on the device."""
    ui = seed_dynamic_ui(application, {_OFFSET: value})
    assert ui is not None
    (memory,) = build_image(application, ui=ui).segments
    return {_SEGMENT: bytes(memory.data)}


@pytest.mark.parametrize(
    ("value", "octet"), [("128", 0x80), ("-127", 0x81), ("127", 0x7F), ("0", 0x00)]
)
def test_the_value_downloaded_is_read_back(
    application: Application, value: str, octet: int
) -> None:
    device = _device(application, value)
    assert device[_SEGMENT][1] == octet
    recovered = recover_parameters(application, device)
    assert recovered.complete
    assert recovered.values[_OFFSET] == value
