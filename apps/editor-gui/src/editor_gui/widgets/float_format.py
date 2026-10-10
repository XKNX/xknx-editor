"""Display and input of float parameter values.

A value is shown with up to 15 significant digits and the regional decimal separator; the
parameter type's display format, factor and offset are not applied.
"""

from __future__ import annotations

import math


def format_float(stored: str, *, decimal: str = ".") -> str:
    """The stored value as shown; text that is not a number is returned unchanged."""
    try:
        value = float(stored)
    except ValueError:
        return stored
    if not math.isfinite(value):
        return stored
    return general_float(value).replace(".", decimal)


def parse_float(
    text: str,
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    decimal: str = ".",
    group: str = ",",
) -> str | None:
    """The value to store for ``text`` as entered, clamped to the range; None if not a number.

    With the decimal separator present, group separators are ignored; without it, a point or a
    comma is taken as the decimal separator."""
    cleaned = text.strip().replace(" ", "").replace("\xa0", "")
    if decimal in cleaned:
        if group != decimal:
            cleaned = cleaned.replace(group, "")
        cleaned = cleaned.replace(decimal, ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return general_float(value)


def general_float(value: float) -> str:
    """``value`` with up to 15 significant digits, exponent form for very large or small ones."""
    text = format(value, ".15g").replace("e", "E")
    return "0" if text == "-0" else text
