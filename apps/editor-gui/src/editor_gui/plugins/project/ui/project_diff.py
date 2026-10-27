"""Compare two KNX projects: devices and group addresses, added / removed / changed.

Pure over :class:`ProjectSnapshot` so it is unit-tested without imgui or a database. A snapshot
is the catalog-free slice both an open project and a project loaded from a file can provide (see
``project_snapshot.read_project_snapshot``). :func:`diff_projects` reports how ``other`` differs
from ``base`` (added = only in other, removed = only in base, changed = present in both with
differing attributes).

Devices are matched as the same device, not only by their address, so a device that got another
individual address is one changed device: first by the id the project file it was imported from
gave it, then (both files being the same project) by its id in the project, then by its address.
Their parameters and group objects are compared by a ``details`` callback, which needs their
applications, only for the devices whose stored settings differ.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSnap:
    """A device as compared: identified by ``id`` (in its project) and ``source_id`` (the id in
    the project file it was imported from, "" for one added in the editor); ``inputs`` sums up its
    stored settings (parameters, module instances, group objects and their links), so equal
    inputs mean equal parameters and group objects."""

    ia: str
    name: str
    product: str
    order: str
    id: int = 0
    source_id: str = ""
    application: str = ""
    location: str = ""
    inputs: str = ""


@dataclass(frozen=True)
class GaSnap:
    """A group address as compared: its address is the key; name/datapoint type are compared."""

    address: str
    name: str
    dpt: str


@dataclass(frozen=True)
class ProjectSnapshot:
    name: str
    devices: tuple[DeviceSnap, ...]
    group_addresses: dict[str, GaSnap]  # keyed by formatted address
    # The project's id; two files of the same project share it (and their device ids).
    project_id: str = ""


@dataclass(frozen=True)
class DetailRow:
    """One parameter or group object of a changed device: its value in the open project
    (``current``) and in the other one (``other``), "" where it does not exist."""

    section: str  # "parameter" | "group_object"
    label: str
    kind: str  # "added" | "removed" | "changed"
    current: str
    other: str


@dataclass(frozen=True)
class DiffEntry:
    key: str
    kind: str  # "added" | "removed" | "changed"
    detail: str
    # The parameters and group objects that differ, for a changed device.
    rows: tuple[DetailRow, ...] = ()


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


# The differing parameters and group objects of a device in both projects (base, other).
DeviceDetails = Callable[[DeviceSnap, DeviceSnap], list[DetailRow]]

# The detail of a changed device whose stored settings differ only where no parameter or group
# object shows it (e.g. a hidden parameter).
HIDDEN_SETTINGS = "stored settings differ (not shown as a parameter or group object)"


def diff_projects(
    base: ProjectSnapshot,
    other: ProjectSnapshot,
    details: DeviceDetails | None = None,
) -> DiffResult:
    """Report how ``other`` differs from ``base`` for devices and group addresses; ``details``
    lists the differing parameters and group objects of a device whose settings differ."""
    same_project = bool(base.project_id) and base.project_id == other.project_id
    return DiffResult(
        devices=_diff_devices(
            match_devices(base.devices, other.devices, same_project=same_project),
            details,
        ),
        group_addresses=_diff_section(
            base.group_addresses, other.group_addresses, _ga_detail, _ga_label
        ),
    )


def match_devices(
    base: Iterable[DeviceSnap],
    other: Iterable[DeviceSnap],
    *,
    same_project: bool = False,
) -> list[tuple[DeviceSnap | None, DeviceSnap | None]]:
    """Pair the devices of two projects that are the same device: by the id of the project file
    they were imported from, then (``same_project``: two files of one project) by their id for
    those added in the editor, then by their individual address. A device left over is only in
    its project (the pair's other side is None)."""
    left = list(base)
    right = list(other)
    pairs: list[tuple[DeviceSnap | None, DeviceSnap | None]] = []

    def pair_by(key: Callable[[DeviceSnap], object | None]) -> None:
        by_key = _unique(right, key)
        mine = _unique(left, key)
        for k, device in mine.items():
            match = by_key.get(k)
            if match is not None:
                pairs.append((device, match))
                left.remove(device)
                right.remove(match)

    pair_by(lambda d: d.source_id or None)
    if same_project:
        pair_by(lambda d: d.id if not d.source_id else None)
    pair_by(lambda d: d.ia or None)
    pairs.extend((device, None) for device in left)
    pairs.extend((None, device) for device in right)
    return pairs


def _unique(
    devices: list[DeviceSnap], key: Callable[[DeviceSnap], object | None]
) -> dict[object, DeviceSnap]:
    """The devices by ``key``, leaving out a key that is None or that several devices share."""
    found: dict[object, DeviceSnap] = {}
    shared: set[object] = set()
    for device in devices:
        k = key(device)
        if k is None:
            continue
        if k in found:
            shared.add(k)
        found[k] = device
    return {k: d for k, d in found.items() if k not in shared}


def _diff_devices(
    pairs: list[tuple[DeviceSnap | None, DeviceSnap | None]],
    details: DeviceDetails | None,
) -> list[DiffEntry]:
    entries: list[DiffEntry] = []
    for b, o in pairs:
        if b is None:
            assert o is not None
            entries.append(DiffEntry(_device_key(o), "added", _device_label(o)))
        elif o is None:
            entries.append(DiffEntry(_device_key(b), "removed", _device_label(b)))
        else:
            entry = _changed_device(b, o, details)
            if entry is not None:
                entries.append(entry)
    return sorted(entries, key=lambda e: e.key)


def _changed_device(
    b: DeviceSnap, o: DeviceSnap, details: DeviceDetails | None
) -> DiffEntry | None:
    """The entry of a device in both projects, None when nothing differs."""
    fields = _device_detail(b, o)
    rows: tuple[DetailRow, ...] = ()
    if b.inputs != o.inputs and details is not None:
        rows = tuple(details(b, o))
    if b.inputs != o.inputs and not rows:
        fields = ", ".join(part for part in (fields, HIDDEN_SETTINGS) if part)
    if not fields and not rows:
        return None
    # Named by its address in the open project, where it is looked for.
    return DiffEntry(_device_key(b), "changed", fields, rows)


def _device_key(d: DeviceSnap) -> str:
    return d.ia or "-"


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
            ("address", b.ia, o.ia),
            ("name", b.name, o.name),
            ("product", b.product, o.product),
            ("order", b.order, o.order),
            ("application", b.application, o.application),
            ("location", b.location, o.location),
        ]
    )


def _ga_detail(b: GaSnap, o: GaSnap) -> str:
    return _field_changes([("name", b.name, o.name), ("DPT", b.dpt or "", o.dpt or "")])
