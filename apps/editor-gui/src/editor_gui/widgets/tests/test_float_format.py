from __future__ import annotations

import pytest

from editor_gui.widgets.float_format import format_float, parse_float


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("5.011537700000000E+001", "50.115377"),
        ("8.683764000000000E+000", "8.683764"),
        ("1.234567800000000E+004", "12345.678"),
        ("0", "0"),
        ("-0.0", "0"),
        ("1E12", "1000000000000"),
        ("1E15", "1E+15"),
        ("0.0001", "0.0001"),
        ("0.00001", "1E-05"),
        ("not a number", "not a number"),
    ],
)
def test_format(stored: str, shown: str) -> None:
    assert format_float(stored) == shown


def test_format_with_regional_decimal_separator() -> None:
    assert format_float("2.150000000000000E+001", decimal=",") == "21,5"
    assert format_float("1E-05", decimal=",") == "1E-05"


@pytest.mark.parametrize(
    ("text", "stored"),
    [
        ("50.115377", "50.115377"),
        ("50,115377", "50.115377"),
        (" 8.5 ", "8.5"),
        ("120", "90"),
        ("-120", "-90"),
        ("abc", None),
        ("nan", None),
    ],
)
def test_parse(text: str, stored: str | None) -> None:
    assert parse_float(text, minimum=-90, maximum=90) == stored


def test_parse_with_regional_separators() -> None:
    german = {"decimal": ",", "group": "."}
    assert parse_float("50,115377", **german) == "50.115377"
    assert parse_float("1.234,5", **german) == "1234.5"
    assert parse_float("50.115377", **german) == "50.115377"
    assert parse_float("1.234.567", **german) is None
