"""Serial numbers that cannot tell a device apart.

Such a serial number, read from a device or kept in the project, is never used to address the
device (its individual address is then downloaded through programming mode) and is not recorded
in the project.
"""

from __future__ import annotations

import functools
import json
import os
from dataclasses import dataclass
from importlib.resources import files

SERIAL_NUMBER_LENGTH = 6


def suspicious_serial_number(serial: bytes) -> bool:
    """Whether a serial number cannot tell the device apart: an unset or placeholder
    manufacturer part, a number part of only zeros or ones, or one that devices of its
    manufacturer are known to share (``suspicious_serials.json``). The environment variable
    ``XKNX_NO_SERIAL_NUMBER_CHECK`` switches the check off."""
    if os.environ.get("XKNX_NO_SERIAL_NUMBER_CHECK"):
        return False
    if len(serial) != SERIAL_NUMBER_LENGTH:
        return True
    manufacturer = int.from_bytes(serial[:2], "big")
    number = int.from_bytes(serial[2:], "big")
    known = _suspicious_serials()
    if manufacturer in known.manufacturers or number in known.numbers:
        return True
    if manufacturer & 0xF0FF == 0 and number <= 0xFFFFFF:
        return True
    rule = known.by_manufacturer.get(manufacturer)
    return rule is not None and rule.matches(number)


@dataclass(frozen=True, slots=True)
class _ManufacturerRule:
    """The numbers devices of one manufacturer are known to share."""

    equals: frozenset[int]
    ranges: tuple[tuple[int, int], ...]
    masked: tuple[tuple[int, int], ...]
    lists: tuple[frozenset[int], ...]

    def matches(self, number: int) -> bool:
        return (
            number in self.equals
            or any(low <= number <= high for low, high in self.ranges)
            or any(number & mask == value for mask, value in self.masked)
            or any(number in numbers for numbers in self.lists)
        )


@dataclass(frozen=True, slots=True)
class _SuspiciousSerials:
    manufacturers: frozenset[int]
    numbers: frozenset[int]
    by_manufacturer: dict[int, _ManufacturerRule]


@functools.cache
def _suspicious_serials() -> _SuspiciousSerials:
    data = json.loads(
        (files("xknxeditor.download") / "suspicious_serials.json").read_text("utf-8")
    )
    lists = {name: frozenset(numbers) for name, numbers in data["lists"].items()}
    return _SuspiciousSerials(
        manufacturers=frozenset(data["manufacturer_words"]),
        numbers=frozenset(data["serial_numbers"]),
        by_manufacturer={
            int(word, 16): _ManufacturerRule(
                equals=frozenset(rule["equals"]),
                ranges=tuple((low, high) for low, high in rule["ranges"]),
                masked=tuple((mask, value) for mask, value in rule["masked"]),
                lists=tuple(lists[name] for name in rule["lists"]),
            )
            for word, rule in data["by_manufacturer"].items()
        },
    )
