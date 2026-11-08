"""A SubModule's numeric argument with a ``BaseValue`` adds the value of that argument of the
enclosing module instance: each instance of the outer module places its SubModule's memory
behind its own base. Outside a module a ``BaseValue`` counts 0."""

from __future__ import annotations

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_APP = "M-00FA_A-0004-01-0000"


def _xml(top_level_base: bool = False) -> str:
    # The third top-level instance names a BaseValue although no module encloses it.
    extra = (
        f'<Module Id="{_APP}_MD-1_M-3" RefId="{_APP}_MD-1">'
        f'<NumericArg RefId="{_APP}_MD-1_A-1" Value="16" BaseValue="{_APP}_MD-1_A-1" /></Module>'
        if top_level_base
        else ""
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="4" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Submodule base"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="32"><Data>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Room">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-1" Name="Mode" ParameterType="{_APP}_PT-N" Text="Mode" Value="7">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
                </ParameterRefs>
              </Static>
              <SubModuleDefs>
                <ModuleDef Id="{_APP}_MD-1_SM-1" Name="Display">
                  <Arguments>
                    <Argument Id="{_APP}_MD-1_SM-1_A-1" Name="Offset" />
                  </Arguments>
                  <Static>
                    <Parameters>
                      <Parameter Id="{_APP}_MD-1_SM-1_P-1" Name="Brightness" ParameterType="{_APP}_PT-N" Text="Brightness" Value="9">
                        <Memory CodeSegment="{_APP}_AS-0000" Offset="3" BitOffset="0" BaseOffset="{_APP}_MD-1_SM-1_A-1" />
                      </Parameter>
                    </Parameters>
                    <ParameterRefs>
                      <ParameterRef Id="{_APP}_MD-1_SM-1_P-1_R-1" RefId="{_APP}_MD-1_SM-1_P-1" />
                    </ParameterRefs>
                  </Static>
                  <Dynamic>
                    <ParameterBlock Id="{_APP}_MD-1_SM-1_PB-1" Text="Display">
                      <ParameterRefRef RefId="{_APP}_MD-1_SM-1_P-1_R-1" />
                    </ParameterBlock>
                  </Dynamic>
                </ModuleDef>
              </SubModuleDefs>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Room">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
                </ParameterBlock>
                <Module Id="{_APP}_MD-1_SM-1_M-1" RefId="{_APP}_MD-1_SM-1">
                  <NumericArg RefId="{_APP}_MD-1_SM-1_A-1" Value="1" BaseValue="{_APP}_MD-1_A-1" />
                </Module>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="0" /></Module>
              <Module Id="{_APP}_MD-1_M-2" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="8" /></Module>
              {extra}
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


def _memory(top_level_base: bool = False) -> bytes:
    app = parse_application_xml(_xml(top_level_base).encode(), "M-00FA")[0]
    ui = DynamicUI(app.program, tree_builder=DynamicTreeBuilder(app.program))
    ui.ui()
    ((data, _mask),) = ui.encode_to_memory_masked().values()
    return bytes(data)


def test_the_submodule_adds_the_base_of_its_enclosing_instance() -> None:
    data = _memory()
    # Room at 0 and 8; its Display at 0 + 1 + 3 and 8 + 1 + 3, behind the room's own memory.
    assert (data[0], data[4]) == (7, 9)
    assert (data[8], data[12]) == (7, 9)
    assert data[3] == 0


def test_a_base_value_outside_a_module_counts_zero() -> None:
    data = _memory(top_level_base=True)
    # The third room stays at its own value 16: no module encloses it.
    assert (data[16], data[20]) == (7, 9)
