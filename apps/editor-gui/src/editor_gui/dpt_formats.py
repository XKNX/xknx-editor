"""Value formats of the data point subtypes, from the KNX master data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class BitFormat:
    cleared: str
    set: str


@dataclass(frozen=True, slots=True)
class EnumFormat:
    texts: dict[int, str]


@dataclass(frozen=True, slots=True)
class NumberFormat:
    unit: str = ""
    coefficient: float | None = None


DptFormat = BitFormat | EnumFormat | NumberFormat


def master_formats(master: Any) -> dict[str, DptFormat]:
    """Value formats of the data point subtypes in the KNX master data, by ``DPST-x-y`` id."""
    raw = getattr(master, "raw", None)
    holder = getattr(raw, "datapoint_types", None)
    formats: dict[str, DptFormat] = {}
    if holder is None:
        return formats
    for dpt in cast("list[Any]", holder.datapoint_type):
        subtypes = getattr(dpt, "datapoint_subtypes", None)
        if subtypes is None:
            continue
        for subtype in cast("list[Any]", subtypes.datapoint_subtype):
            fmt = getattr(subtype, "format", None)
            choices = cast("list[Any]", fmt.choice) if fmt is not None else []
            if len(choices) != 1:
                continue
            item = choices[0]
            kind = type(item).__name__
            if kind.endswith("FormatBit"):
                formats[subtype.id] = BitFormat(item.cleared, item.set)
            elif kind.endswith("FormatEnumeration"):
                formats[subtype.id] = EnumFormat(
                    {value.value: value.text for value in item.enum_value}
                )
            elif kind.endswith(("FormatUnsignedInteger", "FormatSignedInteger")):
                formats[subtype.id] = NumberFormat(
                    item.unit or "", getattr(item, "coefficient", None)
                )
            elif kind.endswith("FormatFloat"):
                formats[subtype.id] = NumberFormat(item.unit or "")
    return formats


_cached: tuple[int, dict[str, DptFormat]] = (0, {})


def cached_master_formats(master: Any) -> dict[str, DptFormat]:
    """:func:`master_formats` of ``master``, computed once per master data object."""
    global _cached
    if master is None:
        return {}
    if _cached[0] != id(master):
        _cached = (id(master), master_formats(master))
    return _cached[1]
