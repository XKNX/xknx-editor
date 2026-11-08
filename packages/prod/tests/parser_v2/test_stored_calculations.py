"""Calculations over values read from a device: outputs a parameter does not take, and the
settings found for values an RL transformation does not give back."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2 import calculation
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.script import CalculationError

APP = "M-00FA_A-0002-01-0000"
SHIFT, SHIFT_VALUE = f"{APP}_P-1_R-1", f"{APP}_P-2_R-2"
FROST, FROST_VALUE = f"{APP}_P-3_R-3", f"{APP}_P-4_R-4"
PROTECTION = f"{APP}_P-5_R-5"
LEVEL, LEVEL_VALUE = f"{APP}_P-6_R-6", f"{APP}_P-7_R-7"
LEVEL_HIGH = f"{APP}_P-8_R-8"
TIMER, NIGHT = f"{APP}_P-9_R-9", f"{APP}_P-10_R-10"
HOUR, HOUR_VALUE = f"{APP}_P-11_R-11", f"{APP}_P-12_R-12"

XML = (
    Path(__file__).parents[1] / "fixtures" / "stored_calculations_application.xml"
).read_bytes()


@pytest.fixture
def ui(monkeypatch: pytest.MonkeyPatch) -> DynamicUI:
    monkeypatch.setattr(calculation, "_failures_logged", set())
    app = parse_application_xml(XML, "M-00FA")[0]
    dui = DynamicUI(app.program)
    dui.ui()
    return dui


def _read(ui: DynamicUI, values: dict[str, str]) -> dict[str, str | None]:
    """Recalculate ``values`` as read from a device; the settings they give."""
    ui.apply_parameter_values(values)
    ui.recalculate(list(values), stored=True)
    return {ref: ui.get_value(ref) for ref in (SHIFT, FROST, LEVEL)}


def test_an_output_its_parameter_does_not_take_leaves_the_others_written(
    ui: DynamicUI, caplog: pytest.LogCaptureFixture
) -> None:
    ui.apply_parameter_values({PROTECTION: "1", FROST_VALUE: "0", SHIFT_VALUE: "5"})
    with caplog.at_level("WARNING", logger=calculation.__name__):
        ui.recalculate([FROST_VALUE, SHIFT_VALUE])
    # The frost value 0 / 2 is below its range; the shift is written all the same.
    assert ui.get_value(FROST) == "7"
    assert ui.get_value(SHIFT) == "5"
    assert [r.getMessage() for r in caplog.records] == [
        f"parameter calculation {APP}_PC-1 failed"
    ]


def test_an_edit_with_an_output_its_parameter_does_not_take_fails(
    ui: DynamicUI,
) -> None:
    with pytest.raises(CalculationError, match="valid range: 0-10, but was 40"):
        ui.edit_parameter(SHIFT_VALUE, "40")
    assert ui.get_value(SHIFT_VALUE) == "30"
    assert ui.get_value(SHIFT) == "3"


def test_stored_values_give_back_their_settings(
    ui: DynamicUI, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger=calculation.__name__):
        settings = _read(ui, {PROTECTION: "1", FROST_VALUE: "21", SHIFT_VALUE: "45"})
    # The RL transformation gives the frost, the shift (45, out of range) is searched.
    assert settings[FROST] == "10.5"
    assert settings[SHIFT] == "4.5"
    assert caplog.records == []


def test_an_rl_result_that_does_not_give_the_value_back_is_replaced(
    ui: DynamicUI,
) -> None:
    # The RL transformation computes a shift of 5, which downloads as 50.
    assert _read(ui, {SHIFT_VALUE: "5"})[SHIFT] == "0.5"


def test_a_calculation_without_rl_transformation_is_inverted(ui: DynamicUI) -> None:
    assert _read(ui, {LEVEL_VALUE: "12"})[LEVEL] == "4"


def test_the_values_not_read_follow_the_settings_found(ui: DynamicUI) -> None:
    # The level found from its stored value is above 2: the switch the same calculation
    # computes from it follows, as an edit of the level sets it.
    assert _read(ui, {LEVEL_VALUE: "12"})[LEVEL] == "4"
    assert ui.get_value(LEVEL_HIGH) == "1"


def test_the_values_not_read_follow_settings_that_give_the_values_back(
    ui: DynamicUI,
) -> None:
    # The level the device was given already gives its stored value back.
    ui.apply_parameter_values({LEVEL_HIGH: "1"})
    assert _read(ui, {LEVEL_VALUE: "3"})[LEVEL] == "1"
    assert ui.get_value(LEVEL_HIGH) == "0"


def test_a_stored_value_no_setting_gives_back_is_reported(
    ui: DynamicUI, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger=calculation.__name__):
        settings = _read(ui, {PROTECTION: "1", FROST_VALUE: "0", SHIFT_VALUE: "40"})
    assert settings[FROST] == "7"
    assert settings[SHIFT] == "4"
    assert [r.getMessage() for r in caplog.records] == [
        f"parameter calculation {APP}_PC-1 gives no settings for the stored values "
        f"{FROST_VALUE}=0"
    ]


def test_the_value_of_an_inactive_parameter_is_not_matched(
    ui: DynamicUI, caplog: pytest.LogCaptureFixture
) -> None:
    # Without protection the frost value is not downloaded: the device holds 0 there.
    with caplog.at_level("WARNING", logger=calculation.__name__):
        settings = _read(ui, {FROST_VALUE: "0", SHIFT_VALUE: "40"})
    assert settings[FROST] == "7"
    assert settings[SHIFT] == "4"
    assert caplog.records == []


@pytest.mark.parametrize(("hour", "on"), [("7", "1"), ("255", "0")])
def test_two_switches_that_only_together_give_a_value_back_are_found(
    ui: DynamicUI, caplog: pytest.LogCaptureFixture, hour: str, on: str
) -> None:
    # The hour is downloaded only with the timer and the night switch on: neither switch
    # alone gives the stored hour back.
    with caplog.at_level("WARNING", logger=calculation.__name__):
        ui.apply_parameter_values({HOUR_VALUE: hour})
        ui.recalculate([HOUR_VALUE], stored=True)
    assert (ui.get_value(TIMER), ui.get_value(NIGHT)) == (on, on)
    assert ui.get_value(HOUR) == ("7" if on == "1" else "0")
    assert caplog.records == []


def test_pairs_are_tried_only_up_to_their_limit(
    ui: DynamicUI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calculation, "_PAIR_TRIALS", 0)
    ui.apply_parameter_values({HOUR_VALUE: "7"})
    ui.recalculate([HOUR_VALUE], stored=True)
    assert (ui.get_value(TIMER), ui.get_value(NIGHT)) == ("0", "0")


def test_trial_results_are_reused(ui: DynamicUI) -> None:
    _read(ui, {SHIFT_VALUE: "45"})
    results = dict(ui.indexer.calculation_results)
    assert results
    ui.apply_parameter_values({SHIFT: "3"})
    _read(ui, {SHIFT_VALUE: "45"})
    assert ui.indexer.calculation_results == results


@pytest.mark.parametrize(
    ("type_id", "current", "expected"),
    [
        ("PT-Shift", "3", ["0", "0.5", "1"]),
        ("PT-Level", "0", ["1", "2", "3"]),
        ("PT-Mode", "0", ["1"]),
    ],
)
def test_candidates(
    ui: DynamicUI, type_id: str, current: str, expected: list[str]
) -> None:
    tc = ui.indexer.parameter_types[f"{APP}_{type_id}"].choice
    assert calculation._candidates(tc, current)[: len(expected)] == expected


def test_no_candidates_for_a_large_domain() -> None:
    from xknxeditor.namespaces.intermediate.parameter_type_t_type_float import (
        ParameterTypeTypeFloat,
    )
    from xknxeditor.namespaces.intermediate.parameter_type_t_type_float_encoding import (
        ParameterTypeTypeFloatEncoding,
    )
    from xknxeditor.namespaces.intermediate.parameter_type_t_type_number import (
        ParameterTypeTypeNumber,
    )
    from xknxeditor.namespaces.intermediate.parameter_type_t_type_number_type import (
        ParameterTypeTypeNumberType,
    )
    from xknxeditor.namespaces.intermediate.parameter_type_t_type_text import (
        ParameterTypeTypeText,
    )

    number = ParameterTypeTypeNumber(
        size_in_bit=16,
        type_value=ParameterTypeTypeNumberType.UNSIGNED_INT,
        min_inclusive=0,
        max_inclusive=1000,
    )
    fine = ParameterTypeTypeFloat(
        encoding=ParameterTypeTypeFloatEncoding.DPT_9,
        min_inclusive=0,
        max_inclusive=100,
        increment=0.1,
    )
    no_step = ParameterTypeTypeFloat(
        encoding=ParameterTypeTypeFloatEncoding.DPT_9, min_inclusive=0, max_inclusive=1
    )
    assert calculation._candidates(number, "0") == []
    assert calculation._candidates(fine, "0") == []
    assert calculation._candidates(no_step, "0") == []
    assert calculation._candidates(ParameterTypeTypeText(size_in_bit=8), "") == []
