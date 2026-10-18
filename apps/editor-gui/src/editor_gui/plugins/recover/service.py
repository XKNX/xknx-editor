"""Recover-plugin service: scan the bus, identify devices, read them back, add to a project.

Bus work (scan, identify, read-back) runs on the connection's asyncio loop via
``api.connection.run_async``; the resulting :class:`RecoverEntry` list is polled on
the UI thread. Writing recovered devices into a project happens on the UI thread
through :class:`ProjectService` (which is not thread-safe). All bus access is
read-only.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from xknxeditor.recover import (
    AppId,
    iter_addresses,
    probe_and_identify,
    recover_device_at,
    snapshots_json,
    validate_group_communication,
    verify_recovered,
)
from xknxeditor.recover.recover import com_object_ref_by_number

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger, PluginAPI
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
    # True when more than one product matched, or when no exact-version product
    # existed and a different version's layout was used: needs user confirmation.
    ambiguous: bool = False
    candidates: int = 0
    # Read-only round-trip result: bytes that would still change (0 == verified).
    verify_changed: int | None = None
    # True once this device has been written into the open project (so it is not
    # added twice by live auto-apply and a later explicit apply).
    applied: bool = False

    @property
    def recoverable(self) -> bool:
        return self.application is not None


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
        self._fetch_thread: threading.Thread | None = None
        # Outcome of the last add-to-project action, shown in the window.
        self.apply_status = ""
        # Cooperative cancel flag for a running scan/recover/verify.
        self._cancel = False
        # Add recovered devices to the open project as soon as they are read.
        self.auto_apply = True

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
        return self._pending is not None or self._fetch_thread is not None

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

    def _identify_catalog(self, entry: RecoverEntry) -> None:
        """Match a device's application id against the local catalog (no network).

        Matches manufacturer + application number + version + mask first. If no
        exact-version product exists, falls back to manufacturer + number + mask
        (a different version's layout), which is flagged ambiguous so the user must
        confirm before recovering - the layout may not match the installed one.
        More than one candidate is likewise flagged for confirmation."""
        from editor_gui.plugins.recover.strings import S

        if entry.app_id is None:
            entry.state = S.STATE_UNPROGRAMMED
            entry.selected = False
            return
        mask = f"MV-{entry.mask_version:04X}"
        exact = self._api.catalog.find_products_for_application(
            manufacturer_id=entry.app_id.manufacturer_id,
            application_number=entry.app_id.application_number,
            application_version=entry.app_id.application_version,
            mask_version=mask,
        )
        version_fallback = False
        products = exact
        if not products:
            products = self._api.catalog.find_products_for_application(
                manufacturer_id=entry.app_id.manufacturer_id,
                application_number=entry.app_id.application_number,
                mask_version=mask,
            )
            version_fallback = bool(products)
        if not products:
            entry.state = S.STATE_NO_APP
            entry.selected = False
            return
        distinct_refs = {p.product_ref_id for p in products}
        entry.candidates = len(distinct_refs)
        entry.ambiguous = version_fallback or len(distinct_refs) > 1
        product = products[0]
        entry.product_ref_id = product.product_ref_id
        entry.hardware2program_ref_id = product.hardware2program_ref_id
        entry.product_name = product.name or product.order_number
        if product.application_id is not None:
            entry.application = self._api.catalog.get_application(
                product.application_id
            )
        if not entry.recoverable:
            entry.state = S.STATE_NO_APP
            entry.selected = False
            return
        # Ambiguous / version-fallback matches are not auto-selected: the user must
        # tick them explicitly after checking the product, so we never silently
        # recover with a possibly-wrong device model.
        entry.state = S.STATE_AMBIGUOUS if entry.ambiguous else S.STATE_FOUND
        entry.selected = not entry.ambiguous

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
                entry.recovered = await recover_device_at(
                    xknx,
                    entry.address,
                    entry.application,
                    progress=self._on_recover_stage,
                )
                entry.state = S.STATE_RECOVERED
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
        """Aggregate counts over all recovered devices, for the overview line."""
        totals = {"devices": 0, "group_addresses": 0, "links": 0, "unknown": 0}
        for entry in self.entries:
            recovered = entry.recovered
            if recovered is None:
                continue
            totals["devices"] += 1
            totals["group_addresses"] += len(recovered.group_addresses)
            totals["links"] += len(recovered.links)
            totals["unknown"] += len(recovered.parameters.unknown)
        return totals

    @staticmethod
    def entry_detail(entry: RecoverEntry) -> str:
        """A short per-device summary of what was recovered (empty until recovered)."""
        recovered = entry.recovered
        if recovered is None:
            return ""
        unknown = len(recovered.parameters.unknown)
        detail = (
            f"{len(recovered.group_addresses)} GA, "
            f"{len(recovered.links)} links, "
            f"{len(recovered.parameters.values)} params"
        )
        if unknown:
            detail += f" ({unknown} unknown)"
        order = recovered.dossier.order_info
        if order:
            detail += f" | {order}"
        if entry.verify_changed is not None:
            detail += (
                " | verified"
                if entry.verify_changed == 0
                else f" | verify: {entry.verify_changed} B differ"
            )
        return detail

    # --- polling (UI thread) ----------------------------------------------

    def poll(self) -> None:
        """Advance the phase when a pending bus operation has finished."""
        # Live: as soon as a device has been read, add it to the open project so it
        # shows under Devices immediately (not only after an explicit apply).
        self._auto_apply()
        if self._pending is None or not self._pending.done():
            return
        future, self._pending = self._pending, None
        # The bus operation is over (success, failure or cancel): release the slot
        # so programming/testing (or another scan) can use the tunnel again.
        self._api.connection.end_operation()
        if future.cancelled():
            self.phase = "idle" if self.phase == "scanning" else "scanned"
            return
        exc = future.exception()
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

    # --- read-only round-trip verification --------------------------------

    def start_verify(self) -> None:
        """Re-encode each recovered device's group comms and diff against the bus."""
        if self.busy or not self.connected():
            return
        targets = [e for e in self.entries if e.recovered is not None]
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
        """A JSON forensic snapshot of every recovered device."""
        recovered = [e.recovered for e in self.entries if e.recovered is not None]
        return snapshots_json(recovered)

    # --- online-catalog auto-fetch ---------------------------------------

    def _unresolved_manufacturers(self) -> list[str]:
        """Manufacturer ids of scanned devices whose application is not in the catalog."""
        seen: list[str] = []
        for entry in self.entries:
            if entry.recoverable or entry.app_id is None:
                continue
            if entry.app_id.manufacturer_id not in seen:
                seen.append(entry.app_id.manufacturer_id)
        return seen

    def _start_autofetch(self) -> None:
        """Automatically pull unresolved devices' products from the online catalog.

        Bus recovery only reads a device's manufacturer, application number and
        version - never the hardware/program ref a ``.knxproj`` import has - so it
        cannot map a device to one online product. Instead it downloads the
        unresolved manufacturers' catalogs, imports them, and re-matches. Runs on a
        worker thread (network + import + parse)."""
        if self._fetch_thread is not None:
            return
        manufacturers = self._unresolved_manufacturers()
        if not manufacturers:
            return
        thread = threading.Thread(
            target=self._autofetch, args=(manufacturers,), daemon=True
        )
        self._fetch_thread = thread
        thread.start()

    def _autofetch(self, manufacturers: list[str]) -> None:
        from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError

        try:
            for index, manufacturer_id in enumerate(manufacturers, start=1):
                self.fetch_status = (
                    f"catalog {index}/{len(manufacturers)}: {manufacturer_id}"
                )
                self._fetch_manufacturer(manufacturer_id, OnlineCatalogError)
            self._reidentify_unresolved()
        finally:
            self.fetch_status = ""
            self._fetch_thread = None

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
        except error_type as exc:  # network / index error: skip this manufacturer
            if self._log is not None:
                self._log.error(
                    "online index failed", manufacturer=manufacturer_id, error=str(exc)
                )
            return
        item_ids = [item.id for item in items if item.downloadable]
        if not item_ids:
            return
        try:
            with self._api.catalog.io_lock:
                added = self._api.catalog.download_online_products(item_ids)
        except error_type as exc:
            if self._log is not None:
                self._log.error(
                    "online download failed",
                    manufacturer=manufacturer_id,
                    error=str(exc),
                )
            return
        self._api.catalog.refresh()
        if self._log is not None:
            self._log.info(
                "fetched manufacturer catalog",
                manufacturer=manufacturer_id,
                products=len(item_ids),
                added=len(added),
            )

    def _reidentify_unresolved(self) -> None:
        for entry in self.entries:
            if not entry.recoverable and entry.app_id is not None:
                self._identify_catalog(entry)

    # --- assemble into a project (UI thread) ------------------------------

    def _auto_apply(self) -> None:
        """Add newly-recovered devices to the open project (live), if enabled."""
        if not self.auto_apply or not self._api.project.is_open:
            return
        ga_ids: dict[int, int] = {}
        for entry in self.entries:
            if entry.applied or entry.recovered is None or entry.product_ref_id is None:
                continue
            self._add_recovered(entry, ga_ids)

    def apply_to_project(self) -> int:
        """Write every not-yet-added recovered device into the open project."""
        ga_ids: dict[int, int] = {}
        added = 0
        for entry in self.entries:
            if entry.applied or entry.recovered is None or entry.application is None:
                continue
            if entry.product_ref_id is None:
                continue
            if self._add_recovered(entry, ga_ids):
                added += 1
        return added

    def apply_to_open_project(self) -> int:
        """Add recovered devices to the currently open project, with a status message."""
        from editor_gui.plugins.recover.strings import S

        added = self.apply_to_project()
        path = self._api.project.path
        target = path.name if path is not None else "?"
        self.apply_status = S.APPLY_MERGED % {"count": added, "target": target}
        return added

    def create_project_and_apply(self, path: Any) -> int:
        """Create a fresh project at ``path`` and add every recovered device to it."""
        from editor_gui.plugins.recover.strings import S

        # Live auto-apply may have added devices to a previously-open project; the
        # new project is empty, so re-add all recovered devices to it.
        self._api.project.new(path)
        for entry in self.entries:
            entry.applied = False
        added = self.apply_to_project()
        self.apply_status = S.APPLY_NEW % {"count": added, "target": path.name}
        return added

    def _add_recovered(self, entry: RecoverEntry, ga_ids: dict[int, int]) -> bool:
        from editor_gui.plugins.recover.strings import S

        project = self._api.project
        recovered = entry.recovered
        application = entry.application
        assert recovered is not None and application is not None
        assert entry.product_ref_id is not None
        # A device already sits at this address (project already had it, or a
        # previous apply): skip rather than crash on the unique-address check. Mark
        # it applied so live auto-apply does not retry it every frame.
        if project.find_device_by_address(entry.address) is not None:
            entry.applied = True
            entry.state = S.STATE_EXISTS
            return False
        # This runs on the UI thread (live auto-apply in poll); any failure must be
        # contained, never propagate into the render loop.
        try:
            # Place the device on the segment for its own area/line (creating them
            # if needed) and set the address at creation, so no fragile post-hoc
            # "move".
            segment_id = project.find_or_create_segment_for_address(entry.address)
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
            if device_id is None:
                return False
            # The bus carries no ETS clear name/description, so stamp the individual
            # address as the description to record where the device came from.
            project.set_device_description(device_id, "", entry.address)
            device = next((d for d in project.devices if d.node_id == device_id), None)
            if device is None:
                return False
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
            self._apply_flags(device, recovered, number_to_ref)
            self._apply_links(device, recovered, number_to_ref, ga_ids)
            project.recalculate_params(device_id, list(recovered.parameters.values))
        except Exception as exc:  # never crash the render loop / batch on one device
            entry.applied = True  # do not retry this device every frame
            entry.state = S.STATE_ERROR
            if self._log is not None:
                self._log.warning(
                    "add to project failed", address=entry.address, error=str(exc)
                )
            return False
        entry.applied = True
        return True

    def _apply_flags(
        self, device: Any, recovered: RecoveredDevice, number_to_ref: dict[int, str]
    ) -> None:
        project = self._api.project
        for number, group_object in recovered.group_objects.items():
            ref_id = number_to_ref.get(number)
            if ref_id is None or device.find_com_object(ref_id) is None:
                continue
            for flag, value in (
                ("communication", group_object.communication),
                ("read", group_object.read),
                ("write", group_object.write),
                ("transmit", group_object.transmit),
                ("update", group_object.update),
                ("read_on_init", group_object.read_on_init),
            ):
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
