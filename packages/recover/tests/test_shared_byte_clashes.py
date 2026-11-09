"""Settings of a few bits each that share a byte: a setting whose memory is no value of its type
counts with its bytes, also those it shares with a setting written after it, and a setting
written only into shared bytes is one a configuration writes."""

from __future__ import annotations

import pytest

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml

_APP = "M-00FA_A-0007-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_MODE = f"{_APP}_P-1"
_LEVEL = f"{_APP}_P-2"
_SHOW = f"{_APP}_P-3_R-3"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared byte"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-Mode" Name="Mode">
                <TypeRestriction Base="Value" SizeInBit="4">
                  <Enumeration Text="Off" Value="0" Id="{_APP}_PT-Mode_EN-0" />
                  <Enumeration Text="On" Value="1" Id="{_APP}_PT-Mode_EN-1" />
                </TypeRestriction>
              </ParameterType>
              <ParameterType Id="{_APP}_PT-Level" Name="Level">
                <TypeNumber SizeInBit="4" Type="unsignedInt" minInclusive="0" maxInclusive="15" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_MODE}" Name="Mode" ParameterType="{_APP}_PT-Mode" Text="Mode" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-3" Name="Show" ParameterType="{_APP}_PT-Mode" Text="Show Mode" Value="1" />
              <Parameter Id="{_LEVEL}" Name="Level" ParameterType="{_APP}_PT-Level" Text="Level" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="4" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_MODE}_R-1" RefId="{_MODE}" />
              <ParameterRef Id="{_LEVEL}_R-2" RefId="{_LEVEL}" />
              <ParameterRef Id="{_SHOW}" RefId="{_APP}_P-3" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_SHOW}" />
                <choose ParamRefId="{_SHOW}">
                  <when test="1"><ParameterRefRef RefId="{_MODE}_R-1" /></when>
                </choose>
                <ParameterRefRef RefId="{_LEVEL}_R-2" />
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


def test_a_byte_a_setting_shares_counts_where_its_memory_is_no_value_of_it(
    application: Application,
) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    # The mode's four bits hold 7, no value of it; the level written after it shares the byte.
    segments = {_SEGMENT: bytes([0x75])}
    assert parameters._undecodable_cells(ui, segments, {_MODE}) == 1  # pyright: ignore[reportPrivateUsage]
    assert parameters._undecodable_cells(ui, segments, {_LEVEL}) == 0  # pyright: ignore[reportPrivateUsage]
    # A mode the memory holds a value of: nothing.
    assert parameters._undecodable_cells(ui, {_SEGMENT: bytes([0x15])}, {_MODE}) == 0  # pyright: ignore[reportPrivateUsage]


def test_a_setting_written_only_into_shared_bytes_counts_as_written(
    application: Application,
) -> None:
    shown = application.dynamic_ui()
    hidden = application.dynamic_ui()
    assert shown is not None
    assert hidden is not None
    hidden.set_parameter_ref(_SHOW, "0")
    assert shown.written_parameter_cells() == {(_SEGMENT, 0): {_MODE, _LEVEL}}
    assert hidden.written_parameter_cells() == {(_SEGMENT, 0): {_LEVEL}}
    # Of the configuration that writes the mode the device holds no value of, the byte the
    # mode shares with the level counts against it.
    clashes = parameters._fixed_clashes(  # pyright: ignore[reportPrivateUsage]
        {"shown": shown, "hidden": hidden}, {_SEGMENT: bytes([0x75])}, {}
    )
    assert clashes == {"shown": 1, "hidden": 0}
