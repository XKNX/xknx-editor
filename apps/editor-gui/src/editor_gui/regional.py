"""Number and date formats of the operating system's regional settings.

The separators, date patterns and month names are read from the operating system's regional
settings (including user changes); where they cannot be read, they come from fixed tables for
the regional locale. Date patterns use ``yyyy``/``yy`` (year), ``MMMM``/``MM``/``M`` (month)
and ``dd``/``d`` (day).
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


@dataclass(frozen=True, slots=True)
class RegionalFormat:
    decimal: str = "."
    group: str = ","
    short_date: str = "yyyy-MM-dd"
    # Day and month name without the year, e.g. "d. MMMM".
    day_month: str = "MMMM d"
    months: tuple[str, ...] = field(default=_EN_MONTHS)


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


def _table_format() -> RegionalFormat:
    from xknxeditor.prod.script.compat.runtime import (
        date_names,
        number_separators,
        system_locale,
    )

    locale = system_locale()
    decimal, group = number_separators(locale)
    short_date = _SHORT_DATES.get(locale) or _SHORT_DATES.get(
        locale.split("-")[0], RegionalFormat.short_date
    )
    long_date, months = date_names(locale)
    day_month = re.sub(r"dddd,?\s*|,?\s*yyyy", "", long_date).strip()
    return RegionalFormat(decimal, group, short_date, day_month, tuple(months))


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
        months = tuple(
            locale.nl_langinfo(getattr(locale, f"MON_{i}")) for i in range(1, 13)
        )
    except (AttributeError, ValueError, locale.Error):
        return None
    short_date = re.sub(r"%[deMmYyB]", lambda m: _STRFTIME.get(m[0], m[0]), date_format)
    return {
        "decimal": str(conventions["decimal_point"]),
        "group": str(conventions["thousands_sep"]),
        "short_date": short_date if "%" not in short_date else None,
        "months": months if all(months) else None,
    }


# GetLocaleInfoEx information types.
_LOCALE_SDECIMAL = 0x0E
_LOCALE_STHOUSAND = 0x0F
_LOCALE_SSHORTDATE = 0x1F
_LOCALE_SMONTHNAME1 = 0x38
_LOCALE_SMONTHDAY = 0x78
