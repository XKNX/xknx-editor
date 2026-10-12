"""Tests for the monitor service: recordings, sessions and latest-value tracking."""

from __future__ import annotations

from typing import Any

from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.dpt import DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import (
    DeviceDescriptorRead,
    DeviceDescriptorResponse,
    GroupValueRead,
    GroupValueWrite,
    IndividualAddressRead,
)

from editor_gui.plugins.connection.busmonitor import (
    TP1Acknowledgement,
    build_busmon_ind,
    encode_tp1,
)
from editor_gui.plugins.connection.service import BusmonitorState, LinkInfo
from editor_gui.plugins.monitor.records import Mode, MonitorRecord, RecordKind
from editor_gui.plugins.monitor.service import (
    LINK_LAYER_KINDS,
    MonitorKind,
    MonitorService,
)


def _raw(dst: str, payload: Any, src: str = "1.1.1") -> bytes:
    telegram = Telegram(destination_address=GroupAddress(dst), payload=payload)
    data = CEMILData.init_from_telegram(telegram, src_addr=IndividualAddress(src))
    return CEMIFrame(code=CEMIMessageCode.L_DATA_IND, data=data).to_knx()


def _service() -> MonitorService:
    svc = MonitorService(connection=object())  # type: ignore[arg-type]  # on_raw_cemi ignores it
    return svc


def test_write_recorded_in_log_and_latest() -> None:
    svc = _service()
    svc.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(1))), None)  # type: ignore[arg-type]
    log = svc.telegrams()
    assert len(log) == 1
    assert log[0].destination == "1/2/3"
    assert log[0].service == "Write"
    assert log[0].source == "1.1.1"
    assert svc.latest("1/2/3") is not None


def test_read_logged_but_not_a_latest_value() -> None:
    svc = _service()
    svc.on_raw_cemi(_raw("0/0/1", GroupValueRead()), None)  # type: ignore[arg-type]
    assert len(svc.telegrams()) == 1
    assert svc.telegrams()[0].service == "Read"
    assert svc.latest("0/0/1") is None  # a read carries no value


def test_clear_empties_log_and_values() -> None:
    svc = _service()
    svc.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(1))), None)  # type: ignore[arg-type]
    svc.clear()
    assert svc.telegrams() == []
    assert svc.latest("1/2/3") is None


class FakeXknx:
    def __init__(self, address: str) -> None:
        self.current_address = IndividualAddress(address)


class FakeConnection:
    """The parts of ConnectionService the monitor service uses."""

    def __init__(self) -> None:
        self.xknx: FakeXknx | None = None
        self.link_info = LinkInfo(
            "IP Tunneling", "10.0.0.2", 3671, "IpTunneling", "Udp"
        )
        self.busmonitor_link = self.link_info
        self.sent: list[bytes] = []
        self.busmonitor_requests: list[str] = []

    def send_cemi(self, raw: bytes) -> None:
        self.sent.append(raw)

    def start_busmonitor(self) -> None:
        self.busmonitor_requests.append("start")

    def stop_busmonitor(self) -> None:
        self.busmonitor_requests.append("stop")


def _connected() -> tuple[MonitorService, FakeConnection]:
    connection = FakeConnection()
    service = MonitorService(connection)  # type: ignore[arg-type]
    connection.xknx = FakeXknx("1.1.250")
    service.on_connected()
    return service, connection


def _kinds(service: MonitorService, kind: MonitorKind) -> list[RecordKind]:
    return [record.kind for record in service.channel(kind).log.snapshot()]


def _frame(code: CEMIMessageCode, destination: Any, payload: Any, src: str) -> bytes:
    telegram = Telegram(destination_address=destination, payload=payload)
    data = CEMILData.init_from_telegram(telegram, src_addr=IndividualAddress(src))
    return CEMIFrame(code=code, data=data).to_knx()


