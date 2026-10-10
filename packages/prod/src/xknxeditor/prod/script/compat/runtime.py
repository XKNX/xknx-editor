"""Host functions behind the JScript compatibility profile."""

from __future__ import annotations

import datetime
import json
import locale as pylocale
import re
import unicodedata
import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from ..errors import ScriptCompileError
from . import mathfn, numfmt
from .frontend import transform

_PKG = "xknxeditor.prod.script.compat"


@dataclass(frozen=True)
class JScriptEnv:
    """Time zone (IANA name) and locale (BCP 47) the engine emulates; ``None`` means the system."""

    tz: str | None = None
    locale: str | None = None


# ---- time zone ------------------------------------------------------------------------------


def system_tz() -> str:
    try:
        from tzlocal import (
            get_localzone_name,  # pyright: ignore[reportMissingTypeStubs]
        )

        name = get_localzone_name()
        if name:
            return str(name)
    except Exception:
        pass
    return "UTC"


def _offset_minutes(zone: zoneinfo.ZoneInfo, instant: datetime.datetime) -> int:
    off = instant.astimezone(zone).utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


def _find_transition(
    zone: zoneinfo.ZoneInfo, lo: datetime.datetime, hi: datetime.datetime
) -> datetime.datetime:
    before = _offset_minutes(zone, lo)
    while hi - lo > datetime.timedelta(minutes=1):
        mid = lo + (hi - lo) / 2
        if _offset_minutes(zone, mid) == before:
            lo = mid
        else:
            hi = mid
    return hi.replace(second=0, microsecond=0)


def _rule_point(local: datetime.datetime) -> list[int]:
    days_in_month = (
        (local.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        - datetime.timedelta(days=1)
    ).day
    week = 5 if local.day + 7 > days_in_month else (local.day - 1) // 7 + 1
    weekday = (local.weekday() + 1) % 7
    return [local.month - 1, week, weekday, local.hour * 60 + local.minute]


@cache
def tz_rule(name: str, year: int) -> dict[str, Any]:
    """Current-year DST rule of ``name`` in the form JScript applies to every year."""
    zone = zoneinfo.ZoneInfo(name)
    utc = datetime.UTC
    start = datetime.datetime(year, 1, 1, tzinfo=utc)
    transitions: list[tuple[datetime.datetime, int, int]] = []
    prev = _offset_minutes(zone, start)
    day = start
    for _ in range(367):
        nxt = day + datetime.timedelta(days=1)
        off = _offset_minutes(zone, nxt)
        if off != prev:
            t = _find_transition(zone, day, nxt)
            transitions.append((t, prev, off))
            prev = off
        day = nxt
    if len(transitions) < 2:
        std = _offset_minutes(zone, start) if not transitions else transitions[-1][2]
        return {"std": std, "dst": None}
    up = next(t for t in transitions if t[2] > t[1])
    down = next(t for t in transitions if t[2] < t[1])
    std = down[2]
    delta = up[2] - up[1]
    start_local = up[0] + datetime.timedelta(minutes=std)
    end_local = down[0] + datetime.timedelta(minutes=std + delta)
    return {
        "std": std,
        "dst": {
            "delta": delta,
            "start": _rule_point(start_local),
            "end": _rule_point(end_local),
        },
    }


# ---- locale ---------------------------------------------------------------------------------

_EN_DAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]
_EN_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip
_DE_DAYS = [
    "Sonntag",
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
]
_DE_MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]  # fmt: skip
_NL_DAYS = [
    "zondag",
    "maandag",
    "dinsdag",
    "woensdag",
    "donderdag",
    "vrijdag",
    "zaterdag",
]
_NL_MONTHS = [
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]  # fmt: skip


@dataclass(frozen=True)
class _Locale:
    days: list[str]
    months: list[str]
    long_date: str
    long_time: str
    decimal: str
    group: str
    list_sep: str


