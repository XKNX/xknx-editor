"""Display and input of time parameter values in the format of their ``Time_``/``Duration_`` hint.

A value is stored as a count of the type's unit (e.g. seconds) and shown with the fields the hint
names: ``d`` days, ``hh`` hours, ``mm`` minutes, ``ss`` seconds and one to three fraction digits,
e.g. ``Duration_hhmmss`` as ``hh:mm:ss``. The leading field is not limited, so ``mm:ss`` shows
90 minutes as ``90:00``.
"""

from __future__ import annotations

import re

# Milliseconds per unit; packed units are not shown as a time.
_UNIT_MS = {
    "Hours": 3_600_000,
    "Minutes": 60_000,
    "Seconds": 1_000,
    "HundredMilliseconds": 100,
    "TenMilliseconds": 10,
    "Milliseconds": 1,
}
_FIELD_MS = {"d": 86_400_000, "hh": 3_600_000, "mm": 60_000, "ss": 1_000}
_HINT_FIELDS = re.compile(r"(?P<d>d)?(?P<hh>hh)?(?P<mm>mm)?(?P<ss>ss)?(?P<f>f{1,3})?")


def unit_ms(unit: str) -> int | None:
    """Milliseconds per unit, None for a packed unit."""
    return _UNIT_MS.get(unit)


def time_fields(hint: str | None) -> tuple[list[str], int] | None:
    """The whole fields and the number of fraction digits a hint shows; None without one."""
    if not hint or "_" not in hint:
        return None
    match = _HINT_FIELDS.fullmatch(hint.split("_", 1)[1])
    if match is None:
        return None
    fields = [name for name in ("d", "hh", "mm", "ss") if match[name]]
    if not fields:
        return None
    return fields, len(match["f"] or "")


def time_pattern(hint: str | None) -> str | None:
    """The hint's format as shown next to the field, e.g. ``hh:mm:ss`` or ``mm:ss.fff``."""
    parsed = time_fields(hint)
    if parsed is None:
        return None
    fields, digits = parsed
    clock = ":".join(f for f in fields if f != "d")
    text = ("d " if "d" in fields else "") + clock
    return text.strip() + ("." + "f" * digits if digits else "")


def format_time(stored: str, unit: str, hint: str | None) -> str:
    """The stored count of ``unit`` in the hint's format; unchanged without a format."""
    parsed = time_fields(hint)
    per_unit = unit_ms(unit)
    try:
        count = int(stored)
    except ValueError:
        return stored
    if parsed is None or per_unit is None:
        return stored
    fields, digits = parsed
    remaining = abs(count) * per_unit
    parts: list[str] = []
    for index, name in enumerate(fields):
        value, remaining = divmod(remaining, _FIELD_MS[name])
        if name == "d":
            parts.append(f"{value} ")
        else:
            parts.append("" if index == 0 or fields[index - 1] == "d" else ":")
            parts.append(f"{value:02d}")
    text = "".join(parts)
    if digits:
        text += "." + f"{remaining:03d}"[:digits]
    return f"-{text}" if count < 0 else text


def parse_time(
    text: str,
    unit: str,
    hint: str | None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> str | None:
    """The count of ``unit`` to store for ``text`` in the hint's format, clamped to the range;
    None if it does not match the format."""
    parsed = time_fields(hint)
    per_unit = unit_ms(unit)
    if parsed is None or per_unit is None:
        return None
    fields, digits = parsed
    regex = ""
    for index, name in enumerate(fields):
        if name == "d":
            regex += r"(?P<d>\d+)\s+"
        else:
            if index and fields[index - 1] != "d":
                regex += ":"
            width = r"\d+" if index == 0 or fields[index - 1] == "d" else r"\d{1,2}"
            regex += f"(?P<{name}>{width})"
    if digits:
        regex += rf"(?:[.,](?P<f>\d{{1,{digits}}}))?"
    match = re.fullmatch(regex, text.strip())
    if match is None:
        return None
    total = sum(int(match[name]) * _FIELD_MS[name] for name in fields)
    if digits and match["f"]:
        total += int(match["f"].ljust(3, "0"))
    count = round(total / per_unit)
    if minimum is not None:
        count = max(minimum, count)
    if maximum is not None:
        count = min(maximum, count)
    return str(count)
