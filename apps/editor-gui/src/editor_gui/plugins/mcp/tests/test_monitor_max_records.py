"""MCP tools for the most telegrams each monitor keeps (the Max field of its toolbar)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


async def test_the_caps_are_read_and_set(tmp_path: Path) -> None:
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        caps = await _data(client, "monitor_max_records")
        assert caps == {"group": 10_000, "bus": 10_000, "activity": 10_000}
        changed = await _data(client, "monitor_set_max_records", name="bus", value=3)
        assert changed == {"group": 10_000, "bus": 10, "activity": 10_000}
        with pytest.raises(Exception, match="unknown monitor"):
            await client.call_tool("monitor_set_max_records", {"name": "x", "value": 5})
