from __future__ import annotations

import pytest

from editor_gui.widgets.strings import S
from editor_gui.widgets.time_format import format_time, parse_time, time_pattern


@pytest.mark.parametrize(
    ("hint", "pattern"),
    [
        ("Duration_hhmmss", "hh:mm:ss"),
        ("Time_mmssfff", "mm:ss.fff"),
        ("Time_hhmm", "hh:mm"),
        ("Time_ssf", "ss.f"),
        ("Time_dhhmmss", "d hh:mm:ss"),
        ("Time_dhh", "d hh"),
        (None, None),
    ],
)
def test_pattern(hint: str | None, pattern: str | None) -> None:
    assert time_pattern(hint) == pattern


@pytest.mark.parametrize(
    ("stored", "unit", "hint", "shown"),
    [
        ("3725", "Seconds", "Duration_hhmmss", "01:02:05"),
        ("61500", "Milliseconds", "Time_mmssfff", "01:01.500"),
        ("450", "Minutes", "Time_hhmm", "07:30"),
        ("90061", "Seconds", "Time_dhhmmss", "1 01:01:01"),
        ("5400", "Seconds", "Duration_mmss", "90:00"),
        ("15", "HundredMilliseconds", "Time_ssf", "01.5"),
        ("90", "Seconds", None, "90"),
        # The packed units are stored as seconds and milliseconds (B.E.G. follow-up time).
        ("300", "PackedDaysHoursMinutesAndSeconds", "Duration_hhmmss", "00:05:00"),
        ("86400", "PackedDaysHoursMinutesAndSeconds", "Duration_hhmmss", "24:00:00"),
        ("3500", "PackedSecondsAndMilliseconds", "Time_ssfff", "03.500"),
        ("65250", "PackedMinutesSecondsAndMilliseconds", "Time_mmssfff", "01:05.250"),
    ],
)
def test_format(stored: str, unit: str, hint: str | None, shown: str) -> None:
    assert format_time(stored, unit, hint) == shown


@pytest.mark.parametrize(
    ("text", "unit", "hint", "stored"),
    [
        ("01:02:05", "Seconds", "Duration_hhmmss", "3725"),
        ("1:2:5", "Seconds", "Duration_hhmmss", "3725"),
        ("01:01.5", "Milliseconds", "Time_mmssfff", "61500"),
        ("01:01,500", "Milliseconds", "Time_mmssfff", "61500"),
        ("07:30", "Minutes", "Time_hhmm", "450"),
        ("1 01:01:01", "Seconds", "Time_dhhmmss", "90061"),
        ("90:00", "Seconds", "Duration_mmss", "5400"),
        ("30:00:00", "Seconds", "Duration_hhmmss", "100000"),
        ("7.30", "Minutes", "Time_hhmm", None),
        ("00:05:00", "PackedDaysHoursMinutesAndSeconds", "Duration_hhmmss", "300"),
        ("03.5", "PackedSecondsAndMilliseconds", "Time_ssfff", "3500"),
        ("01:05.25", "PackedMinutesSecondsAndMilliseconds", "Time_mmssfff", "65250"),
    ],
)
def test_parse(text: str, unit: str, hint: str, stored: str | None) -> None:
    assert parse_time(text, unit, hint, 0, 100000) == stored


@pytest.mark.parametrize(
    ("unit", "plain"),
    [
        ("PackedDaysHoursMinutesAndSeconds", "Seconds"),
        ("PackedSecondsAndMilliseconds", "Milliseconds"),
        ("PackedMinutesSecondsAndMilliseconds", "Milliseconds"),
    ],
)
def test_a_packed_unit_is_named_like_its_count(unit: str, plain: str) -> None:
    # Without a Time_/Duration_ hint the value is a number field labelled with the unit.
    assert S.time_unit(unit) == S.time_unit(plain)
