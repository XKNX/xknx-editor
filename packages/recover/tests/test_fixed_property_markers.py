"""A channel's function the device keeps no copy of, told by a hidden marker with a fixed
value per function that the device stores in a property. The search first reads the markers
as the device holds them, so a wrong function fits there too; checked with the markers at their
fixed values, the function is corrected by the property's bytes as by memory. A hidden switch a
calculation computes from the function is computed again for each function tried: one kept as
the wrong function had it shows a setting the right function hides.
"""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.encode import TypedPropertyKey
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui

_APP = "M-00FA_A-000A-01-0000"
_FUNCTION = f"{_APP}_P-1_R-1"
_MARKER = f"{_APP}_P-2"
_SWITCH = f"{_APP}_P-3_R-3"
_KEY: TypedPropertyKey = (6, None, 52, 0)


def _marker_refs() -> str:
    return "\n".join(
        f'              <ParameterRef Id="{_MARKER}_R-{n}" RefId="{_MARKER}" Access="None"'
        f' Value="{n}" />'
        for n in (11, 12, 13)
    )


def _whens() -> str:
    return "\n".join(
        f'                  <when test="{n}"><ParameterRefRef RefId="{_MARKER}_R-{10 + n}" />'
        "</when>"
        for n in (1, 2, 3)
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="10" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-0701" Name="Property markers"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="4.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-F" Name="F">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="3" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-M" Name="M">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Function" ParameterType="{_APP}_PT-F" Text="Function" Value="0" />
              <Parameter Id="{_MARKER}" Name="Marker" ParameterType="{_APP}_PT-M" Text="Marker" Access="None" Value="0">
                <Property ObjectIndex="6" PropertyId="52" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-3" Name="Switch" ParameterType="{_APP}_PT-B" Text="" Access="None" Value="0" />
              <Parameter Id="{_APP}_P-4" Name="Enable" ParameterType="{_APP}_PT-B" Text="Enable" Access="None" Value="1">
                <Property ObjectIndex="6" PropertyId="52" Offset="1" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_FUNCTION}" RefId="{_APP}_P-1" />
{_marker_refs()}
              <ParameterRef Id="{_SWITCH}" RefId="{_APP}_P-3" Access="None" />
              <ParameterRef Id="{_APP}_P-4_R-4" RefId="{_APP}_P-4" Access="None" Value="1" />
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{_APP}_PC-1" Name="Switch" Language="JavaScript">
                <LRTransformation>R = L0 === 1 ? 1 : 0;</LRTransformation>
                <RLTransformation>//Not implemented</RLTransformation>
                <LParameters><ParameterRefRef RefId="{_FUNCTION}" AliasName="L0" /></LParameters>
                <RParameters><ParameterRefRef RefId="{_SWITCH}" AliasName="R" /></RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="Channel">
                <ParameterRefRef RefId="{_FUNCTION}" />
                <choose ParamRefId="{_FUNCTION}">
{_whens()}
                </choose>
                <ParameterRefRef RefId="{_SWITCH}" />
                <choose ParamRefId="{_SWITCH}">
                  <when test="1"><ParameterRefRef RefId="{_APP}_P-4_R-4" /></when>
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


def _device(application: Application, function: str) -> bytes:
    """The property after a download of ``function``: the bytes only hidden settings cover
    hold the base (zeros)."""
    ui = seed_dynamic_ui(application, {_FUNCTION: function})
    assert ui is not None
    ui.recalculate([_FUNCTION])
    written = ui.encode_to_typed_properties_masked()[_KEY]
    return bytes(
        data & mask for data, mask in zip(written.data, written.mask, strict=False)
    ).ljust(2, b"\x00")


def test_the_marker_and_the_switch_follow_the_function(
    application: Application,
) -> None:
    assert _device(application, "1") == b"\x0b\x01"
    assert _device(application, "3") == b"\x0d\x00"


@pytest.mark.parametrize("effort", list(RecoveryEffort))
@pytest.mark.parametrize("function", ["1", "2", "3"])
def test_the_marker_in_the_property_picks_the_function(
    application: Application, effort: RecoveryEffort, function: str
) -> None:
    recovered = recover_parameters(
        application, {}, {_KEY: _device(application, function)}, effort=effort
    )
    assert recovered.complete
    assert recovered.values[_FUNCTION] == function
    assert recovered.values.get(_SWITCH, "0") == ("1" if function == "1" else "0")
