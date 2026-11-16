"""Recover-plugin service: scan the bus, identify devices, read them back, add to a project.

Bus work (scan, identify, read-back) runs on the connection's asyncio loop via
``api.connection.run_async``; the resulting :class:`RecoverEntry` list is polled on
the UI thread. Writing recovered devices into a project happens on the UI thread
through :class:`ProjectService` (which is not thread-safe). All bus access is
read-only.
"""

from __future__ import annotations

import asyncio
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.recover.matching import (
    CatalogCandidate,
    OnlineVersion,
    catalog_candidates,
)
from xknxeditor.proj.core.medium import MediumError
from xknxeditor.recover import (
    AppId,
    RecoveryEffort,
    devices_from_snapshot,
    iter_addresses,
    probe_and_identify,
    recover_device_at,
    search_again,
    snapshots_json,
    validate_group_communication,
    verify_recovered,
)
from xknxeditor.recover.recover import com_object_ref_by_number, seed_dynamic_ui

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger, PluginAPI
    from xknxeditor.download.factory_image import FactoryImage
    from xknxeditor.prod import Application
    from xknxeditor.recover import LinkWarning, RecoveredDevice

# Pause between devices so the KNXnet/IP tunnel settles before the next connect.
_SETTLE_SECONDS = 0.15


@dataclass
class RecoverEntry:
    """One discovered device and its recovery progress."""

    address: str
    mask_version: int
    app_id: AppId | None = None
    product_ref_id: str | None = None
    hardware2program_ref_id: str | None = None
    product_name: str | None = None
    application: Application | None = None
    recovered: RecoveredDevice | None = None
    state: str = ""
    selected: bool = True
    # Multiple variants or a different application version require confirmation.
    ambiguous: bool = False
    candidates: int = 0
    catalog_candidates: list[CatalogCandidate] = field(
        default_factory=list[CatalogCandidate]
    )
    match_reason: str = ""
    product_mask_version: str | None = None
    compatibility_checked: bool = False
    online_versions: tuple[OnlineVersion, ...] = ()
    # Read-only round-trip result: bytes that would still change (0 == verified).
    verify_changed: int | None = None
    # True once this device has been written into the open project (so it is not
    # added twice by live auto-apply and a later explicit apply).
    applied: bool = False
    # True once live auto-apply has tried this device: a failed one is not retried every
    # frame, only by an explicit add.
    auto_tried: bool = False
    # Why the last attempt to add it to a project failed, shown in its details.
    error: str | None = None
    # The device this recovery added to the project, which a search again replaces.
    device_id: int | None = None

    @property
    def recoverable(self) -> bool:
        return self.application is not None

    @property
    def version_mismatch(self) -> bool:
        if self.app_id is None:
            return False
        layout_id = (
            self.recovered.application_id
            if self.recovered is not None
            else self.application.id
            if self.application is not None
            else ""
        )
        layout = _app_id(layout_id)
        return (
            layout is not None
            and layout.application_version != self.app_id.application_version
        )

    @property
    def other_versions_available(self) -> bool:
        return (
            bool(self.online_versions)
            and self.recovered is None
            and (not self.recoverable or self.version_mismatch)
        )


def _unknown_parts(unrecoverable: int, not_found: int) -> list[str]:
    """The parameters read without a value, as parts of a list of counts: those that cannot
    be recovered and those not found yet, each only when there are some."""
    from editor_gui.plugins.recover.strings import S

    parts: list[str] = []
    if unrecoverable:
        parts.append(
            S.PARAMETERS_UNRECOVERABLE(unrecoverable) % {"count": unrecoverable}
        )
    if not_found:
        parts.append(S.PARAMETERS_NOT_FOUND(not_found) % {"count": not_found})
    return parts


def _app_id(application_id: str) -> AppId | None:
    """The manufacturer, application number and version of an application id, as a device
    tells them (the rest of the id, a hash or the suffix of a converted product, is the
    catalog's)."""
    match = re.match(r"(M-[0-9A-F]{4})_A-([0-9A-F]+)-([0-9A-F]+)", application_id, re.I)
    if match is None:
        return None
    return AppId(
        match.group(1).upper(), int(match.group(2), 16), int(match.group(3), 16)
    )


def _incomplete(entry: RecoverEntry) -> bool:
    """Whether a selected entry was read back and its parameters do not fit what was read."""
    return (
        entry.selected
        and entry.application is not None
        and entry.recovered is not None
        and not entry.recovered.parameters.complete
    )


def _recovered_state(recovered: RecoveredDevice) -> str:
    """The state of a device read back: whether its parameters fit what was read off it."""
    from editor_gui.plugins.recover.strings import S

    if recovered.parameters.complete:
        return S.STATE_RECOVERED
    return S.STATE_INCOMPLETE


def _error_text(error: Exception) -> str:
    """The text of a failed add, as the editor shows it (a medium rule translated)."""
    from editor_gui.plugins.project.medium_text import medium_error_text

    if isinstance(error, MediumError):
        return medium_error_text(error)
    return str(error)


