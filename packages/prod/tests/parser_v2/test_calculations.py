"""ParameterCalculation plans and the DynamicUI change API."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.allocator import AllocatorOverflow
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI
from xknxeditor.prod.script import CalculationError, ParameterValidationError

APP = "M-00FA_A-0001-01-0000"


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


MD = f"{APP}_MD-1"
MREF_X = f"{MD}_P-1_R-1"
MREF_Y = f"{MD}_P-2_R-2"

XML = (
    Path(__file__).parents[1] / "fixtures" / "calculations_application.xml"
).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def builder() -> DynamicTreeBuilder:
    app = parse_application_xml(XML.encode(), "M-00FA")[0]
    return DynamicTreeBuilder(app.program)


@pytest.fixture
def ui(builder: DynamicTreeBuilder) -> DynamicUI:
    app = parse_application_xml(XML.encode(), "M-00FA")[0]
    dui = DynamicUI(app.program, tree_builder=builder)
    dui.ui()
    return dui


def test_plan_is_topological_and_runs_each_calculation_once(
    builder: DynamicTreeBuilder,
) -> None:
    plan = [
        (c.id.rsplit("_", 1)[1], d) for c, d in builder.idx.calculation_plan(_ref(1))
    ]
    assert plan[0] == ("PC-1", "LR")
    assert plan.index(("PC-2", "LR")) < plan.index(("PC-3", "LR"))
    assert ("PC-4", "RL") in plan
    assert len(plan) == len(set(plan)) == 5


def test_lr_chain_with_context_and_output_prefill(ui: DynamicUI) -> None:
    changes = ui.edit_parameter(_ref(1), "5")
    assert ui.get_value(_ref(2)) == "11"
    assert ui.get_value(_ref(3)) == "14"
    assert ui.get_value(_ref(4)) == "15"
    assert ui.get_value(_ref(6)) == "n=5"
    assert changes == {
        _ref(1): ("1", "5"),
        _ref(2): ("2", "11"),
        _ref(3): ("3", "14"),
        _ref(4): ("4", "15"),
        _ref(6): ("n=1", "n=5"),
    }


@pytest.mark.parametrize(
    "context", ["{ offset: 1}", "{offset:1,}", "{'offset': 1 /* one */}"]
)
def test_context_written_as_object_literal(context: str) -> None:
    xml = XML.replace('"{&quot;offset&quot;: 1}"', f'"{context}"')
    app = parse_application_xml(xml.encode(), "M-00FA")[0]
    dui = DynamicUI(app.program)
    dui.ui()
    dui.edit_parameter(_ref(1), "5")
    assert dui.get_value(_ref(2)) == "11"


def test_rl_direction_does_not_ping_pong(ui: DynamicUI) -> None:
    ui.edit_parameter(_ref(2), "21")
    assert ui.get_value(_ref(1)) == "10"
    assert ui.get_value(_ref(2)) == "21"
    assert ui.get_value(_ref(6)) == "n=10"


def test_unset_outputs_are_kept(ui: DynamicUI) -> None:
    assert ui.edit_parameter(_ref(3), "9") == {
        _ref(3): ("3", "9"),
        _ref(4): ("4", "10"),
    }
    assert ui.get_value(_ref(2)) == "2"


def test_script_error_rolls_back_the_edit(ui: DynamicUI) -> None:
    with pytest.raises(CalculationError) as err:
        ui.edit_parameter(_ref(5), "9")
    assert str(err.value) == "bad"
    assert ui.get_value(_ref(5)) == "5"
    changes = ui.set_parameter_ref(_ref(5), "9")
    assert changes[_ref(5)] == ("5", "9")
    assert _ref(1) not in changes


def test_strict_type_check(ui: DynamicUI) -> None:
    with pytest.raises(ValueError):
        ui.edit_parameter(_ref(1), "5000")
    assert ui.get_value(_ref(1)) == "1"


def test_module_calculation_writes_qualified_ref(ui: DynamicUI) -> None:
    x1, y1 = f"{MD}_M-1_MI-1_P-1_R-1", f"{MD}_M-1_MI-1_P-2_R-2"
    y2 = f"{MD}_M-2_MI-1_P-2_R-2"
    assert ui.edit_parameter(x1, "3") == {x1: ("0", "3"), y1: ("0", "103")}
    assert ui.get_value(y1) == "103"
    assert ui.get_value(y2) == "0"


def test_apply_parameter_values_is_raw(ui: DynamicUI) -> None:
    ui.apply_parameter_values({_ref(1): "9"})
    assert ui.get_value(_ref(2)) == "2"
    ui.apply_parameter_values({_ref(1): None})
    assert ui.get_value(_ref(1)) == "1"


def test_active_parameter_ref_ids(ui: DynamicUI) -> None:
    active = ui.active_parameter_ref_ids()
    assert _ref(1) in active
    assert f"{MD}_M-2_MI-1_P-1_R-1" in active


@pytest.mark.parametrize("batch", [False, True])
def test_allocator_overflow_rejects_the_edit(
    ui: DynamicUI, monkeypatch: pytest.MonkeyPatch, batch: bool
) -> None:
    # A value the tree cannot allocate numbers for (e.g. too many channels) is rejected with the
    # allocator's message and leaves nothing changed.
    evaluate = DynamicUI.ui
    calls = 0

    def overflowing(self: DynamicUI) -> list[object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AllocatorOverflow("overflow", "L-1", f"{APP}_M-1")
        return list(evaluate(self))

    monkeypatch.setattr(DynamicUI, "ui", overflowing)
    with pytest.raises(ParameterValidationError) as err:
        if batch:
            ui.edit_parameters([(_ref(1), "5")])
        else:
            ui.edit_parameter(_ref(1), "5")
    assert err.value.message == "Code must look like K123."
    monkeypatch.undo()
    assert ui.get_value(_ref(1)) == "1"
    assert ui.get_value(_ref(2)) == "2"
