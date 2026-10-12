"""Tests for replaying recorded group telegrams with their original timing."""

from __future__ import annotations

from xknx.cemi import CEMIFrame, CEMIMessageCode

from editor_gui.plugins.monitor.records import (
    TICKS_PER_SECOND,
    MonitorRecord,
    RecordKind,
)
from editor_gui.plugins.monitor.replay import Replayer, ReplayItem, build_plan

WRITE = bytes.fromhex("2900BCE0102F1820010081")
READ = bytes.fromhex("2900BCE0102F1820010000")
SECURE = bytes.fromhex("2900BCE0110108010A03F1100000000000")
INDIVIDUAL = bytes.fromhex("2900B0601101110500" + "80")


def _record(seconds: float, raw: bytes) -> MonitorRecord:
    return MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=int(seconds * TICKS_PER_SECOND), raw=raw
    )


def test_plan_keeps_relative_timing_and_skips_secure() -> None:
    records = [
        MonitorRecord(kind=RecordKind.START, ticks=0),
        _record(10.0, WRITE),
        _record(10.5, SECURE),
        _record(11.25, INDIVIDUAL),
        _record(12.0, READ),
    ]
    plan = build_plan(records)
    assert plan.skipped_secure == 1
    assert [item.offset for item in plan.items] == [0.0, 2.0]
    first = CEMIFrame.from_knx(plan.items[0].raw)
    assert first.code is CEMIMessageCode.L_DATA_REQ


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        return False


def test_replayer_sends_at_offsets() -> None:
    fake = FakeClock()
    sent: list[tuple[float, bytes]] = []
    replayer = Replayer(
        lambda raw: sent.append((fake.now, raw)), clock=fake.clock, wait=fake.wait
    )
    items = [
        ReplayItem(0.0, b"a"),
        ReplayItem(1.5, b"b"),
        ReplayItem(1.5, b"c"),
        ReplayItem(4.0, b"d"),
    ]
    assert replayer.run(items) == 4
    assert sent == [(100.0, b"a"), (101.5, b"b"), (101.5, b"c"), (104.0, b"d")]
    assert fake.waits == [1.5, 2.5]


def test_replayer_cancel_stops_sending() -> None:
    fake = FakeClock()
    sent: list[bytes] = []

    def wait(seconds: float) -> bool:
        replayer.cancel()
        return True

    replayer = Replayer(sent.append, clock=fake.clock, wait=wait)
    assert replayer.run([ReplayItem(0.0, b"a"), ReplayItem(5.0, b"b")]) == 1
    assert sent == [b"a"]
    assert replayer.cancelled
