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


def test_a_progress_wait_ticks_until_the_operation_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a tick callback the wait wakes every poll interval (to push progress) instead of
    blocking on one long ``result`` call, and still returns the result once it arrives."""
    from editor_gui.plugins.mcp.tools import connection

    monkeypatch.setattr(connection, "_PROGRESS_POLL", 0.01)
    future: Future[Any] = Future()
    ticks = 0

    def on_tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            future.set_result("done")

    assert connection._await_result(future, 5.0, on_tick) == "done"
    assert ticks == 3


def test_a_progress_wait_still_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from editor_gui.plugins.mcp.tools.connection import _await_result

    monkeypatch.setattr("editor_gui.plugins.mcp.tools.connection._PROGRESS_POLL", 0.01)
    future: Future[Any] = Future()
    with pytest.raises(TimeoutError):
        _await_result(future, 0.05, lambda: None)


def test_a_progress_wait_returns_a_future_that_finished_just_past_the_deadline() -> (
    None
):
    """A result set during a (possibly slow) tick must be returned, not reported as a timeout."""
    from editor_gui.plugins.mcp.tools.connection import _await_result

    future: Future[Any] = Future()
    future.set_result("done")
    assert _await_result(future, 0.0, lambda: None) == "done"


def test_a_progress_wait_surfaces_a_future_that_raised_timeout_without_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future that itself completed with a TimeoutError must surface at once, not spin the poll
    loop (which cannot tell that exception from a per-poll timeout) until the deadline."""
    from editor_gui.plugins.mcp.tools import connection

    monkeypatch.setattr(connection, "_PROGRESS_POLL", 0.01)
    future: Future[Any] = Future()
    future.set_exception(TimeoutError("the device did not answer"))
    ticks = 0

    def on_tick() -> None:
        nonlocal ticks
        ticks += 1

    with pytest.raises(TimeoutError, match="did not answer"):
        connection._await_result(future, 5.0, on_tick)
    assert ticks == 0  # surfaced immediately, no spinning


def test_the_download_progress_reporter_forwards_the_running_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reporter reads the running download's ``(done, total)`` for its own address and forwards
    it; it is None without a client context or an address, and sends nothing while nothing runs."""
    from editor_gui.plugins.mcp.tools import connection

    sent: list[tuple[float, float | None, str | None]] = []
    monkeypatch.setattr(
        connection, "send_progress", lambda ctx, p, t, m: sent.append((p, t, m))
    )

    class _Download:
        def __init__(self, progress: tuple[int, int] | None) -> None:
            self.progress = progress

        def busy_progress_for(self, address: str) -> tuple[int, int] | None:
            return self.progress if address == "1.1.5" else None

    service = _Download((3, 10))
    tick = connection._download_progress(service, object(), "1.1.5")
    assert tick is not None
    tick()
    assert sent == [(3.0, 10.0, "Programming")]

    service.progress = None  # no load control running yet
    tick()
    assert sent == [(3.0, 10.0, "Programming")]  # unchanged

    # No context, no address -> no reporter.
    assert connection._download_progress(service, None, "1.1.5") is None
    assert connection._download_progress(service, object(), None) is None


def test_a_queued_operation_does_not_report_another_operations_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``busy_progress_for`` ties the ownership check to the read, so a wait for device B reports
    nothing while device A holds the bus, and only B's own progress once B runs."""
    from editor_gui.plugins.connection.service import ConnectionService

    sent: list[tuple[float, float | None, str | None]] = []
    monkeypatch.setattr(
        "editor_gui.plugins.mcp.tools.connection.send_progress",
        lambda ctx, p, t, m: sent.append((p, t, m)),
    )
    from editor_gui.plugins.mcp.tools import connection

    service = ConnectionService.__new__(ConnectionService)
    import threading

    service._busy_lock = threading.Lock()
    service._busy = ("program", "1.1.1")  # device A running
    service._busy_progress = (9, 10)

    tick_b = connection._download_progress(service, object(), "1.1.2")  # waiting for B
    assert tick_b is not None
    tick_b()
    assert sent == []  # A's progress is not reported under B's token

    service._busy = ("program", "1.1.2")  # hand-off: now B holds the bus
    service._busy_progress = (1, 20)
    tick_b()
    assert sent == [(1.0, 20.0, "Programming")]


def test_send_progress_without_a_context_is_a_noop() -> None:
    from editor_gui.plugins.mcp.context import send_progress

    assert send_progress(None, 1.0, 2.0, "x") is None
