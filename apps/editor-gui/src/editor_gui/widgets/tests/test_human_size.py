"""Sizes in the unit that suits them: whole bytes, larger units with one decimal in the regional
format, the unit as the UI language writes it."""

from __future__ import annotations

import pytest

from editor_gui import regional
from editor_gui.regional import RegionalFormat
from editor_gui.strings import get_locale, set_locale
from editor_gui.widgets.text_util import human_size


@pytest.fixture
def point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regional, "regional_format", lambda: RegionalFormat(decimal=".")
    )


@pytest.mark.usefixtures("point")
@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (292_027, "285.2 KB"),
        (5_840_123, "5.6 MB"),
        (3 * 1024**3, "3.0 GB"),
        (5 * 1024**4, "5120.0 GB"),
    ],
)
def test_the_unit_that_suits_the_size(size: int, text: str) -> None:
    assert human_size(size) == text


def test_the_regional_decimal_and_the_language_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        regional, "regional_format", lambda: RegionalFormat(decimal=",")
    )
    previous = get_locale()
    try:
        set_locale("de")
        assert human_size(5_840_123) == "5,6 MB"
        set_locale("fr")
        assert human_size(5_840_123) == "5,6 Mo"
        assert human_size(12) == "12 o"
    finally:
        set_locale(previous)