_LOCALES: dict[str, _Locale] = {
    "de-DE": _Locale(
        _DE_DAYS, _DE_MONTHS, "dddd, d. MMMM yyyy", "HH:mm:ss", ",", ".", ";"
    ),
    "de-CH": _Locale(
        _DE_DAYS, _DE_MONTHS, "dddd, d. MMMM yyyy", "HH:mm:ss", ".", chr(0x2019), ";"
    ),
    "en-US": _Locale(
        _EN_DAYS, _EN_MONTHS, "dddd, MMMM d, yyyy", "h:mm:ss tt", ".", ",", ","
    ),
    "en-GB": _Locale(_EN_DAYS, _EN_MONTHS, "dd MMMM yyyy", "HH:mm:ss", ".", ",", ","),
    "nl-NL": _Locale(
        _NL_DAYS, _NL_MONTHS, "dddd d MMMM yyyy", "HH:mm:ss", ",", ".", ";"
    ),
}
_LANGUAGE_DEFAULT = {"de": "de-DE", "en": "en-US", "nl": "nl-NL"}


def system_locale() -> str:
    names: list[str] = []
    try:
        loc = pylocale.getlocale(pylocale.LC_TIME)[0] or pylocale.getlocale()[0]
        if loc:
            names.append(loc)
    except (ValueError, TypeError):
        pass
    for name in names:
        norm = pylocale.normalize(name).split(".")[0]
        tag = norm.replace("_", "-")
        if tag in _LOCALES:
            return tag
        lang = tag.split("-")[0].lower()
        if lang in _LANGUAGE_DEFAULT:
            return _LANGUAGE_DEFAULT[lang]
    return "en-US"


def _locale(name: str) -> _Locale:
    if name in _LOCALES:
        return _LOCALES[name]
    return _LOCALES.get(
        _LANGUAGE_DEFAULT.get(name.split("-")[0].lower(), "en-US"), _LOCALES["en-US"]
    )


def number_separators(locale: str | None) -> tuple[str, str]:
    """Decimal and group separator of ``locale`` (``None``: the system locale)."""
    loc = _locale(locale or system_locale())
    return loc.decimal, loc.group


def date_names(locale: str | None) -> tuple[str, list[str]]:
    """Long date pattern and month names of ``locale`` (``None``: the system locale)."""
    loc = _locale(locale or system_locale())
    return loc.long_date, loc.months


def _format_date(fmt: str, loc: _Locale, f: list[int]) -> str:
    y, mo, d, wd, h, mi, s = f
    tokens = {
        "dddd": loc.days[wd],
        "MMMM": loc.months[mo],
        "yyyy": str(y) if y > 0 else f"{1 - y} B.C.",
        "dd": f"{d:02d}",
        "d": str(d),
        "HH": f"{h:02d}",
        "hh": f"{(h % 12) or 12:02d}",
        "h": str((h % 12) or 12),
        "mm": f"{mi:02d}",
        "ss": f"{s:02d}",
        "tt": "AM" if h < 12 else "PM",
    }
    return re.sub(
        r"dddd|MMMM|yyyy|dd|d|HH|hh|h|mm|ss|tt", lambda m: tokens[m.group(0)], fmt
    )


def locale_date(name: str, f: list[int], part: str) -> str:
    loc = _locale(name)
    if part == "date":
        return _format_date(loc.long_date, loc, f)
    if part == "time":
        return _format_date(loc.long_time, loc, f)
    return (
        _format_date(loc.long_date, loc, f) + " " + _format_date(loc.long_time, loc, f)
    )


def locale_number(name: str, x: float) -> str:
    if x != x:
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "Infinity" if x > 0 else "-Infinity"
    loc = _locale(name)
    text = numfmt.to_fixed(x, 2)
    sign = ""
    if text.startswith("-"):
        sign = "-"
        text = text[1:]
    whole, _, frac = text.partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + loc.group.join(groups) + loc.decimal + frac


