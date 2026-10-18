# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Connection capture tools, backed by the Group Monitor and Bus Activity recordings.

Together the two monitors hold every frame of the normal connection: the Group Monitor the group
telegrams, Bus Activity everything else including every frame the app sends. These tools start,
stop, clear and read both recordings together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.telegram import GroupAddress, Telegram, TelegramDirection

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.plugins.monitor.frames import individual_address_text
from editor_gui.plugins.monitor.records import ticks_to_datetime
from editor_gui.plugins.monitor.service import LINK_LAYER_KINDS

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.monitor.records import MonitorRecord
    from editor_gui.plugins.monitor.service import MonitorKind, MonitorService

# Every frame of these recordings comes from the normal connection.
_SOURCE_TYPE = "connection"
_MONITOR_NAMES = {"group": "group_monitor", "activity": "bus_activity"}


def capture_state(monitor: MonitorService) -> dict[str, Any]:
    """Recording state and entry counts of the Group Monitor and Bus Activity together."""
    recording = {
        _MONITOR_NAMES[kind.value]: monitor.channel(kind).recording
        for kind in LINK_LAYER_KINDS
    }
    entries = monitor.merged_log()
    telegrams = sum(
        1
        for _kind, record in entries
        if record.frame is not None and record.frame.is_data
    )
    return {
        "state": "capturing" if any(recording.values()) else "stopped",
        "recording": recording,
        "telegrams": telegrams,
        "cemi_records": len(entries),
    }


def _telegram(record: MonitorRecord) -> Telegram | None:
    """The xknx telegram of an L_Data frame, or ``None``."""
    try:
        data = CEMIFrame.from_knx(record.raw).data
    except Exception:
        return None
    if not isinstance(data, CEMILData):
        return None
    return Telegram(
        source_address=data.src_addr,
        destination_address=data.dst_addr,
        direction=(
            TelegramDirection.INCOMING
            if record.from_bus
            else TelegramDirection.OUTGOING
        ),
        payload=data.payload,
        tpci=data.tpci,
    )


def _message_code(code: int) -> str:
    try:
        return CEMIMessageCode(code).name
    except ValueError:
        return f"0x{code:02x}"


def _cemi_item(kind: MonitorKind, record: MonitorRecord) -> dict[str, Any]:
    frame = record.frame
    src_addr = ""
    dst_addr = ""
    hops: int | None = None
    if frame is not None and frame.is_data:
        src_addr = individual_address_text(frame.source or 0)
        destination = frame.destination or 0
        dst_addr = (
            str(GroupAddress(destination))
            if frame.group
            else individual_address_text(destination)
        )
        hops = frame.hop_count
    return {
        "timestamp": ticks_to_datetime(record.ticks).isoformat(),
        "source_type": _SOURCE_TYPE,
        "monitor": _MONITOR_NAMES[kind.value],
        "msg_code": _message_code(record.raw[0]),
        "src_addr": src_addr,
        "dst_addr": dst_addr,
        "hops": hops,
        "raw": record.raw.hex(" "),
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    monitor = ctx.monitor

    @tool
    def network_state() -> dict[str, Any]:
        """Read recording state for the normal connection's Group Monitor and Bus Activity.
        Returns {state, recording, telegrams, cemi_records}; recording has {group_monitor, bus_activity}
        booleans, and state is "capturing" while either records, otherwise "stopped". telegrams counts
        buffered L_Data frames; cemi_records counts all buffered frames. Recording state does not
        establish whether a bus connection is open."""
        return capture_state(monitor)

    @tool
    def network_start() -> dict[str, str]:
        """Enable recording in the Group Monitor and Bus Activity for the normal connection.
        Existing entries are kept; network_clear clears them. Returns {status: "capturing"}.
        This enables local recording without opening a bus connection."""
        for kind in LINK_LAYER_KINDS:
            monitor.start(kind)
        return {"status": "capturing"}

    @tool
    def network_stop() -> dict[str, str]:
        """Stop Group Monitor and Bus Activity recording while keeping buffered entries and the
        normal bus connection unchanged. Returns {status: "stopped"}."""
        for kind in LINK_LAYER_KINDS:
            monitor.stop(kind)
        return {"status": "stopped"}

    @tool
    def network_clear() -> dict[str, str]:
        """Clear the Group Monitor and Bus Activity logs; active recording continues and cached
        latest values remain. Returns {status: "cleared"}. Cleared logs cannot be restored with
        project_undo."""
        for kind in LINK_LAYER_KINDS:
            monitor.clear_log(kind)
        return {"status": "cleared"}

    @tool
    def network_telegrams(limit: int = 100) -> dict[str, Any]:
        """Read decoded L_Data telegrams from the Group Monitor and Bus Activity, merged oldest first.
        limit selects the most recent decodable entries (<=0 means all). Returns {items, count}; each
        item has {timestamp, source_type, monitor, telegram}. timestamp is ISO-formatted, source_type
        is "connection", monitor is "group_monitor" or "bus_activity", and telegram is a display
        string. count is the returned count."""
        items: list[dict[str, Any]] = []
        for kind, record in reversed(monitor.merged_log()):
            if 0 < limit <= len(items):
                break
            telegram = _telegram(record)
            if telegram is None:
                continue
            items.append(
                {
                    "timestamp": ticks_to_datetime(record.ticks).isoformat(),
                    "source_type": _SOURCE_TYPE,
                    "monitor": _MONITOR_NAMES[kind.value],
                    "telegram": str(telegram),
                }
            )
        items.reverse()
        return {"items": items, "count": len(items)}

    @tool
    def network_cemi_records(limit: int = 100) -> dict[str, Any]:
        """Read raw cEMI frames from the Group Monitor and Bus Activity, merged oldest first.
        limit selects the most recent frames (<=0 means all). Returns {items, count}; each item has
        {timestamp, source_type, monitor, msg_code, src_addr, dst_addr, hops, raw}. timestamp is
        ISO-formatted, source_type is "connection", monitor is "group_monitor" or "bus_activity",
        and raw is space-separated hex. Non-data frames have empty addresses and null hops.
        count is the returned count."""
        entries = monitor.merged_log()
        if limit > 0:
            entries = entries[-limit:]
        items = [_cemi_item(kind, record) for kind, record in entries]
        return {"items": items, "count": len(items)}
