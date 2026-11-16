"""MCP: nothing is sent to a protected group address (Settings > Connection)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.connection.write_guard import set_protected
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx


async def test_a_protected_group_address_is_refused(tmp_path: Path) -> None:
    ctx = _build_ctx(tmp_path)
    sent: list[str] = []
    ctx.monitor.send_elsewhere = lambda address, _apci: sent.append(address) or True
    ctx.monitor.elsewhere_available = lambda: True
    mcp = build_server(ctx)
    set_protected(["7/0/"])
    try:
        async with Client(mcp) as client:
            for tool, args in (
                ("monitor_send_read", {"address": "7/0/1"}),
                ("monitor_send_read", {"address": "14337"}),
                ("monitor_read_value", {"address": "7/1", "timeout": 5.0}),
                (
                    "monitor_send_write",
                    {"address": "7/0/1", "value": "1", "dpt": "1.001"},
                ),
            ):
                with pytest.raises(Exception, match="protected group 7/0/"):
                    await client.call_tool(tool, args)
            await client.call_tool("monitor_send_read", {"address": "1/0/1"})
        assert sent == ["1/0/1"]
    finally:
        set_protected([])
