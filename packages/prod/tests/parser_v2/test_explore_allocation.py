"""An explore pass evaluates every branch of every choose, also branches no configuration has
together: the modules they place may take more than the allocator holds, which an edit that
chose them all would be refused for. The pass still records every placement."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.allocator import AllocatorOverflow
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_APP = "M-00FA_A-00F4-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_FIRST = f"{_APP}_P-1_R-1"
_SECOND = f"{_APP}_P-2_R-2"
_MD = f"{_APP}_MD-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="244" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Explore allocation"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="8"><Data>AAAAAAAAAAA=</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-B" Name="B"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="1" /></ParameterType>
        </ParameterTypes>
        <Parameters>
          <Parameter Id="{_APP}_P-1" Name="First" ParameterType="{_APP}_PT-B" Text="First" Value="0" />
          <Parameter Id="{_APP}_P-2" Name="Second" ParameterType="{_APP}_PT-B" Text="Second" Value="0" />
        </Parameters>
        <ParameterRefs>
          <ParameterRef Id="{_FIRST}" RefId="{_APP}_P-1" />
          <ParameterRef Id="{_SECOND}" RefId="{_APP}_P-2" />
        </ParameterRefs>
        <Allocators><Allocator Id="{_APP}_L-1" Name="Address" Start="1" maxInclusive="4" /></Allocators>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_MD}" Name="Channel">
          <Arguments><Argument Id="{_MD}_A-1" Name="Offset" Allocates="3" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_MD}_P-1" Name="Value" ParameterType="{_APP}_PT-B" Text="Value" Value="0">
                <Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" BaseOffset="{_MD}_A-1" />
              </Parameter>
            </Parameters>
            <ParameterRefs><ParameterRef Id="{_MD}_P-1_R-1" RefId="{_MD}_P-1" /></ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{_MD}_PB-1" Text="Channel"><ParameterRefRef RefId="{_MD}_P-1_R-1" /></ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <ParameterBlock Id="{_APP}_PB-1" Text="General">
            <ParameterRefRef RefId="{_FIRST}" />
            <ParameterRefRef RefId="{_SECOND}" />
          </ParameterBlock>
        </ChannelIndependentBlock>
        <choose ParamRefId="{_FIRST}"><when test="1">
          <Module Id="{_MD}_M-1" RefId="{_MD}"><NumericArg RefId="{_MD}_A-1" AllocatorRefId="{_APP}_L-1" /></Module>
        </when></choose>
        <choose ParamRefId="{_SECOND}"><when test="1">
          <Module Id="{_MD}_M-2" RefId="{_MD}"><NumericArg RefId="{_MD}_A-1" AllocatorRefId="{_APP}_L-1" /></Module>
        </when></choose>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _ui() -> DynamicUI:
    ui = parse_application_xml(_XML.encode(), "M-00FA")[0].dynamic_ui()
    assert ui is not None
    return ui


def test_an_explore_pass_places_modules_that_do_not_fit_together() -> None:
    placed = {p.parameter.ref_id for p in _ui().explore_parameters()}
    assert {
        _FIRST,
        _SECOND,
        f"{_MD}_M-1_MI-1_P-1_R-1",
        f"{_MD}_M-2_MI-1_P-1_R-1",
    } <= placed


def test_a_configuration_with_both_is_refused() -> None:
    ui = _ui()
    ui.edit_parameter(_FIRST, "1")
    with pytest.raises((AllocatorOverflow, ValueError)):
        ui.edit_parameter(_SECOND, "1")
