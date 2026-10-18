"""Sending recorded group telegrams again with their original relative timing."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.telegram import GroupAddress, Telegram

from editor_gui.plugins.monitor.records import TICKS_PER_SECOND, MonitorRecord


@dataclass(frozen=True, slots=True)
class ReplayItem:
    # Seconds after the first telegram.
    offset: float
    # The L_Data.req to send.
    raw: bytes


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    items: list[ReplayItem]
    # Data Secure telegrams, which are not sent.
    skipped_secure: int


def build_plan(records: Iterable[MonitorRecord]) -> ReplayPlan:
    """The group telegrams among ``records`` as L_Data.req, timed relative to the first one."""
    items: list[ReplayItem] = []
    skipped = 0
    first: int | None = None
    for record in records:
        frame = record.frame if record.is_telegram else None
        if frame is None or not frame.group_value:
            continue
        if frame.data_secure:
            skipped += 1
            continue
        if frame.apci is None or frame.destination is None:
            continue
        telegram = Telegram(
            destination_address=GroupAddress(frame.destination), payload=frame.apci
        )
        raw = CEMIFrame(
            code=CEMIMessageCode.L_DATA_REQ,
            data=CEMILData.init_from_telegram(telegram),
        ).to_knx()
        if first is None:
            first = record.ticks
        items.append(ReplayItem((record.ticks - first) / TICKS_PER_SECOND, raw))
    return ReplayPlan(items, skipped)


class Replayer:
    """Sends a plan's frames at their offsets until done or cancelled.

    ``clock`` and ``wait`` can be replaced for tests; ``wait(seconds)`` returns ``True`` when
    the replay was cancelled while waiting."""

    def __init__(
        self,
        send: Callable[[bytes], object],
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._send = send
        self._clock = clock
        self._cancel = threading.Event()
        self._wait = wait or self._cancel.wait
        self.sent = 0
        self.total = 0

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self, items: list[ReplayItem]) -> int:
        """Send the items; returns how many were sent."""
        self.total = len(items)
        start = self._clock()
        for item in items:
            if self._cancel.is_set():
                break
            delay = start + item.offset - self._clock()
            if delay > 0 and self._wait(delay):
                break
            if self._cancel.is_set():
                break
            self._send(item.raw)
            self.sent += 1
        return self.sent
