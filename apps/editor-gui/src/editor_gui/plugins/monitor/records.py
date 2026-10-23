"""Recorded monitor entries and the bounded, thread-safe log that holds them."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from editor_gui.plugins.connection.busmonitor import TP1Acknowledgement
from editor_gui.plugins.monitor.frames import (
    L_BUSMON_IND,
    L_DATA_IND,
    FrameInfo,
    decode_cemi,
)

# Timestamps are 100 ns ticks since the Unix epoch (UTC), the resolution of recordings.
TICKS_PER_SECOND = 10_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Most records a monitor keeps; the oldest tenth is dropped when it is exceeded.
DEFAULT_MAX_RECORDS = 100_000


def now_ticks() -> int:
    return time.time_ns() // 100


def ticks_to_datetime(ticks: int) -> datetime:
    """The UTC datetime of ``ticks`` (microsecond precision)."""
    return _EPOCH + timedelta(microseconds=ticks // 10)


def datetime_to_ticks(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.astimezone()
    delta = moment - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * TICKS_PER_SECOND + (
        delta.microseconds * 10
    )


class RecordKind(Enum):
    TELEGRAM = "telegram"
    START = "start"
    STOP = "stop"
    CONNECTION_ESTABLISHED = "established"
    CONNECTION_LOST = "lost"
    COMMENT = "comment"


class Mode(Enum):
    LINK_LAYER = "LinkLayer"
    BUSMONITOR = "Busmonitor"
    UNKNOWN = "Unknown"


@dataclass(slots=True, eq=False)
class MonitorRecord:
    """One row of a monitor: a telegram or an event such as the start of a recording."""

    kind: RecordKind
    ticks: int
    # The complete cEMI frame, including message code and additional information.
    raw: bytes = b""
    connection_name: str = ""
    # Start entries: how the recording was made.
    mode: Mode = Mode.UNKNOWN
    host: str = ""
    connection_options: str = ""
    connector_type: str = ""
    medium_type: str = ""
    # Comment text.
    text: str = ""
    # Row number, assigned by the log.
    number: int = 0
    # Acknowledgement of a bus monitor data frame, from the character that followed it.
    iack: TP1Acknowledgement | None = None
    # Decoded frame and formatted cells, filled on first use.
    _frame: FrameInfo | None = field(default=None, repr=False)
    _decoded: bool = field(default=False, repr=False)
    cells: tuple[str, ...] | None = field(default=None, repr=False)
    cells_key: object = field(default=None, repr=False)
    haystack: str = field(default="", repr=False)

    @property
    def frame(self) -> FrameInfo | None:
        if not self._decoded:
            self._frame = decode_cemi(self.raw) if self.raw else None
            self._decoded = True
        return self._frame

    @property
    def is_telegram(self) -> bool:
        return self.kind is RecordKind.TELEGRAM

    @property
    def from_bus(self) -> bool:
        """Received from the bus (indications), as opposed to sent by the app."""
        return bool(self.raw) and self.raw[0] in (L_DATA_IND, L_BUSMON_IND)

    def set_iack(self, ack: TP1Acknowledgement) -> None:
        self.iack = ack
        self.cells = None


class TelegramLog:
    """The records of one monitor, appended from bus threads and read by the UI."""

    def __init__(self, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._records: list[MonitorRecord] = []
        self._max = max(max_records, 10)
        self._next_number = 1
        # Changes whenever records are removed (clear, import, trim): views rebuild then.
        self._generation = 0
        self._unsaved = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def max_records(self) -> int:
        return self._max

    def set_max_records(self, value: int) -> None:
        """Change the cap and trim down right away if the current log is longer."""
        value = max(value, 10)
        with self._lock:
            if value == self._max:
                return
            self._max = value
            if len(self._records) > self._max:
                del self._records[: len(self._records) - self._max]
                self._generation += 1

    def __len__(self) -> int:
        return len(self._records)

    @property
    def has_unsaved(self) -> bool:
        """Telegrams were recorded since the log was last cleared, loaded or exported."""
        return self._unsaved

    def mark_saved(self) -> None:
        self._unsaved = False

    def append(self, record: MonitorRecord) -> None:
        with self._lock:
            record.number = self._next_number
            self._next_number += 1
            self._records.append(record)
            if record.is_telegram:
                self._unsaved = True
            if len(self._records) > self._max:
                del self._records[: max(self._max // 10, 1)]
                self._generation += 1

    def last(self) -> MonitorRecord | None:
        with self._lock:
            return self._records[-1] if self._records else None

    def clear(self) -> None:
        with self._lock:
            self._records = []
            self._next_number = 1
            self._generation += 1
            self._unsaved = False

    def replace(self, records: Iterable[MonitorRecord]) -> None:
        """Replace the content (e.g. with a loaded recording), numbering it from 1."""
        with self._lock:
            self._records = []
            self._next_number = 1
            for record in records:
                record.number = self._next_number
                self._next_number += 1
                record.cells = None
                self._records.append(record)
            self._records = self._records[-self._max :]
            self._generation += 1
            self._unsaved = False

    def snapshot(self) -> list[MonitorRecord]:
        with self._lock:
            return list(self._records)

    def since(self, generation: int, seen: int) -> list[MonitorRecord] | None:
        """The records after the first ``seen`` ones, or ``None`` when the log changed in another
        way since ``generation`` (the caller then rebuilds from :meth:`snapshot`)."""
        with self._lock:
            if generation != self._generation:
                return None
            return self._records[seen:]
