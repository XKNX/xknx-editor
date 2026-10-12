"""Tests for the monitor log: numbering, bounds, incremental reads and unsaved telegrams."""

from __future__ import annotations

from editor_gui.plugins.monitor.records import MonitorRecord, RecordKind, TelegramLog

RAW = bytes.fromhex("2900BCE0102F1820010081")


def _telegram() -> MonitorRecord:
    return MonitorRecord(kind=RecordKind.TELEGRAM, ticks=0, raw=RAW)


def test_rows_are_numbered_sequentially() -> None:
    log = TelegramLog()
    for _ in range(3):
        log.append(_telegram())
    assert [record.number for record in log.snapshot()] == [1, 2, 3]
    log.clear()
    log.append(_telegram())
    assert log.snapshot()[0].number == 1


def test_unsaved_telegrams() -> None:
    log = TelegramLog()
    assert not log.has_unsaved
    log.append(MonitorRecord(kind=RecordKind.START, ticks=0))
    assert not log.has_unsaved  # an event alone is nothing to save
    log.append(_telegram())
    assert log.has_unsaved
    log.mark_saved()
    assert not log.has_unsaved
    log.append(_telegram())
    assert log.has_unsaved
    log.clear()
    assert not log.has_unsaved
    log.append(_telegram())
    log.replace([_telegram(), _telegram()])
    assert not log.has_unsaved  # a loaded recording is saved already
    assert [record.number for record in log.snapshot()] == [1, 2]


def test_incremental_reads_and_trimming() -> None:
    log = TelegramLog(max_records=10)
    generation = log.generation
    for _ in range(4):
        log.append(_telegram())
    assert log.since(generation, 0) is not None
    new = log.since(generation, 2)
    assert new is not None and len(new) == 2
    for _ in range(7):
        log.append(_telegram())
    # Exceeding the bound drops the oldest records and invalidates incremental reads.
    assert len(log) == 10
    assert log.since(generation, 4) is None
    assert log.snapshot()[0].number == 2
