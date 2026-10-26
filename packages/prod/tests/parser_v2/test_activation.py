"""Plans that show a hidden parameter: the gates on its way, the parameters behind them, and a
check of every plan on a copy of the device's state."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import (
    ActivationError,
    plan_activation,
    shown_parameters,
)
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0003-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "activation_application.xml"
).read_bytes()


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(XML, "M-00FA")[0].program)


def _steps(
    ui: DynamicUI, target: int | str, value: str | None = None
) -> list[tuple[str, str]]:
    ref = _ref(target) if isinstance(target, int) else target
    plan = plan_activation(ui, ref, value)
    return [(step.parameter.label, step.value) for step in plan.steps]


def _extra(ui: DynamicUI) -> str:
    """The module parameter ``Extra`` of the first repeat pass (instance-qualified)."""
    return next(
        p.parameter.ref_id
        for p in ui.explore_parameters()
        if p.parameter.label == "Extra"
    )


def test_every_place_of_a_parameter_with_its_gates_and_sections() -> None:
    placements = {p.parameter.ref_id: p for p in _ui().explore_parameters()}
    dimming = placements[_ref(4)]
    assert [(g.param, g.when) for g in dimming.gates] == [
        (_ref(1), "1"),
        (_ref(2), "1 2"),
        (_ref(3), "1"),
    ]
    # The channel-independent block is the "General" tab.
    assert dimming.sections == ("General", "Display", "Control")
    advanced = placements[_ref(6)].gates[0]
    assert (advanced.when, advanced.unless) == (None, ("0",))
    extra = next(p for p in placements.values() if p.parameter.label == "Extra")
    gate = extra.gates[0]
    assert (gate.param, gate.when, gate.needs_active) == (_ref(11), ">=1", False)
    assert extra.sections == ("General", "General", "Extra Channel")


def test_a_hidden_parameter_is_shown_switch_by_switch() -> None:
    """Slot 1, then Visualisation, then Function: each switch only appears once the one before
    it is set, and the plan keeps that order."""
    ui = _ui()
    assert _steps(ui, 4) == [
        ("Slot 1", "1"),
        ("Visualisation", "1"),
        ("Function", "1"),
    ]
    plan = plan_activation(ui, _ref(4))
    assert [step.value_label for step in plan.steps] == [
        "Active",
        "One Button",
        "Dimming",
    ]
    assert plan.steps[2].sections == ("General", "Display", "Control")


def test_the_device_keeps_its_values() -> None:
    ui = _ui()
    before = dict(shown_parameters(ui.ui()))
    plan_activation(ui, _ref(4), "20")
    assert shown_parameters(ui.ui()).keys() == before.keys()
    assert ui.get_value(_ref(1)) == "0"
    assert _ref(4) not in shown_parameters(ui.ui())


def test_a_simulation_is_undone_also_when_it_fails() -> None:
    ui = _ui()
    shown = shown_parameters(ui.ui())
    with pytest.raises(RuntimeError), ui.simulation():
        ui.edit_parameter(_ref(1), "1")
        assert _ref(2) in shown_parameters(ui.ui())
        raise RuntimeError
    assert shown_parameters(ui.ui()) is not None
    assert shown_parameters(ui.ui()).keys() == shown.keys()
    assert ui.get_value(_ref(1)) == "0"


def test_a_value_is_part_of_the_plan() -> None:
    ui = _ui()
    plan = plan_activation(ui, _ref(4), "20")
    assert (plan.steps[-1].ref_id, plan.steps[-1].value) == (_ref(4), "20")
    with pytest.raises(ActivationError, match="cannot be shown"):
        plan_activation(ui, _ref(4), "500")


def test_the_plan_reports_what_else_it_shows() -> None:
    plan = plan_activation(_ui(), _ref(4))
    assert plan.shown == ()
    plan = plan_activation(_ui(), _ref(3))
    assert [step.ref_id for step in plan.steps] == [_ref(1), _ref(2)]
    assert plan.shown == ()


def test_a_default_branch_takes_a_value_no_other_branch_tests() -> None:
    assert _steps(_ui(), 6) == [("Mode", "1")]


def test_switches_that_already_pass_are_kept() -> None:
    ui = _ui()
    ui.edit_parameter(_ref(1), "1")
    ui.edit_parameter(_ref(2), "2")
    assert _steps(ui, 4) == [("Function", "1")]


def test_a_shown_parameter_needs_no_steps() -> None:
    plan = plan_activation(_ui(), _ref(1))
    assert plan.steps == ()


def test_a_calculated_gate_is_reached_through_its_input() -> None:
    """``Many Channels`` (not set by users) is calculated from ``Channels``: values of the input
    are tried until the repeat that shows ``Extra`` runs."""
    ui = _ui()
    assert _steps(ui, _extra(ui)) == [("Channels", "3")]


def test_a_parameter_never_shown_to_users() -> None:
    with pytest.raises(
        ActivationError, match=r"'Internal' cannot be shown: .*never shown"
    ):
        plan_activation(_ui(), _ref(7))


def test_a_gate_the_user_cannot_set() -> None:
    with pytest.raises(ActivationError, match="'Locked' is set by the application"):
        plan_activation(_ui(), _ref(9))


def test_an_unknown_parameter() -> None:
    with pytest.raises(ActivationError, match="not a parameter of this device"):
        plan_activation(_ui(), f"{APP}_P-99_R-99")
