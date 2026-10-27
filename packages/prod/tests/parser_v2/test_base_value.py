"""A module parameter with a BaseValue takes the value of that module argument: in the
parameter view, for the branches it selects, and in the download. Its own value, default or
stored, does not count while the argument has a value."""

from __future__ import annotations

from typing import Any

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI
from xknxeditor.prod.parser_v2.ui import UiParameter

_APP = "M-00FA_A-0003-01-0000"
_KIND = f"{_APP}_MD-1_P-1_R-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="3" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Base value"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="8"><Data>AAAAAAAAAAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Channel">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
                <Argument Id="{_APP}_MD-1_A-2" Name="IsDimmer" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-1" Name="Kind" ParameterType="{_APP}_PT-B" Text="" Access="None" Value="1" BaseValue="{_APP}_MD-1_A-2">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="7" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                  <Parameter Id="{_APP}_MD-1_P-2" Name="Switch" ParameterType="{_APP}_PT-N" Text="Switching delay" Value="3">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="1" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                  <Parameter Id="{_APP}_MD-1_P-3" Name="Dim" ParameterType="{_APP}_PT-N" Text="Dimming speed" Value="5">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="2" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
                  <ParameterRef Id="{_APP}_MD-1_P-2_R-2" RefId="{_APP}_MD-1_P-2" />
                  <ParameterRef Id="{_APP}_MD-1_P-3_R-3" RefId="{_APP}_MD-1_P-3" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Channel">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
                  <choose ParamRefId="{_APP}_MD-1_P-1_R-1">
                    <when test="0"><ParameterRefRef RefId="{_APP}_MD-1_P-2_R-2" /></when>
                    <when test="1"><ParameterRefRef RefId="{_APP}_MD-1_P-3_R-3" /></when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1" Name="A"><NumericArg RefId="{_APP}_MD-1_A-1" Value="0" /><NumericArg RefId="{_APP}_MD-1_A-2" Value="0" /></Module>
              <Module Id="{_APP}_MD-1_M-2" RefId="{_APP}_MD-1" Name="B"><NumericArg RefId="{_APP}_MD-1_A-1" Value="4" /><NumericArg RefId="{_APP}_MD-1_A-2" Value="1" /></Module>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


def _ui(**stored: str) -> DynamicUI:
    app = parse_application_xml(_XML.encode(), "M-00FA")[0]
    refs = [ParameterInstanceRef(ref_id=k, value=v) for k, v in stored.items()]
    ui = DynamicUI(
        app.program,
        parameter_instance_refs=refs,
        tree_builder=DynamicTreeBuilder(app.program),
    )
    ui.ui()
    return ui


def _labels(nodes: Any) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, UiParameter):
            out.append(node.label)
        out += _labels(getattr(node, "children", []))
    return out


def test_the_argument_selects_the_branch() -> None:
    labels = _labels(_ui().ui())
    # Instance A is a switching channel (argument 0), B a dimming one (argument 1), although
    # the parameter's own value is 1.
    assert labels.count("Switching delay") == 1
    assert labels.count("Dimming speed") == 1


def test_the_download_writes_the_argument() -> None:
    ((data, mask),) = _ui().encode_to_memory_masked().values()
    assert data[0] & 0x01 == 0
    assert data[4] & 0x01 == 1
    assert mask[0] & 0x01 and mask[4] & 0x01


def test_a_stored_value_does_not_count() -> None:
    ui = _ui(**{f"{_APP}_MD-1_M-1_MI-1_P-1_R-1": "1"})
    assert _labels(ui.ui()).count("Switching delay") == 1
    ((data, _mask),) = ui.encode_to_memory_masked().values()
    assert data[0] & 0x01 == 0
