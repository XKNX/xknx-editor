"""The load controls of a device's module instances by merge id (see
:mod:`xknxeditor.prod.parser_v2.module_procedures`): a submodule's procedure runs at the merge
point of its module's, once per submodule instance; one its module does not merge is the
application's to merge."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_delay_t import LdCtrlDelay
from xknxeditor.namespaces.intermediate.ld_ctrl_read_function_prop_t import (
    LdCtrlReadFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_APP = "M-00FA_A-0007-01-0000"
_ROOM = f"{_APP}_MD-1"
_DISPLAY = f"{_ROOM}_SM-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="7" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Nested procedures"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Hidden" ParameterType="{_APP}_PT-N" Text="Hidden" Value="1" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1" />
            </ParameterRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_ROOM}" Name="Room">
              <Arguments>
                <Argument Id="{_ROOM}_A-1" Name="Room" />
                <Argument Id="{_ROOM}_A-2" Name="Unset" />
              </Arguments>
              <Static>
                <LoadProcedures>
                  <LoadProcedure MergeId="106">
                    <LdCtrlWriteProp ObjType="50001" PropId="51" Verify="false" BaseOccurrence="{_ROOM}_A-1" />
                    <LdCtrlMerge MergeId="200" />
                    <LdCtrlCompareProp ObjType="50001" PropId="51" StartElement="1" InlineData="00" BaseStartElement="{_ROOM}_A-1" />
                    <LdCtrlReadFunctionProp ObjType="50001" PropId="51" BaseOccurrence="{_ROOM}_A-2" />
                    <choose ParamRefId="{_APP}_P-1_R-1">
                      <when default="true"><LdCtrlDelay MilliSeconds="1" /></when>
                    </choose>
                  </LoadProcedure>
                </LoadProcedures>
              </Static>
              <SubModuleDefs>
                <ModuleDef Id="{_DISPLAY}" Name="Display">
                  <Arguments>
                    <Argument Id="{_DISPLAY}_A-1" Name="Display" />
                  </Arguments>
                  <Static>
                    <LoadProcedures>
                      <LoadProcedure MergeId="200">
                        <LdCtrlWriteProp ObjType="50002" PropId="51" Verify="false" BaseOccurrence="{_DISPLAY}_A-1" />
                      </LoadProcedure>
                      <LoadProcedure MergeId="201">
                        <LdCtrlWriteProp ObjType="50003" PropId="51" Verify="false" BaseOccurrence="{_DISPLAY}_A-1" />
                      </LoadProcedure>
                    </LoadProcedures>
                  </Static>
                </ModuleDef>
              </SubModuleDefs>
              <Dynamic>
                <Repeat Id="{_ROOM}_X-1" Name="Displays" Count="2">
                  <Module Id="{_DISPLAY}_M-1" RefId="{_DISPLAY}">
                    <NumericArg RefId="{_DISPLAY}_A-1" Value="1" BaseValue="{_ROOM}_A-1" />
                  </Module>
                </Repeat>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Module Id="{_ROOM}_M-1" RefId="{_ROOM}"><NumericArg RefId="{_ROOM}_A-1" Value="0" /></Module>
              <Module Id="{_ROOM}_M-2" RefId="{_ROOM}"><NumericArg RefId="{_ROOM}_A-1" Value="4" /></Module>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


def _controls() -> dict[int, list[object]]:
    app = parse_application_xml(_XML.encode(), "M-00FA")[0]
    ui = DynamicUI(app.program, tree_builder=DynamicTreeBuilder(app.program))
    return ui.module_load_controls()


def _summary(controls: list[object]) -> list[tuple[str, int | None, int, int]]:
    result: list[tuple[str, int | None, int, int]] = []
    for c in controls:
        if isinstance(c, (LdCtrlWriteProp, LdCtrlCompareProp)):
            result.append((type(c).__name__, c.obj_type, c.occurrence, c.start_element))
        else:
            result.append((type(c).__name__, None, 0, 0))
    return result


def test_the_displays_run_inside_their_room() -> None:
    controls = _controls()
    # Room 1 at 0 (displays at 1), room 2 at 4 (displays at 5): each room's procedure with
    # its displays' at the merge point. The read with an argument without a value is left
    # out; the choose on an application parameter no page shows selects nothing.
    assert _summary(controls[106]) == [
        ("LdCtrlWriteProp", 50001, 0, 1),
        ("LdCtrlWriteProp", 50002, 1, 1),
        ("LdCtrlWriteProp", 50002, 1, 1),
        ("LdCtrlCompareProp", 50001, 0, 1),
        ("LdCtrlWriteProp", 50001, 4, 1),
        ("LdCtrlWriteProp", 50002, 5, 1),
        ("LdCtrlWriteProp", 50002, 5, 1),
        ("LdCtrlCompareProp", 50001, 0, 5),
    ]
    assert not any(
        isinstance(c, (LdCtrlReadFunctionProp, LdCtrlDelay)) for c in controls[106]
    )


def test_a_display_procedure_the_room_does_not_merge_is_the_applications() -> None:
    assert _summary(_controls()[201]) == [
        ("LdCtrlWriteProp", 50003, 1, 1),
        ("LdCtrlWriteProp", 50003, 1, 1),
        ("LdCtrlWriteProp", 50003, 5, 1),
        ("LdCtrlWriteProp", 50003, 5, 1),
    ]
