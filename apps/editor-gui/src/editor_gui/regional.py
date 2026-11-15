"""Number, date and time formats of the operating system's regional settings.

The separators, date and time patterns and month names are read from the operating system's
regional settings (including user changes); where they cannot be read, they come from fixed
tables for the regional locale. Date patterns use ``yyyy``/``yy`` (year), ``MMMM``/``MM``/``M``
(month) and ``dd``/``d`` (day); time patterns ``HH``/``H`` (hour, 24 h), ``hh``/``h`` (hour,
12 h), ``mm``/``m`` (minute), ``ss``/``s`` (second) and ``tt``/``t`` (AM/PM).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace
from functools import cache

# Short date pattern per locale, else per language, when the system does not tell.
_SHORT_DATES = {
    "de": "dd.MM.yyyy",
    "en-US": "M/d/yyyy",
    "en-GB": "dd/MM/yyyy",
    "nl": "d-M-yyyy",
}
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
# strftime directives of a POSIX date format and their date pattern tokens.
_STRFTIME = {"%d": "dd", "%e": "d", "%m": "MM", "%Y": "yyyy", "%y": "yy", "%B": "MMMM"}
# strftime directives of a POSIX time format and their time pattern tokens.
_STRFTIME_TIME = {
    "%H": "HH", "%k": "H", "%I": "hh", "%l": "h", "%M": "mm", "%S": "ss", "%p": "tt",
    "%T": "HH:mm:ss", "%R": "HH:mm", "%r": "hh:mm:ss tt",
}  # fmt: skip
# The seconds of a time pattern with the separator before them.
_SECONDS = re.compile(r"[^Hhmst']?s{1,2}")


@dataclass(frozen=True, slots=True)
class RegionalFormat:
    decimal: str = "."
    group: str = ","
    short_date: str = "yyyy-MM-dd"
    # Day and month name without the year, e.g. "d. MMMM".
    day_month: str = "MMMM d"
    months: tuple[str, ...] = field(default=_EN_MONTHS)
    # Time of day with and without the seconds.
    long_time: str = "HH:mm:ss"
    short_time: str = "HH:mm"
    am: str = "AM"
    pm: str = "PM"


def without_seconds(pattern: str) -> str:
    """A time pattern without its seconds: ``h:mm:ss tt`` -> ``h:mm tt``."""
    return _SECONDS.sub("", pattern, count=1)


@cache
def regional_format() -> RegionalFormat:
    """The regional format of the operating system."""
    table = _table_format()
    system = _windows_format() if sys.platform == "win32" else _posix_format()
    if system is None:
        return table
    return replace(
        table,
        **{key: value for key, value in system.items() if value},
    )


@cache
def os_region() -> str:
    """The country of the operating system's regional settings as an ISO 3166 code ("DE"), or ""
    when it cannot be read."""
    if sys.platform == "win32":
        import ctypes

        buffer = ctypes.create_unicode_buffer(16)
        found = ctypes.windll.kernel32.GetLocaleInfoEx(
            None, _LOCALE_SISO3166CTRYNAME, buffer, 16
        )
        return buffer.value.upper() if found else ""
    import locale
    import os

    name = locale.getlocale(locale.LC_TIME)[0] or os.environ.get("LANG", "")
    parts = name.split(".")[0].replace("-", "_").split("_")
    return parts[1].upper() if len(parts) > 1 and len(parts[1]) == 2 else ""


def _table_format() -> RegionalFormat:
    from xknxeditor.prod.script.compat.runtime import (
        date_names,
        number_separators,
        system_locale,
        time_pattern,
    )

    locale = system_locale()
    decimal, group = number_separators(locale)
    short_date = _SHORT_DATES.get(locale) or _SHORT_DATES.get(
        locale.split("-")[0], RegionalFormat.short_date
    )
    long_date, months = date_names(locale)
    day_month = re.sub(r"dddd,?\s*|,?\s*yyyy", "", long_date).strip()
    long_time = time_pattern(locale)
    return RegionalFormat(
        decimal,
        group,
        short_date,
        day_month,
        tuple(months),
        long_time,
        without_seconds(long_time),
    )


def _windows_format() -> dict[str, object] | None:
    if sys.platform != "win32":
        return None
    import ctypes

    get_locale_info = ctypes.windll.kernel32.GetLocaleInfoEx

    def info(kind: int) -> str:
        buffer = ctypes.create_unicode_buffer(128)
        # A null locale name is the user's default locale with the user's own changes.
        return buffer.value if get_locale_info(None, kind, buffer, 128) else ""

    months = tuple(info(_LOCALE_SMONTHNAME1 + i) for i in range(12))
    return {
        "decimal": info(_LOCALE_SDECIMAL),
        "group": info(_LOCALE_STHOUSAND),
        "short_date": info(_LOCALE_SSHORTDATE),
        "day_month": info(_LOCALE_SMONTHDAY),
        "months": months if all(months) else None,
        "long_time": info(_LOCALE_STIMEFORMAT),
        "short_time": info(_LOCALE_SSHORTTIME),
        "am": info(_LOCALE_S1159),
        "pm": info(_LOCALE_S2359),
    }


def _posix_format() -> dict[str, object] | None:
    if sys.platform == "win32":
        return None
    import locale

    if locale.getlocale(locale.LC_TIME)[0] is None:
        return None
    try:
        conventions = locale.localeconv()
        date_format = locale.nl_langinfo(locale.D_FMT)
        time_format = locale.nl_langinfo(locale.T_FMT)
        am, pm = locale.nl_langinfo(locale.AM_STR), locale.nl_langinfo(locale.PM_STR)
        months = tuple(
            locale.nl_langinfo(getattr(locale, f"MON_{i}")) for i in range(1, 13)
        )
    except (AttributeError, ValueError, locale.Error):
        return None
    short_date = re.sub(r"%[deMmYyB]", lambda m: _STRFTIME.get(m[0], m[0]), date_format)
    long_time = re.sub(
        r"%[HkIlMSpTRr]", lambda m: _STRFTIME_TIME.get(m[0], m[0]), time_format
    )
    if "%" in long_time:  # a directive without a token: the table's pattern is kept
        long_time = ""
    return {
        "decimal": str(conventions["decimal_point"]),
        "group": str(conventions["thousands_sep"]),
        "short_date": short_date if "%" not in short_date else None,
        "long_time": long_time,
        "short_time": without_seconds(long_time),
        # A 24-hour locale has no AM/PM texts; the table's are kept for a 12-hour pattern.
        "am": am,
        "pm": pm,
        "months": months if all(months) else None,
    }


# GetLocaleInfoEx information types.
_LOCALE_SDECIMAL = 0x0E
_LOCALE_STHOUSAND = 0x0F
_LOCALE_SSHORTDATE = 0x1F
_LOCALE_SMONTHNAME1 = 0x38
_LOCALE_SMONTHDAY = 0x78
_LOCALE_SSHORTTIME = 0x79
_LOCALE_STIMEFORMAT = 0x1003
_LOCALE_S1159 = 0x28
_LOCALE_S2359 = 0x29
_LOCALE_SISO3166CTRYNAME = 0x5A
