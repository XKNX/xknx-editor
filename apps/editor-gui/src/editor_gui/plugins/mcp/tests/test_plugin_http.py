"""The embedded server must not be drivable by a web page the user happens to have open.

The MCP endpoint binds to loopback by default and is unauthenticated unless the user sets a token,
so without a Host/Origin guard any site can POST to it from the user's browser (DNS rebinding) and
reach tools that edit the project and program devices on a live KNX bus.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from fastmcp import FastMCP

from editor_gui.plugins.mcp import plugin, server
from editor_gui.plugins.mcp.context import McpContext

_MCP_PATH = "/mcp/"
_BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_HEADERS = {"Accept": "application/json, text/event-stream"}
_BASE_URL = "http://127.0.0.1:8765"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real ``build_http_app`` over a bare FastMCP, so the guard wiring is what is tested."""
    monkeypatch.setattr(server, "build_server", lambda ctx, auth=None: FastMCP("probe"))
    return plugin.build_http_app(cast("McpContext", None), None)


async def _post(app: Any, **headers: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        return await client.post(_MCP_PATH, json=_BODY, headers={**_HEADERS, **headers})


async def test_rejects_a_request_from_a_foreign_origin(app: Any) -> None:
    assert (await _post(app, Origin="https://evil.example")).status_code == 403


async def test_allows_a_request_without_an_origin_header(app: Any) -> None:
    """A normal MCP client (Claude Code, Codex, ...) sends no Origin and must be unaffected."""
    assert (await _post(app)).status_code != 403


async def test_allows_a_same_origin_request(app: Any) -> None:
    assert (await _post(app, Origin=_BASE_URL)).status_code != 403
