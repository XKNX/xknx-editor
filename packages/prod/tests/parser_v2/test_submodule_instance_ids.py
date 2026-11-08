"""A submodule instance is named after the module instance it is in, as a project stores it
(``MD-1_M-1_MI-2_SM-1_M-1_MI-2-1-1``): the instances of the submodule in two instances of its
module are two instances, each with its own parameters. Values a project stored under the
earlier id without the enclosing instance (``MD-1_SM-1_M-1_MI-1``) are still read."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
from xknxeditor.namespaces.intermediate.parameter_instance_ref_t import (
    ParameterInstanceRef,
)
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_APP = "M-00FA_A-0005-01-0000"
_ROOM = f"{_APP}_MD-1"
_DISPLAY = f"{_ROOM}_SM-1"

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="5" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Submodule ids"
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
                <!-- The displays' addresses, counted in each room. -->
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
                <Repeat Id="{_ROOM}_X-1" Name="Displays" Count="2">
                  <Module Id="{_DISPLAY}_M-1" RefId="{_DISPLAY}">
                    <NumericArg RefId="{_DISPLAY}_A-1" AllocatorRefId="{_ROOM}_L-1" BaseValue="{_ROOM}_A-1" />
                  </Module>
                </Repeat>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Repeat Id="{_APP}_X-1" Name="Rooms" Count="2">
                <Module Id="{_ROOM}_M-1" RefId="{_ROOM}">
                  <NumericArg RefId="{_ROOM}_A-1" AllocatorRefId="{_APP}_L-1" />
                </Module>
              </Repeat>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""

# The displays of the two rooms, in the order of the rooms and their displays.
_DISPLAYS = [
    f"{_ROOM}_M-1_MI-1_SM-1_M-1_MI-1-1-1",
    f"{_ROOM}_M-1_MI-1_SM-1_M-1_MI-1-1-2",
    f"{_ROOM}_M-1_MI-2_SM-1_M-1_MI-2-1-1",
    f"{_ROOM}_M-1_MI-2_SM-1_M-1_MI-2-1-2",
]


def _brightness(display: str) -> str:
    return f"{display}_SM-1_P-1_R-1"


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _ui(
    values: dict[str, str] | None = None,
    instances: list[tuple[str, str]] | None = None,
) -> DynamicUI:
    app = _application()
    return DynamicUI(
        app.program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=ref, value=value)
            for ref, value in (values or {}).items()
        ],
        module_instances=[
            ModuleInstance(id=iid, ref_id=ref) for iid, ref in instances or []
        ],
        tree_builder=DynamicTreeBuilder(app.program),
    )


def _memory(ui: DynamicUI) -> bytes:
    ((data, _mask),) = ui.encode_to_memory_masked().values()
    return bytes(data)


def test_each_room_has_its_own_displays() -> None:
    ids = [iid for iid, _ in _ui().get_module_instances()]
    assert sorted(ids) == sorted([f"{_ROOM}_M-1_MI-1", f"{_ROOM}_M-1_MI-2", *_DISPLAYS])


def test_a_display_parameter_belongs_to_its_room() -> None:
    ui = _ui()
    ui.ui()
    first, _, third, _ = _DISPLAYS
    assert ui.local_ref_id(_brightness(third)) == f"{_DISPLAY}_P-1_R-1"
    changes = ui.set_parameter_ref(_brightness(third), "5")
    assert changes == {_brightness(third): ("9", "5")}
    ui.ui()
    assert ui.get_value(_brightness(third)) == "5"
    assert ui.get_value(_brightness(first)) == "9"
    # Room 1 at 0, room 2 at 8, each display behind its room: 0 + 1, 0 + 2, 8 + 1, 8 + 2.
    data = _memory(ui)
    assert (data[1], data[2], data[9], data[10]) == (9, 9, 5, 9)


def test_the_values_a_project_stores_are_read_per_room() -> None:
    # As a project stores them: ids relative to the application, the submodule instances named
    # after the module (``RefId`` "MD-1_M-1"), listed in no particular order.
    relative = [display.removeprefix(f"{_APP}_") for display in _DISPLAYS]
    ui = _ui(
        {
            f"{_APP}_{_brightness(relative[1])}": "4",
            f"{_APP}_{_brightness(relative[2])}": "6",
        },
        [
            *((display, "MD-1_M-1") for display in reversed(relative)),
            ("MD-1_M-1_MI-2", "MD-1_M-1"),
            ("MD-1_M-1_MI-1", "MD-1_M-1"),
        ],
    )
    ui.ui()
    assert [ui.get_value(_brightness(display)) for display in _DISPLAYS] == [
        "9",
        "4",
        "6",
        "9",
    ]
    data = _memory(ui)
    assert (data[1], data[2], data[9], data[10]) == (9, 4, 6, 9)


def test_a_value_stored_without_the_room_is_read_by_every_room() -> None:
    # The id without the enclosing instance stood for the display of every room.
    legacy = f"{_DISPLAY}_M-1_MI-2"
    ui = _ui(
        {f"{legacy}_P-1_R-1": "3"},
        [(f"{_ROOM}_M-1_MI-1", _ROOM), (legacy, _DISPLAY)],
    )
    ui.ui()
    assert [ui.get_value(_brightness(display)) for display in _DISPLAYS] == [
        "9",
        "3",
        "9",
        "3",
    ]
    data = _memory(ui)
    assert (data[1], data[2], data[9], data[10]) == (9, 3, 9, 3)
    # The instances are saved under the new ids, the old one is gone.
    assert legacy not in {iid for iid, _ in ui.get_module_instances()}


def test_a_new_value_of_one_room_keeps_the_others_stored_without_the_room() -> None:
    legacy = f"{_DISPLAY}_M-1_MI-1"
    ui = _ui(
        {f"{legacy}_P-1_R-1": "3", _brightness(_DISPLAYS[2]): "7"},
        [(legacy, _DISPLAY), (_DISPLAYS[2], f"{_ROOM}_M-1")],
    )
    ui.ui()
    assert [ui.get_value(_brightness(display)) for display in _DISPLAYS] == [
        "3",
        "9",
        "7",
        "9",
    ]


def test_a_value_stored_without_the_room_and_without_its_instance_is_read() -> None:
    # The project lists no instance for the value: it still is the display's.
    ui = _ui({f"{_DISPLAY}_M-1_MI-1_P-1_R-1": "2"})
    ui.ui()
    assert ui.get_value(_brightness(_DISPLAYS[0])) == "2"
    assert ui.get_value(_brightness(_DISPLAYS[2])) == "2"
