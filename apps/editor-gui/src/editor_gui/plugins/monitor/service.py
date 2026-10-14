"""Monitor service: the recordings of the Group Monitor, Bus Monitor and Bus Activity, the
latest group-address values, and GroupValueWrite / GroupValueRead / replay sending.

Every frame of the normal connection lands in exactly one log: group telegrams in the Group
Monitor, everything else (including every frame the app sends) in Bus Activity.

Frames arrive on the connection threads and are stored raw; decoding and formatting happen on
the UI thread when rows are shown."""

from __future__ import annotations

import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any

from xknx.dpt.payload import DPTArray, DPTBinary
from xknx.telegram import GroupAddress as XGroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from editor_gui.dpt import transcoder_for
from editor_gui.plugins.connection.busmonitor import TP1Acknowledgement
from editor_gui.plugins.connection.service import BusmonitorState
from editor_gui.plugins.monitor.frames import (
    L_DATA_CON,
    L_DATA_IND,
    L_DATA_REQ,
    FrameInfo,
    individual_address_text,
)
from editor_gui.plugins.monitor.records import (
    Mode,
    MonitorRecord,
    RecordKind,
    TelegramLog,
    now_ticks,
)
from editor_gui.plugins.monitor.replay import Replayer, ReplayPlan, build_plan

if TYPE_CHECKING:
    from editor_gui.net import TelegramSource
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.connection.service import ConnectionService, LinkInfo


@dataclass
class LiveValue:
    payload: Any  # DPTArray | DPTBinary
    timestamp: datetime
    service: str  # "Write" | "Response"

    @cached_property
    def time_str(self) -> str:
        # Cached: strftime does a tzset (getenv/notify) on macOS and the monitor re-renders every
        # frame; format the fixed timestamp once, not per frame.
        return self.timestamp.strftime("%H:%M:%S")


@dataclass
class TelegramRecord:
    """A group telegram of the Group Monitor in the shape the MCP tools report."""

    timestamp: datetime
    source: str  # individual address
    destination: str  # group address
    service: str  # "Write" | "Response" | "Read"
    payload: Any  # raw DPTArray/DPTBinary value, or None for Read
    destination_raw: int = 0  # 16-bit group address value

    @cached_property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")


class MonitorKind(Enum):
    GROUP = "group"
    BUS = "bus"
    ACTIVITY = "activity"


# The monitors that record the normal connection; together they hold every frame it carries.
LINK_LAYER_KINDS = (MonitorKind.GROUP, MonitorKind.ACTIVITY)


class Channel:
    """The recording state of one monitor."""

    def __init__(self, kind: MonitorKind) -> None:
        self.kind = kind
        self.log = TelegramLog()
        # The user wants to record (Group Monitor and Bus Activity record while connected).
        self.recording = kind is not MonitorKind.BUS
        # A start entry was written and no stop entry since.
        self.session = False
        self.connection_name = ""


