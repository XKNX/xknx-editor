"""The monitors' Time and Date columns, the last value's time and the log's time: local time in
the regional formats."""

from __future__ import annotations

import datetime

import pytest

from editor_gui import local_time
from editor_gui.plugins.logger.service import LogRecord
from editor_gui.plugins.monitor.formatting import date_text, sample_text, time_text
from editor_gui.plugins.monitor.records import TICKS_PER_SECOND
from editor_gui.plugins.monitor.service import LiveValue
from editor_gui.regional import RegionalFormat

US = RegionalFormat(short_date="M/d/yyyy", long_time="h:mm:ss tt", short_time="h:mm tt")
MOMENT = datetime.datetime(2026, 10, 12, 19, 41, 18, 824_000)


@pytest.fixture(autouse=True)
def _us(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_time, "regional_format", lambda: US)


def _ticks(moment: datetime.datetime) -> int:
    whole = moment.replace(microsecond=0).timestamp()
    return int(whole) * TICKS_PER_SECOND + moment.microsecond * 10


def test_the_time_and_date_of_a_record() -> None:
    ticks = _ticks(MOMENT)
    assert time_text(ticks) == "7:41:18.824 PM"
    assert date_text(ticks) == "10/12/2026"


def test_the_time_and_date_columns_fit_their_widest_text() -> None:
    assert sample_text("time") == "10:58:58.888 PM"
    assert sample_text("date") == "12/28/2026"
    assert sample_text("service") is None


def test_the_last_value_and_a_log_record_show_their_regional_time() -> None:
    value = LiveValue(payload=None, timestamp=MOMENT, service="Write")
    assert value.time_str == "7:41:18 PM"
    record = LogRecord(timestamp=MOMENT.timestamp(), level="info", plugin="", event="")
    assert record.timestamp_str == "7:41:18 PM"
