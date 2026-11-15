"""Timestamp display respects local offsets and regional date order."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from editor_gui.regional import RegionalFormat
from editor_gui.widgets import datetime_format


@pytest.mark.parametrize(
    ("stamp", "pattern", "expected"),
    [
        ("2025-01-02T03:04:05", "dd.MM.yyyy", "02.01.2025 04:04"),
        ("2025-07-01T23:30:00Z", "dd.MM.yyyy", "02.07.2025 01:30"),
        ("2025-01-02T03:04:05.1234567+02:00", "M/d/yyyy", "1/2/2025 02:04"),
        ("2025-07-02T01:30:00+02:00", "M/d/yyyy", "7/2/2025 01:30"),
        ("not a date", "dd.MM.yyyy", "-"),
    ],
)
def test_local_timestamp(stamp, pattern, expected, monkeypatch):
    class LocalDateTime(datetime):
        def astimezone(self, tz=None):
            return super().astimezone(tz or ZoneInfo("Europe/Berlin"))

    monkeypatch.setattr(datetime_format, "datetime", LocalDateTime)
    monkeypatch.setattr(
        datetime_format, "regional_format", lambda: RegionalFormat(short_date=pattern)
    )
    assert datetime_format.timestamp_text(stamp) == expected
