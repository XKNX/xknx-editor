"""Dates and times as the GUI shows them: local time in the regional date and time formats."""

from __future__ import annotations

import datetime

import pytest

from editor_gui import local_time
from editor_gui.local_time import (
    MISSING,
    WIDEST,
    date_text,
    date_time_text,
    iso_date_text,
    parse_stamp,
    recent_text,
    stamp_text,
    time_text,
)
from editor_gui.regional import RegionalFormat, without_seconds

GERMAN = RegionalFormat(
    decimal=",", group=".", short_date="dd.MM.yyyy", long_time="HH:mm:ss"
)
US = RegionalFormat(short_date="M/d/yyyy", long_time="h:mm:ss tt", short_time="h:mm tt")
EVENING = datetime.datetime(2026, 1, 4, 20, 5, 3, 123_456)


def test_a_24_hour_time() -> None:
    assert time_text(EVENING, regional=GERMAN) == "20:05:03"
    assert time_text(EVENING, seconds=False, regional=GERMAN) == "20:05"
    # The milliseconds after the seconds, with the regional decimal separator.
    assert time_text(EVENING, millis=True, regional=GERMAN) == "20:05:03,123"


def test_a_12_hour_time() -> None:
    assert time_text(EVENING, regional=US) == "8:05:03 PM"
    assert time_text(EVENING, seconds=False, regional=US) == "8:05 PM"
    assert time_text(EVENING, millis=True, regional=US) == "8:05:03.123 PM"
    assert time_text(datetime.time(0, 0), regional=US) == "12:00:00 AM"
    assert time_text(datetime.time(12, 30), regional=US) == "12:30:00 PM"


def test_quoted_text_and_a_missing_am_pm() -> None:
    quoted = RegionalFormat(long_time="HH'h'mm", short_time="HH'h'mm")
    assert time_text(EVENING, regional=quoted) == "20h05"
    # A 12-hour pattern of a locale without AM/PM texts leaves no space behind.
    bare = RegionalFormat(long_time="hh:mm:ss tt", am="", pm="")
    assert time_text(EVENING, regional=bare) == "08:05:03"


def test_the_date_and_time() -> None:
    assert date_text(EVENING, GERMAN) == "04.01.2026"
    assert date_text(EVENING.date(), US) == "1/4/2026"
    assert date_time_text(EVENING, regional=GERMAN) == "04.01.2026 20:05"
    assert date_time_text(EVENING, seconds=True, regional=US) == "1/4/2026 8:05:03 PM"


def test_a_time_with_a_time_zone_is_shown_in_local_time() -> None:
    moment = datetime.datetime(2026, 1, 24, 11, 38, 26, tzinfo=datetime.UTC)
    local = moment.astimezone()
    assert date_time_text(moment, regional=GERMAN) == f"{local:%d.%m.%Y %H:%M}"


@pytest.mark.parametrize(
    "stored",
    [
        "2026-01-24T11:38:26.8111136Z",
        "2026-01-24T11:38:26Z",
        "2026-01-24T11:38:26+00:00",
        # Without an offset the project stores UTC.
        "2026-01-24T11:38:26",
        " 2026-01-24T11:38:26.5z ",
    ],
)
def test_a_stored_time_is_shown_in_local_time(stored: str) -> None:
    moment = datetime.datetime(2026, 1, 24, 11, 38, 26, tzinfo=datetime.UTC)
    local = moment.astimezone()
    assert stamp_text(stored, regional=GERMAN) == f"{local:%d.%m.%Y %H:%M}"
    assert stamp_text(stored, seconds=True, regional=GERMAN) == (
        f"{local:%d.%m.%Y %H:%M:%S}"
    )


def test_a_stored_date_and_no_time() -> None:
    assert stamp_text("2026-01-24", regional=GERMAN) == "24.01.2026"
    assert stamp_text(None) == MISSING
    assert stamp_text("") == MISSING
    # A text that is no time is shown as it is.
    assert stamp_text("yesterday") == "yesterday"
    assert stamp_text("not a time") == "not a time"
    assert parse_stamp("yesterday") is None
    assert parse_stamp(None) is None
    assert iso_date_text("2026-10-12", US) == "10/12/2026"
    assert iso_date_text("soon", US) == "soon"


def test_recent_times_show_the_date_only_on_another_day() -> None:
    assert recent_text(EVENING, EVENING, regional=GERMAN) == "20:05:03"
    tomorrow = EVENING + datetime.timedelta(days=1)
    assert recent_text(EVENING, tomorrow, regional=GERMAN) == "04.01.2026 20:05:03"


def test_the_system_format_is_used_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_time, "regional_format", lambda: US)
    assert time_text(EVENING) == "8:05:03 PM"
    assert date_time_text(EVENING) == "1/4/2026 8:05 PM"


def test_the_widest_moment_is_as_wide_as_any() -> None:
    widest = time_text(WIDEST, regional=US)
    for hour in range(24):
        moment = datetime.time(hour, 59, 59)
        assert len(time_text(moment, regional=US)) <= len(widest)


@pytest.mark.parametrize(
    ("pattern", "short"),
    [
        ("HH:mm:ss", "HH:mm"),
        ("h:mm:ss tt", "h:mm tt"),
        ("tt hh:mm:ss", "tt hh:mm"),
        ("HH.mm.ss", "HH.mm"),
        ("H:mm", "H:mm"),
    ],
)
def test_a_time_pattern_without_its_seconds(pattern: str, short: str) -> None:
    assert without_seconds(pattern) == short


def test_the_table_time_pattern_of_a_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    from editor_gui import regional
    from xknxeditor.prod.script.compat import runtime

    monkeypatch.setattr(runtime, "system_locale", lambda: "en-US")
    table = regional._table_format()  # pyright: ignore[reportPrivateUsage]
    assert (table.long_time, table.short_time) == ("h:mm:ss tt", "h:mm tt")
    monkeypatch.setattr(runtime, "system_locale", lambda: "de-DE")
    table = regional._table_format()  # pyright: ignore[reportPrivateUsage]
    assert (table.long_time, table.short_time) == ("HH:mm:ss", "HH:mm")
