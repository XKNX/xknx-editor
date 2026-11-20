"""Date and time encodings for classic Siemens DALI properties."""

from __future__ import annotations

from datetime import date, datetime, time


def encode_date(value: date | datetime | str) -> bytes:
    """3-byte Date: day, month, year-mod-100. XML format is ``YYYY-MM-DDTHH:MM:SS.sss``."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", ""))
    d = value.date() if isinstance(value, datetime) else value
    return bytes([d.day, d.month, d.year % 100])


def encode_time(value: time | datetime | str) -> bytes:
    """3-byte Time: hour, minute, second. XML format is ``HH:MM:SS`` or an ISO datetime."""
    if isinstance(value, str):
        if "T" in value:
            value = datetime.fromisoformat(value.replace("Z", ""))
        else:
            value = time.fromisoformat(value)
    t = value.time() if isinstance(value, datetime) else value
    return bytes([t.hour, t.minute, t.second])
