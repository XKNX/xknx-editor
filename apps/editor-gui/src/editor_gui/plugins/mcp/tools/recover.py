# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Recover tools (BUS): rebuild a project from the devices on the bus, as the Recover window does.

Scan an address range (each device is identified against the catalog; devices whose product is
missing are fetched from the online catalog), read the selected devices back (parameters, group
addresses, links, flags), verify them, and add them to the open project or to a new one. Reading
from the bus changes nothing on the devices.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.connection import not_started

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.recover.service import RecoverEntry, RecoverService

# How often a waiting tool looks whether the bus operation has ended.
_POLL_SECONDS = 0.25
# Adding to a new project creates and fills it, which takes a while for many devices.
_SLOW = 600.0


def _status_code(state: str) -> str:
    """A stable code for an entry's state (the window shows it translated)."""
    from editor_gui.plugins.recover.strings import S

    return {
        S.STATE_FOUND: "found",
        S.STATE_AMBIGUOUS: "ambiguous",
        S.STATE_NO_APP: "no_application",
        S.STATE_UNPROGRAMMED: "unprogrammed",
        S.STATE_RECOVERED: "recovered",
        S.STATE_EXISTS: "exists",
        S.STATE_ERROR: "error",
    }.get(state, "")


def _entry(service: RecoverService, entry: RecoverEntry) -> dict[str, Any]:
    from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga

    app_id = entry.app_id
    recovered = entry.recovered
    return {
        "address": entry.address,
        "mask_version": f"{entry.mask_version:#06x}",
        "manufacturer_id": app_id.manufacturer_id if app_id is not None else None,
        "application_number": app_id.application_number if app_id else None,
        "application_version": app_id.application_version if app_id else None,
        "product_ref_id": entry.product_ref_id,
        "product_name": entry.product_name,
        "status": _status_code(entry.state),
        "state": entry.state,
        "selected": entry.selected,
        "recoverable": entry.recoverable,
        "ambiguous": entry.ambiguous,
        "candidates": entry.candidates,
        "recovered": (
            {
                "group_addresses": [
                    format_ga(ga, GroupAddressStyle.THREE_LEVEL)
                    for ga in recovered.group_addresses
                ],
                "links": len(recovered.links),
                "parameters": len(recovered.parameters.values),
                "unknown_parameters": len(recovered.parameters.unknown),
                "order_info": recovered.dossier.order_info,
            }
            if recovered is not None
            else None
        ),
        "verify_changed_bytes": entry.verify_changed,
        "applied": entry.applied,
        "error": entry.error,
        "details": service.entry_detail(entry),
    }


