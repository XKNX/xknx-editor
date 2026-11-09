"""Settling the dynamic tree: passes repeat until neither the active parameters nor a value or
text written by an Assign or a Rename changes, and the passes reuse the widget of a parameter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try
from xknxeditor.prod.parser_v2.context import EvalContext, GateChecks
from xknxeditor.prod.parser_v2.dynamic import (
    _MAX_GATE_PASSES,  # pyright: ignore[reportPrivateUsage]
    DynamicUI,
)
from xknxeditor.prod.parser_v2.state import GlobalState
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameter

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


APPLICATIONS = sorted(path.name for path in FIXTURES.glob("*_application.xml"))


def _counted_passes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the passes of every settle from now on (one entry, increased per pass)."""
    passes = [0]
    settle = DynamicUI._settle  # pyright: ignore[reportPrivateUsage]

    def counted(
        self: DynamicUI, make_ctx: Callable[[], EvalContext]
    ) -> tuple[list[object], bool]:
        def make() -> EvalContext:
            passes[0] += 1
            return make_ctx()

        return settle(self, make)  # pyright: ignore[reportReturnType]

    monkeypatch.setattr(DynamicUI, "_settle", counted)
    return passes


def _render_pass_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluate the render pass after the union discovery every time."""
    discover = DynamicUI._discover_union_activity  # pyright: ignore[reportPrivateUsage]

    def without_reuse(self: DynamicUI) -> None:
        discover(self)

    monkeypatch.setattr(DynamicUI, "_discover_union_activity", without_reuse)


@pytest.mark.parametrize("name", APPLICATIONS)
def test_the_last_discovery_pass_gives_what_the_render_pass_gives(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a union member the discovery reached that the snapshot suppresses, the render
    # pass would evaluate the tree as the last discovery pass did: its tree is taken instead.
    program = parse_application_xml((FIXTURES / name).read_bytes(), "M-00FA")[0].program
    reused = DynamicUI(program)
    tree = reused.ui()
    _render_pass_forced(monkeypatch)
    rendered = DynamicUI(program)
    assert tree == rendered.ui()
    assert reused.active_parameter_ref_ids() == rendered.active_parameter_ref_ids()
    assert reused.get_module_instances() == rendered.get_module_instances()
    assert reused.state_layout() == rendered.state_layout()


def test_the_render_pass_is_left_out_when_it_would_repeat_the_last_discovery_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = parse_application_xml(
        (FIXTURES / "activation_application.xml").read_bytes(), "M-00FA"
    )[0].program
    passes = _counted_passes(monkeypatch)
    DynamicUI(program).ui()
    reused = passes[0]
    _render_pass_forced(monkeypatch)
    passes[0] = 0
    DynamicUI(program).ui()
    assert passes[0] == reused + 1


def test_a_suppressed_union_member_is_evaluated_by_the_render_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The discovery reaches both members of the union; the snapshot suppresses the one the
    # settled values do not select, which the render pass leaves out.
    program = parse_application_xml(
        (FIXTURES / "union_late_assign_application.xml").read_bytes(), "M-00FA"
    )[0].program
    passes = _counted_passes(monkeypatch)
    DynamicUI(program).ui()
    reused = passes[0]
    _render_pass_forced(monkeypatch)
    passes[0] = 0
    DynamicUI(program).ui()
    assert passes[0] == reused


def test_with_a_capture_the_render_pass_is_evaluated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The capture of the active group objects records the gates of the render pass.
    program = parse_application_xml(
        (FIXTURES / "activation_application.xml").read_bytes(), "M-00FA"
    )[0].program
    passes = _counted_passes(monkeypatch)
    DynamicUI(program).ui()
    reused = passes[0]
    passes[0] = 0
    DynamicUI(program).active_parameter_driven_com_object_ref_ids()
    assert passes[0] == reused + 1


def _plain_settle(
    self: DynamicUI, make_ctx: Callable[[], EvalContext]
) -> tuple[list[UiNode], bool]:
    """A settle that evaluates the next pass also when it would repeat the last one."""
    state = self._state  # pyright: ignore[reportPrivateUsage]
    tree: list[UiNode] = []
    for _ in range(_MAX_GATE_PASSES):
        state.reset_active()
        tree = self._tree.eval(make_ctx())  # pyright: ignore[reportPrivateUsage]
        gate_changed = state.update_gate_active()
        if not gate_changed and not state.pass_changed_state():
            return tree, True
    return tree, False


def _evaluations(program: object) -> list[tuple[object, ...]]:
    """What a UI of ``program`` evaluates to, before and after each shown parameter is set to
    another value it can take (one after the other)."""
    ui = DynamicUI(program)  # pyright: ignore[reportArgumentType]

    def snapshot() -> tuple[object, ...]:
        return (
            ui.ui(),
            ui.active_parameter_ref_ids(),
            ui.get_module_instances(),
            ui.active_parameter_driven_com_object_ref_ids(),
            ui.state_layout(),
        )

    found = [snapshot()]
    for ref_id, parameter in shown_parameters(ui.ui()).items():
        tried = values_to_try(parameter, parameter.value)
        if tried and ref_id in shown_parameters(ui.ui()):
            ui.set_parameter_ref(ref_id, tried[0])
            found.append(snapshot())
    return found


@pytest.mark.parametrize("name", APPLICATIONS)
def test_a_pass_the_next_one_would_repeat_ends_the_settle(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pass that changes no value and after which each Choose it reached selects what it
    # selected is what the next pass would do again: leaving that pass out changes nothing.
    program = parse_application_xml((FIXTURES / name).read_bytes(), "M-00FA")[0].program
    passes = _counted_passes(monkeypatch)
    skipping = _evaluations(program)
    skipped = passes[0]
    monkeypatch.setattr(DynamicUI, "_settle", _plain_settle)
    passes = _counted_passes(monkeypatch)
    assert _evaluations(program) == skipping
    assert skipped <= passes[0]


def test_the_pass_that_would_repeat_the_last_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every Choose of the first pass has its parameter shown: the discovery settles with it.
    program = parse_application_xml(
        (FIXTURES / "calculations_application.xml").read_bytes(), "M-00FA"
    )[0].program
    passes = _counted_passes(monkeypatch)
    DynamicUI(program).ui()
    assert passes[0] == 1
    monkeypatch.setattr(DynamicUI, "_settle", _plain_settle)
    passes = _counted_passes(monkeypatch)
    DynamicUI(program).ui()
    assert passes[0] == 2


def test_a_choose_whose_parameter_turns_inactive_takes_another_pass() -> None:
    # The first pass, ungated, renders the Choose of a parameter no pass shows; the next pass
    # renders nothing under it, so the first is not taken as settled.
    state = GlobalState()
    ctx = EvalContext(state)
    checks: GateChecks = []
    ctx.record_gate_checks(checks)
    assert ctx.is_gate_active("selector")
    state.update_gate_active()
    assert [(ref, active) for _scope, ref, active in checks] == [("selector", True)]
    assert not state.is_gate_active("selector")
