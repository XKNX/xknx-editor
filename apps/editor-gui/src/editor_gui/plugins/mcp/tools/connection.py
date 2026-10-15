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

    def _project_serial(node_id: int) -> bytes | None:
        from editor_gui.device_info import serial_from_project

        info = ctx.run_locked(lambda: ctx.api.project.get_device_info(node_id))
        return serial_from_project(info.serial_number) if info is not None else None

    @tool
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
        """Set and persist the gateway/connection settings (applied on the next connect)."""
        conn.configure(controller_ip, multicast_group, routing)
        return {"status": "configured"}

    @tool
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

    @tool
    def connection_connect(timeout: float = 15.0) -> dict[str, Any]:
        """Connect using the configured gateway IP (tunneling). Waits until connected or fails."""
        conn.connect()
        return _await_connect(timeout)

    @tool
    def connection_connect_gateway(ip: str, timeout: float = 15.0) -> dict[str, Any]:
        """Connect to a discovered gateway by IP (run connection_scan first). Waits for the result."""
        if not conn.connect_to_gateway_by_ip(ip):
            raise ToolError(
                f"no scanned gateway with ip {ip!r}; run connection_scan first"
            )
        return _await_connect(timeout)

    @tool
    def connection_disconnect() -> dict[str, Any]:
        """Disconnect from the bus. Returns the resulting connection state."""
        conn.disconnect()
        return {"status": "disconnecting", "state": conn.state.value}

    @tool
    def connection_program_device(
        node_id: int,
        scope: Scope | None = None,
        timeout: float = 300.0,
        without_plugin: bool = False,
        current_address: str | None = None,
        use_serial_number: bool = False,
    ) -> dict[str, Any]:
        """Download the device's application/parameters/links onto the bus (``timeout`` seconds).

        Needs an open connection and the device's individual address, and the device must physically
        be on the bus. NEW device (first commissioning): set the address in the project
        (project_set_individual_address), assign it to the device in programming mode
        (connection_assign_individual_address), then program with scope FULL. ALREADY-programmed
        device: run connection_evaluate_device ONCE to preview the diff, then program.

        ``scope`` — pick the one matching what you changed (all scopes run the same load procedure,
        just filtered, and only changed bytes are written; only the two address scopes program the
        individual address):
        - PARAMETERS: parameter values only.
        - GROUP_COMMUNICATION: group-address links only (address/association/group-object tables).
        - APPLICATION: parameters + com-object (group-object) table, but NOT the group-address links.
        - FULL (default): everything = APPLICATION + GROUP_COMMUNICATION. Use when a change touches
          BOTH the application AND the links (e.g. changing a push-button function type AND relinking
          its objects) — no single partial scope covers both — or when unsure. Not heavier than the
          partials (same procedure, delta writes), it just does not re-flash firmware or the address.
        - UNLOAD: remove the application (reset the device to unloaded).
        - INDIVIDUAL_ADDRESS: only the device's project address, to the single device in
          programming mode; waits until someone presses its programming button (until ``timeout``).
          Refused when another device already uses the address.
        - OVERWRITE_INDIVIDUAL_ADDRESS: change the address of the device that currently answers at
          ``current_address`` to the project address, without pressing its programming button (it
          is switched to programming mode remotely).

        ``use_serial_number`` addresses the device by the serial number stored in the project
        (project_set_serial_number) instead of programming mode: for INDIVIDUAL_ADDRESS, and for a
        FULL download of a device that does not answer at its address yet.

        A device whose hardware is marked NoDownloadWithoutPlugin is refused unless
        ``without_plugin`` is set - only after the user explicitly confirmed programming it without
        the manufacturer's plugin, which may leave it misconfigured."""
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
        try:
            future = service.program_device(
                device,
                resolved,
                group_communication,
                without_plugin=without_plugin,
                current_address=current_address,
                serial_number=serial,
                ip_config=ctx.run_locked(
                    lambda: ctx.api.project.ip_download_config(device)
                ),
                tool_key=tool_key,
            )
        except DeviceCertificateRequired as exc:
            raise ToolError(
                f"device {node_id} is commissioned securely for the first time: add its "
                "device certificate (the FDSK from its label) with "
                "project_add_device_certificate first"
            ) from exc
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
        if getattr(result, "tool_key_loaded", False):
            ctx.run_locked(lambda: ctx.api.project.set_tool_key_loaded(device))
        return {"status": "programmed", "node_id": node_id}

    @tool
    def connection_evaluate_device(
        node_id: int, scope: Scope | None = None, timeout: float = 120.0
    ) -> dict[str, Any]:
        """Preview what a download would change on an ALREADY-programmed device (``timeout`` seconds).

        Reads the live device point-to-point and diffs it against the generated image. Meaningful
        only for a device that is already commissioned and answers at its individual address — for a
        NEW/virgin device it fails (nothing responds), so skip it and program directly with scope
        FULL. Needs an open connection and the device's individual address."""
        device, group_communication = _programmable_device(node_id)
        resolved = _resolve_scope(scope)
        if resolved.individual_address_only:
            raise ToolError("an individual address download cannot be evaluated")
        future = service.evaluate_device(
            device,
            resolved,
            group_communication,
            ctx.run_locked(lambda: ctx.api.project.ip_download_config(device)),
        )
        if future is None:
            raise ToolError("evaluation could not be scheduled (no connection)")
        report = future.result(timeout=timeout)
        return {
            "node_id": node_id,
            "changed_bytes": report.total_changed_bytes,
            "changed_segments": len(report.changed_segments),
            "changed_properties": len(report.changed_properties),
            "summary": report.summary(),
        }

    @tool
    def connection_assign_individual_address(
        node_id: int, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Write the device's project individual address to a device in programming mode.

        Needs an open connection and a device in programming mode on the bus. ``timeout`` seconds."""
        _ensure_connected()
        device = _device(node_id)
        if not device.individual_address:
            raise ToolError(
                f"device {node_id} has no individual address; set one with "
                "project_set_individual_address first"
            )
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

    @tool
    def connection_read_programming_mode_devices(
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Read the individual addresses of devices currently in programming mode (needs a connection)."""
        future = service.read_programming_mode_devices()
        if future is None:
            raise ToolError("not connected")
        addresses = {str(a) for a in future.result(timeout=timeout)}
        return {"addresses": sorted(addresses, key=address_order)}
