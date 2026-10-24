# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Connection and programming tools (BUS): connect/scan/disconnect, program/evaluate/assign.

Bus operations go through the connection service, which schedules onto the KNX event loop and returns
a ``concurrent.futures.Future``; these tools await that future. Any device object needed to build the
request is fetched on the imgui thread first (``ctx.run_on_ui``).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.device import address_order
from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import Device

# Download scopes (schema enum); maps to xknxeditor.download.scope.DownloadScope in _resolve_scope.
Scope = Literal[
    "FULL",
    "PARAMETERS",
    "GROUP_COMMUNICATION",
    "PARAMETERS_AND_GROUP_COMMUNICATION",
    "APPLICATION",
    "UNLOAD",
    "UNLOAD_ALL",
    "INDIVIDUAL_ADDRESS",
    "OVERWRITE_INDIVIDUAL_ADDRESS",
]


def _resolve_scope(scope: str | None) -> Any:
    from xknxeditor.download.scope import DownloadScope

    if scope is None:
        return DownloadScope.FULL
    try:
        return DownloadScope[scope.upper()]
    except KeyError as exc:
        valid = ", ".join(s.name for s in DownloadScope)
        raise ToolError(f"unknown scope {scope!r}; expected one of: {valid}") from exc


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    conn = ctx.connection_plugin
    service = ctx.api.connection

    def _ensure_connected() -> None:
        if ctx.api.connection.xknx is None:
            raise ToolError(
                "no KNX connection; connect first with connection_connect (configured gateway) "
                "or connection_scan + connection_connect_gateway"
            )

    def _device(node_id: int) -> Device:
        device = ctx.run_locked(lambda: ctx.api.project.find_device_by_node_id(node_id))
        if device is None:
            raise ToolError(
                f"no device with node_id {node_id}; call project_list_devices for valid ids"
            )
        return device

    def _programmable_device(node_id: int) -> tuple[Device, Any]:
        """Precondition-check a device for a bus operation and build its GroupCommunication.

        The download must include the project's group-address/association data (the GUI supplies it);
        building it reads project links, so it happens on the imgui thread with the device. Raises a
        specific error for each distinct fix (no connection / unknown device / no address)."""
        _ensure_connected()
        device = _device(node_id)
        if not device.individual_address:
            raise ToolError(
                f"device {node_id} has no individual address; set one with "
                "project_set_individual_address before programming"
            )
        group_communication = ctx.run_locked(
            lambda: ctx.api.project.group_communication_for(device)
        )
        return device, group_communication

    def _certificate_error(node_id: int) -> ToolError:
        return ToolError(
            f"device {node_id} is commissioned securely for the first time: add its "
            "device certificate (the FDSK from its label) with "
            f"project_add_device_certificate(code, node_id={node_id}) first, or deactivate "
            "its secure commissioning "
            "with project_set_secure_commissioning to program it in the clear"
        )

    def _run_queued(node_id: int, scope: Any, timeout: float, **options: Any) -> bool:
        """Run a programming in the editor's operations queue, labelled as started by an MCP
        client, so the user sees it there (also in the history) and can cancel it; it waits behind
        operations already queued. Returns False when the editor has no queue (no project plugin,
        e.g. a headless server), so the caller programs directly. Raises ToolError when the
        operation fails, is canceled, or does not end within ``timeout`` (it is canceled then)."""
        queue = ctx.api.queue_program
        if queue is None:
            return False
        from editor_gui.plugins.project.program_queue import ORIGIN_MCP, OperationState
        from editor_gui.plugins.project.strings import S

        item = ctx.run_locked(
            lambda: queue(node_id, scope, origin=ORIGIN_MCP, **options)
        )
        if item is None or item.ended is None:
            raise ToolError(f"no device with node_id {node_id}")
        try:
            ended = item.ended.result(timeout=timeout)
        except TimeoutError as exc:
            cancel = ctx.api.cancel_operation
            if cancel is not None:
                ctx.run_on_ui(lambda: cancel(item))
            raise ToolError(
                f"the operation did not finish within {timeout} s and was canceled"
            ) from exc
        if ended.state is OperationState.FINISHED:
            return True
        if ended.message == S.DEVICE_CERTIFICATE_REQUIRED:
            raise _certificate_error(node_id)
        if ended.state is OperationState.CANCELED:
            raise ToolError(ended.message or S.OPERATION_CANCELED_BY_USER)
        raise ToolError(ended.message or "the operation failed")

    def _project_serial(node_id: int) -> bytes | None:
        from editor_gui.device_info import serial_from_project

        info = ctx.run_locked(lambda: ctx.api.project.get_device_info(node_id))
        return serial_from_project(info.serial_number) if info is not None else None

    @tool(read_only=True)
    def connection_status() -> dict[str, Any]:
        """Current connection state and target."""
        return {
            "state": conn.state.value,
            "connected": conn.connected,
            "controller_ip": conn.controller_ip,
            "multicast_group": conn.multicast_group,
            "routing": conn.is_routing,
            "busmonitor": ctx.api.connection.busmonitor_state.value,
        }

    @tool
    def connection_configure(
        controller_ip: str, multicast_group: str = "", routing: bool = False
    ) -> dict[str, str]:
        """Persist the gateway/connection settings. Empty or whitespace-only ``controller_ip`` /
        ``multicast_group`` strings keep their previous value; non-empty strings replace them.
        ``routing`` here is only the stored preference — connection_connect always uses tunnelling,
        connection_connect_gateway prefers tunnelling and otherwise routing. Disconnect before
        changing an active connection's target."""
        conn.configure(controller_ip, multicast_group, routing)
        return {"status": "configured"}

    @tool(open_world=True)
    def connection_scan(timeout: float = 6.0) -> dict[str, Any]:
        """Discover KNX gateways on the network (``timeout`` in seconds).

        Returns ``{complete, count, gateways}``; ``complete`` is false if the timeout elapsed while a
        scan was still running (the gateway list may then be incomplete). Connect with
        connection_connect_gateway(ip)."""
        conn.scan()
        deadline = time.monotonic() + timeout
        while conn.scanning and time.monotonic() < deadline:
            time.sleep(0.1)
        gateways = [
            {
                "name": g.name,
                "ip": g.ip_addr,
                "port": g.port,
                "individual_address": str(g.individual_address)
                if g.individual_address
                else None,
                "supports_tunnelling": g.supports_tunnelling,
                "supports_routing": g.supports_routing,
                "supports_secure": g.supports_secure,
            }
            for g in conn.gateways
        ]
        return {
            "complete": not conn.scanning,
            "count": len(gateways),
            "gateways": gateways,
        }

    def _await_connect(timeout: float) -> dict[str, Any]:
        from editor_gui.plugins.connection.plugin import ConnectionState

        deadline = time.monotonic() + timeout
        while conn.state == ConnectionState.CONNECTING and time.monotonic() < deadline:
            time.sleep(0.1)
        if conn.state != ConnectionState.CONNECTED:
            raise ToolError(f"connect failed (state: {conn.state.value})")
        return {"state": conn.state.value, "connected": True}

    @tool(open_world=True)
    def connection_connect(timeout: float = 15.0) -> dict[str, Any]:
        """Connect using the configured gateway IP (tunneling). Waits until connected or fails."""
        conn.connect()
        return _await_connect(timeout)

    @tool(open_world=True)
    def connection_connect_gateway(ip: str, timeout: float = 15.0) -> dict[str, Any]:
        """Connect to a discovered gateway by IP (run connection_scan first). Waits for the result."""
        if not conn.connect_to_gateway_by_ip(ip):
            raise ToolError(
                f"no scanned gateway with ip {ip!r}; run connection_scan first"
            )
        return _await_connect(timeout)

    @tool(open_world=True)
    def connection_disconnect() -> dict[str, Any]:
        """Disconnect from the bus. Returns the resulting connection state."""
        conn.disconnect()
        return {"status": "disconnecting", "state": conn.state.value}

    @tool(destructive=True, open_world=True)
    def connection_program_device(
        node_id: int,
        scope: Scope | None = None,
        timeout: float = 300.0,
        without_plugin: bool = False,
        current_address: str | None = None,
        use_serial_number: bool = False,
    ) -> dict[str, Any]:
        """Download the device (``node_id`` from project_list_devices) onto the bus (``timeout``
        seconds). Needs an open connection, a project individual address, and the device physically on
        the bus; secure devices need a matching loaded keyring. Bus changes are NOT undoable with
        project_undo, and a failure or timeout may leave partial changes.

        ALREADY-programmed device: run connection_evaluate_device once to preview the diff, then
        program with the scope matching your change. NEW device: assign its address first
        (project_set_individual_address, then connection_assign_individual_address with the device in
        programming mode), then program with scope FULL.

        ``scope`` (default FULL) selects which loadable parts run; the procedure may reload/restart
        the device, so do not assume only changed bytes are written:
        - PARAMETERS: parameter values only.
        - GROUP_COMMUNICATION: the group-address link tables (address/association/group-object).
        - PARAMETERS_AND_GROUP_COMMUNICATION: both of the above — the scope for a change that touches
          parameters AND links (e.g. changing a push-button function type AND relinking its objects).
        - APPLICATION: the complete application load — parameters, com-object table, AND the
          group-address link tables (like FULL, without the standalone address-assignment step).
        - FULL: the full application load; for a device not yet answering it can also assign the
          project address first (optionally via ``use_serial_number``).
        - UNLOAD: remove the application. UNLOAD_ALL also resets the single device in programming mode
          to 15.15.255 — make sure that is the intended device.
        - INDIVIDUAL_ADDRESS: write ONLY the project address to the single device in programming mode
          (waits until its programming button is pressed, up to ``timeout``); refused when another
          device already uses the address. OVERWRITE_INDIVIDUAL_ADDRESS writes it to the device
          currently answering at ``current_address`` (required), switching it to programming mode
          remotely.

        ``use_serial_number`` addresses the device by its project serial (project_set_serial_number)
        instead of programming mode — for INDIVIDUAL_ADDRESS, or a FULL commissioning download of a
        device that does not answer at its address yet. A device whose hardware is
        NoDownloadWithoutPlugin is refused unless ``without_plugin=true``, set only after the user
        explicitly confirmed programming it without the manufacturer's plugin.

        The programming runs in the editor's operations queue, labelled MCP: the user sees it there
        and in its history, it waits behind operations already queued, and the user can cancel it
        (the call then fails with the cancel message). Returns {status, node_id}."""
        device, group_communication = _programmable_device(node_id)
        resolved = _resolve_scope(scope)
        if resolved.name == "OVERWRITE_INDIVIDUAL_ADDRESS" and not current_address:
            raise ToolError("OVERWRITE_INDIVIDUAL_ADDRESS needs current_address")
        serial = None
        if use_serial_number:
            if resolved.name not in ("INDIVIDUAL_ADDRESS", "FULL"):
                raise ToolError(
                    "use_serial_number applies to INDIVIDUAL_ADDRESS and FULL only"
                )
            serial = _project_serial(node_id)
            if serial is None:
                raise ToolError(
                    f"device {node_id} has no serial number; set one with "
                    "project_set_serial_number"
                )
        if (
            device.download_plugin_required
            and not without_plugin
            and not resolved.individual_address_only
        ):
            raise ToolError(
                f"device {node_id} can only be programmed with the manufacturer's plugin "
                "(its hardware is marked NoDownloadWithoutPlugin); pass without_plugin=true only "
                "if the user explicitly confirmed programming it anyway"
            )
        from editor_gui.plugins.connection.service import DeviceCertificateRequired
        from xknxeditor.proj import ProjectPasswordRequired

        try:
            tool_key = ctx.run_locked(lambda: ctx.api.project.download_tool_key(device))
        except ProjectPasswordRequired as exc:
            raise ToolError(
                "the device is commissioned securely: set a project password first"
            ) from exc
        if _run_queued(
            node_id,
            resolved,
            timeout,
            without_plugin=without_plugin,
            current_address=current_address,
            serial_number=serial,
        ):
            return {"status": "programmed", "node_id": node_id}
        ip_secure = ctx.run_locked(
            lambda: ctx.api.project.ip_secure_config(device, service.keyring)
        )
        # Pre-read all project state on the UI thread (plain values), then run program_device
        # itself under the same lock: its synchronous security lookup reads the (non-thread-safe)
        # project session, which must not run on this MCP worker thread. The args are already plain
        # here, so there is no nested run_locked. The bus Future is awaited OUTSIDE the lock below.
        ip_config = ctx.run_locked(lambda: ctx.api.project.ip_download_config(device))
        disable_security = ctx.run_locked(
            lambda: ctx.api.project.download_disables_security(device)
        )
        routing_blocking = ctx.run_locked(
            lambda: ctx.api.project.download_routing_blocking(device)
        )
        try:
            future = ctx.run_locked(
                lambda: service.program_device(
                    device,
                    resolved,
                    group_communication,
                    without_plugin=without_plugin,
                    current_address=current_address,
                    serial_number=serial,
                    ip_config=ip_config,
                    tool_key=tool_key,
                    ip_secure=ip_secure,
                    disable_security=disable_security,
                    routing_blocking=routing_blocking,
                )
            )
        except DeviceCertificateRequired as exc:
            raise _certificate_error(node_id) from exc
        if future is None:
            raise ToolError("programming could not be scheduled (no connection)")
        try:
            result = future.result(timeout=timeout)
        except TimeoutError as exc:
            future.cancel()
            raise ToolError(f"programming did not finish within {timeout} s") from exc
        serial = result if isinstance(result, bytes) else None
        mac = getattr(result, "mac_address", None)
        ctx.run_locked(
            lambda: ctx.api.project.record_download(
                node_id, resolved.value, serial, mac
            )
        )
        ctx.run_locked(lambda: ctx.api.project.record_secure_download(device, result))
        if ip_secure is not None and getattr(result, "secured", False):
            ctx.run_locked(
                lambda: ctx.api.project.record_ip_secure_download(device, ip_secure)
            )
        return {"status": "programmed", "node_id": node_id}

    @tool(open_world=True)
    def connection_evaluate_device(
        node_id: int, scope: Scope | None = None, timeout: float = 120.0
    ) -> dict[str, Any]:
        """Preview what a download would change on an ALREADY-programmed device, without changing it.

        Reads the live device point-to-point and diffs it against the generated image. Needs an open
        connection and the device answering at its project individual address — a device that does not
        respond (e.g. a virgin/unprogrammed one) fails, so skip this and program a NEW device directly
        with scope FULL. ``scope`` defaults to FULL; the individual-address scopes are rejected.
        ``timeout`` in seconds.

        Returns {node_id, changed_bytes, config_bytes, default_bytes, runtime_bytes, config_locations,
        changed_segments, changed_properties, summary}. ``changed_bytes`` is the raw total; it is split
        into three classes, and ONLY ``config_bytes`` is a real configuration change you would push:
        - config_bytes: bytes carrying a configured parameter value / a property write (flags, tables).
        - default_bytes: bytes merely reset to the application default (the device holds an older value)
          — benign.
        - runtime_bytes: bytes the device firmware manages itself (e.g. a download counter) — benign,
          and they typically never converge to zero even right after a successful download.
        So a non-zero ``changed_bytes`` with ``config_bytes == 0`` (``config_locations == 0``) means the
        device already carries the configuration; do not treat that as a pending change. changed_segments/
        changed_properties are counts and do not describe unload/restart or address-reset effects."""
        device, group_communication = _programmable_device(node_id)
        resolved = _resolve_scope(scope)
        if resolved.individual_address_only:
            raise ToolError("an individual address download cannot be evaluated")
        # Run evaluate_device under the lock on the UI thread: its synchronous security lookup
        # reads the non-thread-safe project session and must not run on this MCP worker thread.
        # The bus Future is awaited outside the lock below.
        ip_config = ctx.run_locked(lambda: ctx.api.project.ip_download_config(device))
        future = ctx.run_locked(
            lambda: service.evaluate_device(
                device, resolved, group_communication, ip_config
            )
        )
        if future is None:
            raise ToolError("evaluation could not be scheduled (no connection)")
        report = future.result(timeout=timeout)
        # Classify the raw changed bytes (config vs. benign default/runtime) with the same logic
        # the GUI pre-flight uses; the classifiers read the device UI, so run them on the UI thread.
        from editor_gui.programming import (
            classify_preflight,
            parameter_driven_bits,
            runtime_managed_addresses,
        )

        runtime_addresses, driven_bits = ctx.run_locked(
            lambda: (runtime_managed_addresses(device), parameter_driven_bits(device))
        )
        classes = classify_preflight(report, runtime_addresses, driven_bits)
        return {
            "node_id": node_id,
            "changed_bytes": report.total_changed_bytes,
            "config_bytes": classes.config_bytes,
            "default_bytes": classes.default_bytes,
            "runtime_bytes": classes.runtime_bytes,
            "config_locations": classes.config_locations,
            "changed_segments": len(report.changed_segments),
            "changed_properties": len(report.changed_properties),
            "summary": report.summary(),
        }

    @tool(destructive=True, open_world=True)
    def connection_assign_individual_address(
        node_id: int, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Write the device's project individual address to the single device currently in
        programming mode. Needs an open connection and an unused project address set beforehand with
        project_set_individual_address (this does not check address occupancy); exactly one device
        must be in programming mode. This bus change is NOT undoable. ``timeout`` in seconds.

        It runs in the editor's operations queue, labelled MCP, like connection_program_device with
        scope INDIVIDUAL_ADDRESS: it waits until a device is in programming mode (its programming
        button pressed), up to ``timeout``, and the user can cancel it. Returns
        {status, node_id, address}."""
        _ensure_connected()
        device = _device(node_id)
        if not device.individual_address:
            raise ToolError(
                f"device {node_id} has no individual address; set one with "
                "project_set_individual_address first"
            )
        from xknxeditor.download.scope import DownloadScope

        if _run_queued(node_id, DownloadScope.INDIVIDUAL_ADDRESS, timeout):
            return {
                "status": "assigned",
                "node_id": node_id,
                "address": device.individual_address,
            }
        future = service.assign_individual_address_for_device(device)
        if future is None:
            raise ToolError("assignment could not be scheduled (no connection)")
        future.result(timeout=timeout)
        ctx.run_locked(lambda: ctx.api.project.record_download(node_id, "ia"))
        return {
            "status": "assigned",
            "node_id": node_id,
            "address": device.individual_address,
        }

    @tool(open_world=True)
    def connection_read_programming_mode_devices(
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Read the individual addresses of devices currently in programming mode (needs a connection)."""
        future = service.read_programming_mode_devices()
        if future is None:
            raise ToolError("not connected")
        addresses = {str(a) for a in future.result(timeout=timeout)}
        return {"addresses": sorted(addresses, key=address_order)}
