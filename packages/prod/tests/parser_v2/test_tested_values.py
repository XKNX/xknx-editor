"""The values the chooses on a selector test for, and the values worth trying for a parameter of
a large number range that such chooses select on."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import values_to_try
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.nodes import choose
from xknxeditor.prod.parser_v2.ui.parameter import (
    CheckBoxWidget,
    NumberWidget,
    UiParameter,
)

APP = "M-00FA_A-0003-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "activation_application.xml"
).read_bytes()


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


@pytest.mark.parametrize(
    ("condition", "values"),
    [
        ("2", ["2"]),
        ("1 2  3", ["1", "2", "3"]),
        ("=4", ["4"]),
        (">4", ["5"]),
        (">=4", ["4"]),
        ("<4", ["3"]),
        ("<=4", ["4"]),
        ("!=4", []),
        (">x", []),
        ("on", ["on"]),
    ],
)
def test_the_values_a_test_names(condition: str, values: list[str]) -> None:
    assert choose.tested_values(condition) == values
    # Each of them satisfies the test.
    assert all(choose.satisfies(condition, value) for value in values)


def test_the_values_the_chooses_on_a_selector_test_for() -> None:
    ui = DynamicUI(parse_application_xml(XML, "M-00FA")[0].program)
    assert ui.tested_values(_ref(2)) == ("1", "2")
    # A default branch names no value.
    assert ui.tested_values(_ref(5)) == ("0",)
    # No choose is on it.
    assert ui.tested_values(_ref(4)) == ()


def _number(low: int, high: int) -> UiParameter:
    return UiParameter(
        ref_id="P", label="", value="0", widget=NumberWidget(min=low, max=high)
    )


def test_a_large_range_tries_the_tested_values_first() -> None:
    parameter = _number(0, 255)
    assert values_to_try(parameter, "0") == ["1", "255"]
    assert values_to_try(parameter, "0", ("2", "1", "300", "x")) == ["2", "1", "255"]
    # The current value is not tried again.
    assert values_to_try(parameter, "2", ("2",)) == ["3", "1", "255", "0"]


def test_a_small_range_and_a_switch_try_every_value_alike() -> None:
    assert values_to_try(_number(0, 3), "0", ("7",)) == ["1", "2", "3"]
    switch = UiParameter(ref_id="P", label="", value="0", widget=CheckBoxWidget())
    assert values_to_try(switch, "0", ("1",)) == ["1"]
