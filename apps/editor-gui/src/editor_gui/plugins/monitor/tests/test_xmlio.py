"""Tests for CommunicationLog export and import."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.connection.busmonitor import build_busmon_ind, encode_tp1
from editor_gui.plugins.monitor.records import Mode, MonitorRecord, RecordKind
from editor_gui.plugins.monitor.xmlio import (
    export_xml,
    format_timestamp,
    import_xml,
    parse_timestamp,
)

SAMPLE = Path(__file__).parent / "data" / "GroupMonitorExport.xml"


def _text(data: bytes) -> str:
    return data.decode("utf-8").replace("\r\n", "\n")


def test_timestamps_keep_seven_digits() -> None:
    ticks = parse_timestamp("2026-10-12T17:41:18.8244181Z")
    assert format_timestamp(ticks) == "2026-10-12T17:41:18.8244181Z"
    # Trailing zeros are dropped.
    assert format_timestamp(parse_timestamp("2026-10-12T17:41:24.116684Z")) == (
        "2026-10-12T17:41:24.116684Z"
    )
    assert format_timestamp(parse_timestamp("2026-10-12T17:41:24Z")) == (
        "2026-10-12T17:41:24Z"
    )
    assert parse_timestamp("2026-10-12T19:41:24+02:00") == parse_timestamp(
        "2026-10-12T17:41:24Z"
    )
    with pytest.raises(ValueError):
        parse_timestamp("yesterday")


def test_sample_round_trip_is_identical() -> None:
    data = SAMPLE.read_bytes()
    result = import_xml(data)
    assert result.skipped == 0
    kinds = [record.kind for record in result.records]
    assert kinds == [RecordKind.START] + [RecordKind.TELEGRAM] * 7
    start = result.records[0]
    assert start.mode is Mode.LINK_LAYER
    assert start.host == "KNX-PC"
    assert start.connector_type == "IpTunneling"
    assert 'Name="IP Tunneling"' in start.connection_options
    assert result.records[1].raw == bytes.fromhex("2900BCE0102F1820010081")
    assert result.records[1].connection_name == "IP Tunneling (192.168.1.10:3671 Tcp)"
    assert _text(export_xml(result.records)) == _text(data)


def test_round_trip_of_all_entry_kinds() -> None:
    frame = encode_tp1(source=0x1101, destination=0x0A03, group=True, tpdu=b"\x00\x81")
    records = [
        MonitorRecord(
            kind=RecordKind.START,
            ticks=17_602_752_788_244_181,
            connection_name="Gateway",
            mode=Mode.BUSMONITOR,
            host="HOST",
            connection_options='Type=IpTunneling;HostAddress=10.0.0.2;ProtocolType=Udp;Name="Gateway";AutoReconnect=True',
            connector_type="IpTunneling",
            medium_type="Tp",
        ),
        MonitorRecord(
            kind=RecordKind.TELEGRAM,
            ticks=17_602_752_788_244_182,
            raw=build_busmon_ind(frame, status=1, timestamp=7),
            connection_name="Gateway (10.0.0.2:3671 Udp)",
        ),
        MonitorRecord(
            kind=RecordKind.TELEGRAM, ticks=17_602_752_788_300_000, raw=b"\x2b\x00\xcc"
        ),
        MonitorRecord(kind=RecordKind.CONNECTION_LOST, ticks=17_602_752_790_000_000),
        MonitorRecord(
            kind=RecordKind.CONNECTION_ESTABLISHED, ticks=17_602_752_800_000_000
        ),
        MonitorRecord(
            kind=RecordKind.COMMENT, ticks=17_602_752_800_000_001, text="a & b"
        ),
        MonitorRecord(kind=RecordKind.STOP, ticks=17_602_752_810_000_000),
    ]
    data = export_xml(records)
    text = data.decode()
    assert 'Service="L_Busmon.ind"' in text
    assert 'Mode="Busmonitor"' in text
    assert "<Connection Timestamp=" in text and 'State="Lost"' in text
    loaded = import_xml(data).records
    assert [r.kind for r in loaded] == [r.kind for r in records]
    assert [r.ticks for r in loaded] == [r.ticks for r in records]
    assert loaded[1].raw == records[1].raw
    assert loaded[5].text == "a & b"
    assert export_xml(loaded) == data


def test_unknown_elements_and_formats_are_skipped() -> None:
    data = b"""<?xml version="1.0" encoding="utf-8"?>
<CommunicationLog xmlns="http://knx.org/xml/telegrams/01">
  <RecordTrigger Timestamp="2026-10-12T17:41:18Z" PreTriggerCount="1" PostTriggerCount="1" />
  <Telegram Timestamp="2026-10-12T17:41:19Z" FrameFormat="Emi1" RawData="1100" />
  <Unknown Timestamp="2026-10-12T17:41:19Z" />
  <Telegram Timestamp="2026-10-12T17:41:20Z" FrameFormat="CommonEmi" RawData="2900BCE0102F1820010081" />
</CommunicationLog>"""
    result = import_xml(data)
    assert result.skipped == 1
    assert len(result.records) == 1


def test_import_rejects_other_documents() -> None:
    with pytest.raises(ValueError):
        import_xml(b"<Other />")
    with pytest.raises(ValueError):
        import_xml(b"not xml")