def test_sessions_write_start_and_stop_entries() -> None:
    service, connection = _connected()
    start = service.channel(MonitorKind.GROUP).log.snapshot()[0]
    assert start.kind is RecordKind.START
    assert start.mode is Mode.LINK_LAYER
    assert start.connection_options.startswith("Type=IpTunneling;HostAddress=10.0.0.2")
    service.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(1))))
    service.on_link_state(False)
    service.on_link_state(True)
    service.stop(MonitorKind.GROUP)
    service.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(0))))
    assert _kinds(service, MonitorKind.GROUP) == [
        RecordKind.START,
        RecordKind.TELEGRAM,
        RecordKind.CONNECTION_LOST,
        RecordKind.CONNECTION_ESTABLISHED,
        RecordKind.STOP,
    ]
    telegram = service.channel(MonitorKind.GROUP).log.snapshot()[1]
    assert telegram.connection_name == "IP Tunneling (10.0.0.2:3671 Udp)"
    service.start(MonitorKind.GROUP)
    connection.xknx = None
    service.on_disconnected()
    assert _kinds(service, MonitorKind.GROUP)[-2:] == [
        RecordKind.START,
        RecordKind.STOP,
    ]


def test_group_monitor_records_own_telegrams_as_to_bus() -> None:
    service, _ = _connected()
    con = _frame(
        CEMIMessageCode.L_DATA_CON,
        GroupAddress("1/2/3"),
        GroupValueWrite(DPTBinary(1)),
        "1.1.250",
    )
    service.on_raw_cemi(con)
    record = service.channel(MonitorKind.GROUP).log.snapshot()[-1]
    assert record.is_telegram
    assert not record.from_bus


def _telegram_raws(service: MonitorService, kind: MonitorKind) -> list[bytes]:
    return [r.raw for r in service.channel(kind).log.snapshot() if r.is_telegram]


def _negative(raw: bytes) -> bytes:
    """``raw`` (an L_Data.con) with the confirm error bit set in Ctrl1."""
    data = bytearray(raw)
    data[2 + data[1]] |= 0x01
    return bytes(data)


def test_bus_activity_records_everything_but_group_telegrams() -> None:
    service, _ = _connected()
    request = _frame(
        CEMIMessageCode.L_DATA_REQ,
        IndividualAddress("1.1.5"),
        DeviceDescriptorRead(),
        "0.0.0",
    )
    service.on_sent_cemi(request)
    answer = _frame(
        CEMIMessageCode.L_DATA_IND,
        IndividualAddress("1.1.250"),
        DeviceDescriptorResponse(value=0x07B0),
        "1.1.5",
    )
    service.on_raw_cemi(answer)
    other = _frame(
        CEMIMessageCode.L_DATA_IND,
        IndividualAddress("1.1.7"),
        DeviceDescriptorRead(),
        "1.1.5",
    )
    service.on_raw_cemi(other)
    broadcast = _frame(
        CEMIMessageCode.L_DATA_IND,
        GroupAddress("0/0/0"),
        IndividualAddressRead(),
        "1.1.9",
    )
    service.on_raw_cemi(broadcast)
    service.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(1))))
    records = service.channel(MonitorKind.ACTIVITY).log.snapshot()
    assert [r.kind for r in records] == [RecordKind.START] + [RecordKind.TELEGRAM] * 4
    assert [r.raw for r in records[1:]] == [request, answer, other, broadcast]
    assert not records[1].from_bus
    assert all(r.from_bus for r in records[2:])
    assert _telegram_raws(service, MonitorKind.GROUP) == [
        _raw("1/2/3", GroupValueWrite(DPTBinary(1)))
    ]


def test_bus_activity_confirmations() -> None:
    service, _ = _connected()
    request = _frame(
        CEMIMessageCode.L_DATA_REQ,
        IndividualAddress("1.1.5"),
        DeviceDescriptorRead(),
        "1.1.250",
    )
    service.on_sent_cemi(request)
    confirmation = _frame(
        CEMIMessageCode.L_DATA_CON,
        IndividualAddress("1.1.5"),
        DeviceDescriptorRead(),
        "1.1.250",
    )
    # A positive confirmation repeats the request: recorded once.
    service.on_raw_cemi(confirmation)
    assert _telegram_raws(service, MonitorKind.ACTIVITY) == [request]
    # A negative confirmation of a non-group or a group frame is kept.
    failed = _negative(confirmation)
    service.on_raw_cemi(failed)
    group_failed = _negative(
        _frame(
            CEMIMessageCode.L_DATA_CON,
            GroupAddress("1/2/3"),
            GroupValueWrite(DPTBinary(1)),
            "1.1.250",
        )
    )
    service.on_raw_cemi(group_failed)
    assert _telegram_raws(service, MonitorKind.ACTIVITY) == [
        request,
        failed,
        group_failed,
    ]
    assert _telegram_raws(service, MonitorKind.GROUP) == []


