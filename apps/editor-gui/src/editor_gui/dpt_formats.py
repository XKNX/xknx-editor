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


@dataclass(frozen=True, slots=True)
class FieldFormat:
    """One field of a data point subtype, most significant bits first.

    ``kind`` is ``bit``, ``reserved``, ``unsigned``, ``signed``, ``float``, ``enum`` or
    ``string``. A number is shown as ``raw * coefficient + offset``; ``minimum`` and ``maximum``
    are in shown units."""

    kind: str
    width: int
    name: str = ""
    unit: str = ""
    coefficient: float | None = None
    offset: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    cleared: str = ""
    set: str = ""
    texts: tuple[tuple[int, str], ...] = ()
    encoding: str = ""
    variable_length: bool = False
    null_terminated: bool = False


@dataclass(frozen=True, slots=True)
class CompositeFormat:
    """The field layout of a subtype that is not a single bit, enumeration or number."""

    fields: tuple[FieldFormat, ...]


@dataclass(frozen=True, slots=True)
class SubtypeFormat:
    """A main type (``DPT-16``) that is written like one of its subtypes (``DPST-16-1``)."""

    subtype: str


DptFormat = BitFormat | EnumFormat | NumberFormat | CompositeFormat | SubtypeFormat

_KINDS = {
    "FormatBit": "bit",
    "FormatReserved": "reserved",
    "FormatUnsignedInteger": "unsigned",
    "FormatSignedInteger": "signed",
    "FormatFloat": "float",
    "FormatEnumeration": "enum",
    "FormatString": "string",
}


def _field(item: Any) -> FieldFormat | None:
    kind = next(
        (k for name, k in _KINDS.items() if type(item).__name__.endswith(name)), None
    )
    if kind is None:
        return None
    width = 1 if kind == "bit" else int(getattr(item, "width", 0) or 0)
    encoding = getattr(item, "encoding", None)

    def number(attr: str) -> float | None:
        value = getattr(item, attr, None)
        return None if value is None else float(value)

    return FieldFormat(
        kind=kind,
        width=width,
        name=getattr(item, "name", None) or "",
        unit=getattr(item, "unit", None) or "",
        coefficient=number("coefficient"),
        offset=number("offset"),
        minimum=number("min_inclusive") if kind != "float" else number("min_value"),
        maximum=number("max_inclusive") if kind != "float" else number("max_value"),
        cleared=getattr(item, "cleared", None) or "",
        set=getattr(item, "set", None) or "",
        texts=tuple(
            (int(value.value), str(value.text))
            for value in cast("list[Any]", getattr(item, "enum_value", None) or [])
        ),
        encoding=str(getattr(encoding, "value", encoding) or ""),
        variable_length=bool(getattr(item, "variable_length", False)),
        null_terminated=bool(getattr(item, "null_terminated", False)),
    )


def _composite(choices: list[Any], by_id: dict[str, Any]) -> CompositeFormat | None:
    fields: list[FieldFormat] = []
    for item in choices:
        ref = getattr(item, "ref_id", None)
        if ref is not None:
            item = by_id.get(ref)
        field = _field(item) if item is not None else None
        if field is None:
            return None
        fields.append(field)
    return CompositeFormat(tuple(fields)) if fields else None


def _simple(item: Any) -> DptFormat | None:
    kind = type(item).__name__
    if kind.endswith("FormatBit"):
        return BitFormat(item.cleared, item.set)
    if kind.endswith("FormatEnumeration"):
        return EnumFormat({value.value: value.text for value in item.enum_value})
    if kind.endswith(("FormatUnsignedInteger", "FormatSignedInteger")):
        return NumberFormat(item.unit or "", getattr(item, "coefficient", None))
    if kind.endswith("FormatFloat"):
        return NumberFormat(item.unit or "")
    return None


def _generic(fmt: CompositeFormat) -> CompositeFormat:
    """The layout without what only one subtype means: no names, texts, units or scaling."""
    fields: list[FieldFormat] = []
    for f in fmt.fields:
        if f.kind == "bit":
            fields.append(FieldFormat("bit", 1, cleared="0", set="1"))
        elif f.kind in ("enum", "unsigned"):
            fields.append(FieldFormat("unsigned", f.width))
        elif f.kind in ("signed", "float", "reserved"):
            fields.append(FieldFormat(f.kind, f.width))
        else:
            fields.append(
                FieldFormat(
                    f.kind,
                    f.width,
                    # ISO 8859-1 holds ASCII, so the ASCII and Latin-1 subtypes share it.
                    encoding="iso-8859-1" if f.encoding == "us-ascii" else f.encoding,
                    variable_length=f.variable_length,
                    null_terminated=f.null_terminated,
                )
            )
    return CompositeFormat(tuple(fields))


