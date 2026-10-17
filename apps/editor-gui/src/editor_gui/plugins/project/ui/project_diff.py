"""Compare two KNX projects: devices and group addresses, added / removed / changed.

Pure over :class:`ProjectSnapshot` so it is unit-tested without imgui or a database. A snapshot
is the minimal, catalog-free slice both an open project and a project loaded from a file can
provide: devices keyed by individual address, group addresses keyed by their formatted address.
:func:`diff_projects` reports how ``other`` differs from ``base`` (added = only in other,
removed = only in base, changed = present in both with differing attributes).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSnap:
    """A device as compared: its address is the key; name/product/order are the attributes."""

    ia: str
    name: str
    product: str
    order: str


@dataclass(frozen=True)
class GaSnap:
    """A group address as compared: its address is the key; name/datapoint type are compared."""

    address: str
    name: str
    dpt: str


@dataclass(frozen=True)
class ProjectSnapshot:
    name: str
    devices: dict[str, DeviceSnap]  # keyed by individual address
    group_addresses: dict[str, GaSnap]  # keyed by formatted address


@dataclass(frozen=True)
class DiffEntry:
    key: str
    kind: str  # "added" | "removed" | "changed"
    detail: str


@dataclass
class DiffResult:
    devices: list[DiffEntry]
    group_addresses: list[DiffEntry]

    @property
    def total(self) -> int:
        return len(self.devices) + len(self.group_addresses)


@dataclass(frozen=True)
class DiffOutcome:
    """A comparison's ``result``, or the ``error`` to show; ``needs_password`` when the other
    project is password-protected and the (given) password did not open it."""

    result: DiffResult | None
    error: str = ""
    needs_password: bool = False


def diff_projects(base: ProjectSnapshot, other: ProjectSnapshot) -> DiffResult:
    """Report how ``other`` differs from ``base`` for devices and group addresses."""
    return DiffResult(
        devices=_diff_section(
            base.devices, other.devices, _device_detail, _device_label
        ),
        group_addresses=_diff_section(
            base.group_addresses, other.group_addresses, _ga_detail, _ga_label
        ),
    )


def _diff_section[V](
    base: dict[str, V],
    other: dict[str, V],
    changed_detail: Callable[[V, V], str],
    label: Callable[[V], str],
) -> list[DiffEntry]:
    """Diff two keyed dicts: present-only entries are added/removed, common-but-unequal changed."""
    entries: list[DiffEntry] = []
    for key in sorted(set(base) | set(other)):
        b = base.get(key)
        o = other.get(key)
        if b is None:
            assert o is not None
            entries.append(DiffEntry(key, "added", label(o)))
        elif o is None:
            entries.append(DiffEntry(key, "removed", label(b)))
        elif b != o:
            entries.append(DiffEntry(key, "changed", changed_detail(b, o)))
    return entries


def _device_label(d: DeviceSnap) -> str:
    # A device the user named shows only that name, an unnamed one its product.
    return d.name or d.product or d.order


def _ga_label(g: GaSnap) -> str:
    return f"{g.name} ({g.dpt})" if g.dpt else g.name


def _field_changes(pairs: list[tuple[str, str, str]]) -> str:
    """'<field> X -> Y' for each differing (label, base, other) pair, joined by ', '."""
    return ", ".join(f"{lbl} {b!r} -> {o!r}" for lbl, b, o in pairs if b != o)


def _device_detail(b: DeviceSnap, o: DeviceSnap) -> str:
    return _field_changes(
        [
            ("name", b.name, o.name),
            ("product", b.product, o.product),
            ("order", b.order, o.order),
        ]
    )


def _ga_detail(b: GaSnap, o: GaSnap) -> str:
    return _field_changes([("name", b.name, o.name), ("DPT", b.dpt or "", o.dpt or "")])
