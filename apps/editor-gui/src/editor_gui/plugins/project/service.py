"""GUI project facade: lazy device view over ProjectService with selection and pub/sub."""

import hashlib
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from editor_gui.concurrency import io_guarded, revision_cached
from editor_gui.device import Device, UnloadedDevice, address_order
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.history import HistoryEntry
from editor_gui.settings import config_dir
from xknxeditor.namespaces.intermediate import (
    ComObjectInstanceRef,
    ParameterInstanceRef,
)
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.prod import Application
from xknxeditor.prod.app_id import parse_app_id
from xknxeditor.prod.gc_pause import gc_paused
from xknxeditor.prod.parser_v2.calculation import VALIDATION_FAILED
from xknxeditor.proj import ProjectService as _ProjectService
from xknxeditor.proj import ProjectStorageError, ensure_sqlite_writable
from xknxeditor.proj import import_ga_export as _import_ga_export
from xknxeditor.proj import import_knxproj as _import_knxproj
from xknxeditor.proj import is_ga_export as _is_ga_export
from xknxeditor.proj.core.addressing import GroupAddressStyle, parse_ga
from xknxeditor.proj.core.identity import qualified_com_object_ref
from xknxeditor.proj.core.skeleton import MEDIUM_TP

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.catalog.service import CatalogService
    from xknxeditor.catalog import ProductSummary
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.proj.core.import_notes import ImportLoss
    from xknxeditor.proj.core.service import (
        DeviceInfo,
        GroupRangeInfo,
        SpaceDeviceInfo,
        SpaceInfo,
    )

_INSTALLATION = 0

ParamMode = Literal["edit", "transfer", "raw"]
"""How a parameter change is applied: ``edit`` runs calculations and validations, ``transfer``
calculations only, ``raw`` stores the values as they are."""


@dataclass(frozen=True)
class UpdateApplicationResult:
    """Outcome of an application update: the version updated to and how many parameter/
    com-object rows carried over vs were dropped as incompatible."""

    new_version: int
    kept: int
    dropped: int


_FLAG_COLUMNS: dict[str, str] = {
    "communication": "communication_flag",
    "read": "read_flag",
    "write": "write_flag",
    "transmit": "transmit_flag",
    "update": "update_flag",
    "read_on_init": "read_on_init_flag",
}


def _parse_individual_address(text: str) -> int | None:
    """Parse ``area.line.device`` into a raw 16-bit individual address."""
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        area, line, device = (int(p) for p in parts)
    except ValueError:
        return None
    if not (0 <= area <= 0xF and 0 <= line <= 0xF and 0 <= device <= 0xFF):
        return None
    return (area << 12) | (line << 8) | device


def _parse_group_address(text: str) -> int | None:
    """Parse a 3-level (``a/b/c``), 2-level (``a/b``) or free group address into a raw value."""
    parts = text.split("/")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return (nums[0] << 11) | (nums[1] << 8) | nums[2]
    if len(nums) == 2:
        return (nums[0] << 11) | nums[1]
    if len(nums) == 1:
        return nums[0]
    return None


def _parameter_instance_refs(
    parameters: list[tuple[str, str]] | None,
) -> list[ParameterInstanceRef]:
    """Wrap ``(ref_id, value)`` overrides as parameter instance refs for a fresh ``Device``.

    A device built with these resolves its dynamic UI against the given values, so the
    activated (param-driven) com-object set matches the configuration rather than the raw
    application defaults. Empty when there are no overrides (the default set is kept)."""
    if not parameters:
        return []
    return [
        ParameterInstanceRef(ref_id=ref_id, value=value) for ref_id, value in parameters
    ]


def _row_values(obj: Any) -> tuple[Any, ...]:
    return tuple(getattr(obj, column.key) for column in obj.__table__.columns)


def _device_source_key(row: Any) -> tuple[Any, ...]:
    """Everything a device is built from: its program, parameters, module instances, com objects."""
    return (
        row.hardware2program_ref_id,
        tuple(_row_values(p) for p in row.parameters),
        tuple(_row_values(mi) for mi in row.module_instances),
        tuple(_row_values(co) for co in row.com_objects),
    )


def _qualified_com_object_ref(co_row: Any, app_program_id: str) -> str:
    """The app-prefixed, per-instance-qualified com-object ref that the dynamic UI emits.

    Thin adapter over :func:`xknxeditor.proj.core.identity.qualified_com_object_ref` for callers that
    hold a persisted-row object (ORM ``ComObject`` or a stored-row namespace) rather than the raw
    strings. The single source of truth for the mapping lives in the proj layer so display,
    reconciliation, undo, and application upgrades all resolve instance identity identically.
    """
    return qualified_com_object_ref(
        co_row.ref_id, getattr(co_row, "instance_ref_id", "") or "", app_program_id
    )


def _co_instance_ref_from_row(
    row: Any, ref_id: str | None = None
) -> ComObjectInstanceRef | None:
    def _e(v: bool | None) -> Enable | None:
        return None if v is None else (Enable.ENABLED if v else Enable.DISABLED)

    if all(
        getattr(row, col) is None
        for col in (
            "communication_flag",
            "read_flag",
            "write_flag",
            "transmit_flag",
            "update_flag",
            "read_on_init_flag",
        )
    ):
        return None
    return ComObjectInstanceRef(
        ref_id=ref_id if ref_id is not None else row.ref_id,
        communication_flag=_e(row.communication_flag),
        read_flag=_e(row.read_flag),
        write_flag=_e(row.write_flag),
        transmit_flag=_e(row.transmit_flag),
        update_flag=_e(row.update_flag),
        read_on_init_flag=_e(row.read_on_init_flag),
    )


def _rejection_text(exc: ValueError) -> str:
    message = str(exc)
    return S.VALIDATION_FAILED if message == VALIDATION_FAILED else message


def _history_device_id(data: dict[str, Any]) -> int | None:
    """The device id an undone/redone event touched — for a ``SyncDeviceComObjects`` (``device_id``)
    or a ``Composite`` (from the first sub-event that carries one), so only that device is rebuilt."""
    if "device_id" in data:
        try:
            return int(data["device_id"])
        except (TypeError, ValueError):
            return None
    for sub in data.get("events", []):
        node_id = _history_device_id(sub.get("data", {}))
        if node_id is not None:
            return node_id
    return None


