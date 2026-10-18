"""Compare two devices: header, parameters and group objects — added / removed / changed.

:func:`diff_devices` is pure over :class:`DeviceSnapshot` (no imgui, no DB), so it is unit-tested
directly. :func:`snapshot_device` builds a snapshot from a live UI ``Device`` plus the project
service's link resolvers. Parameters and com-objects are keyed by their application ref id, so two
instances of the SAME application line up and two different applications fall out as all
added/removed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from editor_gui.device import (
    ComObject,
    ComObjectFlags,
    Device,
    com_object_display_name,
)

if TYPE_CHECKING:
    from xknxeditor.prod.parser_v2.ui import UiParameter

# A single group-object link as the resolvers return it: a group-address id and the sending flag.
LinkInfo = tuple[int, bool]
LinksResolver = Callable[[int], Iterable[LinkInfo]]
AddressResolver = Callable[[int], str]


@dataclass(frozen=True)
class ParamCell:
    label: str
    value: str


@dataclass(frozen=True)
class CoCell:
    name: str
    dpt: str
    flags: str  # letters, e.g. "C R - T -"
    priority: str
    links: tuple[tuple[str, bool], ...]  # (group address, is_sending), sending first


@dataclass(frozen=True)
class DeviceSnapshot:
    header: dict[
        str, str
    ]  # field label -> value (Name / Address / Product / Application)
    parameters: dict[str, ParamCell]  # keyed by parameter ref id
    com_objects: dict[str, CoCell]  # keyed by com-object ref id


@dataclass(frozen=True)
class DiffRow:
    key: str
    label: str  # human label shown in the first column
    kind: str  # "added" | "removed" | "changed" | "same"
    a: str  # value on device A ("" when absent)
    b: str  # value on device B ("" when absent)


@dataclass(frozen=True)
class DeviceDiffResult:
    header: list[DiffRow]
    parameters: list[DiffRow]
    com_objects: list[DiffRow]

    @property
    def sections(self) -> list[list[DiffRow]]:
        return [self.header, self.parameters, self.com_objects]

    @property
    def difference_count(self) -> int:
        return sum(1 for s in self.sections for r in s if r.kind != "same")


def diff_devices(a: DeviceSnapshot, b: DeviceSnapshot) -> DeviceDiffResult:
    """Report how device ``b`` differs from device ``a`` across header, parameters and com-objects."""
    return DeviceDiffResult(
        header=_rows(a.header, b.header, lambda key, _v: key, lambda v: v),
        parameters=_rows(
            a.parameters, b.parameters, lambda _k, v: v.label, lambda v: v.value
        ),
        com_objects=_rows(a.com_objects, b.com_objects, lambda _k, v: v.name, _co_text),
    )


def _rows[T](
    a: dict[str, T],
    b: dict[str, T],
    label_for: Callable[[str, T], str],
    text_for: Callable[[T], str],
) -> list[DiffRow]:
    """Diff two keyed dicts into rows, in sorted key order (present-only = added/removed)."""
    rows: list[DiffRow] = []
    for key in sorted(a.keys() | b.keys()):
        va, vb = a.get(key), b.get(key)
        at = text_for(va) if va is not None else ""
        bt = text_for(vb) if vb is not None else ""
        label = label_for(key, va if va is not None else vb)  # type: ignore[arg-type]
        if va is None:
            kind = "added"
        elif vb is None:
            kind = "removed"
        elif at == bt:
            # Compare the VALUE only; the label may legitimately differ between the two devices
            # (it carries the per-device channel/section name) without that being a difference.
            kind = "same"
        else:
            kind = "changed"
        rows.append(DiffRow(key, label, kind, at, bt))
    return rows


def _co_text(cell: CoCell) -> str:
    """One-line summary of a com-object cell for the diff column."""
    parts = [p for p in (cell.dpt, cell.flags, cell.priority) if p]
    if cell.links:
        parts.append("-> " + " ".join(addr for addr, _ in cell.links))
    return "  ·  ".join(parts)


# --- snapshot building (from a live UI Device) -----------------------------------------------


def snapshot_device(
    device: Device, get_links: LinksResolver, get_address: AddressResolver
) -> DeviceSnapshot:
    """Build a :class:`DeviceSnapshot` from a UI ``Device``. ``get_links`` maps a com-object's
    ``db_id`` to its ``(group_address_id, is_sending)`` links; ``get_address`` maps a group-address
    id to its formatted address."""
    header = {
        "Name": device.name or "",
        "Address": device.individual_address or "",
        "Product": device.product_name or "",
        "Application": _app_label(device),
    }
    # Key parameters by their application-relative ref (strip the app-program prefix): two devices
    # of the same product but a different application version qualify their refs with different
    # program ids, so without this their parameters never line up (all added/removed instead of
    # changed). Com-objects use the object number for the same reason (see below).
    prog = getattr(device.app, "program", None)
    app_prefix = f"{prog.id}_" if prog is not None and getattr(prog, "id", "") else ""
    parameters = {
        _strip_prefix(p.ref_id, app_prefix): ParamCell(
            label=" > ".join([*path, p.label]) if path else p.label, value=p.value
        )
        for p, path in _walk_parameters(device.get_ui(), [])
    }
    # Key com-objects by their object number (zero-padded so the sort is numeric), not the ref id:
    # the number is the stable per-device object identity, so the SAME object lines up across two
    # devices even when they carry different channel names / group addresses (then the row is a
    # "changed" one showing both sides, instead of two unmatched added/removed rows).
    com_objects = {
        f"{co.number:05d}": CoCell(
            name=com_object_display_name(co) or co.name,
            dpt=(co.dpt.code if co.dpt.major else ""),
            flags=_flag_letters(co.flags),
            priority=co.priority or "",
            links=_device_links(co, get_links, get_address),
        )
        for co in device.get_visible_com_objects()
    }
    return DeviceSnapshot(header=header, parameters=parameters, com_objects=com_objects)


def _app_label(device: Device) -> str:
    app = device.app
    name = getattr(app, "name", "") or ""
    version = getattr(app, "version", "") or ""
    return f"{name} ({version})" if name and version else name or version


def _strip_prefix(ref_id: str, prefix: str) -> str:
    return ref_id[len(prefix) :] if prefix and ref_id.startswith(prefix) else ref_id


def _walk_parameters(
    nodes: object, path: list[str]
) -> Iterator[tuple[UiParameter, list[str]]]:
    """Yield ``(UiParameter, section_path)`` for every parameter leaf, carrying the names of the
    enclosing tabs/blocks so a label that repeats across channels (e.g. "Description of objects")
    can be shown with its section ("Channel 1 > Description of objects")."""
    from xknxeditor.prod.parser_v2.ui import UiParameter, UiParameterBlock, UiTab

    for node in nodes:  # type: ignore[attr-defined]
        if isinstance(node, UiParameter):
            yield node, path
        elif isinstance(node, (UiTab, UiParameterBlock)):
            name = (
                getattr(node, "text", None) or getattr(node, "name", None) or ""
            ).strip()
            yield from _walk_parameters(node.children, [*path, name] if name else path)


_FLAG_LETTERS = (
    ("communication", "C"),
    ("read", "R"),
    ("write", "W"),
    ("transmit", "T"),
    ("update", "U"),
)


def _flag_letters(flags: ComObjectFlags) -> str:
    """``C R - T -`` style flag summary; an unset flag shows ``-``."""
    return " ".join(
        letter if getattr(flags, attr, False) else "-" for attr, letter in _FLAG_LETTERS
    )


def _device_links(
    co: ComObject, get_links: LinksResolver, get_address: AddressResolver
) -> tuple[tuple[str, bool], ...]:
    """Linked group addresses as ``(address, is_sending)``, sending first; empty when unlinked."""
    if co.db_id is None:
        return ()
    links = sorted(get_links(co.db_id), key=lambda link: not link[1])  # sending first
    return tuple(
        (addr, sending) for ga_id, sending in links if (addr := get_address(ga_id))
    )
