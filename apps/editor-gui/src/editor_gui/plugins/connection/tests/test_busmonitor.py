"""Tests for the bus monitor module: cEMI L_Busmon.ind and TP1 frame decoding."""

from __future__ import annotations

import pytest
from xknx.exceptions import CouldNotParseCEMI, UnsupportedCEMIMessage
from xknx.knxip import ErrorCode
from xknx.telegram import GroupAddress, IndividualAddress
from xknx.telegram.apci import GroupValueWrite

from editor_gui.plugins.connection.busmonitor import (
    CEMIAdditionalInfoType,
    CEMIFrameType,
    CEMIPriority,
    TP1Acknowledgement,
    TP1DataFrame,
    _busmonitor_cri,  # pyright: ignore[reportPrivateUsage]
    build_busmon_ind,
    busmon_frame_from_knx,
    encode_tp1,
    parse_tp1_frame,
    rejection_reason,
    tp1_checksum,
)

# A group write 1.1.1 -> 1/2/3 "on" as a standard TP1 frame, low priority, hop count 6.
STANDARD = bytes.fromhex("BC 11 01 0A 03 E1 00 81 3A")


def test_checksum_is_not_xor() -> None:
    assert tp1_checksum(STANDARD[:-1]) == 0x3A
    assert tp1_checksum(b"") == 0xFF


def test_standard_frame() -> None:
    frame = parse_tp1_frame(STANDARD)
    assert isinstance(frame, TP1DataFrame)
    assert frame.frame_type is CEMIFrameType.STANDARD
    assert frame.src_addr == IndividualAddress("1.1.1")
    assert frame.dst_addr == GroupAddress("1/2/3")
    assert frame.address_type is GroupAddress
    assert frame.priority is CEMIPriority.LOW
    assert frame.hop_count == 6
    assert not frame.repeated
    assert frame.tpdu == bytes((0x00, 0x81))
    assert frame.checksum == 0x3A
    assert frame.checksum_valid
    telegram = frame.telegram()
    assert isinstance(telegram.payload, GroupValueWrite)


def test_bad_checksum_keeps_the_header() -> None:
    frame = parse_tp1_frame(STANDARD[:-1] + b"\x00")
    assert isinstance(frame, TP1DataFrame)
    assert not frame.checksum_valid
    assert frame.dst_addr == GroupAddress("1/2/3")


def test_repeated_and_system_priority() -> None:
    raw = encode_tp1(
        source=0x1105,
        destination=0x1101,
        group=False,
        tpdu=bytes((0x80,)),
        priority=CEMIPriority.SYSTEM,
        repeated=True,
    )
    frame = parse_tp1_frame(raw)
    assert isinstance(frame, TP1DataFrame)
    assert frame.repeated
    assert frame.priority is CEMIPriority.SYSTEM
    assert frame.dst_addr == IndividualAddress("1.1.1")
    assert frame.checksum_valid


def test_extended_frame() -> None:
    tpdu = bytes((0x00, 0x80)) + bytes(range(20))
    raw = encode_tp1(source=0x1102, destination=0x0801, group=True, tpdu=tpdu)
    assert raw[0] & 0x80 == 0  # FT = 0: extended
    frame = parse_tp1_frame(raw)
    assert isinstance(frame, TP1DataFrame)
    assert frame.frame_type is CEMIFrameType.EXTENDED
    assert frame.tpdu == tpdu
    assert frame.hop_count == 6
    assert frame.checksum_valid
    ctrl1, ctrl2 = frame.control_fields()
    assert ctrl1 & 0x80 == 0
    assert ctrl2 & 0x80


@pytest.mark.parametrize(
    ("octet", "ack"),
    [
        (0xCC, TP1Acknowledgement.ACK),
        (0x0C, TP1Acknowledgement.NAK),
        (0xC0, TP1Acknowledgement.BUSY),
        (0x00, TP1Acknowledgement.NAK_BUSY),
    ],
)
def test_acknowledgements(octet: int, ack: TP1Acknowledgement) -> None:
    assert parse_tp1_frame(bytes((octet,))) is ack


def test_malformed_and_unsupported_frames() -> None:
    with pytest.raises(CouldNotParseCEMI):
        parse_tp1_frame(b"\x55")
    with pytest.raises(CouldNotParseCEMI):
        parse_tp1_frame(STANDARD[:-2])
    with pytest.raises(UnsupportedCEMIMessage):
        parse_tp1_frame(bytes((0xF0, 0x11, 0x01, 0x0A, 0x03, 0x01, 0x00)))
    lte = bytearray(
        encode_tp1(source=1, destination=2, group=True, tpdu=b"\x00\x80", extended=True)
    )
    lte[1] |= 0x04
    with pytest.raises(UnsupportedCEMIMessage):
        parse_tp1_frame(bytes(lte))


def test_busmon_ind_additional_info() -> None:
    raw = build_busmon_ind(
        STANDARD, status=0b1010_1011, timestamp=0x1234, extended_timestamp=0xDEADBEEF
    )
    frame = busmon_frame_from_knx(raw)
    assert frame.data.raw_frame == STANDARD
    assert frame.to_knx() == raw
    status = frame.info.busmonitor_status
    assert status is not None
    assert status.frame_error
    assert not status.bit_error
    assert status.parity_error
    assert not status.overflow
    assert status.lost
    assert status.sequence_number == 3
    assert frame.info.timestamp_relative == 0x1234
    assert frame.info.extended_relative_timestamp == 0xDEADBEEF
    assert frame.info.get(CEMIAdditionalInfoType.TIMESTAMP_RELATIVE) == b"\x12\x34"
    assert [kind for kind, _ in frame.info.items()] == [0x03, 0x04, 0x06]


def test_busmon_ind_spec_example() -> None:
    # EMI/IMI 03.06.03 §4.1.5.8.1: 2Bh, AddIL 7, status, relative timestamp, frame.
    raw = bytes.fromhex("2B 07 03 01 05 04 02 00 10") + STANDARD
    frame = busmon_frame_from_knx(raw)
    assert frame.info.timestamp_relative == 0x0010
    assert frame.info.busmonitor_status is not None
    assert frame.info.busmonitor_status.sequence_number == 5
    assert parse_tp1_frame(frame.data.raw_frame) == parse_tp1_frame(STANDARD)


def test_busmon_ind_rejects_other_frames() -> None:
    with pytest.raises(CouldNotParseCEMI):
        busmon_frame_from_knx(bytes.fromhex("29 00 BC E0 11 01 0A 03 01 00 81"))
    with pytest.raises(CouldNotParseCEMI):
        busmon_frame_from_knx(bytes.fromhex("2B 09 03 01"))


def test_connect_request_uses_busmonitor_layer() -> None:
    assert _busmonitor_cri().to_knx() == bytes((0x04, 0x04, 0x80, 0x00))


def test_rejection_reasons() -> None:
    assert rejection_reason(ErrorCode.E_TUNNELLING_LAYER) == "unsupported"
    assert rejection_reason(ErrorCode.E_NO_MORE_CONNECTIONS) == "busy"
    assert rejection_reason(None) == ""
