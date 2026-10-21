"""Inline VBScript ParameterCalculations through the DynamicUI change API."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI
from xknxeditor.prod.script import CalculationError
from xknxeditor.prod.script.compat.runtime import JScriptEnv

APP = "M-00FA_A-0002-01-0000"

XML = (Path(__file__).parents[1] / "fixtures" / "vbscript_application.xml").read_text(
    encoding="utf-8"
)

DELAY, RAW, LOCK, INVERT, FLAGS, OFFSET, LABEL, MODE, RESULT = (
    f"{APP}_P-{n}_R-{n}" for n in range(1, 10)
)


@pytest.fixture(scope="module")
def builder() -> DynamicTreeBuilder:
    app = parse_application_xml(XML.encode(), "M-00FA")[0]
    return DynamicTreeBuilder(app.program)


def _ui(builder: DynamicTreeBuilder, locale: str = "en-US") -> DynamicUI:
    app = parse_application_xml(XML.encode(), "M-00FA")[0]
    dui = DynamicUI(app.program, tree_builder=builder)
    dui.script_env = JScriptEnv(locale=locale)
    dui.ui()
    return dui


@pytest.fixture
def ui(builder: DynamicTreeBuilder) -> DynamicUI:
    return _ui(builder)


def test_lr_encodes_the_time(ui: DynamicUI) -> None:
    assert ui.edit_parameter(DELAY, "3000") == {
        DELAY: ("1000", "3000"),
        RAW: ("356", "843"),
    }


def test_inputs_changed_by_the_code_are_not_written(ui: DynamicUI) -> None:
    changes = ui.edit_parameter(DELAY, "100")
    assert ui.get_value(DELAY) == "100"
    assert ui.get_value(RAW) == "60"
    assert changes[RAW] == ("356", "60")


def test_rl_decodes_the_time(ui: DynamicUI) -> None:
    changes = ui.edit_parameter(RAW, "843")
    assert ui.get_value(DELAY) == "3000"
    assert changes[DELAY] == ("1000", "3000")


def test_outputs_start_with_their_current_values(ui: DynamicUI) -> None:
    assert ui.edit_parameter(LOCK, "1") == {LOCK: ("0", "1"), FLAGS: ("240", "241")}
    assert ui.edit_parameter(INVERT, "1") == {INVERT: ("0", "1"), FLAGS: ("241", "243")}
    changes = ui.edit_parameter(FLAGS, "2")
    assert ui.get_value(LOCK) == "0"
    assert ui.get_value(INVERT) == "1"
    assert changes[FLAGS] == ("243", "2")
    assert changes[LOCK] == ("1", "0")


def test_unchanged_outputs_are_not_written(ui: DynamicUI) -> None:
    assert ui.edit_parameter(DELAY, "1001") == {DELAY: ("1000", "1001")}


def test_text_output_uses_the_number_format_of_the_locale(
    builder: DynamicTreeBuilder,
) -> None:
    en = _ui(builder, "en-US")
    en.edit_parameter(OFFSET, "3")
    assert en.get_value(LABEL) == "Offset 1.5 K"
    de = _ui(builder, "de-DE")
    de.edit_parameter(OFFSET, "3")
    assert de.get_value(LABEL) == "Offset 1,5 K"


def test_select_case_and_resume_next(ui: DynamicUI) -> None:
    ui.edit_parameter(MODE, "1")
    assert ui.get_value(RESULT) == "10"
    ui.edit_parameter(MODE, "3")
    assert ui.get_value(RESULT) == "99"
    ui.edit_parameter(MODE, "7")
    assert ui.get_value(RESULT) == "255"


def test_runtime_error_rolls_back_the_edit(ui: DynamicUI) -> None:
    with pytest.raises(CalculationError) as err:
        ui.edit_parameter(MODE, "2")
    assert err.value.message == "Type mismatch: 'CInt'"
    assert err.value.calculation_id == f"{APP}_PC-4"
    assert ui.get_value(MODE) == "0"
    assert ui.get_value(RESULT) == "0"
