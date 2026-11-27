"""Convert editable values using the product's display format."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .description import TIME_KINDS, TIME_UNIT_MS, Description, TypeKind, integer


@dataclass(frozen=True)
class ValueFormat:
    kind: TypeKind
    format: str = ""
    clock: str = ""
    pattern: str = ""
    minimum: int | None = None
    maximum: int | None = None

    @property
    def percent(self) -> bool:
        return self.kind is TypeKind.NUMBER and self.format.lower() == "percent"

    @property
    def time(self) -> bool:
        return self.kind in TIME_KINDS and bool(self.clock)

    def display(self, value: str) -> str:
        if not value or not (self.percent or self.time):
            return value
        raw = integer(value)
        if self.percent:
            return str(round(raw * 100 / 255))
        self._clock_parts()
        millis = raw * TIME_UNIT_MS[self.kind]
        sign = "-" if millis < 0 else ""
        seconds, fraction = divmod(abs(millis), 1000)
        minutes, secs = divmod(seconds, 60)
        hours, mins = divmod(minutes, 60)
        tail = f".{fraction:03d}".rstrip("0") if fraction else ""
        shape = self.clock.lower().removesuffix(".f")
        if shape == "mm":
            # Keep sub-minute precision when a stored value has it.
            return sign + format(Decimal(abs(millis)) / 60000, ".6f").rstrip(
                "0"
            ).rstrip(".")
        if shape == "ss":
            return f"{sign}{seconds:02d}{tail}"
        if shape == "mm:ss":
            return f"{sign}{minutes:02d}:{secs:02d}{tail}"
        hour = str(hours) if self.clock.startswith("H:") else f"{hours:02d}"
        if shape in ("h:mm", "hh:mm") and not secs and not fraction:
            return f"{sign}{hour}:{mins:02d}"
        return f"{sign}{hour}:{mins:02d}:{secs:02d}{tail}"

    def parse(self, value: str, current: str) -> str:
        """Keep an unchanged display byte-exact, including rounded percentages."""
        if not (self.percent or self.time):
            return value
        value = value.strip()
        if value == self.display(current):
            return current
        if self.percent:
            try:
                number = Decimal(value.replace(",", "."))
            except InvalidOperation as exc:
                raise ValueError("A percentage between 0 and 100 is required") from exc
            if not number.is_finite() or not 0 <= number <= 100:
                raise ValueError("A percentage between 0 and 100 is required")
            if number == Decimal(self.display(current)):
                return current
            raw = round(float(number) / 0.3922)
            minimum = max(0, self.minimum if self.minimum is not None else 0)
            maximum = min(255, self.maximum if self.maximum is not None else 255)
            if not minimum <= raw <= maximum:
                low, high = self.display(str(minimum)), self.display(str(maximum))
                raise ValueError(f"Percentage must be between {low} and {high}")
            return str(raw)
        parts = self._clock_parts()
        values = value.replace(",", ".").split(":")
        if len(values) != len(parts):
            raise ValueError(f"Enter a time in {self.clock} format")
        seconds = Decimal(0)
        for index, (part, text) in enumerate(zip(parts, values, strict=True)):
            if not re.fullmatch(r"\d+(?:\.\d+)?", text):
                raise ValueError(f"Enter a time in {self.clock} format")
            number = Decimal(text)
            if index and number >= 60:
                raise ValueError("Minutes and seconds must be below 60")
            if index < len(parts) - 1 and number != int(number):
                raise ValueError("Only the last time component may have a fraction")
            seconds += number * {"h": 3600, "hh": 3600, "mm": 60, "ss": 1}[part]
        raw = seconds * 1000 / TIME_UNIT_MS[self.kind]
        if raw != int(raw):
            raise ValueError("The time exceeds the parameter's precision")
        if self.pattern and re.fullmatch(self.pattern, value) is None:
            raise ValueError(
                f"Time is outside the allowed format or range: {self.clock}"
            )
        return str(int(raw))

    def _clock_parts(self) -> tuple[str, ...]:
        shape = self.clock.lower().removesuffix(".f")
        if shape not in ("mm", "ss", "mm:ss", "h:mm", "hh:mm", "h:mm:ss", "hh:mm:ss"):
            raise ValueError(f"Unsupported time format: {self.clock}")
        return tuple(shape.split(":"))


def value_format(description: Description, ref_id: str) -> ValueFormat:
    ref = description.refs[ref_id]
    parameter = description.parameters[ref.parameter_id]
    ptype = description.types[parameter.type_id]
    return ValueFormat(
        ptype.kind,
        ref.format or parameter.format,
        ref.format_string or parameter.format_string,
        ref.pattern or parameter.pattern,
        ptype.minimum,
        ptype.maximum,
    )
