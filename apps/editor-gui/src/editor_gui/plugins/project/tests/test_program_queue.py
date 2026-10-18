"""Unit tests for the programming queue (no bus): sequencing, dedupe, cancel, advance, history."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any

from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
    ScriptRun,
)
from editor_gui.plugins.project.strings import S
from xknxeditor.download.scope import DownloadScope
from xknxeditor.prod.parser_v2.ui import UiButton
from xknxeditor.prod.script import ScriptAborted, ScriptError


class _Harness:
    """Drives a ProgramQueue with a controllable bus + futures, marshaling inline (UI-thread)."""

    def __init__(self, start_returns_none: bool = False) -> None:
        self.busy = False
        self.started: list[int] = []  # node_ids in start order
        self._futures: dict[int, Future[Any]] = {}
        self._start_none = start_returns_none
        # Futures asked to stop; like the connection's cancel_operation they end only later.
        self.cancel_requests: list[Future[Any]] = []
        self.queue = ProgramQueue(
            is_busy=lambda: self.busy,
            start=self._start,
            submit=None,  # run advance inline
            connection=lambda item: f"iface {item.node_id}",
            cancel=self.cancel_requests.append,
        )

    def _start(self, item: QueueItem) -> Future[Any] | None:
        if self._start_none:
            return None
        self.started.append(item.node_id)
        self.busy = True
        fut: Future[Any] = Future()
        self._futures[item.node_id] = fut
        return fut

    def finish(
        self,
        node_id: int,
        *,
        error: bool = False,
        exc: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        """Complete a running device's future (bus frees, queue advances)."""
        self.busy = False
        fut = self._futures.pop(node_id)
        if cancelled:
            fut.cancel()
        elif exc is not None:
            fut.set_exception(exc)
        elif error:
            fut.set_exception(RuntimeError("boom"))
        else:
            fut.set_result(None)


def _item(node_id: int, scope: str = "FULL") -> QueueItem:
    return QueueItem(
        node_id=node_id,
        address=f"1.1.{node_id}",
        name=f"D{node_id}",
        scope=DownloadScope[scope],
    )


def test_idle_enqueue_starts_immediately() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))
    assert h.started == [1]
    current = h.queue.current
    assert current is not None and current.node_id == 1
    assert current.state is OperationState.RUNNING
    assert current.connection == "iface 1"
    assert current.started_at is not None and current.ended_at is None
    assert h.queue.queued == []
    assert h.queue.take_focus_request() is True
    assert h.queue.take_focus_request() is False


def test_second_press_while_busy_queues() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))  # runs
    h.queue.enqueue(_item(2))  # busy -> queued
    assert h.started == [1]
    assert [q.node_id for q in h.queue.queued] == [2]
    assert [q.node_id for q in h.queue.active] == [1, 2]
    assert h.queue.queued[0].state is OperationState.WAITING


def test_fifo_drains_on_completion() -> None:
    h = _Harness()
    for n in (1, 2, 3):
        h.queue.enqueue(_item(n))
    assert h.started == [1]
    h.finish(1)
    assert h.started == [1, 2]
    h.finish(2)
    assert h.started == [1, 2, 3]
    h.finish(3)
    assert h.queue.current is None and h.queue.queued == []
    assert h.queue.active == []
    assert [i.node_id for i in h.queue.history] == [3, 2, 1]
    assert all(i.state is OperationState.FINISHED for i in h.queue.history)
    assert all(i.ended_at is not None for i in h.queue.history)
    h.queue.clear_history()
    assert h.queue.history == []


def test_dedupe_updates_scope_no_duplicate() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))  # running
    h.queue.enqueue(_item(2, scope="FULL"))
    h.queue.enqueue(_item(2, scope="PARAMETERS"))  # same device -> update scope, no dup
    assert [q.node_id for q in h.queue.queued] == [2]
    assert h.queue.queued[0].scope is DownloadScope.PARAMETERS


def test_continue_on_error_advances() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))
    h.queue.enqueue(_item(2))
    h.finish(1, error=True)  # device 1 failed
    assert h.started == [1, 2]  # queue still advances to 2
    failed = h.queue.history[0]
    assert failed.state is OperationState.FAILED
    assert failed.message == "boom"


def test_cancel_waiting_items() -> None:
    h = _Harness()
    for n in (1, 2, 3, 4):
        h.queue.enqueue(_item(n))
    h.queue.cancel(2)
    assert [q.node_id for q in h.queue.queued] == [3, 4]
    h.queue.cancel_item(h.queue.queued[0])
    assert [q.node_id for q in h.queue.queued] == [4]
    h.queue.cancel_all()
    assert h.queue.queued == []
    assert [i.node_id for i in h.queue.history] == [4, 3, 2]
    assert all(i.state is OperationState.CANCELED for i in h.queue.history)
    assert all(i.message == S.OPERATION_CANCELED_BY_USER for i in h.queue.history)
    # the running download is asked to stop as well, and ends once it has
    current = h.queue.current
    assert current is not None and current.node_id == 1 and current.canceled_by_user
    h.finish(1, cancelled=True)
    assert h.queue.current is None
    assert [i.node_id for i in h.queue.history] == [1, 4, 3, 2]


