from __future__ import annotations

import pytest

from editor_gui.regional import RegionalFormat
from editor_gui.widgets.value_pickers import (
    format_date,
    parse_color,
    parse_date,
    shade,
)

_DE_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip


@pytest.mark.parametrize(
    ("text", "color"),
    [
        ("#ffffff", "#FFFFFF"),
        ("00b050", "#00B050"),
        (" #1F497D ", "#1F497D"),
        ("#12345", None),
        ("red", None),
    ],
)
def test_parse_color(text: str, color: str | None) -> None:
    assert parse_color(text) == color


def test_shade_lightens_and_darkens() -> None:
    assert shade(0x000000, 0.5) == 0x808080
    assert shade(0xFFFFFF, -0.5) == 0x808080
    assert shade(0x4F81BD, 0.0) == 0x4F81BD


_ISO = RegionalFormat(short_date="yyyy-MM-dd", day_month="d. MMMM", months=_DE_MONTHS)
_GERMAN = RegionalFormat(
    short_date="dd.MM.yyyy", day_month="d. MMMM", months=_DE_MONTHS
)
_US = RegionalFormat(short_date="M/d/yyyy", day_month="MMMM d")


@pytest.mark.parametrize(
    ("regional", "year_shown", "shown"),
    [
        (_ISO, True, "2026-12-24"),
        (_ISO, False, "24. Dezember"),
        (_GERMAN, True, "24.12.2026"),
        (_US, True, "12/24/2026"),
        (_US, False, "December 24"),
        (RegionalFormat(short_date="dd.MM.yy"), True, "24.12.26"),
        (RegionalFormat(short_date="d 'de' MMMM yyyy"), True, "24 de December 2026"),
    ],
)
def test_format_date(regional: RegionalFormat, year_shown: bool, shown: str) -> None:
    assert format_date("2026-12-24", year_shown, regional) == shown


@pytest.mark.parametrize(
    ("text", "regional", "year_shown", "date"),
    [
        ("2026-10-09", _ISO, True, "2026-10-09"),
        ("2026-2-3", _ISO, True, "2026-02-03"),
        ("9.10.2026", _GERMAN, True, "2026-10-09"),
        ("2026-10-09", _GERMAN, True, "2026-10-09"),
        ("10/9/2026", _US, True, "2026-10-09"),
        ("30.02.2026", _GERMAN, True, None),
        ("9.10.2026", _ISO, True, None),
        ("24. Dezember", _ISO, False, "2020-12-24"),
        ("24.dezember", _ISO, False, "2020-12-24"),
        ("24.12", _GERMAN, False, "2020-12-24"),
        ("12-24", _ISO, False, "2020-12-24"),
        ("December 24", _US, False, "2020-12-24"),
        ("nonsense", _GERMAN, True, None),
    ],
)
def test_parse_date(
    text: str, regional: RegionalFormat, year_shown: bool, date: str | None
) -> None:
    assert parse_date(text, year_shown, "2020-01-01", regional) == date
