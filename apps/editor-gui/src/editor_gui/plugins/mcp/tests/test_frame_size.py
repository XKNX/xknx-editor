"""The frame size for downloads through an interface that does not report its maximum, read
and set through MCP as through Gateway > Standard Frames Only for Downloads."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from editor_gui import settings
from editor_gui.interface_apdu_cache import learned_interface_apdu
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path)


async def test_standard_frames_are_set_and_reported(tmp_path: Path) -> None:
    ctx = _build_ctx(tmp_path)
    ctx.api.connection.interface_target = "192.168.3.13"
    async with Client(build_server(ctx)) as client:
        status = (await client.call_tool("connection_status", {})).data
        assert status["standard_frames_only"] is False
        assert status["interface_max_apdu_length"] is None
        assert status["interface_reports_apdu"] is False

        result = await client.call_tool(
            "connection_set_standard_frames_only", {"enabled": True}
        )
        assert result.data == {
            "standard_frames_only": True,
            "interface_max_apdu_length": 15,
        }
        assert learned_interface_apdu("192.168.3.13") == 15
        status = (await client.call_tool("connection_status", {})).data
        assert status["standard_frames_only"] is True

        await client.call_tool(
            "connection_set_standard_frames_only", {"enabled": False}
        )
        assert learned_interface_apdu("192.168.3.13") is None


async def test_the_frame_size_is_refused_without_a_fitting_connection(
    tmp_path: Path,
) -> None:
    ctx = _build_ctx(tmp_path)
    async with Client(build_server(ctx)) as client:
        with pytest.raises(ToolError, match="not connected"):
            await client.call_tool(
                "connection_set_standard_frames_only", {"enabled": True}
            )
        ctx.api.connection.interface_target = "192.168.3.13"
        ctx.api.connection.interface_reports_apdu = True
        with pytest.raises(ToolError, match="reports its own maximum"):
            await client.call_tool(
                "connection_set_standard_frames_only", {"enabled": True}
            )
