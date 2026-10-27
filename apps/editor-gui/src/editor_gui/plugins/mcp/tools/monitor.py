# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Group/bus monitor tools: read latest values and the telegram log, send GroupValue read/write."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    monitor = ctx.monitor

    def _require_connection() -> None:
        if ctx.api.connection.xknx is None:
            raise ToolError("no KNX connection")

    @tool("read")
    def monitor_latest(address: str) -> dict[str, Any]:
        """Read the cached value last seen on a group-address string such as "1/2/3"; no bus request
        is sent. Returns {address, service, timestamp, payload}; service is "Write" or "Response",
        timestamp is ISO-formatted, and payload is a raw payload string. Raises if no value was seen;
        a cached value may be stale."""
        value = monitor.latest(address)
        if value is None:
            raise ToolError(f"no value seen for {address!r}")
        return {
            "address": address,
            "service": value.service,
            "timestamp": value.timestamp.isoformat(),
            "payload": str(value.payload),
        }

    @tool("read")
    def monitor_telegrams(limit: int = 100) -> dict[str, Any]:
        """Read the Group Monitor's recorded group telegrams, oldest first. limit selects the most
        recent entries (<=0 means all). Returns {items, count}; each item has {timestamp, source,
        destination, service, payload}. timestamp is ISO-formatted; service is "Read", "Write" or
        "Response"; payload is a raw payload string or null for reads. count is the returned count."""
        records = monitor.telegrams()
        if limit > 0:
            records = records[-limit:]
        items = [
            {
                "timestamp": r.timestamp.isoformat(),
                "source": r.source,
                "destination": r.destination,
                "service": r.service,
                "payload": None if r.payload is None else str(r.payload),
            }
            for r in records
        ]
        return {"items": items, "count": len(items)}

    @tool("read", open_world=True)
    def monitor_read_value(address: str, timeout: float = 3.0) -> dict[str, Any]:
        """Send a GroupValueRead to a group-address string (e.g. "1/2/3") and wait up to timeout
        seconds for a fresh value. Requires an open connection; raises on timeout. Any Write or
        Response seen on that address since the request can satisfy the wait, including unrelated
        traffic. Returns {address, service, timestamp, payload}; timestamp is ISO-formatted and payload
        is a raw payload string. This sends a bus read without editing the project."""
        _require_connection()
        sent_at = datetime.now()
        monitor.send_read(address)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = monitor.latest(address)
            if value is not None and value.timestamp >= sent_at:
                return {
                    "address": address,
                    "service": value.service,
                    "timestamp": value.timestamp.isoformat(),
                    "payload": str(value.payload),
                }
            time.sleep(0.1)
        raise ToolError(
            f"no response on {address!r} within {timeout}s (device may not answer reads)"
        )

    def _kind(name: str) -> Any:
        from editor_gui.plugins.monitor.service import MonitorKind

        try:
            return MonitorKind(name)
        except ValueError:
            raise ToolError(
                f"unknown monitor {name!r}; one of group, bus, activity"
            ) from None

    @tool("read")
    def monitor_max_records() -> dict[str, int]:
        """The most telegrams each monitor keeps (the Max field in its toolbar): {group, bus,
        activity}; the oldest tenth is dropped when one is exceeded."""
        from editor_gui.plugins.monitor.service import MonitorKind

        return {kind.value: monitor.max_records(kind) for kind in MonitorKind}

    @tool("write")
    def monitor_set_max_records(name: str, value: int) -> dict[str, int]:
        """Set the most telegrams the monitor ``name`` (group, bus or activity) keeps, at
        least 10; it is trimmed down to it at once. Not part of the project's undo history.
        Returns the caps of all monitors (see monitor_max_records)."""
        from editor_gui.plugins.monitor.service import MonitorKind

        monitor.set_max_records(_kind(name), max(value, 10))
        return {kind.value: monitor.max_records(kind) for kind in MonitorKind}

    @tool("destructive")
    def monitor_clear() -> dict[str, str]:
        """Clear cached latest values and the Group Monitor log; active recording continues.
        Returns {status: "cleared"}. Cleared data cannot be restored with project_undo."""
        monitor.clear()
        return {"status": "cleared"}

    @tool("destructive", open_world=True)
    def monitor_send_write(
        address: str, value: str, dpt: str | None = None
    ) -> dict[str, str]:
        """Queue a GroupValueWrite to a group-address string such as "1/2/3". Requires an open
        connection. value is encoded using dpt, or the address's project DPT when omitted; missing
        or unsupported DPTs and values that cannot be encoded raise an error.
        Returns {status: "queued", address}; this does not confirm delivery or device action.
        This can actuate hardware and is not reversible with project_undo; it does not edit the
        project. Inspect fresh bus feedback to verify the effect."""
        _require_connection()
        resolved = dpt
        if resolved is None:
            resolved = ctx.run_locked(
                lambda: next(
                    (
                        g.datapoint_type
                        for g in ctx.api.project.group_addresses
                        if g.address == address
                    ),
                    None,
                )
            )
        if not monitor.send_write(address, resolved, value):
            raise ToolError(f"could not encode value {value!r} for DPT {resolved!r}")
        # Fire-and-forget: the value was encoded and queued onto the bus loop; actual delivery is not
        # awaited here. Read it back with monitor_latest to confirm the effect.
        return {"status": "queued", "address": address}

    @tool("read", open_world=True)
    def monitor_send_read(address: str) -> dict[str, str]:
        """Queue a GroupValueRead to a group-address string such as "1/2/3". Requires an open
        connection. Returns {status: "queued", address} without waiting for a reply; monitor_read_value
        waits for a fresh value. When polling monitor_latest, check timestamp to avoid stale data.
        This sends a bus read without editing the project."""
        _require_connection()
        monitor.send_read(address)
        return {"status": "queued", "address": address}
