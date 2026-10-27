"""Bus operations through MCP: a call that cannot start says why, and one that does not end in
time is canceled, so it does not keep the bus from the next call."""

from __future__ import annotations

import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.mcp.tools.connection import await_operation, not_started

from .test_operations import _devices
from .test_server import _FIXTURE, _build_ctx


class _Service:
    """The parts of the connection service the waits read."""

    def __init__(self, busy: tuple[str, str] | None = None, xknx: Any = None) -> None:
        self.busy = busy
        self.xknx = xknx
        self.canceled: list[Future[Any]] = []

    @property
    def busy_operation(self) -> tuple[str, str] | None:
        return self.busy

    def cancel_operation(self, future: Future[Any]) -> None:
        self.canceled.append(future)
        future.cancel()


def test_another_operation_on_the_bus_is_named() -> None:
    service: Any = _Service(busy=("test", "1.1.25"), xknx=object())
    assert str(not_started(service, "the evaluation")) == (
        "the evaluation could not start: a download preview (1.1.25) is still running on "
        "the bus; wait until it has ended"
    )
    service.busy = ("recover", "1.0.1..1.0.20")
    assert "a project recovery (1.0.1..1.0.20)" in str(
        not_started(service, "programming")
    )
    # An operation kind without a name of its own keeps its kind.
    service.busy = ("calibrate", "1.1.1")
    assert "calibrate (1.1.1)" in str(not_started(service, "the restart"))


def test_a_missing_connection_and_other_reasons() -> None:
    assert str(not_started(_Service(), "the reset")) == (
        "the reset could not start (no KNX connection)"
    )
    assert "see the editor log" in str(
        not_started(_Service(xknx=object()), "the reset")
    )


def test_an_operation_ending_in_time_returns_its_result() -> None:
    service: Any = _Service()
    future: Future[Any] = Future()
    future.set_result(42)
    assert await_operation(service, future, 1.0, "the read") == 42
    assert service.canceled == []


def test_an_operation_failing_raises_its_error() -> None:
    service: Any = _Service()
    future: Future[Any] = Future()
    future.set_exception(ValueError("no answer"))
    with pytest.raises(ValueError, match="no answer"):
        await_operation(service, future, 1.0, "the read")
    assert service.canceled == []


def test_an_operation_running_too_long_is_canceled() -> None:
    service: Any = _Service()
    future: Future[Any] = Future()
    started = time.monotonic()
    with pytest.raises(ToolError) as raised:
        await_operation(service, future, 0.1, "the evaluation")
    assert str(raised.value) == (
        "the evaluation did not finish within 0.1 s and was canceled"
    )
    assert service.canceled == [future]
    # It does not wait out the unwind time for an operation that is already canceled.
    assert time.monotonic() - started < 5


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_an_evaluation_running_too_long_frees_the_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview that does not end within its timeout is canceled; until then a second one
    is refused with the running operation named, afterwards it starts again."""
    ctx = _build_ctx(tmp_path)
    connection = ctx.api.connection
    monkeypatch.setattr(ConnectionService, "xknx", property(lambda self: object()))
    started: list[Future[Any]] = []

    def evaluate_device(device: Any, *args: Any) -> Future[Any] | None:
        # As the service does: the operation holds the bus until its future ends.
        if not connection.begin_operation("test", device.individual_address):
            return None
        future: Future[Any] = Future()
        future.add_done_callback(lambda _: connection.end_operation())
        started.append(future)
        return future

    monkeypatch.setattr(connection, "evaluate_device", evaluate_device)
    async with Client(build_server(ctx)) as client:
        (node_id,) = await _devices(client, tmp_path, "1.1.25")
        with pytest.raises(
            ToolError, match=r"did not finish within 0\.2 s and was canceled"
        ):
            await client.call_tool(
                "connection_evaluate_device", {"node_id": node_id, "timeout": 0.2}
            )
        assert started[0].cancelled()
        assert connection.busy_operation is None

        assert connection.begin_operation("program", "1.1.6")
        with pytest.raises(ToolError, match=r"a download \(1\.1\.6\) is still running"):
            await client.call_tool("connection_evaluate_device", {"node_id": node_id})
        connection.end_operation()

        with pytest.raises(ToolError, match="did not finish"):
            await client.call_tool(
                "connection_evaluate_device", {"node_id": node_id, "timeout": 0.2}
            )
    assert len(started) == 2


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_the_evaluation_defaults_to_the_download_timeout(tmp_path: Path) -> None:
    async with Client(build_server(_build_ctx(tmp_path))) as client:
        tools = {t.name: t for t in await client.list_tools()}
    evaluate = tools["connection_evaluate_device"].input_schema["properties"]["timeout"]
    program = tools["connection_program_device"].input_schema["properties"]["timeout"]
    assert evaluate["default"] == program["default"] == 300.0
