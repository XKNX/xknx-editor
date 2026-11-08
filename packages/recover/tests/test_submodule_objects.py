"""The group objects of submodule instances, numbered from their instance's base: named after
the instance they are in, as the evaluated device names them."""

from __future__ import annotations

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI
from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab
from xknxeditor.recover.parameters import (
    _objects_by_number,  # pyright: ignore[reportPrivateUsage]
)

_APP = "M-00FA_A-0008-01-0000"
_ROOM = f"{_APP}_MD-1"
_DISPLAY = f"{_ROOM}_SM-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="8" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Submodule objects"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static />
      <ModuleDefs>
        <ModuleDef Id="{_ROOM}" Name="Room">
          <Arguments><Argument Id="{_ROOM}_A-1" Name="ObjectBase" /></Arguments>
          <Static />
          <SubModuleDefs>
            <ModuleDef Id="{_DISPLAY}" Name="Display">
              <Arguments><Argument Id="{_DISPLAY}_A-1" Name="ObjectBase" /></Arguments>
              <Static>
                <ComObjects>
                  <ComObject Id="{_DISPLAY}_O-3-0" Name="Show" Text="Show" Number="1" FunctionText=""
                      ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled" CommunicationFlag="Enabled"
                      TransmitFlag="Disabled" UpdateFlag="Disabled" ReadOnInitFlag="Disabled"
                      BaseNumber="{_DISPLAY}_A-1" />
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{_DISPLAY}_O-3-0_R-1" RefId="{_DISPLAY}_O-3-0" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_DISPLAY}_PB-1" Text="Display">
                  <ComObjectRefRef RefId="{_DISPLAY}_O-3-0_R-1" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </SubModuleDefs>
          <Dynamic>
            <Module Id="{_DISPLAY}_M-1" RefId="{_DISPLAY}">
              <NumericArg RefId="{_DISPLAY}_A-1" Value="0" BaseValue="{_ROOM}_A-1" />
            </Module>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <Module Id="{_ROOM}_M-1" RefId="{_ROOM}"><NumericArg RefId="{_ROOM}_A-1" Value="10" /></Module>
          <Module Id="{_ROOM}_M-2" RefId="{_ROOM}"><NumericArg RefId="{_ROOM}_A-1" Value="20" /></Module>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _shown(ui: DynamicUI) -> dict[str, int]:
    shown: dict[str, int] = {}
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            shown[node.ref_id] = node.number
        elif isinstance(node, (UiTab, UiParameterBlock)):
            stack.extend(node.children)
    return shown


def test_each_display_object_is_numbered_in_its_room() -> None:
    app = parse_application_xml(_XML.encode(), "M-00FA")[0]
    ui = DynamicUI(app.program, tree_builder=DynamicTreeBuilder(app.program))
    first = f"{_ROOM}_M-1_MI-1_SM-1_M-1_MI-1_SM-1_O-3-0_R-1"
    second = f"{_ROOM}_M-2_MI-1_SM-1_M-1_MI-1_SM-1_O-3-0_R-1"
    assert _shown(ui) == {first: 11, second: 21}
    numbers = _objects_by_number(ui)
    assert (numbers[11], numbers[21]) == ([first], [second])
