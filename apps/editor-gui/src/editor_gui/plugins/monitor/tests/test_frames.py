"""Tests for decoding recorded cEMI frames into the monitor's fields."""

from __future__ import annotations

from editor_gui.plugins.connection.busmonitor import (
    CEMIPriority,
    TP1Acknowledgement,
    build_busmon_ind,
    encode_tp1,
)
from editor_gui.plugins.monitor.frames import decode_cemi

# L_Data.ind 1.0.47 -> 3/0/32 GroupValueWrite $01 (from the sample export).
IND = bytes.fromhex("2900BCE0102F1820010081")


def test_ldata_ind_fields() -> None:
    frame = decode_cemi(IND)
    assert frame is not None
    assert frame.source == 0x102F
    assert frame.destination == 0x1820
    assert frame.group and frame.group_value and not frame.broadcast
    assert frame.priority == CEMIPriority.LOW
    assert frame.hop_count == 6
    assert frame.standard_frame
    assert not frame.repeated
    assert frame.type_name == "GroupValueWrite"
    assert frame.link_flags == ""
    assert frame.value_payload is not None


def test_repeated_indication_and_priorities() -> None:
    # Ctrl1 9C: standard, repeated (r = 0), broadcast, low priority.
    frame = decode_cemi(bytes.fromhex("29009CE0102F1820010081"))
    assert frame is not None
    assert frame.repeated
    assert frame.link_flags == "R"
    for ctrl1, priority in ((0xB0, 0), (0xB4, 1), (0xB8, 2), (0xBC, 3)):
        raw = bytes((0x29, 0x00, ctrl1)) + IND[3:]
        decoded = decode_cemi(raw)
        assert decoded is not None
        assert decoded.priority == priority


def test_confirmation_with_error() -> None:
    frame = decode_cemi(bytes.fromhex("2E00BDE0102F1820010081"))
    assert frame is not None
    assert frame.confirm_error
    assert frame.link_flags == "C"
    # Repetition is only meaningful for received frames.
    assert not decode_cemi(bytes.fromhex("11009CE000001820010081")).repeated  # type: ignore[union-attr]


def test_management_and_transport_services() -> None:
    connect = decode_cemi(bytes.fromhex("2900B0601101110500" + "80"))
    assert connect is not None
    assert connect.type_name == "T_Connect"
    assert not connect.group
    read = decode_cemi(bytes.fromhex("1100B0600000110501" + "0300"))
    assert read is not None
    assert read.type_name == "DeviceDescriptorRead"
    assert read.hop_count == 6


def test_data_secure_service() -> None:
    frame = decode_cemi(bytes.fromhex("2900BCE0110108010A03F1100000000000"))
    assert frame is not None
    assert frame.data_secure
    assert frame.type_name == "SecureService"


def test_busmonitor_data_frame_and_ack() -> None:
    tp1 = encode_tp1(source=0x1101, destination=0x0A03, group=True, tpdu=b"\x00\x81")
    frame = decode_cemi(build_busmon_ind(tp1, status=0x81))
    assert frame is not None
    assert frame.is_data
    assert frame.source == 0x1101 and frame.destination == 0x0A03
    assert frame.type_name == "GroupValueWrite"
    assert frame.link_flags == "F S=1"
    bad = decode_cemi(build_busmon_ind(tp1[:-1] + b"\x00"))
    assert bad is not None and bad.checksum_error and bad.link_flags == ""
    ack = decode_cemi(build_busmon_ind(b"\xcc"))
    assert ack is not None
    assert ack.ack is TP1Acknowledgement.ACK
    assert ack.type_name == "ACK"
    assert not ack.is_data
    truncated = decode_cemi(build_busmon_ind(tp1[:4]))
    assert truncated is not None and truncated.malformed and truncated.link_flags == ""


def test_link_flags() -> None:
    tp1 = encode_tp1(source=0x1101, destination=0x0A03, group=True, tpdu=b"\x00\x81")
    status = decode_cemi(build_busmon_ind(tp1, status=0x0A))
    assert status is not None and status.link_flags == "L S=2"
    clean = decode_cemi(build_busmon_ind(tp1, status=0x00))
    assert clean is not None and clean.link_flags == "S=0"
    overflow = decode_cemi(build_busmon_ind(tp1, status=0xF0))
    assert overflow is not None and overflow.link_flags == "FBPO S=0"
    extended = encode_tp1(
        source=0x1101,
        destination=0x0A03,
        group=True,
        tpdu=b"\x00\x81",
        extended=True,
        repeated=True,
    )
    both = decode_cemi(build_busmon_ind(extended, status=0x01))
    assert both is not None and both.link_flags == "S=1 ER"
    lte = decode_cemi(bytes.fromhex("29003CE4110108010A0081"))
    assert lte is not None and lte.link_flags == "H"


def test_unrecorded_message_codes() -> None:
    assert decode_cemi(b"") is None
    assert decode_cemi(bytes.fromhex("FC000001")) is None
