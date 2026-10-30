"""The page of a parameter, per kind of application (the fixture's display orders: Controller 10,
Ctrl setting 11, Detection 15, Measurement 20, Filter 25, Channel 1000, Scan rate 1001, Threshold
1100, Use threshold 1200; "Detection" sits in "Controller" under a choose on the heading parameter
of "Measurement"):
- an application of the current format: the page it is nested in;
- one converted from the older format: the page in front of it by display order;
- a converted one with its own parameter dialog (a UI plug-in): the editor's own layout, the
  selector chain and then display order into empty pages.
"""

from __future__ import annotations

from pathlib import Path

from xknxeditor.namespaces.intermediate import ApplicationProgram
from xknxeditor.namespaces.intermediate.application_program_static_t_extension import (
    ApplicationProgramStaticExtension,
)
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameter, UiParameterBlock, UiTab

XML = (
    Path(__file__).parents[1] / "fixtures" / "converted_pages_application.xml"
).read_bytes()


def _pages(nodes: list[UiNode] | tuple[UiNode, ...]) -> dict[str, list[str]]:
    """Each page block's heading text and the labels of the parameters shown directly on it."""
    out: dict[str, list[str]] = {}
    for node in nodes:
        if isinstance(node, UiParameterBlock):
            out[node.text or node.name or node.id] = [
                child.label
                for child in node.children
                if isinstance(child, UiParameter) and child.access.name != "NONE"
            ]
            out.update(_pages(node.children))
        elif isinstance(node, UiTab):
            out.update(_pages(node.children))
    return out


def _program(*, converted: bool, ui_plugin: bool = False) -> ApplicationProgram:
    program = parse_application_xml(XML, "M-00FA")[0].program
    program.pre_ets4_style = converted
    if ui_plugin:
        program.static.extension = ApplicationProgramStaticExtension(
            ets_ui_plugin="{24733AED-35E4-4891-BF66-1812FF9CD337}"
        )
    return program


def test_a_parameter_is_on_the_page_it_is_nested_in() -> None:
    pages = _pages(DynamicUI(_program(converted=False)).ui())
    assert pages["Controller"] == ["Ctrl setting", "Detection"]
    assert pages["Channel"] == ["Scan rate", "Filter", "Use threshold"]
    assert pages["Measurement"] == pages["Threshold"] == []


def test_a_converted_application_pages_by_display_order() -> None:
    assert parse_application_xml(XML, "M-00FA")[0].program.pre_ets4_style
    pages = _pages(DynamicUI(_program(converted=True)).ui())
    assert pages == {
        "Controller": ["Ctrl setting", "Detection"],
        "Measurement": ["Filter"],
        "Channel": ["Scan rate"],
        "Threshold": ["Use threshold"],
    }


def test_a_converted_application_with_its_own_dialog_keeps_the_editor_layout() -> None:
    pages = _pages(DynamicUI(_program(converted=True, ui_plugin=True)).ui())
    # selector chain: "Detection" is placed under "Controller" but its chain names "Measurement"
    assert "Detection" in pages["Measurement"]
    assert pages["Controller"] == ["Ctrl setting"]
    # display order into an empty page: "Use threshold" belongs to the empty "Threshold" page
    assert pages["Threshold"] == ["Use threshold"]
    # but not off a page the chain populated: "Filter" (25) stays on its channel
    assert pages["Channel"] == ["Scan rate", "Filter"]


def test_spaces_in_front_of_a_page_title_indent_it() -> None:
    xml = XML.replace(b'Text="Measurement"', b'Text="   Measurement"')
    program = parse_application_xml(xml, "M-00FA")[0].program
    program.pre_ets4_style = False
    indents: dict[str, int] = {}

    def walk(nodes: list[UiNode] | tuple[UiNode, ...]) -> None:
        for node in nodes:
            if isinstance(node, UiParameterBlock):
                indents[node.text or ""] = node.text_indent
                walk(node.children)
            elif isinstance(node, UiTab):
                walk(node.children)

    walk(DynamicUI(program).ui())
    # The title itself has no spaces in front: the page tree indents it.
    assert indents["Measurement"] == 3
    assert indents["Controller"] == 0