def _history_label(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "AddDevice":
        return f"Add device {data.get('name', '')!r}"
    if event_type == "SetParameter":
        return f"Set {data.get('ref_id', '')} = {data.get('value', '')!r}"
    if event_type == "Composite":
        if data.get("label"):
            return str(data["label"])
        # A composite is a parameter change plus the com-object re-instantiation it triggered; label
        # it by its first sub-event (the SetParameter) so the history reads naturally.
        for sub in data.get("events", []):
            if sub.get("data"):
                return _history_label(str(sub.get("type", "")), sub["data"])
        return "Change"
    if event_type == "CreateArea":
        return f"Create area {data.get('address')}"
    if event_type == "CreateLine":
        return f"Create line {data.get('address')}"
    if event_type == "CreateSegment":
        return "Add segment"
    if event_type == "CreateGroupAddress":
        return f"Create group address {data.get('address')}"
    if event_type == "LinkComObject":
        return "Link com-object"
    if event_type == "UnlinkComObject":
        return "Unlink com-object"
    if event_type == "RenameArea":
        return f"Rename area to {data.get('name', '')!r}"
    if event_type == "RenameLine":
        return f"Rename line to {data.get('name', '')!r}"
    if event_type == "SetDeviceName":
        return f"Rename device to {data.get('name', '')!r}"
    if event_type == "MoveDevice":
        return "Move device"
    if event_type == "SetComObjectFlag":
        return "Set flag"
    if event_type == "SetComObjectSending":
        return "Set sending"
    if event_type == "SetGroupAddressDatapointType":
        return "Set datapoint type"
    if event_type.startswith("Remove"):
        return f"Remove {event_type[len('Remove') :].lower()}"
    if event_type == "AddInstallation":
        return "Add installation"
    return event_type


@dataclass
class _Area:
    id: int
    area_number: int
    name: str


@dataclass
class _Line:
    id: int
    area_id: int
    line_number: int
    name: str


@dataclass
class _GroupAddress:
    id: int
    address: str
    name: str
    datapoint_type: str | None = None
    description: str = ""
    comment: str = ""
    data_secure: bool = False
    raw: int = 0  # raw 16-bit group-address value (style-independent; matches xknx GroupAddress.raw)


@dataclass
class _ProjectInfo:
    id: str
    name: str
    group_address_style: str
    guid: str
    created_by: str
    last_modified: str
    schema_version: str
    tool_version: str
    # Protection artifacts carried over from the imported .knxproj (shown as presence/size,
    # not raw dumps): the source project id the certificate is bound to, the signed master data,
    # the ".validation" file and the "<pid>.certificate".
    original_project_id: str = ""
    master_data_size: int = 0
    validation_size: int = 0
    certificate_size: int = 0


@dataclass
class _ProjectTrace:
    """One project-log entry. ``comment`` is verbatim from the source (it is encrypted on disk);
    ``comment_plain`` is the decrypted text when a trace key is installed, else ``None``."""

    date: str
    user_name: str
    comment: str
    comment_plain: str | None = None


@dataclass
class _Assignment:
    id: int
    com_object_id: int
    group_address_id: int
    is_sending: bool


@dataclass
class DeviceConfigClipboard:
    """A copied device configuration for pasting onto another device of the same application.

    ``params`` are (ref_id, value) parameter overrides. ``links`` are
    (com_object_number, com_object_size, group_address_id, is_sending) tuples, re-attached to a
    target's com-objects matched by number (guarded by object size)."""

    app_id: str
    source_label: str
    params: list[tuple[str, str]]
    links: list[tuple[int, str, int, bool]]


@dataclass(frozen=True)
class DeviceProduct:
    """Display fields of a device's product, stored on the device like an imported one has them."""

    product_name: str = ""
    hardware_name: str = ""
    order_number: str = ""
    manufacturer_name: str = ""

    @classmethod
    def of(cls, product: "ProductSummary") -> "DeviceProduct":
        return cls(
            product_name=product.name or "",
            hardware_name=product.hardware_name or "",
            order_number=product.order_number or "",
            manufacturer_name=product.manufacturer_name or "",
        )


class ProjectService:
    def __init__(self, catalog: "CatalogService") -> None:
        self._catalog = catalog
        # Share the catalog's re-entrant lock: a background import holds it while writing both stores.
        self._io_lock = catalog.io_lock
        self._svc = _ProjectService()
        self._pid: str | None = None
        # Bumped on every new/open/close so a long-running bus operation can tell its originating
        # project from whatever is open when its deferred writes finally run (see persist_script_changes).
        self._generation = 0
        # ``_path`` is the user-facing "home" (may be on a network share). ``_working_path`` is the
        # local file the SQLite engine actually uses; equal to ``_path`` for a normal local project.
        # They differ only in the SMB/network fallback: the engine works on a local mirror and the
        # file is written back to ``_path`` on close/switch/exit (see _teardown_current).
        self._path: Path | None = None
        self._working_path: Path | None = None
        self._log: Logger
        self._listeners: dict[str, list[Callable[..., Any]]] = {}
        self._app_cache: dict[str, Application] = {}
        # Signature of the non-reverted events at open — the baseline the read-only pre-flight
        # compares against, so opening a previously-edited project is not itself "modified".
        self._history_baseline: frozenset[tuple[int, str, str]] = frozenset()
        # Optional (i_done, total) callback invoked while (re)building the device views — lets the
        # GUI show a determinate "opening project" progress bar sized to the project's device count.
        self.build_progress: Callable[[int, int, str], None] | None = None
        self._devices_cache: list[Device] | None = None
        self._unloaded_devices: list[UnloadedDevice] = []
        # Per device, the stored data it was last built from (see _device_source_key).
        self._build_keys: dict[int, tuple[Any, ...]] = {}
        self._areas_cache: list[_Area] | None = None
        self._lines_cache: dict[int, list[_Line]] | None = None
        self._ga_cache: list[_GroupAddress] | None = None
        # Per-frame tree reads for visible panels (Buildings, "Without space", GA view) are cached by
        # revision via the @revision_cached decorator, keyed by method name in this dict, so a static
        # panel does not re-query SQLite and rebuild the ORM tree every frame (that kept the app off
        # idling and churned the GC). Cleared on _reset so one project's cache is never reused for the
        # next. Adding a new per-frame read is now just the decorator, no bespoke cache fields.
        self._revision_cache: dict[str, tuple[int, object]] = {}
        # Each lazy cache tracks the project version it was built at, independently. A single shared
        # counter is wrong: after an edit only the first cache read would rebuild (and stamp the
        # shared version), leaving the others returning stale data until the next edit.
        self._devices_cache_version: int = -1
        self._topology_cache_version: int = -1
        self._ga_cache_version: int = -1
        self._version: int = 0
        self._selected_node_id: int | None = None
        # Multi-selection: the full set of selected device node ids (the primary above is
        # the last-clicked one, kept for all single-device consumers). Empty = single/none.
        self._selected_node_ids: set[int] = set()
        # Recently-viewed devices whose heavy DynamicUI we keep resident (MRU first), so toggling
        # between a few devices is instant instead of re-parsing each time. Older ones are released.
        self._dynui_lru: list[int] = []
        self._dynui_keep = 3
        # Cross-panel "please select this group address" request (consumed by the GA panel).
        self._requested_ga_id: int | None = None
        # Cross-panel "please bring the editor tab to front" request (consumed by the editor panel).
        self._focus_editor_requested = False
        # Cross-panel "please bring the Group Addresses tab to front" request.
        self._focus_group_addresses_requested = False
        # Re-instantiate a device's persisted com-objects on a function/mode parameter change, scoped
        # to exactly the objects that parameter controls (its ChooseWhenNode branches). Only the
        # objects the parameter governs are added/removed; channels and globals stay as configured.
        # (An earlier blanket before/after diff of the whole unpruned set was destructive — it
        # over-activated all channels and removed real objects; the scoped diff in
        # _reconcile_com_objects avoids that.)
        self._co_reconcile_enabled = True

    def set_logger(self, log: "Logger") -> None:
        self._log = log

    def subscribe(self, event: str, handler: Callable[..., Any]) -> Callable[[], None]:
        self._listeners.setdefault(event, []).append(handler)
        return lambda: self._listeners[event].remove(handler)

    def _emit(self, event: str, *args: Any) -> None:
        for handler in list(self._listeners.get(event, [])):
            handler(*args)

    @property
    def is_open(self) -> bool:
        return self._pid is not None

    @property
    def generation(self) -> int:
        """Identity of the currently open project. Changes on every new/open/close; capture it when
        starting a deferred/background operation and pass it back so a stale write is dropped."""
        return self._generation

    @property
    def path(self) -> Path | None:
        """The user-facing project location (the "home"; may be on a network share). Use this for
        display, recents and dialogs. To READ the live SQLite file, use :attr:`working_path`."""
        return self._path

    @property
    def working_path(self) -> Path | None:
        """The local SQLite file the engine uses — equal to :attr:`path` for a local project, or a
        local mirror when the home is on a network share. Consumers that OPEN/READ the db file
        (export) must use this, not :attr:`path`."""
        return self._working_path

    @property
    def mirroring_active(self) -> bool:
        """True when the open project is a network home backed by a local working mirror."""
        return self._working_path is not None and self._working_path != self._path

    @property
    def mirror_home(self) -> Path | None:
        """The network home a mirror is written back to, or ``None`` when not mirroring."""
        return self._path if self.mirroring_active else None

    def location_needs_local_copy(self, path: Path) -> bool:
        """Whether ``path`` is a location SQLite can't run on (network share) and would be mirrored.

        The GUI calls this before open/import to ask the user for consent; the mirroring itself
        happens inside open/import."""
        return self._needs_mirror(path if path.suffix else path.with_suffix(".xknx"))

    def can_open(self, path: Path) -> bool:
        """Whether opening ``path`` can succeed — the home file exists, OR a local mirror exists for
        it (a mirrored project whose home was not yet written back, e.g. after a crash)."""
        home = path if path.suffix else path.with_suffix(".xknx")
        return home.is_file() or self._mirror_path(home).exists()

    # Filesystem types that cannot host a reliable live SQLite db (locking is unreliable → hangs or
    # corruption), even when a quick write probe happens to succeed.
    _NETWORK_FS = frozenset(
        {
            "smbfs",
            "cifs",
            "smb3",
            "nfs",
            "nfs4",
            "afpfs",
            "webdav",
            "fusefs.sshfs",
            "osxfuse",
            "macfuse",
            "ftp",
        }
    )

    def _needs_mirror(self, home: Path) -> bool:
        """True when ``home`` can't host a live SQLite db → work on a local mirror.

        Two independent signals, OR-ed: (1) the location is a network filesystem (SMB/NFS/…), which
        is unreliable for SQLite even when a write probe passes — this is the important one, because
        some SMB mounts accept the probe's create+write but then HANG on real locking; (2) the write
        probe itself fails (read-only dir, or a mount that rejects writes outright)."""
        net = self._is_network_fs(home)
        probe_fail = False
        if not net:
            try:
                ensure_sqlite_writable(home)
            except ProjectStorageError:
                probe_fail = True
        result = net or probe_fail
        log = getattr(self, "_log", None)
        if log is not None:
            log.debug(
                "mirror decision",
                home=str(home),
                network_fs=net,
                probe_failed=probe_fail,
                needs_mirror=result,
            )
        return result

    @classmethod
    def _is_network_fs(cls, home: Path) -> bool:
        """Whether ``home`` lives on a network filesystem. Best-effort per OS; on any error returns
        False (falls back to the write probe)."""
        try:
            target = home if home.exists() else home.parent
            target = target.resolve(strict=False)
        except OSError:
            return False
        system = platform.system()
        try:
            if system == "Windows":
                return cls._is_network_fs_windows(target)
            if system == "Darwin":
                return cls._is_network_fs_darwin(target)
            return cls._is_network_fs_linux(target)
        except Exception:
            return False  # detection is best-effort; the write probe is the fallback

    @staticmethod
    def _is_network_fs_windows(target: Path) -> bool:
        import ctypes

        if str(target).startswith("\\\\"):  # UNC path (\\server\share)
            return True
        drive = os.path.splitdrive(str(target))[0]
        if not drive:
            return False
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == DRIVE_REMOTE  # type: ignore[attr-defined]

    @classmethod
    def _is_network_fs_darwin(cls, target: Path) -> bool:
        # Parse `mount`: lines look like "//user@host/share on /Volumes/backup (smbfs, nodev, ...)".
        out = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=5
        ).stdout
        best_mp = ""
        best_type = ""
        s = str(target)
        for line in out.splitlines():
            if " on " not in line or "(" not in line:
                continue
            mp = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
            fstype = line.rsplit("(", 1)[1].split(",", 1)[0].strip()
            if (s == mp or s.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(
                best_mp
            ):
                best_mp, best_type = mp, fstype
        return best_type in cls._NETWORK_FS

    @classmethod
    def _is_network_fs_linux(cls, target: Path) -> bool:
        # /proc/self/mountinfo: fstype is after " - "; mountpoint is field 5 (0-based 4).
        best_mp = ""
        best_type = ""
        s = str(target)
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                fields = left.split()
                if len(fields) < 5 or not right:
                    continue
                mp = fields[4]
                fstype = right.split(maxsplit=1)[0]
                if (s == mp or s.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(
                    best_mp
                ):
                    best_mp, best_type = mp, fstype
        return best_type in cls._NETWORK_FS

    @staticmethod
    def _mirror_dir() -> Path:
        d = config_dir() / "mirrors"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _mirror_path(self, home: Path) -> Path:
        """Deterministic local mirror path for a network ``home`` (full resolved path → no collision
        between same-named files on different shares)."""
        key = hashlib.sha1(str(home.resolve(strict=False)).encode("utf-8")).hexdigest()
        return self._mirror_dir() / f"{key}.xknx"

    @staticmethod
    def _copy_atomic(src: Path, dst: Path) -> None:
        """Copy ``src`` onto ``dst`` without ever truncating ``dst`` in place: write a sibling temp
        then ``os.replace`` (atomic same-dir rename), so an interrupted copy can't leave a torn
        file. Drops a stale ``dst-journal`` so the copied db is never paired with a foreign journal.
        Used for both seeding a mirror (home→mirror) and writing back (working→home)."""
        tmp = dst.with_name(f"{dst.name}.writeback-{os.getpid()}.tmp")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        finally:
            Path(tmp).unlink(missing_ok=True)
        Path(f"{dst}-journal").unlink(missing_ok=True)

    def _teardown_current(self) -> None:
        """End the active project: close the engine, then (if mirroring) write the local working
        file back to the network home. The single place project teardown happens, so every switch
        (open/new/import) and app exit writes the mirror back. A write-back failure (share gone) is
        logged and KEEPS the local mirror — never loses data."""
        if self._pid is None:
            return
        working, home = self._working_path, self._path
        self._log.debug(
            "teardown: closing project",
            home=str(home) if home else None,
            working=str(working) if working else None,
            mirroring=working is not None and home is not None and working != home,
        )
        self._svc.close(self._pid)
        if working is not None and home is not None and working != home:
            self._log.info("writing project back to network location", home=str(home))
            try:
                self._copy_atomic(working, home)
                self._log.info("wrote project back to network location", home=str(home))
            except OSError as e:
                self._log.error(
                    "could not write the project back to its network location; your changes are "
                    "kept in the local copy",
                    home=str(home),
                    working=str(working),
                    error=f"{type(e).__name__}: {e}",
                )
        self._pid = None
        self._path = None
        self._working_path = None
        self._generation += 1
        self._reset()

    @staticmethod
    def _remove_project_file(path: Path) -> None:
        """Delete a project's SQLite file and its DELETE-mode ``-journal`` sidecar, so a fresh create
        never seeds into a leftover database (a stale sidecar over a new db also corrupts it)."""
        path.unlink(missing_ok=True)
        Path(f"{path}-journal").unlink(missing_ok=True)

    def new(self, path: Path) -> None:
        if not path.suffix:
            path = path.with_suffix(".xknx")
        with self._io_lock:
            self._teardown_current()  # close + write back any previous project first
            working = self._mirror_path(path) if self._needs_mirror(path) else path
            # New project = a fresh file. The save dialog lets the user pick an existing path to
            # overwrite; without clearing the WORKING db, create()'s seed collides with the old
            # project's rows ("UNIQUE constraint failed: installations.index").
            self._remove_project_file(working)
            self._pid = self._svc.create(working)
            self._generation += 1
            self._path = path
            self._working_path = working
            self._reset()
        if self.mirroring_active:
            self._log.info(
                "network location: working on a local copy",
                home=str(path),
                working=str(working),
            )
        self._log.info("project created", path=str(path))

    def save_as(self, new_path: Path) -> Path | None:
        """Save the open project to ``new_path`` and continue there ("Save as").

        The project persists to its .xknx file continuously (event-sourced), so this snapshots the
        current file to the chosen location and re-opens it there — how an auto-named "untitled"
        project (created when the Welcome screen is closed) gets a real, user-chosen home. Returns the
        final path (``.xknx`` suffix ensured), or ``None`` when no project is open."""
        if self._pid is None or self._working_path is None:
            return None
        if not new_path.suffix:
            new_path = new_path.with_suffix(".xknx")
        with self._io_lock:
            # Snapshot the live (working) file BEFORE teardown; teardown closes the engine (releases
            # the file/journal) and writes the OLD home back. The live data is in the working file,
            # not necessarily the home (which is only current after a write-back).
            src = self._working_path
            self._teardown_current()
            working = (
                self._mirror_path(new_path)
                if self._needs_mirror(new_path)
                else new_path
            )
            # Replace any stale destination db/mirror so the fresh copy wins (a leftover mirror for
            # this home must not shadow the just-saved data).
            self._remove_project_file(working)
            shutil.copyfile(src, working)
        self.open(new_path)
        self._log.info("project saved as", path=str(new_path))
        return new_path

    def open(self, path: Path) -> None:
        # Hold the shared lock for the whole open (incl. the device-view build) so per-frame UI reads
        # on other threads bail to empty placeholders instead of racing it — lets a background open
        # run behind a progress spinner. Re-entrant, so our own nested reads still work.
        if not path.suffix:
            path = path.with_suffix(".xknx")
        self._log.debug("project open requested", path=str(path))
        with self._io_lock:
            self._log.debug("opening project", path=str(path))
            # Tear down (and write back) the previous project BEFORE opening the new one. Two
            # different files can carry the same project-id (P-XXXX); the core keys engines by id,
            # so opening-before-closing could collide and then close the just-opened project. The
            # trade-off — a corrupt target leaves nothing open — is acceptable for this fallback.
            self._teardown_current()
            working = self._mirror_path(path) if self._needs_mirror(path) else path
            # First open of a network home: seed the local mirror from it. If the home is missing
            # but a mirror exists (crash before write-back), reuse the mirror as-is (never lose the
            # unsynced local edits) — that is why we only seed when the mirror is absent.
            if working != path and not working.exists() and path.is_file():
                self._log.info(
                    "seeding local copy from network home",
                    home=str(path),
                    working=str(working),
                )
                self._copy_atomic(path, working)
            with gc_paused():
                new_pid = self._svc.open(working)
                self._pid = new_pid
                self._generation += 1
                self._path = path
                self._working_path = working
                self._reset()
                self._history_baseline = self._history_key()
                if self.mirroring_active:
                    self._log.info(
                        "network location: working on a local copy",
                        home=str(path),
                        working=str(working),
                    )
                self._log.info(
                    "project opened",
                    path=str(path),
                    devices=len(self.devices),
                    group_range_roots=len(self.get_group_range_tree()),
                )

    def import_knxproj(
        self, source: Path, dest: Path, *, password: str | None = None
    ) -> None:
        """Import a ``.knxproj`` into a new project at ``dest`` and open it.

        A ``.knxproj`` bundles the (unencrypted) manufacturer/application data for the products it
        uses, so we also ingest it into the catalog — without it the device view cannot resolve the
        applications and would skip every imported device.

        The parse-and-write goes into a sibling temp file the live service never references, so the
        whole schema build/DDL happens off to the side; only once it is complete do we close any
        engine on ``dest``, atomically ``os.replace`` the temp into place, and open it. This is what
        keeps a re-import safe: we never rewrite the file under an open SQLite connection, and the UI
        keeps reading the previous project (stable, no schema mutation) until the swap."""
        if not dest.suffix:
            dest = dest.with_suffix(".xknx")
        self._log.debug(
            "knxproj import requested",
            source=str(source),
            dest=str(dest),
            encrypted=password is not None,
        )
        # When ``dest`` is a network share, SQLite can't run there, so import into a local mirror and
        # write it back to ``dest`` on close (see open/_teardown_current). ``ensure_sqlite_writable``
        # still guards via make_engine if even the local mirror dir were unusable (never, in
        # practice — it is config_dir()).
        working = self._mirror_path(dest) if self._needs_mirror(dest) else dest
        # Hold the shared lock for the whole import so per-frame UI reads on other threads bail to
        # empty placeholders (see editor_gui.concurrency) instead of racing these writes. This method is
        # meant to run on a worker thread; the lock is re-entrant, so our own nested reads still work.
        with self._io_lock:
            self._log.debug(
                "importing knxproj",
                source=str(source),
                dest=str(dest),
                encrypted=password is not None,
            )
            try:
                added = self._catalog.import_knxprod(source)
                self._log.info("catalog updated from knxproj", added=len(added))
            except Exception as e:
                # Best effort: catalog ingest is optional enrichment (it lets the device view resolve
                # applications). Any failure here — including product-parser bugs on odd archives —
                # must not block the project import; topology and group addresses still load.
                self._log.warning(
                    "could not populate catalog from knxproj",
                    source=str(source),
                    error=f"{type(e).__name__}: {e}",
                )
            tmp = working.with_name(f"{working.name}.import-{os.getpid()}.tmp")
            try:
                # Parse + write into a sibling temp of the WORKING file (never next to a network
                # dest). On a wrong password this raises before writing, so the currently-open
                # project is left untouched.
                _import_knxproj(source, tmp, password=password)
                self._log.debug(
                    "knxproj parsed",
                    source=str(source),
                    temp=str(tmp),
                )
                # If we are overwriting the working file the live service currently has open, tear it
                # down first so no engine holds its inode when we replace it (old-inode/new-file +
                # pooled-connection hazard). A different working file needs no early close — open()
                # tears down the previous project after the swap.
                if (
                    self._working_path is not None
                    and self._working_path.resolve() == working.resolve()
                ):
                    self._teardown_current()
                os.replace(tmp, working)
                self._log.debug("knxproj installed", working=str(working))
            finally:
                # Clean up the temp file if it survived a failure (a successful replace consumed it).
                Path(tmp).unlink(missing_ok=True)
            self.open(dest)
        self._log.info("project imported", source=str(source), path=str(dest))

    @staticmethod
    def is_ga_export(path: Path) -> bool:
        """Whether ``path`` is an ETS group-address export (routed to import_ga_export)."""
        return _is_ga_export(path)

    def import_ga_export(self, source: Path, dest: Path) -> None:
        """Import a GA export (``ga-export/01`` XML) into a new project at ``dest`` and open it.

        A GA export carries only the group-address tree (no devices, topology or product
        data), so there is no catalog ingest and no password - unlike import_knxproj. The
        build goes into a sibling temp file and is atomically swapped into place, so the
        currently-open project keeps reading until the import is complete."""
        if not dest.suffix:
            dest = dest.with_suffix(".xknx")
        working = self._mirror_path(dest) if self._needs_mirror(dest) else dest
        with self._io_lock:
            self._log.debug("importing ga export", source=str(source), dest=str(dest))
            tmp = working.with_name(f"{working.name}.import-{os.getpid()}.tmp")
            try:
                # Name the project after the user-chosen dest, not the temp file we build into.
                _import_ga_export(source, tmp, project_name=dest.stem)
                if (
                    self._working_path is not None
                    and self._working_path.resolve() == working.resolve()
                ):
                    self._teardown_current()
                os.replace(tmp, working)
                # Drop a stale DELETE-mode journal left by a prior db at this path, so SQLite can't
                # replay a foreign journal into the freshly imported file on open (see _copy_atomic).
                Path(f"{working}-journal").unlink(missing_ok=True)
            finally:
                Path(tmp).unlink(missing_ok=True)
            self.open(dest)
        self._log.info("ga export imported", source=str(source), path=str(dest))

    def close(self) -> None:
        if self._pid is not None:
            with self._io_lock:
                # Teardown writes the local working file back to a network home (if mirroring).
                self._teardown_current()
                self._history_baseline = frozenset()
                self._log.info("project closed")

    def _reset(self) -> None:
        self._devices_cache = None
        self._build_keys.clear()
        self._areas_cache = None
        self._lines_cache = None
        self._ga_cache = None
        self._revision_cache.clear()
        self._devices_cache_version = -1
        self._topology_cache_version = -1
        self._ga_cache_version = -1
        self._version = 0
        self._selected_node_id = None
        self._selected_node_ids = set()
        self._dynui_lru.clear()
        self._app_cache.clear()

    def _bump(self, *, structural: bool = True) -> None:
        """Advance the revision so views refresh. ``structural=False`` for edits that don't change
        device structure (group-address/link changes): the expensive device cache — each device
        rebuild re-parses its application's dynamic UI — is kept valid instead of being discarded,
        so linking a group address stays instant on large projects."""
        self._version += 1
        if not structural and self._devices_cache is not None:
            self._devices_cache_version = self._version

    def _refresh_device(self, node_id: int) -> None:
        """Rebuild a single device's cached view in place (its com-objects changed), instead of
        discarding the whole device cache. A full structural rebuild re-evaluates every device's
        DynamicUI (``_build_device`` warms it per device), which is very slow on large projects; a
        function/mode change only affects the one edited device, so refresh just that entry. Caller
        bumps the revision."""
        if self._pid is None or self._devices_cache is None:
            return
        row = next((r for r in self._svc.devices(self._pid) if r.id == node_id), None)
        if row is None:
            return
        rebuilt = self._build_device(row)
        if rebuilt is None:
            return
        for i, d in enumerate(self._devices_cache):
            if d.node_id == node_id:
                self._devices_cache[i] = rebuilt
                return

    @property
    def revision(self) -> int:
        """Monotonic counter bumped on every edit/undo/redo — for cheap change detection."""
        return self._version

    def _resolve_app(self, program_ref: str | None) -> Application | None:
        if program_ref is None:
            return None
        cached = self._app_cache.get(program_ref)
        if cached is not None:
            return cached
        # Through the hardware program itself: a program loads its application whether or not a
        # catalog item lists it.
        app_id = self._catalog.get_program_application_id(program_ref)
        app = self._catalog.get_application(app_id) if app_id else None
        if app is not None:
            self._app_cache[program_ref] = app
        return app

    def _build_device(self, row: Any) -> Device | None:
        try:
            app = self._resolve_app(row.hardware2program_ref_id)
        except Exception as e:
            # A broken/unsupported .knxprod (bad application XML) must not abort loading the whole
            # project — report it in the log and skip just this device.
            self._log.error(
                "failed to load device application (broken product data?)",
                device_id=row.id,
                name=row.name,
                program=row.hardware2program_ref_id,
                error=f"{type(e).__name__}: {e}",
            )
            return None
        if app is None:
            self._log.warning(
                "skipping device: application not found",
                device_id=row.id,
                program=row.hardware2program_ref_id,
            )
            return None
        assert self._pid is not None
        from xknxeditor.namespaces.intermediate import ParameterInstanceRef as _PIR
        from xknxeditor.namespaces.intermediate.module_instance_t import (
            ModuleInstance as _MI,
        )

        try:
            pirs = [_PIR(ref_id=p.ref_id, value=p.value) for p in row.parameters]
            mis = [
                _MI(id=mi.instance_id, ref_id=mi.ref_id) for mi in row.module_instances
            ]
            # Every persisted ComObject row is an instantiated object (that is what pins the device's
            # object set against the parser's channel over-activation). Seed one instance ref per row
            # — with its flag overrides where present, else a bare ref — so objects added by a
            # function/mode re-instantiation (default, i.e. all-None, flags) are also counted as
            # instantiated and thus shown/encoded rather than pruned away.
            coirs = [
                _co_instance_ref_from_row(
                    co_row, ref_id=_qualified_com_object_ref(co_row, app.program.id)
                )
                or ComObjectInstanceRef(
                    ref_id=_qualified_com_object_ref(co_row, app.program.id)
                )
                for co_row in row.com_objects
            ]
            ia = self._svc.individual_address(self._pid, row.id) or ""
            device = Device(
                node_id=row.id,
                name=row.name,
                product_name=row.product_name or "",
                app=app,
                individual_address=ia,
                description=row.description,
                parameter_instance_refs=pirs,
                module_instances=mis,
                com_object_instance_refs=coirs,
            )
            for co_row in row.com_objects:
                co = device.find_com_object(
                    _qualified_com_object_ref(co_row, app.program.id)
                )
                if co is not None:
                    co.db_id = co_row.id
                    # Carry the stored per-instance overrides so get_visible_com_objects prefers them
                    # over the app default, and reflect them on the display object right away for the
                    # panels that read device.com_objects directly.
                    co.text_override = co_row.text_override
                    co.function_text_override = co_row.function_text_override
                    co.description = co_row.description_override or ""
                    if co_row.text_override:
                        co.name = co_row.text_override
                    if co_row.function_text_override:
                        co.function_text = co_row.function_text_override
            # Warm the lightweight views the panels read for every device, then drop the heavy
            # per-device DynamicUI evaluator (~15 MB each) — it is rebuilt lazily only when a device
            # is inspected or edited. This keeps memory bounded to the active device.
            device.get_visible_com_objects()
            device.release_dynamic_ui()
            self._build_keys[row.id] = _device_source_key(row)
            return device
        except Exception as e:
            # Building the device (evaluating its dynamic UI from the .knxprod) failed — log the
            # product/device and skip it rather than crashing the editor.
            self._log.error(
                "failed to build device from product data",
                device_id=row.id,
                name=row.name,
                program=row.hardware2program_ref_id,
                error=f"{type(e).__name__}: {e}",
            )
            return None

    def _reuse_device(self, row: Any, previous: dict[int, Device]) -> Device | None:
        """The already built device for ``row`` if it was not changed in place and nothing it was
        built from has changed since.

        Only its name, description and individual address are refreshed then. Rebuilding every
        device after e.g. an address change would load every application again."""
        old = previous.get(row.id)
        if (
            old is None
            or old.edited_in_place
            or old.app is not self._app_cache.get(row.hardware2program_ref_id or "")
            or self._build_keys.get(row.id) != _device_source_key(row)
        ):
            return None
        assert self._pid is not None
        old.name = row.name
        old.product_name = row.product_name or ""
        old.description = row.description
        old.individual_address = self._svc.individual_address(self._pid, row.id) or ""
        return old

    @property
    @io_guarded(list)
    def devices(self) -> list[Device]:
        if self._devices_cache is None or self._devices_cache_version != self._version:
            previous = {d.node_id: d for d in self._devices_cache or []}
            devices: list[Device] = []
            unloaded: list[UnloadedDevice] = []
            if self._pid is not None:
                rows = list(self._svc.devices(self._pid))
                total = len(rows)
                report = self.build_progress
                started = time.monotonic()
                self._log.debug("building devices", total=total)
                # Building creates millions of long-lived objects; the cyclic garbage collector would
                # rescan all of them over and over, which dominates the load time.
                with gc_paused():
                    for i, row in enumerate(rows, start=1):
                        device = self._reuse_device(
                            row, previous
                        ) or self._build_device(row)
                        if device is None:
                            unloaded.append(
                                UnloadedDevice(
                                    node_id=row.id,
                                    name=row.name or "",
                                    product_name=row.product_name or "",
                                    individual_address=self._svc.individual_address(
                                        self._pid, row.id
                                    )
                                    or "",
                                    program_ref=row.hardware2program_ref_id,
                                )
                            )
                        else:
                            devices.append(device)
                            # Verbose per-device trace: the build is otherwise a silent gap
                            # between "opening project" and "project opened".
                            self._log.debug(
                                "built device",
                                index=i,
                                total=total,
                                device_id=row.id,
                                address=device.individual_address,
                                name=device.name,
                            )
                        if report is not None:
                            label = (
                                f"{device.individual_address}  {device.name}".strip()
                                if device is not None
                                else (row.name or "")
                            )
                            report(i, total, label)
                self._log.info(
                    "devices built",
                    built=len(devices),
                    skipped=total - len(devices),
                    seconds=round(time.monotonic() - started, 2),
                )
            devices.sort(key=lambda d: (address_order(d.individual_address), d.node_id))
            unloaded.sort(
                key=lambda d: (address_order(d.individual_address), d.node_id)
            )
            self._devices_cache = devices
            self._unloaded_devices = unloaded
            self._devices_cache_version = self._version
        return self._devices_cache

    @property
    def unloaded_devices(self) -> list[UnloadedDevice]:
        """Project devices whose application could not be loaded, shown without parameters."""
        _ = self.devices
        return self._unloaded_devices

    def find_device_by_node_id(self, node_id: int) -> Device | None:
        return next((d for d in self.devices if d.node_id == node_id), None)

    def find_device_by_address(self, address: str) -> Device | None:
        return next((d for d in self.devices if d.individual_address == address), None)

    @property
    def selected_device(self) -> Device | None:
        if self._selected_node_id is None:
            return None
        return self.find_device_by_node_id(self._selected_node_id)

    @selected_device.setter
    def selected_device(self, device: Device | None) -> None:
        new_id = device.node_id if device is not None else None
        if new_id != self._selected_node_id:
            self._selected_node_id = new_id
            # Any single-device select (command palette, Health/Cockpit navigate) collapses the
            # multi-selection to just this device; set_multi_selection re-widens it afterwards.
            self._selected_node_ids = {new_id} if new_id is not None else set[int]()
            # Keep the few most-recently-viewed devices' DynamicUI resident (instant toggling);
            # release those that fall out of the small LRU (no-op if they have unsaved edits).
            if new_id is not None:
                if new_id in self._dynui_lru:
                    self._dynui_lru.remove(new_id)
                self._dynui_lru.insert(0, new_id)
                for evicted in self._dynui_lru[self._dynui_keep :]:
                    dev = self.find_device_by_node_id(evicted)
                    if dev is not None:
                        dev.release_dynamic_ui()
                del self._dynui_lru[self._dynui_keep :]
            self._emit("device_selected", device)

    @property
    def selected_node_ids(self) -> set[int]:
        """The full multi-selection (device node ids); empty when a single/no device is selected."""
        return set(self._selected_node_ids)

    def set_multi_selection(self, primary_id: int | None, node_ids: list[int]) -> None:
        """Set the selection set and its primary (the device the single-device panels track). The
        primary is forced into the set; an empty selection clears everything."""
        ids = set(node_ids)
        if primary_id is not None and primary_id not in ids:
            primary_id = None
        if primary_id is None and ids:
            primary_id = min(ids)
        # Assign the primary first (the setter collapses _selected_node_ids to {primary}), then
        # widen to the full multi-selection set.
        self.selected_device = (
            self.find_device_by_node_id(primary_id) if primary_id is not None else None
        )
        self._selected_node_ids = ids

    def request_group_address(self, ga_id: int) -> None:
        """Ask the Group Addresses panel to select ``ga_id`` (cross-panel navigation, e.g. Health)."""
        self._requested_ga_id = ga_id

    def take_requested_group_address(self) -> int | None:
        """One-shot: the pending externally-requested group-address selection, then cleared."""
        ga_id = self._requested_ga_id
        self._requested_ga_id = None
        return ga_id

    def focus_editor(self) -> None:
        """Ask the editor tab to come to the front (after selecting a device from another view)."""
        self._focus_editor_requested = True

    def take_focus_editor(self) -> bool:
        """One-shot: whether the editor tab was asked to focus, then cleared."""
        requested = self._focus_editor_requested
        self._focus_editor_requested = False
        return requested

    def focus_group_addresses(self) -> None:
        """Ask the Group Addresses tab to come to the front (e.g. clicking a linked GA)."""
        self._focus_group_addresses_requested = True

    def take_focus_group_addresses(self) -> bool:
        """One-shot: whether the Group Addresses tab was asked to focus, then cleared."""
        requested = self._focus_group_addresses_requested
        self._focus_group_addresses_requested = False
        return requested

    def _installation(self) -> Any:
        assert self._pid is not None
        return self._svc.topology(self._pid, _INSTALLATION)

    def _default_device_segment_id(self) -> int:
        """Segment a new device lands on when no line is chosen: the first TP line (end devices
        belong on a TP line, not the IP backbone/main line). Falls back to the very first segment
        if the project has no TP line (e.g. an IP-only setup)."""
        installation = self._installation()
        for area in installation.areas:
            for line in area.lines:
                for segment in line.segments:
                    if segment.medium_type == MEDIUM_TP:
                        return segment.id
        return installation.areas[0].lines[0].segments[0].id

    def _ensure_topology_cache(self) -> None:
        if (
            self._areas_cache is not None
            and self._topology_cache_version == self._version
        ):
            return
        if self._pid is None:
            self._areas_cache = []
            self._lines_cache = {}
            return
        installation = self._svc.topology(self._pid, _INSTALLATION)
        self._areas_cache = [
            _Area(id=a.id, area_number=a.address, name=a.name)
            for a in installation.areas
        ]
        self._lines_cache = {
            a.id: [
                _Line(
                    id=ln.id, area_id=ln.area_id, line_number=ln.address, name=ln.name
                )
                for ln in a.lines
            ]
            for a in installation.areas
        }
        self._topology_cache_version = self._version

    @io_guarded(list)
    def get_areas(self) -> list[_Area]:
        if self._pid is None:
            return []
        self._ensure_topology_cache()
        return self._areas_cache or []

    @io_guarded(list)
    def get_lines(self, area_id: int) -> list[_Line]:
        if self._pid is None:
            return []
        self._ensure_topology_cache()
        return (self._lines_cache or {}).get(area_id, [])

    @property
    @io_guarded(list)
    def group_addresses(self) -> list[_GroupAddress]:
        if self._pid is None:
            return []
        if self._ga_cache is not None and self._ga_cache_version == self._version:
            return self._ga_cache
        self._ga_cache = [
            _GroupAddress(
                id=g.id,
                address=g.text,
                name=g.name,
                datapoint_type=g.datapoint_type,
                description=g.description,
                comment=g.comment,
                data_secure=g.data_secure,
                raw=g.address,
            )
            for g in self._svc.group_addresses(self._pid)
        ]
        self._ga_cache_version = self._version
        return self._ga_cache

    @io_guarded(lambda: None)
    def get_group_address(self, ga_id: int) -> _GroupAddress | None:
        if self._pid is None:
            return None
        try:
            g = self._svc.group_address(self._pid, ga_id)
        except KeyError:
            return None
        return _GroupAddress(
            id=g.id,
            address=g.text,
            name=g.name,
            datapoint_type=g.datapoint_type,
            description=g.description,
            comment=g.comment,
            data_secure=g.data_secure,
            raw=g.address,
        )

    @io_guarded(list)
    def get_assignments_for_ga(self, ga_id: int) -> list[_Assignment]:
        if self._pid is None:
            return []
        return [
            _Assignment(
                id=link.id,
                com_object_id=link.com_object_id,
                group_address_id=link.group_address_id,
                is_sending=link.is_sending,
            )
            for link in self._svc.group_address_links(self._pid, ga_id)
        ]

    @io_guarded(list)
    def get_links_for_com_object(self, com_object_db_id: int) -> list[_Assignment]:
        """A device com-object's group-address links (the per-com-object direction, for the editor's
        Group Objects view). ``com_object_db_id`` is ``ComObject.db_id``."""
        if self._pid is None:
            return []
        return [
            _Assignment(
                id=link.id,
                com_object_id=link.com_object_id,
                group_address_id=link.group_address_id,
                is_sending=link.is_sending,
            )
            for link in self._svc.com_object_links(self._pid, com_object_db_id)
        ]

    def group_communication_for(self, device: Device) -> "GroupCommunication | None":
        """Collect a device's group-address links into a :class:`GroupCommunication` for programming.

        Returns ``None`` when the device has no (parseable) individual address. Used by both the GUI
        download flow and the embedded MCP server so both program identical address/association data.
        """
        from xknxeditor.download import GroupCommunication
        from xknxeditor.download.project_data import GroupObjectLink

        device_address = _parse_individual_address(device.individual_address)
        if device_address is None:
            return None
        links: list[GroupObjectLink] = []
        for com_object in device.com_objects:
            if com_object.db_id is None:
                continue
            for assignment in self.get_links_for_com_object(com_object.db_id):
                ga = self.get_group_address(assignment.group_address_id)
                if ga is None:
                    continue
                address = _parse_group_address(ga.address)
                if address is None:
                    continue
                links.append(
                    GroupObjectLink(
                        com_object_ref_id=com_object.id,
                        group_address=address,
                        sending=assignment.is_sending,
                    )
                )
        # A line/backbone coupler (address x.y.0) also carries a group-address filter table computed
        # from the whole topology (which group addresses cross it). A non-coupler at .0 gets one too,
        # but its load procedure has no Router-object write, so the extra image segment is unused.
        from xknxeditor.download.filter_table import (
            compute_coupler_filter_table,
            is_coupler_address,
        )

        filter_table = None
        if is_coupler_address(device_address):
            # The project's pass-through addresses have to be included, or the computed table
            # blocks addresses it was explicitly configured to always route: group addresses (or
            # whole ranges) flagged Unfiltered, and the line's AdditionalGroupAddresses.
            unfiltered, additional = self._coupler_pass_through(device_address)
            filter_table = compute_coupler_filter_table(
                device_address,
                self._all_device_group_addresses(),
                unfiltered=unfiltered,
                additional=additional,
            )
        return GroupCommunication(
            device_address=device_address, links=links, filter_table=filter_table
        )

    def _coupler_pass_through(
        self, coupler_address: int
    ) -> tuple[list[int], list[int]]:
        """``(unfiltered, additional)`` pass-through addresses for a coupler; empty when unknown."""
        if self._pid is None:
            return [], []
        return self._svc.coupler_pass_through(self._pid, coupler_address)

    def _all_device_group_addresses(self) -> dict[int, set[int]]:
        """Map every device's raw individual address to the raw group addresses it links (send or
        receive). The coupler filter-table computation uses this to decide which addresses cross a
        coupler (linked both behind it and in front of it)."""
        result: dict[int, set[int]] = {}
        for dev in self.devices:
            ia = _parse_individual_address(dev.individual_address)
            if ia is None:
                continue
            gas: set[int] = set()
            for co in dev.com_objects:
                if co.db_id is None:
                    continue
                for assignment in self.get_links_for_com_object(co.db_id):
                    ga = self.get_group_address(assignment.group_address_id)
                    if ga is None:
                        continue
                    raw = _parse_group_address(ga.address)
                    if raw is not None:
                        gas.add(raw)
            result[ia] = gas
        return result

    @io_guarded(list)
    @revision_cached
    def get_group_range_tree(self) -> list["GroupRangeInfo"]:
        """The named group-address range tree (roots → children → GAs) for the GA view."""
        if self._pid is None:
            return []
        return self._svc.group_ranges(self._pid, _INSTALLATION)

    @io_guarded(list)
    @revision_cached
    def get_space_tree(self) -> list["SpaceInfo"]:
        """The building/location tree (spaces → devices/functions) for the Buildings view."""
        if self._pid is None:
            return []
        return self._svc.space_tree(self._pid, _INSTALLATION)

    @io_guarded(lambda: None)
    def get_project_metadata(self) -> _ProjectInfo | None:
        """Project-level metadata (name, author, tool/schema version, …) from the imported project."""
        if self._pid is None:
            return None
        p = self._svc.project(self._pid)
        return _ProjectInfo(
            id=p.id,
            name=p.name,
            group_address_style=p.group_address_style,
            guid=p.guid,
            created_by=p.created_by,
            last_modified=p.last_modified,
            schema_version=p.schema_version,
            tool_version=p.tool_version,
            original_project_id=p.original_project_id,
            master_data_size=len(p.knx_master_xml) if p.knx_master_xml else 0,
            validation_size=len(p.knx_validation),
            certificate_size=len(p.knx_certificate),
        )

    def get_project_traces(self) -> list[_ProjectTrace]:
        """The project log (ProjectInformation/ProjectTraces), in document order.

        Comments are stored verbatim (they are encrypted on disk); ``comment_plain`` is filled with
        the decrypted text when a trace key has been installed (see :mod:`editor_gui.trace_key`).
        """
        from xknxeditor.proj import decrypt_comment

        if self._pid is None:
            return []
        p = self._svc.project(self._pid)
        return [
            _ProjectTrace(
                date=t.date,
                user_name=t.user_name,
                comment=t.comment,
                comment_plain=decrypt_comment(t.comment),
            )
            for t in p.traces
        ]

    def get_import_notes(self) -> list["ImportLoss"]:
        """Lossy-import notes recorded when the current project was built from a ``.knxproj``
        (data ETS/xknxproject carried that our store cannot fully represent). Empty when the
        project was not imported or nothing was dropped."""
        if self._pid is None:
            return []
        from xknxeditor.proj.core.import_notes import loads

        p = self._svc.project(self._pid)
        return loads(p.import_notes)

    def next_free_group_address(self) -> str | None:
        """The next unused group address, style-formatted (e.g. ``"0/0/2"``); None if no project."""
        if self._pid is None:
            return None
        from xknxeditor.proj.core.addressing import format_ga

        value = self._svc.next_free_group_address(self._pid, _INSTALLATION)
        return format_ga(value, self.group_address_style)

    @io_guarded(lambda: None)
    def get_device_info(self, node_id: int) -> "DeviceInfo | None":
        """Descriptive device metadata (manufacturer/order number/hardware/description) from the
        imported project, independent of catalog resolution.

        Cached by revision per node id. The Configure panel reads this every frame; an uncached read
        there is a 60 Hz SQLite query that both churns the CPU and, when a background writer (import,
        download) holds the database lock, can raise ``database is locked`` straight out of the render
        loop. @revision_cached only memoises zero-arg reads, so this keys the shared revision cache by
        node id itself; the dict is cleared on project open/close like the other per-frame caches."""
        if self._pid is None:
            return None
        cache_key = f"get_device_info:{node_id}"
        hit = self._revision_cache.get(cache_key)
        if hit is not None and hit[0] == self.revision:
            return cast("DeviceInfo | None", hit[1])
        try:
            info = self._svc.device(self._pid, node_id)
        except KeyError:
            info = None
        self._revision_cache[cache_key] = (self.revision, info)
        return info

    def set_device_commissioning(
        self,
        node_id: int,
        *,
        serial_number: str | None = None,
        last_download: str | None = None,
        individual_address_loaded: bool | None = None,
        application_program_loaded: bool | None = None,
        communication_part_loaded: bool | None = None,
        medium_config_loaded: bool | None = None,
        parameters_loaded: bool | None = None,
    ) -> None:
        """Record a device's commissioning state (loaded ticks / serial / last download).

        Called after programming a device. Non-structural: the device's parameter/com-object UI is
        unchanged, so the expensive device cache is preserved (only the revision bumps)."""
        if self._pid is None:
            return
        self._svc.set_device_commissioning(
            self._pid,
            node_id,
            serial_number=serial_number,
            last_download=last_download,
            individual_address_loaded=individual_address_loaded,
            application_program_loaded=application_program_loaded,
            communication_part_loaded=communication_part_loaded,
            medium_config_loaded=medium_config_loaded,
            parameters_loaded=parameters_loaded,
        )
        self._bump(structural=False)

    @io_guarded(set)
    def program_refs(self) -> set[str]:
        """Distinct hardware-program refs (fallback product refs) across all project devices.

        Used to collect the manufacturer archives a ``.knxproj`` export needs to bundle.
        """
        if self._pid is None:
            return set()
        refs: set[str] = set()
        for row in self._svc.devices(self._pid):
            ref = row.hardware2program_ref_id or row.product_ref_id
            if ref:
                refs.add(ref)
        return refs

    @io_guarded(list)
    def missing_program_refs(self) -> list[str]:
        """Hardware-program refs of devices whose application is NOT in the local catalog.

        These are the devices dropped from the view with "application not found"; the refs can be
        fetched from the online catalog (they are a prefix of the online catalog item id).
        Guarded because it is polled every frame and also read from the fetch worker."""
        if self._pid is None:
            return []
        missing: list[str] = []
        for row in self._svc.devices(self._pid):
            ref = row.hardware2program_ref_id
            if ref and self._resolve_app(ref) is None and ref not in missing:
                missing.append(ref)
        return missing

    def refresh_catalog_resolution(self, *, rebuild: bool = False) -> None:
        """Drop the cached catalog lookups and invalidate the device view — call after new .knxprod
        products were imported (so unresolved apps now resolve) or after a UI-language change (so
        device labels re-parse in the new language).

        With ``rebuild=True`` the device views are rebuilt *now*, while the lock is held — use this
        from a worker thread (behind the progress modal) so the slow re-parse doesn't freeze the UI
        thread on the next frame. Holds the shared lock across clear + rebuild so per-frame UI reads
        (which acquire it non-blocking and bail otherwise) never see a half-cleared cache or trigger
        the rebuild themselves. The lock is re-entrant, so the ``devices`` read below still works."""
        with self._io_lock:
            self._app_cache.clear()
            self._bump()
            if rebuild:
                _ = (
                    self.devices
                )  # rebuild under the held lock instead of lazily on the UI thread

    # --- application update ("Update Application Program") -------------

    def newer_application_version(self, device: Device) -> int | None:
        """The newest application version available online for this device that is newer than the
        one it currently runs, or ``None``. Read-only, from the cached online index (empty until the
        online catalog has been fetched)."""
        if self._pid is None:
            return None
        parsed = parse_app_id(device.app.id)
        if parsed is None:
            return None
        info = self._svc.device(self._pid, device.node_id)
        newer = [
            item.application_version
            for item in self._catalog.online_products_for_order(info.order_number)
            if item.application_version is not None
            and item.application_version > parsed.version
        ]
        return max(newer) if newer else None

    def update_application(self, device: Device) -> UpdateApplicationResult | None:
        """Update ``device`` to the newest available version of the *same* application program,
        keeping parameter values and group-address links. Imports the newer ``.knxprod`` from the
        online catalog when it is not already local, then repoints the device and re-maps its refs
        (see :class:`~xknxeditor.proj.core.events.UpdateDeviceApplication`). Returns the outcome, or
        ``None`` when no newer version is available or it cannot be resolved."""
        from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

        if self._pid is None:
            return None
        parsed = parse_app_id(device.app.id)
        if parsed is None:
            return None
        info = self._svc.device(self._pid, device.node_id)
        candidates = [
            item
            for item in self._catalog.online_products_for_order(info.order_number)
            if item.application_version is not None
            and item.application_version > parsed.version
        ]
        if not candidates:
            return None
        target = max(candidates, key=lambda item: item.application_version or 0)
        target_version = target.application_version
        assert target_version is not None

        product = self._resolve_product_for_version(
            parsed.manufacturer_id, parsed.application_number, target_version, target.id
        )
        if product is None or product.application_id is None:
            self._log.warning(
                "update application: target product not resolvable",
                device=device.node_id,
                order=info.order_number,
                version=target_version,
            )
            return None
        new_app = self._catalog.get_application(product.application_id)
        if new_app is None:
            return None
        indexer = ApplicationIndexer(new_app.program)
        valid = list(set(indexer.parameter_refs) | set(indexer.com_object_refs))
        kept, dropped = self._svc.update_device_application(
            self._pid,
            device.node_id,
            product_ref_id=product.product_ref_id,
            hardware2program_ref_id=product.hardware2program_ref_id,
            old_app_id=device.app.id,
            new_app_id=product.application_id,
            valid_ref_ids=valid,
            order_number=product.order_number,
            product_name=product.name,
            manufacturer_name=product.manufacturer_name,
        )
        self._app_cache.clear()
        self._bump()
        updated = self.find_device_by_node_id(device.node_id)
        if updated is not None:
            self.recalculate_params(
                updated.node_id, [p.ref_id for p in updated.parameter_instance_refs]
            )
        self._log.info(
            "application updated",
            device=device.node_id,
            to_version=target_version,
            kept=kept,
            dropped=dropped,
        )
        return UpdateApplicationResult(
            new_version=target_version, kept=kept, dropped=dropped
        )

    def _resolve_product_for_version(
        self,
        manufacturer_id: str,
        application_number: int,
        version: int,
        catalog_item_id: str,
    ) -> "ProductSummary | None":
        """The catalog product for an exact application version, downloading+importing the online
        ``.knxprod`` first when it is not present locally."""
        products = self._catalog.find_products_for_application(
            manufacturer_id=manufacturer_id,
            application_number=application_number,
            application_version=version,
        )
        if not products:
            self._catalog.download_online_products([catalog_item_id])
            self._app_cache.clear()
            products = self._catalog.find_products_for_application(
                manufacturer_id=manufacturer_id,
                application_number=application_number,
                application_version=version,
            )
        return products[0] if products else None

    def find_or_create_segment_for_address(self, individual_address: str) -> int | None:
        """Return the segment id for an address' area/line, creating them if absent.

        A fresh project only has area/line ``0.0``; recovering a device at e.g.
        ``1.1.5`` needs that area and line to exist first, otherwise setting the
        address silently fails. Parses ``area.line.device`` and ensures the area
        and line (which comes with a segment) exist, returning that segment's id."""
        if self._pid is None:
            return None
        try:
            area_num, line_num, _device = (
                int(part) for part in individual_address.split(".")
            )
        except ValueError:
            return None
        topo = self._svc.topology(self._pid, _INSTALLATION)
        area = next((a for a in topo.areas if a.address == area_num), None)
        if area is None:
            self._svc.create_area(self._pid, _INSTALLATION, area_num, "")
            topo = self._svc.topology(self._pid, _INSTALLATION)
            area = next((a for a in topo.areas if a.address == area_num), None)
        if area is None:
            return None
        line = next((line for line in area.lines if line.address == line_num), None)
        if line is None:
            self._svc.create_line(self._pid, area.id, line_num, "")
            topo = self._svc.topology(self._pid, _INSTALLATION)
            area = next((a for a in topo.areas if a.address == area_num), None)
            line = (
                next((line for line in area.lines if line.address == line_num), None)
                if area is not None
                else None
            )
        if line is None or not line.segments:
            return None
        self._bump()
        return line.segments[0].id

    def add_device(
        self,
        product_ref_id: str,
        hardware2program_ref_id: str | None,
        name: str,
        app: Application,
        *,
        segment_id: int | None = None,
        address: int | None = None,
        parameters: list[tuple[str, str]] | None = None,
        product: DeviceProduct | None = None,
    ) -> int | None:
        if self._pid is None:
            return None
        product = product or DeviceProduct()
        if segment_id is None:
            segment_id = self._default_device_segment_id()
        if address is None:
            # Assign the next free individual address on the target line (standard behaviour), instead of
            # leaving the device address-less. Editable afterwards in Configure. If the line is full,
            # fall back to no address rather than failing the add.
            try:
                address = self._svc.next_free_individual_address_for_segment(
                    self._pid, segment_id
                )
            except ValueError:
                address = None
        pirs = _parameter_instance_refs(parameters)
        # A module-scoped parameter override (…_M-100_MI-1_P-…) only routes into its module scope
        # once that scope has been materialized — building with parameter_instance_refs alone leaves
        # module channels on their raw application default, so the override is silently ignored and
        # the persisted com-object set is stale (its refs no longer exist once the override applies,
        # so _build_device prunes those channels on reload). Discover the module instances from a
        # first build, then rebuild with them so overrides take effect and the persisted set is
        # symmetric with _build_device. Non-module devices see an empty list and skip the second pass.
        probe = Device(
            node_id=0,
            name=name,
            app=app,
            individual_address="",
            parameter_instance_refs=pirs,
        )
        module_instances = probe.get_module_instances()
        if module_instances and parameters:
            from xknxeditor.namespaces.intermediate.module_instance_t import (
                ModuleInstance as _MI,
            )

            # Drop the probe's heavy DynamicUI before building the second one, so peak memory holds
            # one evaluator, not two.
            probe.release_dynamic_ui()
            init_device = Device(
                node_id=0,
                name=name,
                app=app,
                individual_address="",
                parameter_instance_refs=pirs,
                module_instances=[
                    _MI(id=instance_id, ref_id=ref_id)
                    for instance_id, ref_id in module_instances
                ],
            )
            module_instances = init_device.get_module_instances()
        else:
            init_device = probe
        com_objects: list[tuple[str, str | None]] = [
            (co.id, None) for co in init_device.com_objects
        ]
        device_id = self._svc.add_device(
            self._pid,
            segment_id,
            product_ref_id,
            name=name,
            address=address,
            hardware2program_ref_id=hardware2program_ref_id,
            parameters=parameters or None,
            com_objects=com_objects,
            module_instances=module_instances if module_instances else None,
            product_name=product.product_name,
            hardware_name=product.hardware_name,
            order_number=product.order_number,
            manufacturer_name=product.manufacturer_name,
        )
        if hardware2program_ref_id is not None:
            self._app_cache[hardware2program_ref_id] = app
        self._bump()
        return device_id

    def set_param(self, device: Device, param_id: str, value: str) -> None:
        """A user edit of one parameter; raises ``ValueError`` when it is rejected."""
        if self._pid is None:
            return
        self._log.debug(
            "param clicked",
            device=device.node_id,
            param=param_id,
            old=device.get_param_value(param_id),
            new=value,
        )
        try:
            self.edit_params(device, [(param_id, value)], mode="edit")
        except ValueError as exc:
            device.param_errors[param_id] = _rejection_text(exc)
            device.param_inputs[param_id] = value
            raise
        device.param_errors.pop(param_id, None)
        device.param_inputs.pop(param_id, None)
        self._retry_rejected_inputs(device)
        self._log_param_tree(device, param_id)

    def _retry_rejected_inputs(self, device: Device) -> None:
        """Submit the inputs still shown in rejected fields again, as after every edit."""
        for ref_id, value in list(device.param_inputs.items()):
            try:
                self.edit_params(device, [(ref_id, value)], mode="edit")
            except ValueError as exc:
                device.param_errors[ref_id] = _rejection_text(exc)
                continue
            device.param_errors.pop(ref_id, None)
            device.param_inputs.pop(ref_id, None)

    def param_error(self, node_id: int, param_id: str) -> str | None:
        """The message of the last rejected edit of a parameter, until it is edited again."""
        device = self.find_device_by_node_id(node_id)
        return None if device is None else device.param_errors.get(param_id)

    def edit_params(
        self,
        device: Device,
        edits: list[tuple[str, str]],
        *,
        mode: ParamMode = "edit",
        label: str | None = None,
        skip_invalid: bool = False,
    ) -> "ChangeSet":
        """Apply parameter edits to a live device and persist them, including calculated values
        and the com-object changes they cause, as one undo step."""
        if self._pid is None:
            return {}
        old_active = (
            device.active_parameter_driven_com_object_ref_ids()
            if self._co_reconcile_enabled
            else set[str]()
        )
        if mode == "raw":
            changes: ChangeSet = {
                ref_id: (device.get_param_value(ref_id), value)
                for ref_id, value in edits
            }
            device.apply_param_values(dict(edits))
        else:
            changes = device.change_param_values(
                edits, validate=mode == "edit", skip_invalid=skip_invalid
            )
        self._persist_param_changes(device, changes, old_active, label=label)
        return changes

    def recalculate_params(self, node_id: int, ref_ids: list[str]) -> "ChangeSet":
        """Run and persist the calculations depending on ``ref_ids``."""
        device = self.find_device_by_node_id(node_id)
        if self._pid is None or device is None:
            return {}
        old_active = (
            device.active_parameter_driven_com_object_ref_ids()
            if self._co_reconcile_enabled
            else set[str]()
        )
        changes = device.recalculate_params(ref_ids)
        self._persist_param_changes(device, changes, old_active)
        return changes

    def parameter_driven_com_objects(self, device: Device) -> set[str]:
        """The active com-object set a later :meth:`commit_param_changes` diffs against."""
        if not self._co_reconcile_enabled:
            return set[str]()
        return device.active_parameter_driven_com_object_ref_ids()

    def persist_script_changes(
        self,
        node_id: int,
        changes: "ChangeSet",
        label: str | None,
        *,
        generation: int | None = None,
    ) -> None:
        """Store values a running parameter script already applied to its locked device.

        ``generation`` is the project identity captured when the operation started; a mismatch means
        the project was closed or replaced while the operation ran, so the write is dropped rather
        than applied to the wrong project."""
        if generation is not None and generation != self._generation:
            self._log.warning(
                "dropping script changes for a project that is no longer open",
                node_id=node_id,
            )
            return
        values = [(ref, new) for ref, (_, new) in changes.items() if new is not None]
        if self._pid is None or not values:
            return
        self._svc.set_parameters(self._pid, node_id, values, label=label)
        self._bump(structural=False)

    def finish_script_changes(
        self, node_id: int, old_active: set[str], *, generation: int | None = None
    ) -> None:
        """After a parameter script: reconcile com-objects and rebuild the device from the project."""
        if generation is not None and generation != self._generation:
            return
        device = self.find_device_by_node_id(node_id)
        if self._pid is None or device is None:
            return
        target = self._com_object_target(device, old_active)
        if target is not None:
            self._svc.set_parameters(
                self._pid,
                node_id,
                [],
                com_object_target=[(r, None) for r in sorted(target)],
                app_program_id=device.app.program.id,
            )
        self._refresh_device(node_id)
        self._bump(structural=False)

    def commit_param_changes(
        self,
        device: Device,
        changes: "ChangeSet",
        old_active: set[str],
        *,
        label: str | None = None,
    ) -> None:
        """Persist changes already applied to the live device as one undo step."""
        self._persist_param_changes(device, changes, old_active, label=label)

    def _persist_param_changes(
        self,
        device: Device,
        changes: "ChangeSet",
        old_active: set[str],
        *,
        label: str | None = None,
    ) -> None:
        values = [(ref, new) for ref, (_, new) in changes.items() if new is not None]
        if self._pid is None or not values:
            return
        target = self._com_object_target(device, old_active)
        self._svc.set_parameters(
            self._pid,
            device.node_id,
            values,
            com_object_target=None
            if target is None
            else [(r, None) for r in sorted(target)],
            app_program_id=device.app.program.id,
            label=label,
        )
        if target is not None:
            self._refresh_device(device.node_id)
        self._bump(structural=False)

    def _log_param_tree(self, device: Device, param_id: str) -> None:
        """Debug: dump the (pruned) parameter tree the device now renders — total parameter count and
        the top-level sections with their parameter counts — so a parameter edit's effect on the tree
        (e.g. a section appearing/disappearing) is visible in the log."""
        from xknxeditor.prod.parser_v2.ui import (
            UiComObject,
            UiParameter,
            UiParameterBlock,
            UiTab,
        )

        def count(nodes: list[Any]) -> tuple[int, int]:
            params = cos = 0
            stack: list[Any] = list(nodes)
            while stack:
                n = stack.pop()
                if isinstance(n, UiParameter):
                    params += 1
                elif isinstance(n, UiComObject):
                    cos += 1
                elif isinstance(n, (UiTab, UiParameterBlock)):
                    stack.extend(n.children)
            return params, cos

        ui = device.get_ui()
        total_params, total_cos = count(list(ui))
        sections: list[str] = []
        for node in ui:
            children: list[Any] = (
                list(node.children) if isinstance(node, UiTab) else [node]
            )
            for child in children:
                if isinstance(child, (UiTab, UiParameterBlock)):
                    # Match the renderer, which shows text first (parameter_widgets.py) — so the log
                    # reflects the actual section header the user sees.
                    name = getattr(child, "text", None) or getattr(child, "name", None)
                    if name:
                        p, _ = count([child])
                        sections.append(f"{name}({p})")
        self._log.debug(
            "param tree",
            device=device.node_id,
            param=param_id,
            params=total_params,
            com_objects=total_cos,
            sections=sections[:40],
        )

    def _com_object_target(
        self, device: Device, old_active: set[str]
    ) -> set[str] | None:
        """The com-object set after a parameter change, or ``None`` when it is unchanged.

        Reconcile on the DELTA of the parameter-driven active set (chain-AND) across the change —
        never the absolute set: ADD objects that became active (``(new_active - old_active) -
        current``), REMOVE objects that became inactive (``(old_active - new_active) & current``).
        Using the delta cancels any pre-existing mismatch between the configured set and the parser's
        derivation, so an unrelated edit touches nothing."""
        if not self._co_reconcile_enabled:
            return None
        new_active = device.active_parameter_driven_com_object_ref_ids()
        current = {co.id for co in device.com_objects}
        add = (new_active - old_active) - current
        remove = (old_active - new_active) & current
        target = (current - remove) | add
        if target == current:
            return None
        self._log.info(
            "sync com-objects",
            device=device.node_id,
            added=sorted(add),
            removed=sorted(remove),
            target=len(target),
        )
        return target

    def set_param_on_matching(self, device: Device, param_id: str, value: str) -> int:
        """Set a parameter on every device running the same application (multi-fill).

        Same-application devices share parameter ref-ids (they come from the one application
        program), so the same ``param_id`` applies. Returns how many devices were changed."""
        if self._pid is None:
            return 0
        app_id = getattr(device.app, "id", None)
        targets = [
            d.node_id for d in self.devices if getattr(d.app, "id", None) == app_id
        ]
        return self.set_param_on_selected(targets, param_id, value)

    def set_param_on_selected(
        self, node_ids: list[int], param_id: str, value: str
    ) -> int:
        """Set a parameter on each of the given devices (multi-device edit over a chosen
        subset). Same as :meth:`set_param` per device; returns how many were changed."""
        if self._pid is None:
            return 0
        count = 0
        for nid in node_ids:
            d = self.find_device_by_node_id(nid)
            if d is None:
                continue
            try:
                self.set_param(d, param_id, value)
            except (KeyError, ValueError) as exc:
                self._log.warning(
                    "multi-edit parameter rejected",
                    device=nid,
                    param=param_id,
                    error=str(exc),
                )
                continue
            count += 1
        return count

    def set_device_name(self, node_id: int, old_name: str, new_name: str) -> None:
        if self._pid is None or old_name == new_name:
            return
        self._svc.set_device_name(self._pid, node_id, new_name)
        # In place: update the live device's name and bump non-structurally (a rename changes no
        # device structure/topology), instead of rebuilding every device's dynamic UI.
        dev = self.find_device_by_node_id(node_id)
        if dev is not None:
            dev.name = new_name
        self._bump(structural=False)

    def set_device_description(
        self, node_id: int, old_description: str, new_description: str
    ) -> None:
        if self._pid is None or old_description == new_description:
            return
        self._svc.set_device_description(self._pid, node_id, new_description)
        # In place, like a rename: description is metadata only, no structure/topology change.
        dev = self.find_device_by_node_id(node_id)
        if dev is not None:
            dev.description = new_description
        self._bump(structural=False)

    def remove_device(self, node_id: int) -> None:
        """Delete a device (and its com-objects/links) from the project."""
        if self._pid is None:
            return
        self._svc.remove_device(self._pid, node_id)
        self._bump()

    def clone_device(
        self,
        node_id: int,
        count: int = 1,
        *,
        include_params: bool = True,
        include_links: bool = False,
    ) -> list[int]:
        """Create ``count`` copies of a device.

        Copies keep the source product/application; parameter values are carried over when
        ``include_params`` (default), and group-address links when ``include_links``. The individual
        address is left unset (the copies land in the first segment) so the user assigns fresh
        addresses. Returns the new device node ids."""
        if self._pid is None:
            return []
        row = next((r for r in self._svc.devices(self._pid) if r.id == node_id), None)
        if row is None:
            return []
        app = self._resolve_app(row.hardware2program_ref_id)
        if app is None:
            self._log.warning("cannot clone: application not resolved", device=node_id)
            return []
        params = (
            [(p.ref_id, p.value) for p in row.parameters] if include_params else None
        )
        source = self.find_device_by_node_id(node_id) if include_links else None
        source_links = self._capture_links(source) if source is not None else []
        created: list[int] = []
        for i in range(max(1, count)):
            suffix = " (copy)" if count == 1 else f" (copy {i + 1})"
            new_id = self.add_device(
                row.product_ref_id,
                row.hardware2program_ref_id,
                f"{row.name}{suffix}" if row.name else "",
                app,
                parameters=params,
                product=DeviceProduct(
                    product_name=row.product_name,
                    hardware_name=row.hardware_name,
                    order_number=row.order_number,
                    manufacturer_name=row.manufacturer_name,
                ),
            )
            if new_id is not None:
                if source_links:
                    new_dev = self.find_device_by_node_id(new_id)
                    if new_dev is not None:
                        self._apply_links(new_dev, source_links, replace_matching=False)
                created.append(new_id)
        self._log.info("device cloned", source=node_id, copies=len(created))
        return created

    def copy_device_config(self, node_id: int) -> DeviceConfigClipboard | None:
        """Snapshot a device's parameter values and group-address links for pasting onto another
        device of the same application."""
        if self._pid is None:
            return None
        device = self.find_device_by_node_id(node_id)
        if device is None:
            return None
        app_id = getattr(device.app, "id", None)
        if not app_id:
            return None
        row = next((r for r in self._svc.devices(self._pid) if r.id == node_id), None)
        params = [(p.ref_id, p.value) for p in row.parameters] if row else []
        return DeviceConfigClipboard(
            app_id=app_id,
            source_label=device.name or getattr(device.app, "name", "") or "",
            params=params,
            links=self._capture_links(device),
        )

    def paste_device_config(
        self,
        node_id: int,
        clip: DeviceConfigClipboard,
        *,
        include_params: bool = True,
        include_links: bool = True,
    ) -> tuple[bool, int, int]:
        """Apply a copied configuration onto a device running the same application. Returns
        (ok, params_changed, links_mapped); ``ok`` is False when the target's application differs."""
        if self._pid is None:
            return (False, 0, 0)
        device = self.find_device_by_node_id(node_id)
        if device is None or getattr(device.app, "id", None) != clip.app_id:
            return (False, 0, 0)
        params_changed = (
            self._apply_params(node_id, clip.params) if include_params else 0
        )
        links_mapped = 0
        if include_links:
            device = self.find_device_by_node_id(node_id)
            if device is not None:
                links_mapped = self._apply_links(
                    device, clip.links, replace_matching=True
                )
        self._bump(structural=False)
        self._log.info(
            "paste device config",
            target=node_id,
            params=params_changed,
            links=links_mapped,
        )
        return (True, params_changed, links_mapped)

    def _capture_links(self, device: Device) -> list[tuple[int, str, int, bool]]:
        """Snapshot a device's group-address links as
        (com_object_number, com_object_size, group_address_id, is_sending)."""
        out: list[tuple[int, str, int, bool]] = []
        for co in device.get_visible_com_objects():
            if co.db_id is None:
                continue
            for link in self.get_links_for_com_object(co.db_id):
                out.append(
                    (co.number, co.object_size, link.group_address_id, link.is_sending)
                )
        return out

    def _apply_links(
        self,
        device: Device,
        links: list[tuple[int, str, int, bool]],
        *,
        replace_matching: bool,
    ) -> int:
        """Attach captured links to a device's com-objects, matched by number (guarded by object
        size). When ``replace_matching``, a matched com-object's existing links are removed first.
        Returns how many com-objects were (re)linked."""
        by_number = {
            co.number: co
            for co in device.get_visible_com_objects()
            if co.db_id is not None
        }
        grouped: dict[int, tuple[str, list[tuple[int, bool]]]] = {}
        for number, size, ga_id, is_sending in links:
            grouped.setdefault(number, (size, []))[1].append((ga_id, is_sending))
        mapped = 0
        for number, (size, gas) in grouped.items():
            nco = by_number.get(number)
            if nco is None or nco.db_id is None:
                continue
            if size and nco.object_size and size != nco.object_size:
                continue
            if replace_matching:
                for assignment in self.get_links_for_com_object(nco.db_id):
                    self.unlink_com_object_from_ga(assignment.id)
            for ga_id, is_sending in gas:
                self.link_com_object_to_ga(nco.db_id, ga_id, is_sending=is_sending)
            mapped += 1
        return mapped

    def _apply_params(self, node_id: int, params: list[tuple[str, str]]) -> int:
        """Transfer (ref_id, value) overrides to a single device: calculations run, validations do
        not, and values the device rejects are skipped. Returns the number of values changed."""
        device = self.find_device_by_node_id(node_id)
        if device is None:
            return 0
        edits = [
            (ref, value)
            for ref, value in params
            if device.get_param_value(ref) != value
        ]
        changes = self.edit_params(device, edits, mode="transfer", skip_invalid=True)
        return sum(1 for ref, _ in edits if ref in changes)

    def set_device_individual_address(
        self, node_id: int, old_address: str, new_address: str
    ) -> bool:
        """Persist a new individual address. Returns ``True`` on success, ``False`` when rejected
        (e.g. the address is already used) so the caller can avoid mutating its live device object
        with an address the project never accepted."""
        if self._pid is None or old_address == new_address:
            return False
        try:
            self._svc.set_individual_address(self._pid, node_id, new_address)
        except (KeyError, ValueError) as e:
            self._log.warning(
                "could not set individual address", address=new_address, error=str(e)
            )
            return False
        self._bump()
        return True

    def set_flag(self, device: Device, co_id: str, flag_name: str, value: bool) -> None:
        if self._pid is None:
            return
        co = device.find_com_object(co_id)
        column = _FLAG_COLUMNS.get(flag_name)
        if co is None or co.db_id is None or column is None:
            return
        self._svc.set_com_object_flag(self._pid, co.db_id, column, value)
        # In place instead of a full rebuild (which would reset the Configure panel's tree/edit
        # state): reflect on the live com-object and push the com-object's full override set into the
        # live dynamic UI — exactly what a rebuild reconstructs from the DB — then bump non-structurally
        # so revision-keyed panels (Health/Cockpit) refresh.
        setattr(co.flags, flag_name, value)
        device.edited_in_place = True
        row = self._find_com_object_row(co.db_id)
        if row is not None:
            coir = _co_instance_ref_from_row(row, ref_id=co_id) or ComObjectInstanceRef(
                ref_id=co_id
            )
            device.set_com_obj_instance_ref(co_id, coir)
        self._bump(structural=False)

    def _find_com_object_row(self, co_db_id: int) -> Any | None:
        """The core ComObject ORM row for ``co_db_id`` (to rebuild its instance-ref overrides)."""
        if self._pid is None:
            return None
        for d in self._svc.devices(self._pid):
            for c in d.com_objects:
                if c.id == co_db_id:
                    return c
        return None

    def create_area(self, area_number: int, name: str = "") -> int | None:
        if self._pid is None:
            return None
        area_id = self._svc.create_area(self._pid, _INSTALLATION, area_number, name)
        self._bump()
        return area_id

    def remove_area(self, area_id: int, area_number: int = 0, name: str = "") -> None:
        if self._pid is None:
            return
        self._svc.remove_area(self._pid, area_id)
        self._bump()

    def rename_area(self, area_id: int, old_name: str, new_name: str) -> None:
        if self._pid is None or old_name == new_name:
            return
        self._svc.rename_area(self._pid, area_id, new_name)
        self._bump()

    def create_line(self, area_id: int, line_number: int, name: str = "") -> int | None:
        if self._pid is None:
            return None
        line_id = self._svc.create_line(self._pid, area_id, line_number, name)
        self._bump()
        return line_id

    def remove_line(
        self, line_id: int, area_id: int = 0, line_number: int = 0, name: str = ""
    ) -> None:
        if self._pid is None:
            return
        self._svc.remove_line(self._pid, line_id)
        self._bump()

    def rename_line(self, line_id: int, old_name: str, new_name: str) -> None:
        if self._pid is None or old_name == new_name:
            return
        self._svc.rename_line(self._pid, line_id, new_name)
        self._bump()

    @property
    @io_guarded(lambda: GroupAddressStyle.THREE_LEVEL)
    def group_address_style(self) -> GroupAddressStyle:
        """The project's group-address style (three-level, two-level, free).

        Read every frame by several panels, so it is IO-guarded: during a background import (which
        rewrites the schema on a worker thread) it returns the default instead of querying the DB
        mid-DDL, which would raise "malformed database schema"."""
        if self._pid is None:
            return GroupAddressStyle.THREE_LEVEL
        return GroupAddressStyle(self._svc.project(self._pid).group_address_style)

    def create_group_address(
        self, address: str | None = None, name: str = ""
    ) -> int | None:
        """Create a group address. ``address`` is a style-formatted string (e.g. ``"1/2/3"``); when
        omitted, the next free address is allocated. Returns ``None`` on an invalid address."""
        if self._pid is None:
            return None
        if address:
            style = GroupAddressStyle(self._svc.project(self._pid).group_address_style)
            try:
                value = parse_ga(address, style)
            except (ValueError, IndexError):
                self._log.warning("invalid group address", address=address)
                return None
        else:
            value = self._svc.next_free_group_address(self._pid, _INSTALLATION)
        ga_id = self._svc.create_group_address(self._pid, _INSTALLATION, value, name)
        self._bump(structural=False)
        return ga_id

    def create_group_address_value(self, value: int, name: str = "") -> int | None:
        """Return the id of the group address with this raw value, creating it if absent.

        Idempotent by value so recovering into a project that already contains a
        group address does not create duplicate rows (as a plain create would)."""
        if self._pid is None:
            return None
        existing = next(
            (
                ga.id
                for ga in self._svc.group_addresses(self._pid)
                if ga.address == value
            ),
            None,
        )
        if existing is not None:
            return existing
        ga_id = self._svc.create_group_address(self._pid, _INSTALLATION, value, name)
        self._bump(structural=False)
        return ga_id

    def rename_group_address(self, ga_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_group_address(self._pid, ga_id, name)
        self._bump(structural=False)

    def set_group_address_dpt(self, ga_id: int, dpt: str | None) -> None:
        if self._pid is None:
            return
        self._svc.set_group_address_datapoint_type(self._pid, ga_id, dpt or None)
        self._bump(structural=False)

    def remove_group_address(
        self, ga_id: int, address: str = "", name: str = ""
    ) -> None:
        if self._pid is None:
            return
        self._svc.remove_group_address(self._pid, ga_id)
        self._bump(structural=False)

    def create_group_range(self, parent_id: int | None, name: str) -> int | None:
        """Create an empty group-range folder (main group when ``parent_id`` is ``None``, else a
        middle group under it). Returns ``None`` when the style has no such folder or it is full."""
        if self._pid is None:
            return None
        rid = self._svc.create_group_range(self._pid, _INSTALLATION, parent_id, name)
        self._bump(structural=False)
        return rid

    def rename_group_range(self, range_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_group_range(self._pid, range_id, name)
        self._bump(structural=False)

    def remove_group_range(self, range_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_group_range(self._pid, range_id)
        self._bump(structural=False)

    def link_com_object_to_ga(
        self, com_object_id: int, group_address_id: int, is_sending: bool = False
    ) -> int | None:
        if self._pid is None:
            return None
        derive = self._dpt_to_derive_on_link(com_object_id, group_address_id)
        link_id = self._svc.link_com_object(
            self._pid,
            com_object_id,
            group_address_id,
            sending=is_sending,
            derive_datapoint_type=derive,
        )
        self._bump(structural=False)
        return link_id

    def _com_object_dpt_token(self, co_db_id: int) -> str | None:
        """The ETS DPT token of a com-object (resolved via the cached device view-models)."""
        for d in self.devices:
            for co in d.com_objects:
                if co.db_id == co_db_id:
                    return co.dpt.token
        return None

    def _dpt_to_derive_on_link(
        self, com_object_id: int, group_address_id: int
    ) -> str | None:
        """DPT token to type the group address with on link, as ETS does, or ``None`` to leave it.

        Only an empty group-address DPT is filled; a differing existing one is kept (and logged),
        never overwritten.
        """
        token = self._com_object_dpt_token(com_object_id)
        if not token:
            return None
        ga = self.get_group_address(group_address_id)
        if ga is None:
            return None
        if not ga.datapoint_type:
            return token
        if ga.datapoint_type != token:
            self._log.warning(
                "linked com-object DPT differs from group address DPT; keeping existing",
                group_address=ga.address,
                existing=ga.datapoint_type,
                com_object=token,
            )
        return None

    def unlink_com_object_from_ga(
        self,
        assignment_id: int,
        com_object_id: int = 0,
        group_address_id: int = 0,
        is_sending: bool = False,
    ) -> None:
        if self._pid is None:
            return
        self._svc.unlink_com_object(self._pid, assignment_id)
        self._bump(structural=False)

    def create_function(
        self, space_id: int, function_type: str, name: str
    ) -> int | None:
        if self._pid is None:
            return None
        fid = self._svc.create_function(self._pid, space_id, function_type, name)
        self._bump(structural=False)
        return fid

    def remove_function(self, function_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_function(self._pid, function_id)
        self._bump(structural=False)

    def rename_function(self, function_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_function(self._pid, function_id, name)
        self._bump(structural=False)

    def set_function_type(self, function_id: int, function_type: str) -> None:
        if self._pid is None:
            return
        self._svc.set_function_type(self._pid, function_id, function_type)
        self._bump(structural=False)

    def add_function_group_address(
        self, function_id: int, group_address_id: int, role: str = ""
    ) -> int | None:
        if self._pid is None:
            return None
        link_id = self._svc.add_function_group_address(
            self._pid, function_id, group_address_id, role
        )
        self._bump(structural=False)
        return link_id

    def remove_function_group_address(self, link_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_function_group_address(self._pid, link_id)
        self._bump(structural=False)

    @io_guarded(list)
    @revision_cached
    def get_unassigned_devices(self) -> "list[SpaceDeviceInfo]":
        # Per-frame read (Spaces panel "Without space" section): guard it like the other tree reads
        # so it bails to an empty list while a background import holds the IO lock and rewrites the
        # schema — an unguarded query races the DDL and SQLite raises "malformed database schema".
        if self._pid is None:
            return []
        return self._svc.unassigned_devices(self._pid, _INSTALLATION)

    def create_space(
        self, parent_id: int | None, space_type: str, name: str
    ) -> int | None:
        if self._pid is None:
            return None
        sid = self._svc.create_space(
            self._pid, _INSTALLATION, space_type, name, parent_id
        )
        self._bump(structural=False)
        return sid

    def rename_space(self, space_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_space(self._pid, space_id, name)
        self._bump(structural=False)

    def set_space_type(self, space_id: int, space_type: str) -> None:
        if self._pid is None:
            return
        self._svc.set_space_type(self._pid, space_id, space_type)
        self._bump(structural=False)

    def move_space(self, space_id: int, new_parent_id: int | None) -> None:
        if self._pid is None:
            return
        self._svc.move_space(self._pid, space_id, new_parent_id)
        self._bump(structural=False)

    def remove_space(self, space_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_space(self._pid, space_id)
        self._bump(structural=False)

    def set_device_space(self, device_id: int, space_id: int | None) -> None:
        if self._pid is None:
            return
        self._svc.set_device_space(self._pid, device_id, space_id)
        self._bump(structural=False)

    def undo(self) -> bool:
        if self._pid is None:
            return False
        peek = self._svc.peek_undo(self._pid)
        result = self._svc.undo(self._pid)
        if result:
            self._refresh_after_history(peek, undo=True)
        return result

    def redo(self) -> bool:
        if self._pid is None:
            return False
        peek = self._svc.peek_redo(self._pid)
        result = self._svc.redo(self._pid)
        if result:
            self._refresh_after_history(peek, undo=False)
        return result

    def _refresh_after_history(
        self, peek: "tuple[str, dict[str, Any]] | None", *, undo: bool
    ) -> None:
        """Refresh views after an undo/redo. A plain parameter change is applied *in place* on the
        affected device (like a live edit); a composite param+com-object change rebuilds only that one
        device (its com-object rows changed in the DB) — both instant, instead of discarding and
        re-parsing every device's dynamic UI. Anything else falls back to a full structural rebuild."""
        if peek is not None and self._devices_cache is not None:
            event_type, data = peek
            if event_type == "SetParameter":
                value = data.get("old_value") if undo else data.get("value")
                device = self.find_device_by_node_id(int(data["device_id"]))
                if device is not None:
                    device.apply_param_values(
                        {str(data["ref_id"]): None if value is None else str(value)}
                    )
                    self._bump(structural=False)
                    return
            elif event_type in ("Composite", "SyncDeviceComObjects"):
                node_id = _history_device_id(data)
                if node_id is not None:
                    self._refresh_device(node_id)  # rebuild only this device (not all)
                    self._bump(structural=False)
                    return
        self._bump(structural=True)

    @io_guarded(lambda: False)
    def can_undo(self) -> bool:
        return self._pid is not None and self._svc.can_undo(self._pid)

    @io_guarded(lambda: False)
    def can_redo(self) -> bool:
        return self._pid is not None and self._svc.can_redo(self._pid)

    @property
    @io_guarded(lambda: 0)
    def cursor(self) -> int:
        return self._svc.cursor(self._pid) if self._pid is not None else 0

    def jump_to(self, event_id: int) -> None:
        if self._pid is None:
            return
        self._svc.jump_to(self._pid, event_id)
        self._bump()

    @io_guarded(list)
    @revision_cached
    def history(self) -> list[HistoryEntry]:
        # The History panel reads this every frame. Cache it by revision like the other per-frame
        # panel reads (see get_device_info): an uncached 60 Hz SELECT contends with a background
        # writer's connection — during an export it blocks the render loop (the "not responding"
        # stall) and can raise "database is locked" straight out of the frame.
        if self._pid is None:
            return []
        return [
            HistoryEntry(
                id=entry.id,
                display_text=_history_label(entry.event_type, entry.data),
                reverted=entry.reverted,
            )
            for entry in self._svc.history(self._pid)
        ]

    def _history_key(self) -> frozenset[tuple[int, str, str]]:
        """Signature of the currently-effective (non-reverted) events.

        Includes the event type and a repr of its data, not just the id: a store that deletes
        events on branch (undo + new command) can reuse an id, which an id-only set would miss.

        Commissioning bookkeeping (recorded right after a successful program) is excluded: it
        never affects the generated image, so it must not count as "edited" and lock out the
        read-only pre-flight self-test on a device that was only re-programmed."""
        if self._pid is None:
            return frozenset()
        return frozenset(
            (entry.id, entry.event_type, repr(entry.data))
            for entry in self._svc.history(self._pid)
            if not entry.reverted and entry.event_type != "SetDeviceCommissioning"
        )

    @io_guarded(lambda: False)
    def edited_since_open(self) -> bool:
        """Whether the effective edit set changed since the project was opened.

        Opening a previously-edited project is not "modified"; only edits (or undo/redo) made in
        this session since the open count. Used to gate the read-only pre-flight self-test."""
        return self._history_key() != self._history_baseline
