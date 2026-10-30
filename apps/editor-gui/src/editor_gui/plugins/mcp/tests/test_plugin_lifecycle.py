"""The embedded server is built and run on its own thread: the editor's start does not import
FastMCP, and a stop at any point leaves no server running."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.mcp import plugin
from editor_gui.ports import PortInUseError


def test_the_editor_does_not_import_fastmcp_to_start() -> None:
    code = (
        "import sys, editor_gui.main; "
        "print(sorted(m for m in ('fastmcp', 'mcp', 'uvicorn') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"


async def _app(scope: Any, receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while (await receive())["type"] != "lifespan.shutdown":
            await send({"type": "lifespan.startup.complete"})
        await send({"type": "lifespan.shutdown.complete"})


def _plugin(monkeypatch: pytest.MonkeyPatch, built: threading.Event) -> Any:
    def build(_ctx: Any, _auth: Any) -> Any:
        built.set()
        return _app

    monkeypatch.setattr(plugin, "build_http_app", build)
    monkeypatch.setattr(plugin.McpServerPlugin, "_context", lambda self: None)
    return plugin.McpServerPlugin(
        cast(Any, SimpleNamespace(log=LogService())),
        lambda *_a: None,
        cast(Any, None),
        cast(Any, None),
        cast(Any, None),
    )


def _wait(condition: Any, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_start_runs_the_server_on_its_thread_until_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = threading.Event()
    mcp = _plugin(monkeypatch, built)
    mcp.start("127.0.0.1", 0)
    assert mcp.is_running
    _wait(lambda: mcp._server is not None and mcp._server.started)
    assert built.is_set()
    mcp.stop()
    assert not mcp.is_running


def test_an_idle_connection_outlives_the_clients_reuse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clients reuse an idle connection for up to 5 s; the server must not close it first."""
    mcp = _plugin(monkeypatch, threading.Event())
    mcp.start("127.0.0.1", 0)
    try:
        _wait(lambda: mcp._server is not None and mcp._server.started)
        assert mcp._server.config.timeout_keep_alive == plugin.KEEP_ALIVE_SECONDS
        assert plugin.KEEP_ALIVE_SECONDS > 5
    finally:
        mcp.stop()


def test_a_stop_before_the_server_is_built_leaves_none_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = threading.Event()
    release = threading.Event()
    mcp = _plugin(monkeypatch, built)
    original = plugin.build_http_app

    def slow_build(ctx: Any, auth: Any) -> Any:
        release.wait(10)
        return original(ctx, auth)

    monkeypatch.setattr(plugin, "build_http_app", slow_build)
    mcp.start("127.0.0.1", 0)
    stopper = threading.Thread(target=mcp.stop)
    stopper.start()
    _wait(lambda: mcp._stop_requested)
    release.set()
    stopper.join(10)
    assert not mcp.is_running
    assert mcp._server is None


def test_a_port_in_use_is_reported_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = threading.Event()
    mcp = _plugin(monkeypatch, built)
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        with pytest.raises(PortInUseError) as raised:
            mcp.start("127.0.0.1", port)
    assert raised.value.port == port
    assert not mcp.is_running
    assert not built.is_set()
