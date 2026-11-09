"""A module instance that a parameter change ends and a later one creates again comes back with
the values it had, as a device built from its stored values has them: the group objects and
branches a value selects do not depend on whether the device was rebuilt in between."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_APP = "M-00FA_A-0005-01-0000"
_ON = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_MD-1_M-1_MI-1_P-1_R-1"
_SUB_MODE = f"{_APP}_MD-1_M-1_MI-1_SM-1_M-1_MI-1_SM-1_P-2_R-2"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="5" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Comes back"
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
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="On" ParameterType="{_APP}_PT-B" Text="Channel on" Value="1">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="7" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Channel">
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-1" Name="Mode" ParameterType="{_APP}_PT-N" Text="Mode" Value="0">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="1" BitOffset="0" />
                  </Parameter>
                  <Parameter Id="{_APP}_MD-1_P-3" Name="Fast" ParameterType="{_APP}_PT-N" Text="Fast setting" Value="0">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="2" BitOffset="0" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
                  <ParameterRef Id="{_APP}_MD-1_P-3_R-3" RefId="{_APP}_MD-1_P-3" />
                </ParameterRefs>
              </Static>
              <SubModuleDefs>
                <ModuleDef Id="{_APP}_MD-1_SM-1" Name="Extra">
                  <Static>
                    <Parameters>
                      <Parameter Id="{_APP}_MD-1_SM-1_P-2" Name="SubMode" ParameterType="{_APP}_PT-N" Text="Sub mode" Value="0">
                        <Memory CodeSegment="{_APP}_AS-0000" Offset="3" BitOffset="0" />
                      </Parameter>
                    </Parameters>
                    <ParameterRefs>
                      <ParameterRef Id="{_APP}_MD-1_SM-1_P-2_R-2" RefId="{_APP}_MD-1_SM-1_P-2" />
                    </ParameterRefs>
                  </Static>
                  <Dynamic>
                    <ParameterBlock Id="{_APP}_MD-1_SM-1_PB-1" Text="Extra">
                      <ParameterRefRef RefId="{_APP}_MD-1_SM-1_P-2_R-2" />
                    </ParameterBlock>
                  </Dynamic>
                </ModuleDef>
              </SubModuleDefs>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Channel">
                  <ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
                  <choose ParamRefId="{_APP}_MD-1_P-1_R-1">
                    <when test="4"><ParameterRefRef RefId="{_APP}_MD-1_P-3_R-3" /></when>
                  </choose>
                </ParameterBlock>
                <Module Id="{_APP}_MD-1_SM-1_M-1" RefId="{_APP}_MD-1_SM-1" Name="Extra" />
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_APP}_P-1_R-1" />
              </ParameterBlock>
              <choose ParamRefId="{_APP}_P-1_R-1">
                <when test="1">
                  <Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1" Name="A" />
                </when>
              </choose>
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
    ui = DynamicUI(
        app.program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=k, value=v) for k, v in stored.items()
        ],
        tree_builder=DynamicTreeBuilder(app.program),
    )
    ui.ui()
    return ui


def test_the_module_instance_and_its_submodule_instance_come_back_with_their_values() -> (
    None
):
    ui = _ui()
    assert f"{_APP}_MD-1_M-1_MI-1" in [i for i, _ in ui.get_module_instances()]
    ui.set_parameter_ref(_MODE, "4")
    ui.set_parameter_ref(_SUB_MODE, "7")
    ui.ui()
    ui.set_parameter_ref(_ON, "0")
    ui.ui()
    assert ui.get_module_instances() == []
    ui.set_parameter_ref(_ON, "1")
    ui.ui()
    assert ui.get_value(_MODE) == "4"
    assert ui.get_value(_SUB_MODE) == "7"
    assert f"{_APP}_MD-1_M-1_MI-1_P-3_R-3" in ui.active_parameter_ref_ids()
    # The same as a device built from the values stored for it.
    rebuilt = _ui(**{_ON: "1", _MODE: "4", _SUB_MODE: "7"})
    assert rebuilt.active_parameter_ref_ids() == ui.active_parameter_ref_ids()
    assert rebuilt.get_value(_MODE) == "4"
    assert rebuilt.get_value(_SUB_MODE) == "7"


def test_an_ended_module_instance_writes_nothing() -> None:
    ui = _ui()
    ui.set_parameter_ref(_MODE, "4")
    ui.set_parameter_ref(_ON, "0")
    ui.ui()
    assert _MODE not in ui.active_parameter_ref_ids()
    ((data, mask),) = ui.encode_to_memory_masked().values()
    assert data[1] == 0
    assert mask[1] == 0