def _collation_key(s: str) -> tuple[tuple[Any, ...], tuple[str, ...], tuple[int, ...]]:
    primary: list[Any] = []
    secondary: list[str] = []
    tertiary: list[int] = []
    expand = {"ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE"}
    for ch in s:
        if ch in "-'":
            continue
        for c in expand.get(ch, ch):
            d = unicodedata.normalize("NFD", c)
            base = d[0]
            cat = unicodedata.category(base)
            weight = 2 if cat.startswith("L") else 1 if cat.startswith("N") else 0
            primary.append((weight, base.casefold()))
            secondary.append(d[1:])
            tertiary.append(0 if base == base.lower() else 1)
    return tuple(primary), tuple(secondary), tuple(tertiary)


def locale_compare(a: str, b: str) -> int:
    ka, kb = _collation_key(a), _collation_key(b)
    return (ka > kb) - (ka < kb)


# ---- case mapping ---------------------------------------------------------------------------


@cache
def _casemap() -> tuple[dict[int, str], dict[int, str]]:
    raw = json.loads(
        resources.files(_PKG).joinpath("casemap.json").read_text(encoding="utf-8")
    )
    upper = {
        int(k, 16): "".join(chr(int(c, 16)) for c in v) for k, v in raw["upper"].items()
    }
    lower = {
        int(k, 16): "".join(chr(int(c, 16)) for c in v) for k, v in raw["lower"].items()
    }
    return upper, lower


def to_upper(s: str) -> str:
    table = _casemap()[0]
    return "".join(table.get(ord(c), c) for c in s)


def to_lower(s: str) -> str:
    table = _casemap()[1]
    return "".join(table.get(ord(c), c) for c in s)


# ---- Date.parse ------------------------------------------------------------------------------

_MONTH_NAMES = [m.lower() for m in _EN_MONTHS]
_DAY_NAMES = [d.lower() for d in _EN_DAYS]
_ZONES = {"utc": 0, "ut": 0, "gmt": 0, "z": 0, "est": -300, "edt": -240, "cst": -360, "cdt": -300,
          "mst": -420, "mdt": -360, "pst": -480, "pdt": -420}  # fmt: skip


def _match_name(word: str, names: list[str]) -> int | None:
    if len(word) < 2:
        return None
    found = None
    for i, name in enumerate(names):
        if name.startswith(word):
            found = i
    return found


def parse_date(text: str) -> list[int | None] | None:
    """JScript ``Date.parse`` grammar: ``[y, month, day, h, m, s, tz_minutes|None]`` or None."""
    s = re.sub(r"\([^)]*\)", " ", text).strip()
    if not s:
        return None
    tokens = re.findall(
        r"[A-Za-z.]+|[+-]\d{1,4}|\d+(?::\d*){1,2}|\d+[/-]\d+[/-]\d+|\d+|[^\sA-Za-z\d,]",
        s,
    )
    year = month = day = None
    hour = minute = second = 0
    have_time = False
    pm: bool | None = None
    zone: int | None = None
    numbers: list[int] = []
    expect_offset = False
    for tok in tokens:
        low = tok.lower().rstrip(".")
        if re.fullmatch(r"\d+(?::\d*){1,2}", tok):
            if have_time:
                return None
            parts = [int(p) if p else 0 for p in tok.split(":")]
            hour, minute = parts[0], parts[1]
            second = parts[2] if len(parts) > 2 else 0
            have_time = True
            continue
        m = re.fullmatch(r"(\d+)([/-])(\d+)[/-](\d+)", tok)
        if m:
            if month is not None:
                return None
            a, sep, b, c = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
            if sep == "/" and a > 31:
                year, month, day = a, b - 1, c
            else:
                month, day, year = a - 1, b, c
            continue
        if re.fullmatch(r"[+-]\d{1,4}", tok) and (expect_offset or have_time):
            sign = -1 if tok[0] == "-" else 1
            digits = tok[1:]
            hh, mm = (
                (int(digits[:-2] or 0), int(digits[-2:]))
                if len(digits) > 2
                else (int(digits), 0)
            )
            zone = (zone or 0) + sign * (hh * 60 + mm)
            expect_offset = False
            continue
        if tok.isdigit():
            numbers.append(int(tok))
            continue
        if low in ("am", "a.m"):
            pm = False
            continue
        if low in ("pm", "p.m"):
            pm = True
            continue
        if low in _ZONES:
            zone = _ZONES[low]
            expect_offset = True
            continue
        mi = _match_name(low, _MONTH_NAMES)
        if mi is not None:
            if month is not None:
                return None
            month = mi
            continue
        if _match_name(low, _DAY_NAMES) is not None:
            continue
        if tok in ",":
            continue
        return None
    for n in numbers:
        if (
            day is None
            and month is not None
            and n <= 31
            and year is None
            and len(numbers) > 1
        ) or (day is None and n <= 31 and (year is not None or len(numbers) > 1)):
            day = n
        elif year is None:
            year = n
        elif day is None:
            day = n
        else:
            return None
    if year is None or month is None or day is None:
        return None
    if (
        not 0 <= month <= 11
        or not 1 <= day <= 31
        or hour > 24
        or minute > 59
        or second > 59
    ):
        return None
    if year < 100:
        year += 1900
    if pm is not None:
        if hour > 12:
            return None
        if pm and hour < 12:
            hour += 12
        if not pm and hour == 12:
            hour = 0
    return [year, month, day, hour, minute, second, zone]