def test_bus_activity_records_other_message_codes() -> None:
    service, _ = _connected()
    reset = bytes.fromhex("F0")  # M_Reset.ind
    malformed = bytes.fromhex("2900BC")
    service.on_raw_cemi(reset)
    service.on_raw_cemi(malformed)
    assert _telegram_raws(service, MonitorKind.ACTIVITY) == [reset, malformed]
    assert _telegram_raws(service, MonitorKind.GROUP) == []


def test_activity_follows_its_recording_state() -> None:
    service, _ = _connected()
    service.stop(MonitorKind.ACTIVITY)
    service.on_raw_cemi(
        _frame(
            CEMIMessageCode.L_DATA_IND,
            IndividualAddress("1.1.7"),
            DeviceDescriptorRead(),
            "1.1.5",
        )
    )
    assert _telegram_raws(service, MonitorKind.ACTIVITY) == []


def test_merged_log_and_limited_telegrams() -> None:
    ticks = iter(range(100, 200))
    connection = FakeConnection()
    service = MonitorService(connection, clock=lambda: next(ticks))  # type: ignore[arg-type]
    connection.xknx = FakeXknx("1.1.250")
    service.on_connected()
    service.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(1))))
    request = _frame(
        CEMIMessageCode.L_DATA_REQ,
        IndividualAddress("1.1.5"),
        DeviceDescriptorRead(),
        "1.1.250",
    )
    service.on_sent_cemi(request)
    service.on_raw_cemi(_raw("1/2/4", GroupValueWrite(DPTBinary(0))))
    merged = service.merged_log(LINK_LAYER_KINDS)
    assert [kind for kind, _record in merged] == [
        MonitorKind.GROUP,
        MonitorKind.ACTIVITY,
        MonitorKind.GROUP,
    ]
    assert [record.ticks for _kind, record in merged] == sorted(
        record.ticks for _kind, record in merged
    )
    newest = service.telegrams(limit=1)
    assert [t.destination for t in newest] == ["1/2/4"]
    assert newest[0].destination_raw == GroupAddress("1/2/4").raw
    assert [t.destination for t in service.telegrams()] == ["1/2/3", "1/2/4"]


def test_bus_monitor_records_frames_and_iack() -> None:
    service, connection = _connected()
    service.start(MonitorKind.BUS)
    assert connection.busmonitor_requests == ["start"]
    service.on_busmonitor_cemi(build_busmon_ind(b"\xcc"))  # not running yet: ignored
    service.on_busmonitor_state(BusmonitorState.RUNNING)
    frame = encode_tp1(source=0x1101, destination=0x0A03, group=True, tpdu=b"\x00\x81")
    service.on_busmonitor_cemi(build_busmon_ind(frame))
    service.on_busmonitor_cemi(build_busmon_ind(b"\xcc"))
    service.on_busmonitor_state(BusmonitorState.OFF)
    records = service.channel(MonitorKind.BUS).log.snapshot()
    assert [r.kind for r in records] == [
        RecordKind.START,
        RecordKind.TELEGRAM,
        RecordKind.TELEGRAM,
        RecordKind.STOP,
    ]
    assert records[0].mode is Mode.BUSMONITOR
    assert records[1].iack is TP1Acknowledgement.ACK
    assert records[2].frame is not None
    assert records[2].frame.type_name == "ACK"


def test_load_stops_recording_and_marks_saved() -> None:
    service, _ = _connected()
    service.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(1))))
    log = service.channel(MonitorKind.GROUP).log
    assert log.has_unsaved
    service.load(
        MonitorKind.GROUP, [MonitorRecord(kind=RecordKind.COMMENT, ticks=0, text="x")]
    )
    assert not service.channel(MonitorKind.GROUP).recording
    assert not log.has_unsaved
    service.on_raw_cemi(_raw("1/2/3", GroupValueWrite(DPTBinary(0))))
    assert _kinds(service, MonitorKind.GROUP) == [RecordKind.COMMENT]


def test_replay_skips_secure_telegrams() -> None:
    service, _ = _connected()
    secure = MonitorRecord(
        kind=RecordKind.TELEGRAM,
        ticks=0,
        raw=bytes.fromhex("2900BCE0110108010A03F1100000000000"),
    )
    plan = service.start_replay([secure])
    assert plan.skipped_secure == 1
    assert not plan.items
    assert not service.replaying
