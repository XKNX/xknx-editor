"""Keep the received frame with errors logged while xknx handles it."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import local
from typing import TYPE_CHECKING

from xknx.cemi.cemi_handler import CEMIHandler
from xknx.cemi.const import CEMIMessageCode
from xknx.telegram import GroupAddress, IndividualAddress

if TYPE_CHECKING:
    from xknx import XKNX


class _ReceiveContext(local):
    frame: tuple[bytes, str, str] | None = None
    packet: KNXIPReceiveContext | None = None


_received = _ReceiveContext()


@dataclass(frozen=True, slots=True)
class KNXIPReceiveContext:
    """Original packet bytes and TCP framing information for one received frame."""

    raw: bytes
    frame_length: int
    peer: str
    chunk_length: int
    buffered_length: int
    frame_offset: int
    trailing_length: int

    def details(self) -> dict[str, str]:
        raw = self.raw
        details = {
            "knxip_raw": raw.hex(),
            "knxip_frame_length": str(self.frame_length),
            "knxip_raw_truncated": str(len(raw) < self.frame_length),
            "knxip_peer": self.peer,
            "tcp_chunk_length": str(self.chunk_length),
            "tcp_buffered_length": str(self.buffered_length),
            "tcp_frame_offset": str(self.frame_offset),
            "tcp_bytes_after_frame": str(self.trailing_length),
        }
        if len(raw) >= 6:
            details.update(
                knxip_header_length=str(raw[0]),
                knxip_protocol_version=f"0x{raw[1]:02x}",
                knxip_service=f"0x{raw[2:4].hex()}",
                knxip_length_declared=str(int.from_bytes(raw[4:6], "big")),
            )
        if len(raw) >= 10 and raw[2:4] == b"\x04\x20":
            details.update(
                tunnel_header_length=str(raw[6]),
                tunnel_channel=str(raw[7]),
                tunnel_sequence=str(raw[8]),
            )
        return details


def current_knxip_packet() -> KNXIPReceiveContext | None:
    """Get the packet associated with the synchronous transport callback."""
    return _received.packet


@contextmanager
def knxip_receive_context(packet: KNXIPReceiveContext | None) -> Generator[None]:
    """Keep packet context local to the current synchronous callback."""
    previous = _received.packet
    _received.packet = packet
    try:
        yield
    finally:
        _received.packet = previous


def handle_cemi_with_packet(
    receive: Callable[[bytes], None], raw: bytes, packet: KNXIPReceiveContext | None
) -> None:
    """Carry packet context across the connection thread without leaking to tasks."""
    with knxip_receive_context(packet):
        receive(raw)


class DiagnosticCEMIHandler(CEMIHandler):
    """Add log context around the synchronous receive handler."""

    __slots__ = ("_mode", "_target")

    def __init__(self, xknx: XKNX, *, mode: str, target: str) -> None:
        super().__init__(xknx)
        self._mode = mode
        self._target = target

    def handle_raw_cemi(self, raw_cemi: bytes) -> None:
        previous = _received.frame
        _received.frame = (raw_cemi, self._mode, self._target)
        try:
            super().handle_raw_cemi(raw_cemi)
        finally:
            _received.frame = previous


def cemi_error_details() -> dict[str, str]:
    """Describe only the active receive call, without decoding its payload."""
    if _received.frame is None:
        return {}
    raw, mode, target = _received.frame
    details = {
        "connection_mode": mode,
        "connection_target": target,
        "cemi_raw": raw.hex(),
        "cemi_length": str(len(raw)),
    }
    if _received.packet is not None:
        details.update(_received.packet.details())
    if not raw:
        return details
    details["cemi_code"] = f"0x{raw[0]:02x}"
    try:
        code = CEMIMessageCode(raw[0])
    except ValueError:
        return details
    details["cemi_service"] = code.name
    if (
        code
        not in (
            CEMIMessageCode.L_DATA_REQ,
            CEMIMessageCode.L_DATA_IND,
            CEMIMessageCode.L_DATA_CON,
        )
        or len(raw) < 2
    ):
        return details
    details["cemi_additional_info_length"] = str(raw[1])
    offset = 2 + raw[1]
    if len(raw) < offset + 7:
        return details
    data = raw[offset:]
    details["cemi_control"] = data[:2].hex()
    details["cemi_source"] = str(IndividualAddress.from_knx(data[2:4]))
    details["cemi_destination"] = str(
        GroupAddress.from_knx(data[4:6])
        if data[1] & 0x80
        else IndividualAddress.from_knx(data[4:6])
    )
    details["tpdu_length_expected"] = str(data[6] + 1)
    details["tpdu_length_received"] = str(len(data) - 7)
    if code is CEMIMessageCode.L_DATA_CON:
        details["cemi_confirm_error"] = str(bool(data[0] & 1))
    return details