# ---- host table ------------------------------------------------------------------------------


def _transform_for_script(code: str) -> dict[str, Any]:
    try:
        result = transform(code)
    except ScriptCompileError as exc:
        return {"error": {"message": exc.message, "number": exc.number}}
    return {"code": result.code, "fnmap": result.fnmap}


def _num(fn: Callable[[float], Any]) -> Callable[[Any], Any]:
    def call(x: Any) -> Any:
        return fn(float(x))

    return call


def _num_int(fn: Callable[[float, int], Any]) -> Callable[[Any, Any], Any]:
    def call(x: Any, n: Any) -> Any:
        return fn(float(x), int(n))

    return call


def _num2(fn: Callable[[float, float], Any]) -> Callable[[Any, Any], Any]:
    def call(x: Any, y: Any) -> Any:
        return fn(float(x), float(y))

    return call


def host_functions(env: JScriptEnv) -> dict[str, Any]:
    tz_name = env.tz or system_tz()
    loc = env.locale or system_locale()
    year = datetime.datetime.now(datetime.UTC).year

    def tz() -> dict[str, Any]:
        return tz_rule(tz_name, year)

    def number(x: Any) -> str:
        return locale_number(loc, float(x))

    def date(f: list[int], part: str) -> str:
        return locale_date(loc, f, part)

    def list_separator() -> str:
        return _locale(loc).list_sep

    return {
        "n.str": _num(numfmt.to_string),
        "n.fixed": _num_int(numfmt.to_fixed),
        "n.prec": _num_int(numfmt.to_precision),
        "n.exp": _num_int(numfmt.to_exponential),
        "n.radix": _num_int(numfmt.to_radix),
        "tz.rule": tz,
        "loc.num": number,
        "loc.date": date,
        "loc.list": list_separator,
        "loc.cmp": locale_compare,
        "case.upper": to_upper,
        "case.lower": to_lower,
        "date.parse": parse_date,
        "fe.transform": _transform_for_script,
        "m.sin": _num(mathfn.sin),
        "m.cos": _num(mathfn.cos),
        "m.tan": _num(mathfn.tan),
        "m.atan": _num(mathfn.atan),
        "m.asin": _num(mathfn.asin),
        "m.acos": _num(mathfn.acos),
        "m.atan2": _num2(mathfn.atan2),
        "m.log": _num(mathfn.log),
        "m.exp": _num(mathfn.exp),
        "m.pow": _num2(mathfn.pow),
    }


@cache
def prelude() -> str:
    return resources.files(_PKG).joinpath("jscript.js").read_text(encoding="utf-8")
