"""Module instances in nested Repeats: a submodule instance has a counter for each Repeat it
is in (``MD-1_M-1_MI-1_SM-1_M-1_MI-1-1-2-1``: the room, its Module, the second iteration of
the outer Repeat, the first of the inner one), and a module instance of the application is
numbered through all its Repeats (``MI-3``: the first iteration of the inner Repeat in the
second of the outer one). Values a project stored under the id with only the innermost
Repeat counted are still read.
"""

from __future__ import annotations

from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
from xknxeditor.namespaces.intermediate.parameter_instance_ref_t import (
    ParameterInstanceRef,
)
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_APP = "M-00FA_A-0006-01-0000"
_ROOM = f"{_APP}_MD-1"
_DISPLAY = f"{_ROOM}_SM-1"
_LAMP = f"{_APP}_MD-2"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Nested repeats"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code>
          <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="16"><Data>AAAAAAAAAAAAAAAAAAAAAA==</Data></AbsoluteSegment>
        </Code>
        <ParameterTypes>
          <ParameterType Id="{_APP}_PT-N" Name="N">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
          </ParameterType>
          <ParameterType Id="{_APP}_PT-C" Name="C">
            <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="2" />
          </ParameterType>
        </ParameterTypes>
        <Allocators>
          <Allocator Id="{_APP}_L-1" Name="Room" Start="0" maxInclusive="15" />
        </Allocators>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{_ROOM}" Name="Room">
          <Arguments>
            <Argument Id="{_ROOM}_A-1" Name="Offset" Allocates="8" />
          </Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{_ROOM}_P-2" Name="Displays" ParameterType="{_APP}_PT-C" Text="Displays" Value="2" />
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_ROOM}_P-2_R-2" RefId="{_ROOM}_P-2" />
            </ParameterRefs>
            <Allocators>
              <Allocator Id="{_ROOM}_L-1" Name="Display" Start="1" maxInclusive="7" />
            </Allocators>
          </Static>
          <SubModuleDefs>
            <ModuleDef Id="{_DISPLAY}" Name="Display">
              <Arguments>
                <Argument Id="{_DISPLAY}_A-1" Name="Offset" Allocates="1" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_DISPLAY}_P-1" Name="Brightness" ParameterType="{_APP}_PT-N" Text="Brightness" Value="9">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" BaseOffset="{_DISPLAY}_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_DISPLAY}_P-1_R-1" RefId="{_DISPLAY}_P-1" />
                </ParameterRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_DISPLAY}_PB-1" Text="Display">
                  <ParameterRefRef RefId="{_DISPLAY}_P-1_R-1" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </SubModuleDefs>
          <Dynamic>
            <ParameterBlock Id="{_ROOM}_PB-1" Text="Room">
              <ParameterRefRef RefId="{_ROOM}_P-2_R-2" />
            </ParameterBlock>
            <Repeat Id="{_ROOM}_X-1" Name="Walls" Count="2">
              <Repeat Id="{_ROOM}_X-2" Name="Displays" ParameterRefId="{_ROOM}_P-2_R-2">
                <Module Id="{_DISPLAY}_M-1" RefId="{_DISPLAY}">
                  <NumericArg RefId="{_DISPLAY}_A-1" AllocatorRefId="{_ROOM}_L-1" BaseValue="{_ROOM}_A-1" />
                </Module>
              </Repeat>
            </Repeat>
          </Dynamic>
        </ModuleDef>
        <ModuleDef Id="{_LAMP}" Name="Lamp">
          <Static />
          <Dynamic><ParameterBlock Id="{_LAMP}_PB-1" Text="Lamp" /></Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock>
          <Repeat Id="{_APP}_X-1" Name="Rooms" Count="2">
            <Module Id="{_ROOM}_M-1" RefId="{_ROOM}">
              <NumericArg RefId="{_ROOM}_A-1" AllocatorRefId="{_APP}_L-1" />
            </Module>
          </Repeat>
          <Repeat Id="{_APP}_X-2" Name="Floors" Count="2">
            <Repeat Id="{_APP}_X-3" Name="Lamps" Count="2">
              <Module Id="{_LAMP}_M-1" RefId="{_LAMP}" />
            </Repeat>
          </Repeat>
        </ChannelIndependentBlock>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""


def _display(room: int, wall: int, display: int) -> str:
    return f"{_ROOM}_M-1_MI-{room}_SM-1_M-1_MI-{room}-1-{wall}-{display}"


# The displays of the two rooms, in the order of the rooms, their walls and displays.
_DISPLAYS = [
    _display(room, wall, display)
    for room in (1, 2)
    for wall in (1, 2)
    for display in (1, 2)
]


def _brightness(display: str) -> str:
    return f"{display}_SM-1_P-1_R-1"


def _ui(
    values: dict[str, str] | None = None,
    instances: list[tuple[str, str]] | None = None,
) -> DynamicUI:
    program = parse_application_xml(_XML.encode(), "M-00FA")[0].program
    return DynamicUI(
        program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=ref, value=value)
            for ref, value in (values or {}).items()
        ],
        module_instances=[
            ModuleInstance(id=iid, ref_id=ref) for iid, ref in instances or []
        ],
        tree_builder=DynamicTreeBuilder(program),
    )


def _memory(ui: DynamicUI) -> bytes:
    ((data, _mask),) = ui.encode_to_memory_masked().values()
    return bytes(data)


def test_each_repeat_has_a_counter_of_its_own() -> None:
    ids = [iid for iid, _ in _ui().get_module_instances()]
    rooms = [f"{_ROOM}_M-1_MI-1", f"{_ROOM}_M-1_MI-2"]
    lamps = [f"{_LAMP}_M-1_MI-{n}" for n in (1, 2, 3, 4)]
    assert sorted(ids) == sorted([*rooms, *_DISPLAYS, *lamps])


def test_the_displays_of_each_wall_have_their_own_values() -> None:
    ui = _ui()
    ui.ui()
    second_wall = _display(1, 2, 1)
    ui.set_parameter_ref(_brightness(second_wall), "5")
    ui.ui()
    assert [ui.get_value(_brightness(display)) for display in _DISPLAYS] == [
        "9",
        "9",
        "5",
        "9",
        "9",
        "9",
        "9",
        "9",
    ]
    # Room 1 at 0, room 2 at 8, each display behind its room.
    assert _memory(ui) == bytes([0, 9, 9, 5, 9, 0, 0, 0, 0, 9, 9, 9, 9, 0, 0, 0])


def test_a_value_stored_with_the_innermost_repeat_only_is_read_by_each_wall() -> None:
    # The id counted the display's own Repeat only: it stood for the display of every wall.
    earlier = f"{_ROOM}_M-1_MI-1_SM-1_M-1_MI-1-1-2"
    relative = earlier.removeprefix(f"{_APP}_")
    ui = _ui(
        {
            f"{_APP}_{_brightness(relative)}": "3",
            _brightness(_display(1, 2, 2)): "7",
        },
        [(relative, "MD-1_M-1"), (_display(1, 2, 2), f"{_ROOM}_M-1")],
    )
    ui.ui()
    assert [ui.get_value(_brightness(display)) for display in _DISPLAYS[:4]] == [
        "9",
        "3",
        "9",
        "7",
    ]
    assert ui.get_value(_brightness(_display(2, 1, 2))) == "9"
    # The instances are saved under the new ids, the earlier one is gone.
    ids = {iid for iid, _ in ui.get_module_instances()}
    assert earlier not in ids
    assert {_display(1, 1, 2), _display(1, 2, 2)} <= ids
