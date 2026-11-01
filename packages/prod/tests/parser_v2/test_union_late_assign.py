"""The member of a Union that the settled values select wins its overlay, also when the value
that selects it is written by an Assign placed after the Choose that reads it.

In the fixture two union members, A and B, overlay one octet. A Selector picks which of them is
shown; it starts at 0 (B) and an Assign at the end of the tree copies Layout (2, A) into it. Each
member's Choose instantiates a module. The union discovery has to settle like the render pass;
a single pass would see Selector at 0, reach only B, and suppress A with its module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0005-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "union_late_assign_application.xml"
).read_text(encoding="utf-8")
ASSIGN = (
    f'<Assign TargetParamRefRef="{APP}_P-2_R-2" SourceParamRefRef="{APP}_P-1_R-1" />'
)
FIRST = f'<ParameterRefRef RefId="{APP}_P-1_R-1" />'


def _ui(xml: str = XML) -> DynamicUI:
    return DynamicUI(parse_application_xml(xml.encode(), "M-00FA")[0].program)


def _assign_first() -> str:
    """The fixture with the Assign moved in front of the Choose that reads its target."""
    assert XML.count(ASSIGN) == 1
    return XML.replace(ASSIGN, "").replace(FIRST, FIRST + ASSIGN)


@pytest.mark.parametrize("xml", [XML, _assign_first()], ids=["late", "early"])
def test_the_assigned_selector_picks_the_union_member(xml: str) -> None:
    ui = _ui(xml)
    shown = set(shown_parameters(ui.ui()))
    instances = {iid for iid, _ in ui.get_module_instances()}
    # Layout 2 selects A: its module is there, B and B's module are not.
    assert ui.get_value(f"{APP}_P-2_R-2") == "2"
    assert f"{APP}_P-3_R-3" in shown
    assert f"{APP}_P-4_R-4" not in shown
    assert f"{APP}_MD-1_M-9_MI-1" in instances
    assert f"{APP}_MD-2_M-16_MI-1" not in instances
    assert f"{APP}_MD-1_M-9_MI-1_P-1_R-1" in shown
    assert f"{APP}_MD-2_M-16_MI-1_P-1_R-1" not in shown


def test_changing_the_layout_moves_the_overlay_to_the_other_member() -> None:
    ui = _ui()
    ui.ui()
    ui.set_parameter_ref(f"{APP}_P-1_R-1", "0")
    shown = set(shown_parameters(ui.ui()))
    instances = {iid for iid, _ in ui.get_module_instances()}
    assert f"{APP}_P-4_R-4" in shown
    assert f"{APP}_P-3_R-3" not in shown
    assert f"{APP}_MD-2_M-16_MI-1" in instances
    assert f"{APP}_MD-1_M-9_MI-1" not in instances