class MonitorService:
    def __init__(
        self,
        connection: ConnectionService,
        clock: Callable[[], int] = now_ticks,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._log: Logger | None = None
        self._lock = threading.RLock()
        # address string -> latest value seen. Written on the interface thread, read on the UI
        # thread; dict item assignment is atomic under the GIL, which is enough here.
        self._values: dict[str, LiveValue] = {}
        self.channels = {kind: Channel(kind) for kind in MonitorKind}
        # Monotonic timestamps of every bus frame, for the bus-load (telegrams/s) readout. Kept
        # separate from the logs so clearing a log doesn't reset the load.
        self._load_stamps: deque[float] = deque(maxlen=8192)
        self._replayer: Replayer | None = None
        # Outcome of the last replay: (sent, cancelled).
        self.replay_result: tuple[int, bool] | None = None
        self.host = socket.gethostname()

    def set_logger(self, log: Logger) -> None:
        self._log = log

    def channel(self, kind: MonitorKind) -> Channel:
        return self.channels[kind]

    # --- incoming (connection threads) ------------------------------------

    def on_raw_cemi(
        self, raw_cemi: bytes, _source: TelegramSource | None = None
    ) -> None:
        """A frame received on the normal connection.

        Group telegrams (L_Data.ind, and positive L_Data.con of the app's own) go to the Group
        Monitor; every other frame goes to Bus Activity (see :func:`is_activity_frame`)."""
        if not raw_cemi:
            return
        record = MonitorRecord(
            kind=RecordKind.TELEGRAM, ticks=self._clock(), raw=raw_cemi
        )
        frame = record.frame
        code = raw_cemi[0]
        if frame is not None and frame.is_data and code in (L_DATA_IND, L_DATA_CON):
            self._load_stamps.append(time.monotonic())
            payload = frame.value_payload
            if (
                payload is not None
                and frame.group_value
                and frame.destination is not None
            ):
                kind = (
                    "Write" if isinstance(frame.apci, GroupValueWrite) else "Response"
                )
                self._values[str(XGroupAddress(frame.destination))] = LiveValue(
                    payload=payload, timestamp=datetime.now(), service=kind
                )
        if is_group_monitor_frame(code, frame):
            self._append(self.channels[MonitorKind.GROUP], record)
        elif is_activity_frame(code, frame):
            self._append(self.channels[MonitorKind.ACTIVITY], record)

    def on_sent_cemi(self, raw_cemi: bytes) -> None:
        """A frame the app sent (L_Data.req)."""
        if not raw_cemi or raw_cemi[0] != L_DATA_REQ:
            return
        self._append(
            self.channels[MonitorKind.ACTIVITY],
            MonitorRecord(kind=RecordKind.TELEGRAM, ticks=self._clock(), raw=raw_cemi),
        )

    def on_busmonitor_cemi(self, raw_cemi: bytes) -> None:
        """A frame of the bus monitor connection (L_Busmon.ind)."""
        channel = self.channels[MonitorKind.BUS]
        record = MonitorRecord(
            kind=RecordKind.TELEGRAM, ticks=self._clock(), raw=raw_cemi
        )
        frame = record.frame
        if frame is not None and frame.ack is not None:
            previous = channel.log.last()
            if (
                previous is not None
                and previous.is_telegram
                and previous.iack is None
                and previous.frame is not None
                and previous.frame.is_data
            ):
                previous.set_iack(TP1Acknowledgement(frame.ack))
        elif frame is not None and frame.is_data:
            self._load_stamps.append(time.monotonic())
        self._append(channel, record)

    def _append(self, channel: Channel, record: MonitorRecord) -> None:
        if not channel.session:
            if channel.kind is MonitorKind.BUS or not channel.recording:
                return
            # Frames can arrive before the connected event.
            self._start_session(channel, self._link_info(), Mode.LINK_LAYER)
        record.connection_name = channel.connection_name
        channel.log.append(record)

    def _link_info(self) -> LinkInfo | None:
        return getattr(self._connection, "link_info", None)

    # --- connection events ------------------------------------------------

    def on_connected(self) -> None:
        self._values = {}
        for kind in (MonitorKind.GROUP, MonitorKind.ACTIVITY):
            channel = self.channels[kind]
            if channel.recording and not channel.session:
                self._start_session(channel, self._link_info(), Mode.LINK_LAYER)

    def on_disconnected(self) -> None:
        for kind in (MonitorKind.GROUP, MonitorKind.ACTIVITY):
            self._stop_session(self.channels[kind])

    def on_link_state(self, established: bool) -> None:
        kind = (
            RecordKind.CONNECTION_ESTABLISHED
            if established
            else RecordKind.CONNECTION_LOST
        )
        for channel in self.channels.values():
            if channel.session:
                channel.log.append(
                    MonitorRecord(
                        kind=kind,
                        ticks=self._clock(),
                        connection_name=channel.connection_name,
                    )
                )

    def on_busmonitor_state(self, state: BusmonitorState) -> None:
        channel = self.channels[MonitorKind.BUS]
        if state is BusmonitorState.RUNNING:
            channel.recording = True
            if not channel.session:
                self._start_session(
                    channel, self._connection.busmonitor_link, Mode.BUSMONITOR
                )
        elif state is BusmonitorState.OFF:
            channel.recording = False
            self._stop_session(channel)

    def _start_session(
        self, channel: Channel, link: LinkInfo | None, mode: Mode
    ) -> None:
        with self._lock:
            channel.connection_name = link.endpoint_name if link is not None else ""
            channel.session = True
            channel.log.append(
                MonitorRecord(
                    kind=RecordKind.START,
                    ticks=self._clock(),
                    connection_name=link.name if link is not None else "",
                    mode=mode,
                    host=self.host,
                    connection_options=link.options if link is not None else "",
                    connector_type=link.connector if link is not None else "",
                    medium_type=link.medium if link is not None else "",
                )
            )

    def _stop_session(self, channel: Channel) -> None:
        with self._lock:
            if not channel.session:
                return
            channel.session = False
            channel.log.append(
                MonitorRecord(
                    kind=RecordKind.STOP,
                    ticks=self._clock(),
                    connection_name=channel.connection_name,
                )
            )

    # --- user actions -----------------------------------------------------

    def is_connected(self) -> bool:
        return self._connection.xknx is not None

    def start(self, kind: MonitorKind) -> None:
        """Start recording; the Bus Monitor connects in bus monitor mode."""
        channel = self.channels[kind]
        if kind is MonitorKind.BUS:
            self._connection.start_busmonitor()
            return
        channel.recording = True
        if self.is_connected() and not channel.session:
            self._start_session(channel, self._connection.link_info, Mode.LINK_LAYER)

    def stop(self, kind: MonitorKind) -> None:
        """Stop recording; the Bus Monitor closes its connection and restores the normal one."""
        channel = self.channels[kind]
        if kind is MonitorKind.BUS:
            self._connection.stop_busmonitor()
            return
        channel.recording = False
        self._stop_session(channel)

    def clear_log(self, kind: MonitorKind) -> None:
        """Remove all entries; a running recording continues with a new start entry."""
        channel = self.channels[kind]
        with self._lock:
            channel.log.clear()
            if channel.session:
                channel.session = False
                link = (
                    getattr(self._connection, "busmonitor_link", None)
                    if kind is MonitorKind.BUS
                    else self._link_info()
                )
                mode = Mode.BUSMONITOR if kind is MonitorKind.BUS else Mode.LINK_LAYER
                self._start_session(channel, link, mode)

    def load(self, kind: MonitorKind, records: Iterable[MonitorRecord]) -> None:
        """Replace the entries with a loaded recording; recording stops."""
        channel = self.channels[kind]
        if kind is MonitorKind.BUS:
            self._connection.stop_busmonitor()
        else:
            channel.recording = False
        with self._lock:
            channel.session = False
            channel.log.replace(records)

    # --- latest values and the legacy telegram list -----------------------

    def latest(self, address: str) -> LiveValue | None:
        return self._values.get(address)

    def merged_log(
        self, kinds: Iterable[MonitorKind] = LINK_LAYER_KINDS
    ) -> list[tuple[MonitorKind, MonitorRecord]]:
        """The telegram entries of the given monitors, merged by time (oldest first)."""
        entries = [
            (kind, record)
            for kind in kinds
            for record in self.channels[kind].log.snapshot()
            if record.is_telegram
        ]
        entries.sort(key=lambda entry: entry[1].ticks)
        return entries

    def telegrams(self, limit: int = 0) -> list[TelegramRecord]:
        """The group telegrams of the Group Monitor, oldest first; only the newest ``limit``
        when ``limit`` is positive."""
        result: list[TelegramRecord] = []
        for record in reversed(self.channels[MonitorKind.GROUP].log.snapshot()):
            if 0 < limit <= len(result):
                break
            frame = record.frame if record.is_telegram else None
            if frame is None or not frame.group_value or frame.destination is None:
                continue
            apci = frame.apci
            if isinstance(apci, GroupValueWrite):
                service, payload = "Write", apci.value
            elif isinstance(apci, GroupValueResponse):
                service, payload = "Response", apci.value
            elif isinstance(apci, GroupValueRead):
                service, payload = "Read", None
            else:
                continue
            result.append(
                TelegramRecord(
                    timestamp=datetime.fromtimestamp(record.ticks / 10_000_000),
                    source=individual_address_text(frame.source or 0),
                    destination=str(XGroupAddress(frame.destination)),
                    service=service,
                    payload=payload,
                    destination_raw=frame.destination,
                )
            )
        result.reverse()
        return result

    def bus_load(self, window: float = 3.0) -> float:
        """Recent bus load in telegrams/second, averaged over the last ``window`` seconds.

        Counts every bus frame (not just group telegrams). Independent of the telegram logs, so
        clearing a log does not reset it."""
        cutoff = time.monotonic() - window
        stamps = self._load_stamps
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        return len(stamps) / window

    def clear(self) -> None:
        """Clear the latest values and the Group Monitor's telegrams."""
        self._values = {}
        self.clear_log(MonitorKind.GROUP)

    # --- outgoing (UI thread) ---------------------------------------------

    def send_write(self, address: str, dpt: str | None, text: str) -> bool:
        """Encode ``text`` per the group address' DPT and send a GroupValueWrite. Returns success."""
        transcoder = transcoder_for(dpt)
        if transcoder is None:
            self._warn("cannot write: unknown DPT", address=address, dpt=dpt)
            return False
        try:
            knx_value = transcoder.to_knx(_coerce(text))
        except Exception as e:  # xknx raises ConversionError/ValueError on bad input
            self._warn("cannot encode value", address=address, value=text, error=str(e))
            return False
        self._send(address, GroupValueWrite(knx_value))
        return True

    def send_payload(self, address: str, payload: DPTBinary | DPTArray) -> None:
        """Send an already encoded value as a GroupValueWrite."""
        self._send(address, GroupValueWrite(payload))

    def send_read(self, address: str) -> None:
        self._send(address, GroupValueRead())

    def _send(self, address: str, payload: GroupValueWrite | GroupValueRead) -> None:
        self._connection.send_group_telegram(address, payload)

    def _warn(self, event: str, **kwargs: Any) -> None:
        if self._log is not None:
            self._log.warning(event, **kwargs)

    # --- replay -----------------------------------------------------------

    @property
    def replaying(self) -> bool:
        return self._replayer is not None

    def replay_progress(self) -> tuple[int, int] | None:
        replayer = self._replayer
        return None if replayer is None else (replayer.sent, replayer.total)

    def start_replay(self, records: Iterable[MonitorRecord]) -> ReplayPlan:
        """Send the group telegrams of ``records`` again with their timing, on a worker thread.
        Data Secure telegrams are skipped."""
        plan = build_plan(records)
        if plan.skipped_secure:
            self._warn(
                "Data Secure telegrams are not replayed", count=plan.skipped_secure
            )
        if not plan.items or self._replayer is not None:
            return plan
        replayer = Replayer(self._connection.send_cemi)
        self._replayer = replayer
        self.replay_result = None

        def run() -> None:
            sent = 0
            try:
                sent = replayer.run(plan.items)
            finally:
                self.replay_result = (sent, replayer.cancelled)
                self._replayer = None

        threading.Thread(target=run, name="Monitor-Replay", daemon=True).start()
        return plan

    def stop_replay(self) -> None:
        replayer = self._replayer
        if replayer is not None:
            replayer.cancel()


def is_group_monitor_frame(code: int, frame: FrameInfo | None) -> bool:
    """A group telegram from the bus, or a positive L_Data.con of one the app sent."""
    if frame is None or not frame.group_value:
        return False
    return code == L_DATA_IND or (code == L_DATA_CON and not frame.confirm_error)


def is_activity_frame(code: int, frame: FrameInfo | None) -> bool:
    """A received frame that Bus Activity records: every frame that is not a Group Monitor
    frame, except a positive L_Data.con, which repeats the L_Data.req already recorded."""
    if is_group_monitor_frame(code, frame):
        return False
    if code != L_DATA_CON or frame is None or not frame.is_data:
        return True
    return frame.confirm_error


def _coerce(text: str) -> Any:
    """Best-effort interpret a user-typed value: bool words, then int, then float, else the string."""
    t = text.strip()
    low = t.lower()
    if low in ("on", "true", "yes"):
        return True
    if low in ("off", "false", "no"):
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t.replace(",", "."))
    except ValueError:
        pass
    return t