class RecoverService:
    """Drives bus scan, device identification, read-back and project assembly."""

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._log: Logger | None = None
        self.entries: list[RecoverEntry] = []
        self.phase: str = "idle"  # idle | scanning | scanned | recovering | recovered
        self.error: str | None = None
        self._pending: Future[Any] | None = None
        # Live scan progress (written from the bus thread, read each UI frame).
        self.scan_done = 0
        self.scan_total = 0
        self.scan_current = ""
        self.recover_done = 0
        self.recover_total = 0
        self.recover_current = ""
        self.recover_stage = ""
        # Online-catalog auto-fetch for devices missing from the local catalog.
        self.fetch_status = ""
        self.fetch_error = ""
        self._catalog_changed = False
        self._fetch_thread: threading.Thread | None = None
        # Outcome of the last add-to-project action, shown in the window.
        self.apply_status = ""
        # The devices the last add-to-project action could not add, shown in the window.
        self.apply_error = ""
        # Cooperative cancel flag for a running scan/recover/verify.
        self._cancel = False
        # Add recovered devices to the open project as soon as they are read.
        self.auto_apply = True
        # How many values the search for the settings a device keeps no copy of tries.
        self.effort = RecoveryEffort.MEDIUM
        # A search again in what was read (no bus), run on a worker thread.
        self._search: Future[list[tuple[RecoverEntry, RecoveredDevice]]] | None = None

    def set_logger(self, log: Logger) -> None:
        self._log = log

    # --- guards -----------------------------------------------------------

    def connected(self) -> bool:
        return self._api.connection.xknx is not None

    def has_project(self) -> bool:
        return self._api.project.is_open

    @property
    def busy(self) -> bool:
        # A bus operation OR the online-catalog fetch: both must block a new scan,
        # otherwise a fetch could re-identify a list a new scan has replaced.
        return (
            self._pending is not None
            or self._fetch_thread is not None
            or (self._search is not None and not self._search.done())
        )

    def _cancelled(self) -> bool:
        """Whether Stop was pressed: a running search ends with its result so far."""
        return self._cancel

    def stop(self) -> None:
        """Request the running scan/recover/verify to stop after the current device."""
        self._cancel = True

    # --- scan + identify --------------------------------------------------

    def start_scan(self, start: str, end: str) -> None:
        """Scan ``[start, end]`` and identify each responder against the catalog."""
        if self.busy or not self.connected():
            return
        # Take the single bus-operation slot so a program/test cannot run on the
        # same tunnel at the same time (and vice versa).
        if not self._api.connection.begin_operation("recover", f"{start}..{end}"):
            self.error = "A bus operation is already running"
            return
        self._cancel = False
        self.error = None
        self.entries = []
        self.fetch_error = ""
        self.scan_done = 0
        self.scan_total = 0
        self.scan_current = ""
        self.phase = "scanning"
        self._pending = self._api.connection.run_async(
            self._scan_and_identify(start, end)
        )
        if self._pending is None:
            self.phase = "idle"
            self._api.connection.end_operation()

    async def _scan_and_identify(self, start: str, end: str) -> list[RecoverEntry]:
        xknx = self._api.connection.xknx
        assert xknx is not None
        addresses = list(iter_addresses(start, end))
        self.scan_total = len(addresses)
        # Probe + read the application id over a SINGLE connection per device (via
        # probe_and_identify), then let the tunnel settle before the next address.
        # Reconnecting per read makes rapid scans return stale/truncated data, so
        # the same device would identify differently between runs.
        found: list[RecoverEntry] = []
        for done, address in enumerate(addresses, start=1):
            if self._cancel:
                break
            self.scan_current = str(address)
            self.scan_done = done
            device, app_id = await probe_and_identify(xknx, address)
            if device is None:
                continue
            entry = RecoverEntry(
                address=device.address, mask_version=device.mask_version
            )
            entry.app_id = app_id
            self._identify_catalog(entry)
            found.append(entry)
            # Publish a fresh list by reassignment (never append to the list the UI
            # is iterating) so the window shows devices as they are found without a
            # "list changed size during iteration" race on the UI thread.
            self.entries = list(found)
            await asyncio.sleep(_SETTLE_SECONDS)
            if self._log is not None:
                self._log.info(
                    "device found",
                    address=entry.address,
                    mask=f"{entry.mask_version:#06x}",
                    state=entry.state,
                )
        self.scan_current = ""
        return list(found)

    def _identify_catalog(
        self, entry: RecoverEntry, *, application_id: str | None = None
    ) -> None:
        """Match local layouts; different versions or multiple variants need confirmation."""
        from editor_gui.plugins.recover.strings import S

        entry.catalog_candidates = []
        entry.candidates = 0
        entry.application = None
        entry.product_ref_id = entry.hardware2program_ref_id = entry.product_name = None
        entry.product_mask_version = None
        entry.match_reason = ""
        entry.ambiguous = False
        entry.selected = False
        mask = f"MV-{entry.mask_version:04X}"
        master = self._api.connection.master
        entry.compatibility_checked = (
            master is not None and master.mask(mask) is not None
        )
        if entry.app_id is None:
            entry.state = S.STATE_UNPROGRAMMED
            return
        with self._api.catalog.io_lock:
            entry.catalog_candidates = catalog_candidates(
                self._api.catalog,
                entry.app_id,
                mask,
                master,
                application_id=application_id,
            )
            entry.candidates = len(entry.catalog_candidates)
            if not entry.catalog_candidates:
                entry.state = S.STATE_NO_APP
                return
            self._use_candidate(entry, entry.catalog_candidates[0])

    def _use_candidate(self, entry: RecoverEntry, candidate: CatalogCandidate) -> None:
        from editor_gui.plugins.recover.strings import S

        application = self._api.catalog.get_application(candidate.application_id)
        entry.application = application
        entry.product_ref_id = candidate.product_ref_id
        entry.hardware2program_ref_id = candidate.hardware2program_ref_id
        entry.product_name = candidate.product_name
        entry.product_mask_version = candidate.mask_version
        entry.match_reason = candidate.match_reason
        entry.ambiguous = len(
            entry.catalog_candidates
        ) > 1 or candidate.match_reason.startswith("different_version")
        entry.state = (
            S.STATE_NO_APP
            if application is None
            else S.STATE_AMBIGUOUS
            if entry.ambiguous
            else S.STATE_FOUND
        )
        entry.selected = application is not None and not entry.ambiguous

    def choose_candidate(self, address: str, candidate_index: int) -> None:
        """Choose a zero-based candidate before read-back; selection confirms the layout."""
        if self.busy:
            raise ValueError("recovery is still running")
        entry = next(
            (entry for entry in self.entries if entry.address == address), None
        )
        if entry is None:
            raise ValueError(f"not found by the scan: {address}")
        if entry.recovered is not None or entry.applied:
            raise ValueError(
                "scan again before changing the product of a recovered device"
            )
        if not 0 <= candidate_index < len(entry.catalog_candidates):
            raise ValueError("candidate index out of range")
        self._use_candidate(entry, entry.catalog_candidates[candidate_index])

    # --- read-back --------------------------------------------------------

    def start_recover(self) -> None:
        """Read back every selected, recoverable device over the bus."""
        if self.busy or not self.connected():
            return
        targets = [e for e in self.entries if e.selected and e.recoverable]
        if not targets:
            return
        if not self._api.connection.begin_operation("recover", "read-back"):
            self.error = "A bus operation is already running"
            return
        self._cancel = False
        self.apply_status = ""
        self.apply_error = ""
        self.recover_done = 0
        self.recover_total = len(targets)
        self.recover_current = ""
        self.phase = "recovering"
        self._pending = self._api.connection.run_async(self._recover(targets))
        if self._pending is None:
            self.phase = "scanned"
            self._api.connection.end_operation()

    async def _recover(self, targets: list[RecoverEntry]) -> list[RecoverEntry]:
        from editor_gui.plugins.recover.strings import S

        xknx = self._api.connection.xknx
        assert xknx is not None
        for done, entry in enumerate(targets, start=1):
            if self._cancel:
                break
            assert entry.application is not None
            self.recover_current = entry.address
            self.recover_done = done
            self.recover_stage = ""
            try:
                from editor_gui.plugin_providers import device_reader

                # A device configured through a manufacturer's plug-in keeps that
                # configuration of its own: it is read as well.
                reader = device_reader(entry.application)
                entry.recovered = await recover_device_at(
                    xknx,
                    entry.address,
                    entry.application,
                    native_read=reader.read_device if reader is not None else None,
                    interface_max_apdu_length=self._api.connection.interface_max_apdu_length,
                    progress=self._on_recover_stage,
                    project_code_page=self._api.project.project_code_page(),
                    effort=self.effort,
                    cancel=self._cancelled,
                    master=self._api.connection.master,
                    factory_image=self._factory_image(entry),
                )
                entry.recovered = replace(entry.recovered, observed_app_id=entry.app_id)
                entry.state = _recovered_state(entry.recovered)
                if self._log is not None:
                    self._log.info(
                        "device recovered",
                        address=entry.address,
                        group_addresses=len(entry.recovered.group_addresses),
                        links=len(entry.recovered.links),
                        parameters=len(entry.recovered.parameters.values),
                        unknown=len(entry.recovered.parameters.unknown),
                    )
            except Exception as exc:  # bus/read failure for one device: keep going
                entry.state = S.STATE_ERROR
                if self._log is not None:
                    self._log.warning(
                        "recover failed", address=entry.address, error=str(exc)
                    )
            # Let the tunnel settle between devices: back-to-back point-to-point
            # sessions over one connection can otherwise deliver a late telegram
            # into the next device's read and desync it.
            await asyncio.sleep(_SETTLE_SECONDS)
        self.recover_current = ""
        self.recover_stage = ""
        return self.entries

    def _on_recover_stage(self, stage: str) -> None:
        self.recover_stage = stage

    def recovery_totals(self) -> dict[str, int]:
        """Aggregate counts over all recovered devices, for the overview line: the parameters
        read without a value split into those that cannot be recovered and those not found yet
        (see ``RecoveredParameters``)."""
        totals = {
            "devices": 0,
            "group_addresses": 0,
            "links": 0,
            "unrecoverable": 0,
            "not_found": 0,
        }
        for entry in self.entries:
            recovered = entry.recovered
            if recovered is None:
                continue
            totals["devices"] += 1
            totals["group_addresses"] += len(recovered.group_addresses)
            totals["links"] += len(recovered.links)
            totals["unrecoverable"] += len(recovered.parameters.unrecoverable)
            totals["not_found"] += len(recovered.parameters.not_found)
        return totals

    def summary(self) -> str:
        """The overview line of every device read: how many, and what was read of them."""
        from editor_gui.plugins.recover.strings import S

        totals = self.recovery_totals()
        parts = [
            S.GROUP_ADDRESSES(totals["group_addresses"])
            % {"count": totals["group_addresses"]},
            S.LINKS(totals["links"]) % {"count": totals["links"]},
            *_unknown_parts(totals["unrecoverable"], totals["not_found"]),
        ]
        head = S.RECOVERED_DEVICES(totals["devices"]) % {"count": totals["devices"]}
        return f"{head}: {', '.join(parts)}."

    @staticmethod
    def entry_detail(entry: RecoverEntry) -> str:
        """A short per-device summary of what was read (a device that can be read and is not
        read yet says so)."""
        from editor_gui.plugins.recover.strings import S

        version_note = RecoverService.version_warning(entry)
        recovered = entry.recovered
        if recovered is None:
            parts = [S.DETAIL_NOT_READ] if entry.recoverable else []
            if entry.product_mask_version is not None:
                parts.append(
                    S.DETAIL_MASK
                    % {
                        "device": f"MV-{entry.mask_version:04X}",
                        "product": entry.product_mask_version,
                    }
                )
            if entry.app_id is not None and not entry.compatibility_checked:
                parts.append(S.DETAIL_NO_MASTER)
            if version_note:
                parts.append(version_note)
            return " | ".join(parts)
        parameters = recovered.parameters
        counts = (
            (S.GROUP_ADDRESSES, len(recovered.group_addresses)),
            (S.LINKS, len(recovered.links)),
            (S.PARAMETERS, len(parameters.values)),
        )
        detail = ", ".join(
            [
                *(text(count) % {"count": count} for text, count in counts),
                *_unknown_parts(
                    len(parameters.unrecoverable), len(parameters.not_found)
                ),
            ]
        )
        if not parameters.complete:
            differs = (
                (S.BYTES, parameters.differing_bytes),
                (S.GROUP_OBJECTS, parameters.differing_objects),
            )
            detail += " | " + S.DETAIL_INCOMPLETE % {
                "what": ", ".join(
                    text(count) % {"count": count} for text, count in differs if count
                )
            }
            if parameters.limited:
                detail += f", {S.DETAIL_MORE_EFFORT}"
        order = recovered.dossier.order_info
        if order:
            detail += f" | {order}"
        if entry.verify_changed is not None:
            detail += " | " + (
                S.DETAIL_VERIFIED
                if entry.verify_changed == 0
                else S.DETAIL_VERIFY_DIFFERS(entry.verify_changed)
                % {"count": entry.verify_changed}
            )
        if entry.error:
            detail += f" | {entry.error}"
        if version_note:
            detail += f" | {version_note}"
        return detail

    @staticmethod
    def version_warning(entry: RecoverEntry) -> str:
        """Keep the observed and decoding versions visible even after byte verification."""
        from editor_gui.plugins.recover.strings import S

        if not entry.version_mismatch or entry.app_id is None:
            return ""
        layout = _app_id(
            entry.recovered.application_id
            if entry.recovered is not None
            else entry.application.id
            if entry.application is not None
            else ""
        )
        assert layout is not None
        return S.VERSION_MISMATCH % {
            "installed": entry.app_id.version_text,
            "used": layout.version_text,
        }

    # --- polling (UI thread) ----------------------------------------------

    def poll(self) -> None:
        """Add newly read devices to the open project (when ``auto_apply``) and advance the
        phase when a pending bus operation has finished."""
        # Live: as soon as a device has been read, add it to the open project so it
        # shows under Devices immediately (not only after an explicit apply).
        self._auto_apply()
        self.advance()
        self._finish_search()

    def advance(self) -> None:
        """Advance the phase when a pending bus operation has finished (no project edits)."""
        if self._catalog_changed and self._fetch_thread is None:
            self._catalog_changed = False
            self._api.project.refresh_catalog_resolution()
        if self._pending is None or not self._pending.done():
            return
        future, self._pending = self._pending, None
        exc = None if future.cancelled() else future.exception()
        # The bus operation is over (success, failure or cancel): release the slot
        # so programming/testing (or another scan) can use the tunnel again. The Operations
        # panel keeps it in its history with how it ended.
        if exc is not None:
            self._api.connection.end_operation("failed", str(exc))
        elif future.cancelled() or self._cancel:
            self._api.connection.end_operation("canceled")
        else:
            self._api.connection.end_operation()
        if future.cancelled():
            self.phase = "idle" if self.phase == "scanning" else "scanned"
            return
        if exc is not None:
            self.error = str(exc)
            self.phase = "idle" if self.phase == "scanning" else "scanned"
            return
        result = future.result()
        if self.phase == "scanning":
            self.entries = result
            self.phase = "scanned"
            self._start_autofetch()
        elif self.phase == "recovering" or self.phase == "verifying":
            self.phase = "recovered"

    # --- search again in what was read (no bus) ----------------------------

    def can_search_again(self) -> bool:
        """Whether a selected device read back does not fit what was read off it."""
        return any(_incomplete(entry) for entry in self.entries)

    def start_search_again(self) -> None:
        """Search the parameters of the selected devices whose result does not fit what was read
        off them again, with ``effort``, in what was read: no bus. A device already added to
        the project is replaced when the search ends (see :meth:`_finish_search`)."""
        if self.busy or self._search is not None:
            return
        targets = [entry for entry in self.entries if _incomplete(entry)]
        if not targets:
            return
        self._cancel = False
        self.apply_status = ""
        self.apply_error = ""
        self.recover_done = 0
        self.recover_total = len(targets)
        self.recover_current = ""
        self.phase = "searching"
        effort = self.effort
        code_page = self._api.project.project_code_page()
        master = self._api.connection.master
        future: Future[list[tuple[RecoverEntry, RecoveredDevice]]] = Future()
        self._search = future

        def run() -> None:
            results: list[tuple[RecoverEntry, RecoveredDevice]] = []
            for done, entry in enumerate(targets, start=1):
                if self._cancel:
                    break
                assert entry.recovered is not None and entry.application is not None
                self.recover_current = entry.address
                self.recover_done = done
                try:
                    found = search_again(
                        entry.recovered,
                        entry.application,
                        effort,
                        code_page,
                        cancel=self._cancelled,
                        master=master,
                    )
                except Exception as exc:  # one device failing must not stop the others
                    if self._log is not None:
                        self._log.warning(
                            "search again failed", address=entry.address, error=str(exc)
                        )
                    continue
                results.append((entry, found))
            future.set_result(results)

        threading.Thread(target=run, name="recover-search", daemon=True).start()

    def _finish_search(self) -> None:
        """Take the results of a finished search again: each device gets its new result, and
        one this recovery added to the project is replaced by it (one undo step)."""
        search = self._search
        if search is None or not search.done():
            return
        self._search = None
        self.recover_current = ""
        self.phase = "recovered"
        ga_ids: dict[int, int] = {}
        for entry, recovered in search.result():
            entry.recovered = recovered
            entry.state = _recovered_state(recovered)
            entry.verify_changed = None
            if self._log is not None:
                self._log.info(
                    "device searched again",
                    address=entry.address,
                    effort=recovered.effort.value,
                    complete=recovered.parameters.complete,
                    differing_bytes=recovered.parameters.differing_bytes,
                    differing_objects=recovered.parameters.differing_objects,
                )
            replaced = entry.device_id
            if (
                entry.applied
                and replaced is not None
                and self._api.project.find_device_by_node_id(replaced) is not None
            ):
                self._add_recovered(entry, ga_ids, replace=replaced)

    # --- read-only round-trip verification --------------------------------

    def chosen(self) -> list[RecoverEntry]:
        """The selected devices read back: those Verify, Export Snapshot and the adds work on."""
        return [e for e in self.entries if e.selected and e.recovered is not None]

    def start_verify(self) -> None:
        """Re-encode each selected recovered device's group comms and diff against the bus."""
        if self.busy or not self.connected():
            return
        targets = self.chosen()
        if not targets:
            return
        if not self._api.connection.begin_operation("recover", "verify"):
            self.error = "A bus operation is already running"
            return
        self._cancel = False
        self.phase = "verifying"
        self._pending = self._api.connection.run_async(self._verify(targets))
        if self._pending is None:
            self.phase = "recovered"
            self._api.connection.end_operation()

    async def _verify(self, targets: list[RecoverEntry]) -> list[RecoverEntry]:
        xknx = self._api.connection.xknx
        assert xknx is not None
        for entry in targets:
            if self._cancel:
                break
            if entry.recovered is None or entry.application is None:
                continue
            try:
                report = await verify_recovered(
                    xknx,
                    entry.recovered,
                    entry.application,
                    master=self._api.connection.master,
                )
                entry.verify_changed = report.total_changed_bytes
            except Exception as exc:  # one device's verify failing must not abort
                entry.verify_changed = None
                if self._log is not None:
                    self._log.warning(
                        "verify failed", address=entry.address, error=str(exc)
                    )
            await asyncio.sleep(_SETTLE_SECONDS)  # let the tunnel settle
        return self.entries

    def link_warnings(self) -> list[LinkWarning]:
        """Cross-device group-communication anomalies over all recovered devices."""
        recovered = [e.recovered for e in self.entries if e.recovered is not None]
        return validate_group_communication(recovered)

    def snapshot_text(self) -> str:
        """A JSON forensic snapshot of every selected recovered device."""
        recovered = [e.recovered for e in self.chosen() if e.recovered is not None]
        return snapshots_json(recovered)

    def _factory_image(self, entry: RecoverEntry) -> FactoryImage | None:
        """The image devices of the product ``entry`` was matched with leave the factory
        with (its ``LoadedImage``), or ``None`` when it has none or it cannot be read."""
        from xknxeditor.download.factory_image import (
            FactoryImageError,
            parse_factory_image,
        )

        if entry.hardware2program_ref_id is None:
            return None
        data = self._api.catalog.factory_image(entry.hardware2program_ref_id)
        try:
            return parse_factory_image(data.image if data is not None else None)
        except FactoryImageError:
            return None

    def import_snapshot(self, text: str, name: str) -> int:
        """Take the devices of a snapshot (see ``snapshot_text``) as read: each is matched
        against the catalog as a scanned one, and replaces a device of the same address in the
        list. Needs no bus. Returns how many devices it holds; one that is no snapshot leaves
        the list as it is and says why (``apply_error``)."""
        from editor_gui.plugins.recover.strings import S

        self.apply_status = ""
        self.apply_error = ""
        try:
            devices = devices_from_snapshot(text)
        except ValueError as exc:
            self.apply_error = S.SNAPSHOT_UNREADABLE % {"file": name, "error": exc}
            return 0
        entries = {entry.address: entry for entry in self.entries}
        for device in devices:
            entry = RecoverEntry(
                address=device.address,
                mask_version=device.mask_version,
                app_id=device.observed_app_id or _app_id(device.application_id),
                recovered=device,
            )
            self._identify_catalog(entry, application_id=device.application_id)
            if entry.application is not None:
                application = entry.application
                device = replace(
                    device,
                    com_object_refs=com_object_ref_by_number(
                        application,
                        seed_dynamic_ui(application, device.parameters.values),
                    ),
                    # A search again compares the properties with the factory contents too.
                    factory_image=self._factory_image(entry),
                )
                entry.recovered = device
                entry.state = _recovered_state(device)
            entries[device.address] = entry
        self.entries = list(entries.values())
        self.phase = "recovered"
        self.apply_status = S.SNAPSHOT_IMPORTED(len(devices)) % {
            "count": len(devices),
            "file": name,
        }
        return len(devices)

    # --- online-catalog auto-fetch ---------------------------------------

    def _needs_catalog(self, entry: RecoverEntry) -> bool:
        return (
            entry.app_id is not None
            and entry.recovered is None
            and (
                not entry.recoverable
                or entry.match_reason.startswith("different_version")
            )
        )

    def _unresolved_manufacturers(self) -> list[str]:
        return list(
            dict.fromkeys(
                entry.app_id.manufacturer_id
                for entry in self.entries
                if self._needs_catalog(entry) and entry.app_id is not None
            )
        )

    def _start_autofetch(self) -> None:
        """Try exact application metadata in the alternative catalog, then the online catalog."""
        if self._fetch_thread is not None or self._cancel:
            return
        if not self._unresolved_manufacturers():
            return
        self.fetch_error = ""
        thread = threading.Thread(target=self._autofetch, daemon=True)
        self._fetch_thread = thread
        thread.start()

    def _autofetch(self) -> None:
        from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError

        try:
            self._fetch_alternative()
            manufacturers = self._unresolved_manufacturers()
            for index, manufacturer_id in enumerate(manufacturers, start=1):
                if self._cancel:
                    break
                self.fetch_status = (
                    f"catalog {index}/{len(manufacturers)}: {manufacturer_id}"
                )
                self._fetch_manufacturer(manufacturer_id, OnlineCatalogError)
                self._reidentify_unresolved()
        except Exception as exc:
            self._catalog_error("catalog", exc)
        finally:
            self.fetch_status = ""
            self._fetch_thread = None

    def _catalog_error(
        self, source: str, error: Exception, *, message: str | None = None
    ) -> None:
        if message is None:
            message = f"{source}: {error}"
        self.fetch_error = f"{self.fetch_error}\n{message}".strip()
        if self._log is not None:
            self._log.error(
                "recovery catalog fetch failed", source=source, error=str(error)
            )

    def _fetch_alternative(self) -> None:
        from editor_gui.plugins.catalog.alternative import AlternativeCatalogError
        from editor_gui.plugins.recover.strings import S

        if self._cancel or not any(
            self._needs_catalog(entry) for entry in self.entries
        ):
            return
        client = self._api.catalog.alternative
        self.fetch_status = "alternative catalog"
        client.load_cache()
        try:
            client.refresh()
        except AlternativeCatalogError as exc:
            self._catalog_error("alternative catalog", exc)
        downloads: set[str] = set()
        for entry in self.entries:
            if not self._needs_catalog(entry) or entry.app_id is None:
                continue
            mask = f"MV-{entry.mask_version:04X}"
            master = self._api.connection.master
            compatible = master.compatible_masks(mask) if master is not None else ()
            for item in client.find_application(
                entry.app_id.manufacturer_id,
                entry.app_id.application_number,
                entry.app_id.application_version,
                (mask, *compatible),
            ):
                downloads.add(item.id)
        for index, entry_id in enumerate(sorted(downloads), start=1):
            if self._cancel:
                break
            self.fetch_status = f"alternative catalog {index}/{len(downloads)}"
            try:
                data = client.download(entry_id)
                if self._cancel:
                    break
                with self._api.catalog.io_lock:
                    if self._cancel:
                        break
                    self._api.catalog.import_knxprod_data(data)
                    self._catalog_changed = True
            except AlternativeCatalogError as exc:
                if exc.code == "NO_ACCOUNT":
                    self._catalog_error(
                        "alternative catalog", exc, message=S.CATALOG_LOGIN_REQUIRED
                    )
                    break
                self._catalog_error("alternative catalog", exc)
                if (
                    exc.status in (401, 403, 429, 503)
                    or exc.code == "DOWNLOAD_DAILY_LIMIT"
                ):
                    break
            except Exception as exc:
                self._catalog_error("alternative catalog import", exc)
        self._reidentify_unresolved()

    def _fetch_manufacturer(
        self, manufacturer_id: str, error_type: type[Exception]
    ) -> None:
        # The "M-XXXX" number is hex (M-0083 = 0x0083 = 131), which is the id the
        # online catalog expects; a decimal parse (83) is rejected as Bad Request.
        try:
            mid = int(manufacturer_id[2:], 16)
        except ValueError:
            return
        try:
            items = self._api.catalog.online_catalog_items(mid)
        except error_type as exc:
            self._catalog_error(manufacturer_id, exc)
            return
        if self._cancel:
            return
        entries: list[RecoverEntry] = []
        for entry in self.entries:
            if (
                self._needs_catalog(entry)
                and entry.app_id is not None
                and entry.app_id.manufacturer_id == manufacturer_id
            ):
                versions = {
                    item.id: OnlineVersion(
                        item.id, item.name, item.order_number, item.application_version
                    )
                    for item in items
                    if item.downloadable
                    and item.application_manufacturer_id == mid
                    and item.application_number == entry.app_id.application_number
                    and item.application_version is not None
                    and item.application_version != entry.app_id.application_version
                }
                entry = replace(
                    entry,
                    online_versions=tuple(
                        sorted(
                            versions.values(),
                            key=lambda item: (
                                item.application_version,
                                item.product_name,
                                item.catalog_item_id,
                            ),
                        )
                    ),
                )
            entries.append(entry)
        self.entries = entries
        wanted = {
            (entry.app_id.application_number, entry.app_id.application_version)
            for entry in self.entries
            if self._needs_catalog(entry)
            and entry.app_id is not None
            and entry.app_id.manufacturer_id == manufacturer_id
        }
        item_ids = sorted(
            {
                item.id
                for item in items
                if item.downloadable
                and item.application_manufacturer_id == mid
                and (item.application_number, item.application_version) in wanted
            }
        )
        if not item_ids:
            if self._log is not None:
                self._log.info(
                    "no online catalog product for the devices' applications",
                    manufacturer=manufacturer_id,
                )
            return
        added = self._download_online(manufacturer_id, item_ids)
        if added is None:
            return
        self._api.catalog.refresh()
        self._catalog_changed = True
        if self._log is not None:
            self._log.info(
                "fetched manufacturer catalog",
                manufacturer=manufacturer_id,
                products=len(item_ids),
                added=added,
            )

    def _download_online(self, manufacturer_id: str, item_ids: list[str]) -> int | None:
        """Download and import the online catalog products; how many product versions were
        added, None when none could be downloaded. A failed download or import of several
        products is tried again product by product, so one failure leaves the others."""
        batches = (
            [item_ids] if len(item_ids) == 1 else [item_ids, *([i] for i in item_ids)]
        )
        added: int | None = None
        for index, batch in enumerate(batches):
            if self._cancel:
                break
            self.fetch_status = f"catalog {manufacturer_id}"
            if len(batch) == 1:
                self.fetch_status += f": {index or 1}/{len(item_ids)}"
            try:
                with self._api.catalog.io_lock:
                    if self._cancel:
                        break
                    added = (added or 0) + len(
                        self._api.catalog.download_online_products(batch)
                    )
            except Exception as exc:
                if len(batch) == 1:
                    self._catalog_error(batch[0], exc)
                continue
            if len(batch) > 1:
                break
        return added

    def _reidentify_unresolved(self) -> None:
        entries: list[RecoverEntry] = []
        for entry in self.entries:
            if self._needs_catalog(entry):
                entry = replace(entry)
                self._identify_catalog(entry)
            entries.append(entry)
        self.entries = entries

    def start_load_other_version(self, address: str, catalog_item_id: str) -> None:
        """Import one explicitly chosen online article; never starts bus recovery."""
        if self.busy:
            raise ValueError("recovery is still running")
        entry = next(
            (entry for entry in self.entries if entry.address == address), None
        )
        if entry is None or not entry.other_versions_available:
            raise ValueError("no other application versions available for this device")
        option = next(
            (
                item
                for item in entry.online_versions
                if item.catalog_item_id == catalog_item_id
            ),
            None,
        )
        if option is None:
            raise ValueError("catalog item is not an offered application version")
        self._cancel = False
        self.fetch_error = ""
        thread = threading.Thread(
            target=self._load_other_version, args=(address, option), daemon=True
        )
        self._fetch_thread = thread
        thread.start()

    def _load_other_version(self, address: str, option: OnlineVersion) -> None:
        try:
            self.fetch_status = option.product_name
            with self._api.catalog.io_lock:
                if self._cancel:
                    return
                self._api.catalog.download_online_products([option.catalog_item_id])
                self._catalog_changed = True
            self._reidentify_unresolved()
            entries: list[RecoverEntry] = []
            for entry in self.entries:
                entry = replace(
                    entry,
                    online_versions=tuple(
                        item
                        for item in entry.online_versions
                        if item.catalog_item_id != option.catalog_item_id
                    ),
                )
                if entry.address == address:
                    entry.selected = False
                entries.append(entry)
            self.entries = entries
        except Exception as exc:
            self._catalog_error(option.catalog_item_id, exc)
        finally:
            self.fetch_status = ""
            self._fetch_thread = None

    # --- assemble into a project (UI thread) ------------------------------

    def _auto_apply(self) -> None:
        """Add newly-recovered devices to the open project (live), if enabled."""
        if not self.auto_apply or not self._api.project.is_open:
            return
        ga_ids: dict[int, int] = {}
        for entry in self.entries:
            if entry.applied or entry.auto_tried or entry.version_mismatch:
                continue
            if entry.recovered is None or entry.product_ref_id is None:
                continue
            entry.auto_tried = True
            self._add_recovered(entry, ga_ids)

    def apply_to_project(self) -> int:
        """Write every selected recovered device not yet added into the open project, retrying
        the ones an earlier attempt could not add; returns how many were added."""
        added, _failed = self._apply_pending()
        return added

    def _apply_pending(self) -> tuple[int, int]:
        """Add every selected recovered device not yet added; returns (added, failed)."""
        ga_ids: dict[int, int] = {}
        added = 0
        failed = 0
        for entry in self.chosen():
            if entry.applied or entry.application is None:
                continue
            if entry.product_ref_id is None:
                continue
            if self._add_recovered(entry, ga_ids):
                added += 1
            elif entry.error is not None:
                failed += 1
        return added, failed

    def _report_apply(self, status: str, failed: int) -> None:
        """Show the outcome of an add-to-project action, and the devices it could not add."""
        from editor_gui.plugins.recover.strings import S

        self.apply_status = status
        self.apply_error = S.APPLY_FAILED(failed) % {"count": failed} if failed else ""

    def apply_to_open_project(self) -> int:
        """Add recovered devices to the currently open project, with a status message."""
        from editor_gui.plugins.recover.strings import S

        added, failed = self._apply_pending()
        path = self._api.project.path
        target = path.name if path is not None else "?"
        self._report_apply(
            S.APPLY_MERGED(added) % {"count": added, "target": target}, failed
        )
        return added

    def create_project_and_apply(self, path: Any) -> int:
        """Create a fresh project at ``path`` and add every selected recovered device to it."""
        from editor_gui.plugins.recover.strings import S

        # Live auto-apply may have added devices to a previously-open project; the
        # new project is empty, so re-add all recovered devices to it.
        self._api.project.new(path)
        for entry in self.entries:
            entry.applied = False
            entry.auto_tried = False
            entry.error = None
        added, failed = self._apply_pending()
        self._report_apply(
            S.APPLY_NEW(added) % {"count": added, "target": path.name}, failed
        )
        return added

    def _add_recovered(
        self, entry: RecoverEntry, ga_ids: dict[int, int], *, replace: int | None = None
    ) -> bool:
        """Add a recovered device to the project; with ``replace`` instead of the device of
        that node id (in the same undo step). Returns whether it was added."""
        from editor_gui.plugins.recover.strings import S

        project = self._api.project
        recovered = entry.recovered
        application = entry.application
        assert recovered is not None and application is not None
        assert entry.product_ref_id is not None
        # A device already sits at this address (project already had it, or a
        # previous apply): skip rather than crash on the unique-address check. Mark
        # it applied so live auto-apply does not retry it every frame.
        if (
            replace is None
            and project.find_device_by_address(entry.address) is not None
        ):
            entry.applied = True
            entry.state = S.STATE_EXISTS
            # Not a failure any more, even if an earlier attempt failed.
            entry.error = None
            return False
        # A failed add takes back what it changed (an area, line or segment it created, a
        # line's medium), so the project stays as it was.
        start = project.cursor
        added = False
        # This runs on the UI thread (live auto-apply in poll); any failure must be
        # contained, never propagate into the render loop.
        try:
            # The device with its settings, flags, group addresses and links: one undo step.
            with project.one_undo_step():
                if replace is not None:
                    project.remove_device(replace)
                # Place the device on the segment for its own area/line (creating them
                # if needed) and set the address at creation, so no fragile post-hoc
                # "move".
                # Always the line of its address, on a segment of a medium its hardware
                # supports (an empty line of another medium is changed to one).
                segment_id = project.find_or_create_segment_for_address(
                    entry.address, entry.hardware2program_ref_id
                )
                try:
                    device_octet = int(entry.address.split(".")[-1])
                except ValueError:
                    device_octet = None
                # The device carries no human-readable name on the bus (only IA, app id and the
                # configured image), so default to the matched catalog product name; fall back to
                # the individual address when no product matched. The ETS clear name is project
                # metadata and stays unrecoverable.
                device_name = entry.product_name or entry.address
                device_id = project.add_device(
                    entry.product_ref_id,
                    entry.hardware2program_ref_id,
                    device_name,
                    application,
                    segment_id=segment_id,
                    address=device_octet,
                    # Recovered values are applied as instance overrides at creation, so
                    # an inactive/conditional parameter is stored (and ignored by the
                    # evaluator) rather than raising mid-device via a live set.
                    parameters=list(recovered.parameters.values.items()),
                    # The addresses the device uses are not read from the bus: picking free ones
                    # could take the numbers of devices recovered later.
                    with_additional_addresses=False,
                )
                device = None
                if device_id is not None:
                    # The bus carries no clear name or description, so stamp the
                    # individual address as the description to record where the device
                    # came from.
                    note = self.version_warning(entry)
                    project.set_device_description(
                        device_id,
                        "",
                        f"{entry.address} | {note}" if note else entry.address,
                    )
                    device = next(
                        (d for d in project.devices if d.node_id == device_id), None
                    )
                if device_id is None or device is None:
                    raise RuntimeError(f"{entry.address} was not added")
                # Prefer the device-matched map computed during recovery (module
                # instances seeded from the recovered parameters); fall back to default.
                number_to_ref = recovered.com_object_refs or com_object_ref_by_number(
                    application
                )
                if self._log is not None:
                    persisted = [
                        co
                        for co in getattr(device, "com_objects", [])
                        if getattr(co, "db_id", None) is not None
                    ]
                    self._log.debug(
                        "recover apply device",
                        address=entry.address,
                        device_id=device_id,
                        com_objects=len(getattr(device, "com_objects", [])),
                        persisted_com_objects=len(persisted),
                        number_to_ref=len(number_to_ref),
                        links=len(recovered.links),
                        group_objects=len(recovered.group_objects),
                    )
                native = self._apply_native(device, recovered, application)
                if native is not None:
                    # The plug-in's configuration places the objects (and holds their
                    # flags): an association names the slot of the device's object table.
                    device, number_to_ref = native
                else:
                    self._apply_flags(device, recovered, number_to_ref)
                self._apply_links(device, recovered, number_to_ref, ga_ids)
                project.recalculate_params(
                    device_id, list(recovered.parameters.values), stored=True
                )
                entry.device_id = device_id
                added = True
        except Exception as exc:  # never crash the render loop / batch on one device
            # Not marked applied: an explicit add tries it again (live auto-apply does
            # not, see ``auto_tried``).
            entry.state = S.STATE_ERROR
            entry.error = _error_text(exc)
            if self._log is not None:
                self._log.warning(
                    "add to project failed", address=entry.address, error=str(exc)
                )
        if not added:
            try:
                while project.cursor > start and project.undo():
                    pass
            except Exception as exc:
                if self._log is not None:
                    self._log.warning(
                        "undoing a failed add failed",
                        address=entry.address,
                        error=str(exc),
                    )
            return False
        entry.applied = True
        entry.error = None
        return True

    def _apply_native(
        self, device: Any, recovered: RecoveredDevice, application: Any
    ) -> tuple[Any, dict[int, str]] | None:
        """Give ``device`` the configuration of a manufacturer's plug-in read off it (in the
        undo step of the device), and return the device and the reference of each slot of
        its object table; None for a device without one."""
        from editor_gui.plugin_providers import device_reader

        if recovered.native_image is None:
            return None
        reader = device_reader(application)
        if reader is None:
            return None
        project = self._api.project
        project.import_plugin_config(
            device, reader.recover(device, recovered.native_image).data
        )
        device = project.find_device_by_node_id(device.node_id) or device
        return device, reader.slot_references(device)

    def _apply_flags(
        self, device: Any, recovered: RecoveredDevice, number_to_ref: dict[int, str]
    ) -> None:
        project = self._api.project
        for number, group_object in recovered.group_objects.items():
            ref_id = number_to_ref.get(number)
            com_object = device.find_com_object(ref_id) if ref_id is not None else None
            if ref_id is None or com_object is None:
                continue
            for flag, value in (
                ("communication", group_object.communication),
                ("read", group_object.read),
                ("write", group_object.write),
                ("transmit", group_object.transmit),
                ("update", group_object.update),
                ("read_on_init", group_object.read_on_init),
            ):
                # A memory-mapped table holds no read-on-init: the product's stays. A flag
                # the group object has already is not set again.
                if value is not None and getattr(com_object.flags, flag) != value:
                    project.set_flag(device, ref_id, flag, value)

    def _apply_links(
        self,
        device: Any,
        recovered: RecoveredDevice,
        number_to_ref: dict[int, str],
        ga_ids: dict[int, int],
    ) -> None:
        project = self._api.project
        for link in recovered.links:
            ref_id = number_to_ref.get(link.group_object_number)
            com_object = device.find_com_object(ref_id) if ref_id is not None else None
            db_id = (
                getattr(com_object, "db_id", None) if com_object is not None else None
            )
            if self._log is not None:
                # One line per link: which links resolve to a persisted com object (and so
                # get a group address) vs. which are dropped, and why.
                self._log.debug(
                    "recover link",
                    address=recovered.address,
                    group_object=link.group_object_number,
                    group_address=link.group_address,
                    ref_id=ref_id,
                    com_object_found=com_object is not None,
                    db_id=db_id,
                    sending=link.sending,
                )
            if ref_id is None or com_object is None or db_id is None:
                continue
            ga_id = ga_ids.get(link.group_address)
            if ga_id is None:
                ga_id = project.create_group_address_value(link.group_address)
                if ga_id is None:
                    continue
                ga_ids[link.group_address] = ga_id
            project.link_com_object_to_ga(db_id, ga_id, is_sending=link.sending)

    def reset(self) -> None:
        self.entries = []
        self.phase = "idle"
        self.error = None
