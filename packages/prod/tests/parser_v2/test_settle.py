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


def _suppressed_chooses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the parameters of the Chooses on union members the render passes suppress
    from now on."""
    found: list[str] = []
    is_discovered_active = EvalContext.is_discovered_active

    def noted(self: EvalContext, ref_id: str) -> bool:
        active = is_discovered_active(self, ref_id)
        if not active:
            found.append(ref_id)
        return active

    monkeypatch.setattr(EvalContext, "is_discovered_active", noted)
    return found


def test_a_suppressed_union_member_leaves_the_render_pass_out_where_it_selects_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The discovery reaches both members of the union; the snapshot suppresses the one the
    # settled values do not select, whose Choose the gates leave out in the last pass of the
    # discovery as well: that pass is what the render pass gives.
    program = parse_application_xml(
        (FIXTURES / "union_late_assign_application.xml").read_bytes(), "M-00FA"
    )[0].program
    passes = _counted_passes(monkeypatch)
    reused = DynamicUI(program)
    reused.ui()
    left_out = passes[0]
    _render_pass_forced(monkeypatch)
    suppressed = _suppressed_chooses(monkeypatch)
    passes[0] = 0
    rendered = DynamicUI(program)
    assert reused.ui() == rendered.ui()
    assert passes[0] == left_out + 1
    assert f"{program.id}_P-4_R-4" in suppressed
    assert reused.active_parameter_ref_ids() == rendered.active_parameter_ref_ids()
    assert reused.get_module_instances() == rendered.get_module_instances()
    assert reused.state_layout() == rendered.state_layout()


def test_a_suppressed_union_member_is_evaluated_by_the_render_pass_after_a_passing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Copy holds First's value between its two Assigns only while they write it: in the last
    # pass of the discovery, which shows WhileCopied, not in the render pass. With a union
    # member suppressed, the render pass is evaluated.
    program = parse_application_xml(
        (FIXTURES / "union_passing_assign_application.xml").read_bytes(), "M-00FA"
    )[0].program
    passes = _counted_passes(monkeypatch)
    ui = DynamicUI(program)
    shown = shown_parameters(ui.ui())
    evaluated = passes[0]
    _render_pass_forced(monkeypatch)
    passes[0] = 0
    rendered = DynamicUI(program)
    assert ui.ui() == rendered.ui()
    assert passes[0] == evaluated
    assert f"{program.id}_P-8_R-8" not in shown
    assert ui.state_layout() == rendered.state_layout()


def test_a_pass_keeps_the_values_where_no_write_changes_one() -> None:
    state = GlobalState()
    state.set("a", "1")
    state.reset_active()
    assert state.pass_kept_values()
    # The value it has: nothing changed.
    state.set("a", "1")
    assert state.pass_kept_values()
    # Written back to what it was: changed in between.
    state.set("a", "2")
    state.set("a", "1")
    assert not state.pass_changed_state()
    assert not state.pass_kept_values()
    state.reset_active()
    assert state.pass_kept_values()


def test_an_assign_taken_again_keeps_the_values_where_it_writes_what_is_there() -> None:
    state = GlobalState()
    state.set_values({"target": "1", "source": "1"})
    state.update_gate_active()
    state.reset_active()
    # Key, target, source, fixed value and the value its source had (see AssignDone).
    state.redo_assigns([(1, "target", "source", None, "1")])
    assert state.pass_kept_values()
    state.redo_assigns([(2, "target", None, "2", None)])
    assert state.get("target") == "2"
    assert not state.pass_kept_values()


def test_a_value_given_back_or_left_to_give_back_is_no_kept_value() -> None:
    state = GlobalState()
    state.set("target", "0")
    state.reset_active()
    # A pass without gates: what its Assigns write waits to be given back.
    state.run_assign(1, "target", "1", from_source=False)
    state.update_gate_active()
    state.reset_active()
    assert not state.pass_kept_values()
    # A pass with gates in which no Assign writes it gives it back.
    state.undo_ungated_assigns()
    assert state.get("target") == "0"
    assert not state.pass_kept_values()
    state.reset_active()
    assert state.pass_kept_values()


def test_a_value_of_a_lapsed_assign_given_back_is_no_kept_value() -> None:
    state = GlobalState()
    state.set("target", "0")
    state.update_gate_active()
    state.reset_active()
    state.run_assign(1, "target", "1", from_source=False)
    # The next pass reaches no Assign of the target: the value it wrote is given back.
    state.reset_active()
    state.give_back_lapsed_assigns()
    assert state.get("target") == "0"
    assert not state.pass_kept_values()


def test_a_module_scope_that_changes_a_value_keeps_no_values() -> None:
    state = GlobalState()
    child = state.module_child(f"{APP}_MD-1_M-1")
    state.reset_active()
    assert state.pass_kept_values()
    child.set("a", "1")
    assert child.get("a") == "1"
    assert not state.pass_kept_values()


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
    settled = False
    for _ in range(_MAX_GATE_PASSES):
        state.reset_active()
        tree = self._tree.eval(make_ctx())  # pyright: ignore[reportPrivateUsage]
        state.undo_ungated_assigns()
        state.give_back_lapsed_assigns()
        gate_changed = state.update_gate_active()
        if not gate_changed and not state.pass_changed_state():
            settled = True
            break
    state.commit_assigns()
    return tree, settled


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
