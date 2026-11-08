"""A device with stored group objects (an imported one) does not show a channel none of whose group
objects it instantiated; one without stored group objects shows every channel."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.namespaces.intermediate import (
    ComObjectInstanceRef,
    ParameterInstanceRef,
)
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameter, UiParameterBlock, UiTab

APP = "M-00FA_A-0007-01-0000"
XML = (Path(__file__).parents[1] / "fixtures" / "fan_coil_application.xml").read_bytes()
ENABLED = f"{APP}_P-1_R-1"
FEEDBACK_OFF = f"{APP}_P-4_R-4"
OUTPUT = f"{APP}_P-5_R-5"


def _ui(values: dict[str, str], objects: list[int]) -> DynamicUI:
    program = parse_application_xml(XML, "M-00FA")[0].program
    return DynamicUI(
        program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=r, value=v) for r, v in values.items()
        ],
        com_object_instance_refs=[
            ComObjectInstanceRef(ref_id=f"{APP}_O-{n}_R-{n}") for n in objects
        ],
    )


def _sections(nodes: list[UiNode] | tuple[UiNode, ...], path: str = "") -> set[str]:
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, (UiTab, UiParameterBlock)) and node.text:
            here = f"{path}/{node.text}"
            out |= {here} | _sections(node.children, here)
    return out


def _parameters(nodes: list[UiNode] | tuple[UiNode, ...]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, UiParameter):
            out.add(node.ref_id)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            out |= _parameters(node.children)
    return out


def test_a_channel_without_instantiated_objects_is_not_shown() -> None:
    ui = _ui({ENABLED: "1", FEEDBACK_OFF: "1"}, [5])
    nodes = ui.ui()
    assert "/Output" not in _sections(nodes)
    assert OUTPUT not in _parameters(nodes)
    assert OUTPUT not in ui.active_parameter_ref_ids()


def test_a_channel_with_an_instantiated_object_is_shown() -> None:
    ui = _ui({ENABLED: "1"}, [1, 2, 3, 4, 5, 6])
    nodes = ui.ui()
    assert {"/Output/Output", "/Fan Coil/General"} <= _sections(nodes)
    assert OUTPUT in _parameters(nodes)


def test_a_device_without_stored_objects_shows_every_channel() -> None:
    ui = _ui({ENABLED: "1", FEEDBACK_OFF: "1"}, [])
    assert {"/Fan Coil/General", "/Fan Coil/Fan", "/Output/Output"} <= _sections(
        ui.ui()
    )
