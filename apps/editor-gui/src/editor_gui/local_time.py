"""Dates and times as the GUI shows them: in local time, in the regional date and time formats.

Every date or time the user sees goes through here; files, logs written to disk and MCP results
keep their ISO 8601 texts. A naive ``datetime`` is taken as local time already (``now()``,
``fromtimestamp()``), one with a time zone is converted to local time.
"""

from __future__ import annotations

import datetime
import re

from editor_gui.regional import RegionalFormat, regional_format
from editor_gui.widgets.value_pickers import format_date

_TIME_TOKEN = re.compile(r"'[^']*'|HH|H|hh|h|mm|m|ss|s|tt|t")
# Shown for a time that is unknown or cannot be read.
MISSING = "-"
# A moment whose texts are as wide as any (two-digit day, month and hour, after noon): a column
# of times or dates is made as wide as its text.
WIDEST = datetime.datetime(2026, 12, 28, 22, 58, 58, 888_000)


def local(moment: datetime.datetime) -> datetime.datetime:
    """``moment`` in local time (a naive one is local time already)."""
    return moment if moment.tzinfo is None else moment.astimezone()


def date_text(
    moment: datetime.date | datetime.datetime, regional: RegionalFormat | None = None
) -> str:
    """The (local) date of ``moment`` in the regional short date format."""
    if isinstance(moment, datetime.datetime):
        moment = local(moment).date()
    return format_date(moment.isoformat(), True, regional or regional_format())


def time_text(
    moment: datetime.datetime | datetime.time,
    *,
    seconds: bool = True,
    millis: bool = False,
    regional: RegionalFormat | None = None,
) -> str:
    """The (local) time of day of ``moment`` in the regional time format, with the seconds or
    without; ``millis`` adds the milliseconds to the seconds (``19:41:18,824``)."""
    regional = regional or regional_format()
    if isinstance(moment, datetime.datetime):
        moment = local(moment).time()
    hour = moment.hour
    half = hour % 12 or 12
    second = f"{moment.second:02d}"
    if millis:
        second += f"{regional.decimal}{moment.microsecond // 1000:03d}"
    meridiem = regional.am if hour < 12 else regional.pm
    fields = {
        "HH": f"{hour:02d}",
        "H": str(hour),
        "hh": f"{half:02d}",
        "h": str(half),
        "mm": f"{moment.minute:02d}",
        "m": str(moment.minute),
        "ss": second,
        "s": second,
        "tt": meridiem,
        "t": meridiem[:1],
    }
    pattern = regional.long_time if seconds or millis else regional.short_time
    return _TIME_TOKEN.sub(
        lambda m: m[0][1:-1] if m[0].startswith("'") else fields[m[0]], pattern
    ).strip()


def date_time_text(
    moment: datetime.datetime,
    *,
    seconds: bool = False,
    regional: RegionalFormat | None = None,
) -> str:
    """The (local) date and time of ``moment`` in the regional formats, to the minute or to the
    second."""
    regional = regional or regional_format()
    moment = local(moment)
    day = date_text(moment, regional)
    return f"{day} {time_text(moment, seconds=seconds, regional=regional)}"


def recent_text(
    moment: datetime.datetime,
    now: datetime.datetime | None = None,
    *,
    regional: RegionalFormat | None = None,
) -> str:
    """When something happened, to the second: the time of day when it was today, else the
    date too."""
    moment = local(moment)
    today = local(now or datetime.datetime.now()).date()
    if moment.date() == today:
        return time_text(moment, regional=regional)
    return date_time_text(moment, seconds=True, regional=regional)


def parse_stamp(stamp: str | None) -> datetime.datetime | None:
    """A stored ISO 8601 time (UTC when it has no offset, "Z", up to seven digits of fraction,
    "2026-01-24T11:38:26.8111136Z") as a ``datetime`` with its time zone; None for none or one
    that cannot be read."""
    if not stamp:
        return None
    text = stamp.strip()
    utc = text.endswith(("Z", "z"))
    text = text.removesuffix("Z").removesuffix("z")
    # Seven digits of fraction are more than fromisoformat reads: cut them to six.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or utc:
        moment = moment.replace(tzinfo=datetime.UTC)
    return moment


def stamp_text(
    stamp: str | None,
    *,
    seconds: bool = False,
    regional: RegionalFormat | None = None,
) -> str:
    """A stored ISO 8601 time (see :func:`parse_stamp`) in local time in the regional formats;
    a date alone in the regional date format; "-" for none and the text as it is when it cannot
    be read."""
    if not stamp:
        return MISSING
    if "T" not in stamp and " " not in stamp.strip():
        return iso_date_text(stamp, regional)
    moment = parse_stamp(stamp)
    if moment is None:
        return stamp
    return date_time_text(moment, seconds=seconds, regional=regional)


def iso_date_text(text: str, regional: RegionalFormat | None = None) -> str:
    """An ISO 8601 date ("2026-10-12") in the regional short date format; the text as it is when
    it is no date."""
    return format_date(text.strip(), True, regional or regional_format())