def _state(service: RecoverService, connected: bool) -> dict[str, Any]:
    from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga

    progress: dict[str, Any] | None = None
    if service.phase == "scanning":
        progress = {
            "done": service.scan_done,
            "total": service.scan_total,
            "address": service.scan_current,
        }
    elif service.phase == "recovering":
        progress = {
            "done": service.recover_done,
            "total": service.recover_total,
            "address": service.recover_current,
            "stage": service.recover_stage,
        }
    return {
        "connected": connected,
        "phase": service.phase,
        "busy": service.busy,
        "progress": progress,
        "error": service.error,
        "auto_apply": service.auto_apply,
        "catalog_fetch": service.fetch_status or None,
        "apply_status": service.apply_status or None,
        "apply_error": service.apply_error or None,
        "totals": service.recovery_totals(),
        "link_warnings": [
            {
                "group_address": format_ga(
                    w.group_address, GroupAddressStyle.THREE_LEVEL
                ),
                "kind": w.kind,
                "senders": w.senders,
                "receivers": w.receivers,
            }
            for w in service.link_warnings()
        ],
        "entries": [_entry(service, entry) for entry in service.entries],
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    def service() -> RecoverService:
        if ctx.recover is None:
            raise ToolError("recovering from the bus is not available here")
        return ctx.recover

    def state() -> dict[str, Any]:
        recover = service()

        def _read() -> dict[str, Any]:
            recover.advance()
            return _state(recover, recover.connected())

        return ctx.run_on_ui(_read)

    def wait(timeout: float) -> dict[str, Any]:
        """The state once the running bus operation (and the catalog fetch after a scan) has
        ended, or after ``timeout`` s with it still running."""
        end = time.monotonic() + max(timeout, 0.0)
        current = state()
        while current["busy"] and time.monotonic() < end:
            time.sleep(_POLL_SECONDS)
            current = state()
        return current

    def run_operation(what: str, begin: Any, timeout: float) -> dict[str, Any]:
        recover = service()

        def _start() -> str | None:
            if not recover.connected():
                return "no"
            if recover.busy:
                return "busy"
            recover.error = None
            begin()
            if recover.error:
                return recover.error
            return None if recover.busy else "not started"

        problem = ctx.run_on_ui(_start)
        if problem == "busy":
            raise ToolError(
                f"{what} could not start: the recovery is still running; recover_stop ends it"
            )
        if problem is not None:
            raise not_started(ctx.api.connection, what)
        return wait(timeout)

    def entries_at(addresses: list[str]) -> list[RecoverEntry]:
        by_address = {entry.address: entry for entry in service().entries}
        unknown = [address for address in addresses if address not in by_address]
        if unknown:
            raise ToolError(f"not found by the scan: {', '.join(unknown)}")
        return [by_address[address] for address in addresses]

    @tool("read")
    def recover_status() -> dict[str, Any]:
        """The state of the recovery from the bus, as the Recover window shows it. Returns
        {connected, phase (idle | scanning | scanned | recovering | verifying | recovered),
        busy, progress ({done, total, address, stage?} while scanning or reading back, else
        null), error, auto_apply, catalog_fetch (the online-catalog fetch after a scan, or
        null), apply_status, apply_error (the outcome of the last add to a project), totals
        {devices, group_addresses, links, unknown}, link_warnings [{group_address, kind
        (no_sender | multiple_senders), senders, receivers}], entries}. Each entry has
        {address, mask_version, manufacturer_id, application_number, application_version,
        product_ref_id, product_name, status (found | ambiguous | no_application | unprogrammed
        | recovered | exists | error), state (the window's text), selected, recoverable,
        ambiguous (more than one product, or another version's: check before selecting),
        candidates, recovered ({group_addresses, links, parameters, unknown_parameters,
        order_info} once read back, else null), verify_changed_bytes (0 = verified), applied
        (in the project), error (why adding it failed), details}."""
        return state()

    @tool("read", open_world=True)
    def recover_scan(start: str, end: str, timeout: float = 600.0) -> dict[str, Any]:
        """Scan the individual addresses ``start`` to ``end`` (e.g. "1.1.1" and "1.1.255") on
        the bus for devices and identify each against the catalog, replacing the last scan; the
        products of devices the catalog lacks are fetched from the online catalog afterwards.
        Only reads from the devices. Waits up to ``timeout`` s for the scan (and the fetch) to
        end; returns the state as recover_status does (``busy`` true when it still runs).
        Unambiguous recoverable devices come selected for the read-back."""
        try:
            from xknxeditor.recover import iter_addresses

            count = sum(1 for _ in iter_addresses(start, end))
        except Exception as exc:
            raise ToolError(f"invalid range {start}..{end}: {exc}") from exc
        if count == 0:
            raise ToolError(f"empty range {start}..{end}")
        recover = service()
        return run_operation(
            "the scan", lambda: recover.start_scan(start, end), timeout
        )

    @tool("write")
    def recover_select(addresses: list[str], selected: bool = True) -> dict[str, Any]:
        """Select (``selected`` true) or deselect the scanned devices at ``addresses`` (their
        individual addresses, e.g. ["1.1.3"]) for the read-back, as the window's checkboxes
        do; only recoverable devices (a product in the catalog) can be selected. Returns the
        state as recover_status does."""
        recover = service()

        def _select() -> None:
            if recover.busy:
                raise ToolError("the recovery is running; wait or recover_stop first")
            targets = entries_at(addresses)
            refused = [e.address for e in targets if selected and not e.recoverable]
            if refused:
                raise ToolError(f"no product in the catalog for: {', '.join(refused)}")
            for entry in targets:
                entry.selected = selected

        ctx.run_on_ui(_select)
        return state()

    @tool("read", open_world=True)
    def recover_read_back(timeout: float = 1800.0) -> dict[str, Any]:
        """Read the selected devices back from the bus: their parameters, group addresses,
        links and flags (Recover Selected). Only reads from the devices. With auto_apply on and
        a project open, each device read is added to it (as recover_add_to_project does) when
        recover_status or this tool sees it. Waits up to ``timeout`` s; returns the state as
        recover_status does."""
        recover = service()
        if not any(e.selected and e.recoverable for e in recover.entries):
            raise ToolError(
                "no recoverable device selected; recover_scan, recover_select"
            )
        result = run_operation("the read-back", recover.start_recover, timeout)
        if recover.auto_apply:
            ctx.run_locked(recover.poll)
            result = state()
        return result

    @tool("read", open_world=True)
    def recover_verify(timeout: float = 1800.0) -> dict[str, Any]:
        """Check each read-back device against the bus: encode its group communication as read
        and compare with the device's memory (Verify); only reads. Sets each entry's
        verify_changed_bytes (0 = verified). Waits up to ``timeout`` s; returns the state as
        recover_status does."""
        recover = service()
        if not any(e.recovered is not None for e in recover.entries):
            raise ToolError("no device read back yet; recover_read_back first")
        return run_operation("the verification", recover.start_verify, timeout)

    @tool("write")
    def recover_stop() -> dict[str, Any]:
        """Stop the running scan, read-back or verification after the current device (Stop).
        Returns the state as recover_status does."""
        recover = service()
        ctx.run_on_ui(recover.stop)
        return state()

    @tool("write")
    def recover_set_auto_apply(enabled: bool) -> dict[str, Any]:
        """Whether devices read back are added to the open project right away (the window's
        checkbox, on by default). Returns the state as recover_status does."""
        recover = service()

        def _set() -> None:
            recover.auto_apply = enabled

        ctx.run_on_ui(_set)
        return state()

    @tool("write")
    def recover_add_to_project() -> dict[str, Any]:
        """Add every device read back and not yet added to the open project (Add to Open
        Project), trying again the ones an earlier add could not add; each device is one
        undoable step, on the line of its address (a line or segment it needs is created). A
        device whose add fails changes nothing; its entry's error says why. Returns {added,
        failed, ...the state as recover_status does}."""
        recover = service()
        require_project(ctx)

        def _apply() -> int:
            return recover.apply_to_open_project()

        added = ctx.run_locked(_apply, timeout=_SLOW)
        current = state()
        failed = sum(1 for e in current["entries"] if e["error"] and not e["applied"])
        return {"added": added, "failed": failed, **current}

    @tool("destructive")
    def recover_add_to_new_project(path: str) -> dict[str, Any]:
        """Create a new project at ``path`` (a ".xknx" file; the extension is added when
        missing), replacing the open one, and add every device read back to it (Add to New
        Project). Returns {added, failed, path, ...the state as recover_status does}."""
        recover = service()
        if not any(e.recovered is not None for e in recover.entries):
            raise ToolError("no device read back yet; recover_read_back first")
        target = Path(path)
        if target.suffix != ".xknx":
            target = target.with_suffix(".xknx")

        def _apply() -> int:
            return recover.create_project_and_apply(target)

        added = ctx.run_locked(_apply, timeout=_SLOW)
        current = state()
        failed = sum(1 for e in current["entries"] if e["error"] and not e["applied"])
        return {"added": added, "failed": failed, "path": str(target), **current}

    @tool("read")
    def recover_snapshot() -> dict[str, Any]:
        """Everything read back from each device, as the window's Export Snapshot writes it:
        {devices: [...]} with the raw tables, parameters and identification per device."""
        recover = service()
        snapshot: dict[str, Any] = json.loads(ctx.run_on_ui(recover.snapshot_text))
        return snapshot