def _main_format(dpt: Any, by_id: dict[str, Any]) -> DptFormat | None:
    """How a group address with only the main type (``DPT-16``) is written: like the subtype
    the master data marks as default, like its only subtype, like the layout all subtypes
    share, or else as a plain number of the main type's size."""
    holder = getattr(dpt, "datapoint_subtypes", None)
    subtypes = cast("list[Any]", holder.datapoint_subtype) if holder else []
    defaults = [s for s in subtypes if getattr(s, "default", False)]
    if len(defaults) == 1:
        return SubtypeFormat(defaults[0].id)
    if len(subtypes) == 1:
        return SubtypeFormat(subtypes[0].id)
    layouts: set[CompositeFormat] = set()
    for subtype in subtypes:
        fmt = getattr(subtype, "format", None)
        composite = _composite(cast("list[Any]", fmt.choice) if fmt else [], by_id)
        if composite is None:
            layouts = set()
            break
        layouts.add(_generic(composite))
    if len(layouts) == 1:
        return layouts.pop()
    size = int(getattr(dpt, "size_in_bit", 0) or 0)
    if 0 < size <= 64:
        return CompositeFormat((FieldFormat("unsigned", size),))
    return None


def master_formats(master: Any) -> dict[str, DptFormat]:
    """Value formats of the data point subtypes in the KNX master data, by ``DPST-x-y`` id."""
    raw = getattr(master, "raw", None)
    holder = getattr(raw, "datapoint_types", None)
    formats: dict[str, DptFormat] = {}
    if holder is None:
        return formats
    subtypes_all: list[Any] = []
    by_id: dict[str, Any] = {}
    for dpt in cast("list[Any]", holder.datapoint_type):
        subtypes = getattr(dpt, "datapoint_subtypes", None)
        if subtypes is None:
            continue
        for subtype in cast("list[Any]", subtypes.datapoint_subtype):
            subtypes_all.append(subtype)
            fmt = getattr(subtype, "format", None)
            for item in cast("list[Any]", fmt.choice) if fmt is not None else []:
                if getattr(item, "id", None):
                    by_id[item.id] = item
    for subtype in subtypes_all:
        fmt = getattr(subtype, "format", None)
        choices = cast("list[Any]", fmt.choice) if fmt is not None else []
        simple = _simple(choices[0]) if len(choices) == 1 else None
        if simple is None:
            simple = _composite(choices, by_id)
        if simple is not None:
            formats[subtype.id] = simple
    for dpt in cast("list[Any]", holder.datapoint_type):
        number = getattr(dpt, "number", None)
        main = _main_format(dpt, by_id) if number is not None else None
        if main is not None:
            formats[f"DPT-{number}"] = main
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


def master_names(master: Any) -> dict[str, str]:
    """Names of the data point types (``DPT-21``) and subtypes (``DPST-15-0``)."""
    raw = getattr(master, "raw", None)
    holder = getattr(raw, "datapoint_types", None)
    names: dict[str, str] = {}
    if holder is None:
        return names
    for dpt in cast("list[Any]", holder.datapoint_type):
        if getattr(dpt, "number", None) is not None and getattr(dpt, "text", None):
            names[f"DPT-{dpt.number}"] = str(dpt.text)
        subtypes = getattr(dpt, "datapoint_subtypes", None)
        for subtype in (
            cast("list[Any]", subtypes.datapoint_subtype) if subtypes else []
        ):
            if getattr(subtype, "text", None):
                names[subtype.id] = str(subtype.text)
    return names


_cached_names: tuple[int, dict[str, str]] = (0, {})


def cached_master_names(master: Any) -> dict[str, str]:
    """:func:`master_names` of ``master``, computed once per master data object."""
    global _cached_names
    if master is None:
        return {}
    if _cached_names[0] != id(master):
        _cached_names = (id(master), master_names(master))
    return _cached_names[1]
