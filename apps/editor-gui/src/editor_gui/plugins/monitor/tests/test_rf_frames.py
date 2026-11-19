"""KNX RF frames: decoded from the bus monitor of an RF interface, and shown in the monitor."""

from __future__ import annotations

import pytest
from xknx.exceptions import CouldNotParseCEMI, UnsupportedCEMIMessage
from xknx.telegram import GroupAddress, IndividualAddress

from editor_gui.plugins.connection.busmonitor import (
    build_busmon_ind,
    encode_rf,
    encode_tp1,
    is_rf_frame,
    parse_rf_frame,
    rf_crc,
)
from editor_gui.plugins.monitor.formatting import COLUMN_INDEX, format_cells
from editor_gui.plugins.monitor.frames import decode_cemi
from editor_gui.plugins.monitor.records import MonitorRecord, RecordKind

from .test_formatting import _index

DOMAIN_ADDRESS = bytes.fromhex("00fa12345678")


def _group_write(**kwargs: object) -> bytes:
    """An RF frame of a group write 3/0/32 $01 from 1.0.47 on the domain address."""
    return encode_rf(
        source=0x102F,
        destination=0x1820,
        group=True,
        tpdu=b"\x00\x81",
        serial_number_or_domain_address=DOMAIN_ADDRESS,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_crc_of_the_specification() -> None:
    assert rf_crc(bytes(range(1, 9))) == 0xFCBC


def test_a_group_write() -> None:
    raw = _group_write(rf_info=0x32, frame_number=3)
    # a first block of 10 octets and a second of 8, each with its CRC
    assert len(raw) == 10 + 2 + 8 + 2
    assert raw[:4] == bytes.fromhex("11 44 ff 32")
    assert is_rf_frame(raw)
    frame = parse_rf_frame(raw)
    assert frame.checksum_valid
    assert frame.src_addr == IndividualAddress("1.0.47")
    assert frame.dst_addr == GroupAddress("3/0/32")
    assert frame.tpdu == b"\x00\x81"
    assert frame.hop_count == 6
    assert frame.frame_number == 3
    assert not frame.system_broadcast
    assert frame.serial_number_or_domain_address == DOMAIN_ADDRESS
    assert frame.medium_info == bytes.fromhex("32") + DOMAIN_ADDRESS + b"\x03"
    assert frame.telegram().destination_address == GroupAddress("3/0/32")
    # in cEMI form: a standard frame, priority low, the broadcast flag of a domain address
    assert frame.control_fields() == (0xBC, 0xE0)


def test_long_and_system_broadcast_frames() -> None:
    raw = encode_rf(
        source=0x1101,
        destination=0x0105,
        group=False,
        tpdu=bytes(range(20)),
        system_broadcast=True,
    )
    # 20 octets of TPDU: a third block
    assert len(raw) == 10 + 2 + 16 + 2 + 10 + 2
    frame = parse_rf_frame(raw)
    assert frame.checksum_valid
    assert frame.dst_addr == IndividualAddress("0.1.5")
    assert frame.tpdu == bytes(range(20))
    assert frame.system_broadcast
    assert frame.control_fields() == (0xAC, 0x60)


def test_malformed_frames() -> None:
    raw = _group_write()
    bad = raw[:-1] + bytes(((raw[-1] + 1) & 0xFF,))
    assert not parse_rf_frame(bad).checksum_valid
    # TP1 frames, truncated frames and frames of another length are no RF frames
    tp1 = encode_tp1(source=0x102F, destination=0x1820, group=True, tpdu=b"\x00\x81")
    for other in (tp1, raw[:-1], raw + b"\x00", b"\x12\x44"):
        assert not is_rf_frame(other)
    with pytest.raises(CouldNotParseCEMI, match="not a KNX RF frame"):
        parse_rf_frame(tp1)
    # a frame with a second block too short for the L_Data fields
    short = encode_rf(source=1, destination=2, group=True, tpdu=b"")
    data = short[:10] + short[12:-2]
    trimmed = bytes((data[0] - 1,)) + data[1:-1]
    blocks = trimmed[:10] + rf_crc(trimmed[:10]).to_bytes(2, "big")
    blocks += trimmed[10:] + rf_crc(trimmed[10:]).to_bytes(2, "big")
    with pytest.raises(CouldNotParseCEMI, match="without a TPDU"):
        parse_rf_frame(blocks)
    # LTE frames
    lte = bytearray(raw)
    lte[12] = 0x04
    second = bytes(lte[12:20])
    lte[20:22] = rf_crc(second).to_bytes(2, "big")
    with pytest.raises(UnsupportedCEMIMessage):
        parse_rf_frame(bytes(lte))


def test_shown_in_the_bus_monitor() -> None:
    raw = build_busmon_ind(_group_write(rf_info=0x32, frame_number=3), status=0x01)
    frame = decode_cemi(raw)
    assert frame is not None
    assert frame.rf is not None and frame.tp1 is None
    assert frame.source == 0x102F and frame.destination == 0x1820
    assert frame.type_name == "GroupValueWrite"
    assert not frame.checksum_error
    assert frame.link_flags == "S=1"
    record = MonitorRecord(kind=RecordKind.TELEGRAM, ticks=0, raw=raw)
    cells = format_cells(record, _index())
    assert cells[COLUMN_INDEX["medium_info"]] == "32 00 FA 12 34 56 78 03"
    assert cells[COLUMN_INDEX["destination_name"]] == "PM 1, Output 1"
    assert cells[COLUMN_INDEX["service"]] == "from bus"
    # a wrong CRC makes it an invalid frame
    broken = bytearray(_group_write())
    broken[-1] ^= 0xFF
    record = MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=0, raw=build_busmon_ind(bytes(broken))
    )
    assert record.frame is not None and record.frame.checksum_error
    assert format_cells(record, _index())[COLUMN_INDEX["service"]] == "Invalid frame"


def test_an_rf_data_frame_shows_its_medium_info() -> None:
    # L_Data.ind with RF medium information from an RF interface
    raw = bytes.fromhex("290a 0208 32 00fa12345678 03 bce0102f1820010081")
    record = MonitorRecord(kind=RecordKind.TELEGRAM, ticks=0, raw=raw)
    cells = format_cells(record, _index())
    assert cells[COLUMN_INDEX["medium_info"]] == "32 00 FA 12 34 56 78 03"
    assert cells[COLUMN_INDEX["destination_address"]] == "3/0/32"
