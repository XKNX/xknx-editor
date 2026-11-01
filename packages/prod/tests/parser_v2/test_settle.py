"""Settling the dynamic tree: passes repeat until neither the active parameters nor a value or
text written by an Assign or a Rename changes, and the passes reuse the widget of a parameter."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.state import GlobalState
from xknxeditor.prod.parser_v2.ui import UiParameter

APP = "M-00FA_A-0006-01-0000"
FIXTURES = Path(__file__).parents[1] / "fixtures"


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _ui() -> DynamicUI:
    xml = (FIXTURES / "assign_chain_application.xml").read_bytes()
    return DynamicUI(parse_application_xml(xml, "M-00FA")[0].program)


def _parameters(nodes: list[object]) -> dict[str, UiParameter]:
    found: dict[str, UiParameter] = {}
    for node in nodes:
        if isinstance(node, UiParameter):
            found[node.ref_id] = node
        found.update(_parameters(list(getattr(node, "children", []))))
    return found


def test_a_value_moved_by_a_chain_of_assigns_is_followed_to_the_end() -> None:
    # Every pass moves Source one Assign further and none changes the active parameters: the
    # passes go on as long as a value changes, until the Choose on Selector sees it.
    ui = _ui()
    shown = set(shown_parameters(ui.ui()))
    assert [ui.get_value(_ref(n)) for n in (2, 3, 4, 5)] == ["1", "1", "1", "1"]
    assert _ref(6) in shown


def test_a_changed_source_moves_through_the_chain_again() -> None:
    ui = _ui()
    ui.ui()
    ui.set_parameter_ref(_ref(1), "0")
    shown = set(shown_parameters(ui.ui()))
    assert ui.get_value(_ref(5)) == "0"
    assert _ref(6) not in shown


def test_a_pass_changes_the_state_only_when_it_ends_on_other_values() -> None:
    state = GlobalState()
    state.set("a", "1")
    state.set_text("t", "Old")
    state.reset_active()
    assert not state.pass_changed_state()
    # Written back to what it was: no change.
    state.set("a", "2")
    state.set("a", "1")
    state.set_text("t", "Old")
    assert not state.pass_changed_state()
    state.set("b", "0")
    assert state.pass_changed_state()
    state.reset_active()
    state.set_text("t", "New")
    assert state.pass_changed_state()
    # The next pass starts from what the last one left.
    state.reset_active()
    state.set_text("t", "New")
    assert not state.pass_changed_state()


def test_settles_reuse_the_widget_of_a_parameter() -> None:
    ui = _ui()
    before = _parameters(list(ui.ui()))[_ref(1)]
    ui.set_parameter_ref(_ref(1), "2")
    after = _parameters(list(ui.ui()))[_ref(1)]
    assert after.value == "2"
    assert after.widget is before.widget