def test_start_none_drops_and_advances() -> None:
    h = _Harness(start_returns_none=True)
    h.queue.enqueue(_item(1))
    h.queue.enqueue(_item(2))
    # neither could start; both dropped, nothing left running or queued
    assert h.queue.current is None
    assert h.queue.queued == []
    assert [i.state for i in h.queue.history] == [OperationState.FAILED] * 2


def test_tick_does_not_double_start_when_busy() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))  # running, busy=True
    h.queue.enqueue(_item(2))
    h.queue.tick()  # extra frame ticks must not start a second op
    h.queue.tick()
    assert h.started == [1]


def _script_item(node_id: int) -> QueueItem:
    button = UiButton(id="B-1", text="Run", handler="h", online="ConnectionOriented")
    return QueueItem(
        node_id=node_id,
        address=f"1.1.{node_id}",
        name=f"D{node_id}",
        scope=None,
        script=ScriptRun(button),
    )


def test_script_runs_are_not_deduped() -> None:
    h = _Harness()
    h.queue.enqueue(_script_item(1))
    h.queue.enqueue(_script_item(1))
    h.queue.enqueue(_script_item(1))
    assert len(h.queue.queued) == 2


def test_cancel_current_only_flags_a_script() -> None:
    h = _Harness()
    h.queue.enqueue(_script_item(1))
    current = h.queue.current
    assert current is not None and current.script is not None
    assert not current.script.canceled
    h.queue.cancel_current()
    assert current.script.canceled
    assert h.queue.current is current
    h.finish(1)
    assert h.queue.current is None
    assert current.state is OperationState.CANCELED


def test_script_outcomes() -> None:
    h = _Harness()
    h.queue.enqueue(_script_item(1))
    h.finish(1, exc=ScriptError("Script failed"))
    h.queue.enqueue(_script_item(2))
    h.finish(2, exc=ScriptAborted())
    h.queue.enqueue(_script_item(3))
    h.finish(3)
    finished, aborted, failed = h.queue.history
    assert (failed.state, failed.message) == (OperationState.FAILED, "Script failed")
    assert (aborted.state, aborted.message) == (OperationState.CANCELED, None)
    assert (finished.state, finished.message) == (OperationState.FINISHED, None)


def test_cancel_current_cancels_an_address_download() -> None:
    h = _Harness()
    h.queue.enqueue(
        QueueItem(
            node_id=1,
            address="1.1.1",
            name="D1",
            scope=DownloadScope.INDIVIDUAL_ADDRESS,
        )
    )
    current = h.queue.current
    assert current is not None
    h.queue.cancel_current()
    h.finish(1, cancelled=True)
    assert current.state is OperationState.CANCELED
    assert current.future is None
    assert h.queue.current is None


def test_a_canceled_download_ends_only_once_it_stopped() -> None:
    h = _Harness()
    h.queue.enqueue(
        QueueItem(node_id=1, address="1.1.1", name="D1", scope=DownloadScope.FULL)
    )
    h.queue.enqueue(_item(2))
    current = h.queue.current
    assert current is not None
    h.queue.cancel_current()
    h.queue.cancel_current()  # a second click asks nothing more
    assert h.cancel_requests == [current.future]
    # still running (Canceling) until the download has unwound; the next one waits
    assert h.queue.current is current and current.state is OperationState.RUNNING
    assert current.canceled_by_user and h.started == [1]
    h.finish(1, cancelled=True)
    assert current.state is OperationState.CANCELED
    assert current.message == S.OPERATION_CANCELED_BY_USER
    assert h.started == [1, 2]


def test_an_error_of_the_interrupted_exchange_counts_as_canceled() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))
    current = h.queue.current
    assert current is not None
    h.queue.cancel_current()
    h.finish(1, error=True)
    assert current.state is OperationState.CANCELED
    assert current.message == S.OPERATION_CANCELED_BY_USER


def test_a_download_that_fails_without_cancel_still_fails() -> None:
    h = _Harness()
    h.queue.enqueue(_item(1))
    h.finish(1, error=True)
    (failed,) = h.queue.history
    assert (failed.state, failed.message) == (OperationState.FAILED, "boom")


def test_dedupe_keeps_the_latest_current_address() -> None:
    h = _Harness()
    h.queue.enqueue(_item(9))
    scope = DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS
    h.queue.enqueue(QueueItem(1, "1.1.1", "D1", scope, current_address="1.1.20"))
    h.queue.enqueue(QueueItem(1, "1.1.1", "D1", scope, current_address="1.1.21"))
    (queued,) = h.queue.queued
    assert queued.current_address == "1.1.21"
