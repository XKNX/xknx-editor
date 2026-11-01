"""A tiny FIFO that serialises the bus operations on devices ("Program", "Test Programming", a
device info readout, a parameter script, a restart, a master reset, a DALI run) onto the single KNX
bus slot.

The bus has one exclusive operation slot (``ConnectionService.begin_operation``), so a second
programming started while one runs would be rejected. This queue instead appends it and drains the
queue one device at a time, reusing the normal single-device path (``connection.program_device``)
verbatim — so slot handling, progress, the completion notice and commissioning recording are
unchanged.

Ended operations are kept in a history (newest first) until cleared.

Threading: ``enqueue``/``tick``/``cancel*`` run on the UI thread. The only callback
from the async loop thread is the future's completion, which is marshalled back onto the UI thread
via ``submit`` before advancing — so all state lives on and is mutated from the UI thread only.
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.connection.service import OperationNotStarted
from editor_gui.plugins.project.strings import S
from xknxeditor.download.partial import FullDownloadDeclined
from xknxeditor.prod.script import AbortToken, ScriptAborted

if TYPE_CHECKING:
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.prod.parser_v2.ui import UiButton


@dataclass
class ScriptRun:
    """State of an online Button handler run ("Parameter Script" operation)."""

    button: UiButton
    progress: float | None = None
    text: str = ""
    cancel_requested_at: float | None = None
    abort: AbortToken = field(default_factory=AbortToken)
    # The parameters the handler stored so far, each with its value before the run and its last
    # value, by ref id; filled on the UI thread.
    changes: ChangeSet = field(default_factory=dict[str, tuple[str | None, str | None]])
    # What the handler logged (info/warn/error), as (level, message).
    log: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def record(self, changes: ChangeSet) -> None:
        """Add stored ``changes``, keeping the value a parameter had before the run."""
        for ref, (old, new) in changes.items():
            first = self.changes.get(ref)
            self.changes[ref] = (first[0] if first is not None else old, new)

    @property
    def canceled(self) -> bool:
        return self.cancel_requested_at is not None

    def cancel(self) -> None:
        if self.cancel_requested_at is None:
            self.cancel_requested_at = time.monotonic()

    def ignored_cancel_for(self) -> float:
        if self.cancel_requested_at is None:
            return 0.0
        return time.monotonic() - self.cancel_requested_at


@dataclass
class BusAction:
    """Any other bus operation on the device: a restart, a master reset, a DALI run. ``start``
    starts it (None: it could not start), ``text`` names it in the Operations panel, ``kind`` is
    its bus operation (``ConnectionService.begin_operation``)."""

    kind: str
    text: str
    start: Callable[[], Future[Any] | None] = field(repr=False)


class OperationState(Enum):
    WAITING = "waiting"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELED = "canceled"
    FAILED = "failed"


@dataclass
class QueueItem:
    node_id: int
    address: str
    name: str
    scope: DownloadScope | None
    script: ScriptRun | None = None
    # A restart, a master reset or a DALI run instead of a download.
    action: BusAction | None = None
    state: OperationState = OperationState.WAITING
    message: str | None = None
    connection: str | None = None
    started_at: datetime.datetime | None = None
    ended_at: datetime.datetime | None = None
    # A device info readout instead of a download; True also reads the group communication.
    device_info: bool | None = None
    # A test of the programming in ``scope`` (reads the device, writes nothing) instead of a
    # download.
    test: bool = False
    # What a finished operation returned (the report of a test).
    result: Any = field(default=None, repr=False)
    # Result rows shown in the details: (section, [(label, value), ...]).
    details: list[tuple[str, list[tuple[str, str]]]] = field(
        default_factory=list[tuple[str, list[tuple[str, str]]]]
    )
    # Confirmed to program a device whose hardware needs the manufacturer's plugin.
    without_plugin: bool = False
    # The device's current address, for an individual address overwrite.
    current_address: str | None = None
    # Address the device by its serial number instead of programming mode.
    serial_number: bytes | None = None
    # An address download waits for the programming button.
    waiting_for_button: bool = False
    # Answered by the user: overwrite the application a download found in the device.
    full_download_question: Future[bool] | None = field(default=None, repr=False)
    future: Future[Any] | None = field(default=None, repr=False)
    # The user canceled it; a running operation shows Canceling until it has ended.
    canceled_by_user: bool = False
    # Who started it when not the editor's own UI: ORIGIN_MCP for an MCP client.
    origin: str | None = None
    # Resolved with the item once it has ended (finished, failed or canceled), for a caller that
    # waits for the outcome (an MCP tool).
    ended: Future[QueueItem] | None = field(default=None, repr=False)


# The origin of an operation an MCP client started.
ORIGIN_MCP = "MCP"


class ProgramQueue:
    """Serialises programmings. ``is_busy() -> bool`` reports whether any bus op is running;
    ``start(item) -> Future | None`` starts one programming (``None`` = could not start); ``submit``
    marshals a callable onto the UI thread (``None`` runs it inline, for tests); ``connection``
    names the bus connection an item runs on; ``cancel(future)`` stops a running operation so its
    future ends only once the operation has unwound (``None`` cancels the future itself)."""

    def __init__(
        self,
        *,
        is_busy: Callable[[], bool],
        start: Callable[[QueueItem], Future[Any] | None],
        submit: Callable[[Callable[[], None]], Any] | None = None,
        connection: Callable[[QueueItem], str | None] | None = None,
        on_finished: Callable[[QueueItem, Any], None] | None = None,
        describe_error: Callable[[QueueItem, BaseException], str | None] | None = None,
        error_text: Callable[[BaseException], str | None] | None = None,
        cancel: Callable[[Future[Any]], None] | None = None,
    ) -> None:
        self._is_busy = is_busy
        self._cancel = cancel
        self._start = start
        self._submit = submit
        self._connection = connection
        self._on_finished = on_finished
        self._describe_error = describe_error
        self._error_text = error_text
        self._current: QueueItem | None = None
        self._queued: list[QueueItem] = []
        self._history: list[QueueItem] = []
        self._focus_requested = False

    @property
    def current(self) -> QueueItem | None:
        return self._current

    @property
    def queued(self) -> list[QueueItem]:
        return list(self._queued)

    @property
    def history(self) -> list[QueueItem]:
        """Ended operations, newest first."""
        return list(self._history)

    def take_focus_request(self) -> bool:
        """Whether an operation was queued since the last call."""
        requested, self._focus_requested = self._focus_requested, False
        return requested

    @property
    def active(self) -> list[QueueItem]:
        """The running operation followed by the waiting ones."""
        current = [self._current] if self._current is not None else []
        return current + self._queued

    def enqueue(self, item: QueueItem) -> None:
        """Add a device to program. Dedupe per device: re-pressing a queued device only updates its
        scope (no duplicate); re-pressing the running device queues exactly one reprogram."""
        for queued in self._queued:
            if (
                queued.node_id == item.node_id
                and queued.script is None
                and item.script is None
                and queued.action is None
                and item.action is None
                and queued.device_info is None
                and item.device_info is None
                and queued.test == item.test
                # A caller waits for each of these, so they stay separate operations.
                and queued.ended is None
                and item.ended is None
            ):
                queued.scope = item.scope
                queued.without_plugin = item.without_plugin
                queued.current_address = item.current_address
                queued.serial_number = item.serial_number
                self.tick()
                return
        self._queued.append(item)
        self._focus_requested = True
        self.tick()

    def cancel(self, node_id: int) -> None:
        """Remove a *queued* device; the running one is canceled with ``cancel_current``."""
        for item in [q for q in self._queued if q.node_id == node_id]:
            self._remove_queued(item)

    def cancel_item(self, item: QueueItem) -> None:
        """Cancel one operation: a waiting one ends at once, a running one is stopped (see
        ``cancel_current``)."""
        if item is self._current:
            self.cancel_current()
        elif item in self._queued:
            self._remove_queued(item)

    def cancel_all(self) -> None:
        for item in list(self._queued):
            self._remove_queued(item)
        self.cancel_current()

    def _remove_queued(self, item: QueueItem) -> None:
        self._queued.remove(item)
        item.canceled_by_user = True
        self._end(item, OperationState.CANCELED)

    def _end(self, item: QueueItem, state: OperationState) -> None:
        item.state = state
        if (
            state is OperationState.CANCELED
            and item.canceled_by_user
            and not item.message
        ):
            item.message = S.OPERATION_CANCELED_BY_USER
        item.ended_at = datetime.datetime.now()
        self._history.insert(0, item)
        if item.ended is not None and not item.ended.done():
            item.ended.set_result(item)

    def clear_history(self) -> None:
        self._history.clear()

    def cancel_current(self) -> None:
        """Cancel the running operation. A parameter script is asked to stop and decides itself
        when to return; any other operation (a download, an address download, a device info
        read, a restart, a DALI run …) is stopped at its next bus exchange and ends once it has closed its connection, so
        the next one does not start on a bus still in use. A device whose download was canceled
        needs a new download."""
        current = self._current
        if current is None:
            return
        if current.script is not None:
            current.script.cancel()
        elif current.future is not None and not current.canceled_by_user:
            current.canceled_by_user = True
            if self._cancel is not None:
                self._cancel(current.future)
            else:
                current.future.cancel()

    def tick(self) -> None:
        """Start the next queued device if the bus is free. Idempotent; safe to call every frame."""
        if self._current is not None or self._is_busy() or not self._queued:
            return
        item = self._queued.pop(0)
        self._current = item
        item.state = OperationState.RUNNING
        item.started_at = datetime.datetime.now()
        if self._connection is not None:
            item.connection = self._connection(item)
        future = self._start(item)
        if future is None:
            # Could not start (e.g. not connected / device gone): drop it and try the next.
            self._current = None
            self._end(item, OperationState.FAILED)
            self.tick()
            return
        item.future = future
        future.add_done_callback(lambda f: self._on_done(item, f))

    def _on_done(self, item: QueueItem, future: Future[Any]) -> None:
        # Fires on the async loop thread -> marshal the advance onto the UI thread.
        if self._submit is not None:
            self._submit(lambda: self._advance(item, future))
        else:
            self._advance(item, future)

    def _advance(self, item: QueueItem, future: Future[Any]) -> None:
        state, message = _outcome(item, future, self._describe_error, self._error_text)
        item.future = None
        if message is not None:
            item.message = message
        if state is OperationState.FINISHED:
            item.result = future.result()
            if self._on_finished is not None:
                self._on_finished(item, item.result)
        self._end(item, state)
        self._current = None
        self.tick()


def _outcome(
    item: QueueItem,
    future: Future[Any],
    describe: Callable[[QueueItem, BaseException], str | None] | None = None,
    error_text: Callable[[BaseException], str | None] | None = None,
) -> tuple[OperationState, str | None]:
    if future.cancelled():
        return OperationState.CANCELED, None
    error = future.exception()
    if item.canceled_by_user and error is not None:
        # Stopped while it ran: whatever the interrupted exchange raised is the cancellation.
        return OperationState.CANCELED, None
    if isinstance(error, ScriptAborted | FullDownloadDeclined):
        return OperationState.CANCELED, None
    if isinstance(error, OperationNotStarted) and item.message:
        # Connected for it, it did not start: for the reason it gave.
        return OperationState.FAILED, None
    if error is not None:
        message = getattr(error, "message", None)
        text = (error_text(error) if error_text is not None else None) or (
            str(message or error) or type(error).__name__
        )
        extra = describe(item, error) if describe is not None else None
        return OperationState.FAILED, f"{text} {extra}" if extra else text
    if item.script is not None and item.script.canceled:
        return OperationState.CANCELED, None
    return OperationState.FINISHED, None
