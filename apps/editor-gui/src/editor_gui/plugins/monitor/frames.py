"""Decoding of the cEMI frames the monitors record: L_Data.req/.con/.ind and L_Busmon.ind.

Link-layer fields are read directly from the octets, so frames xknx cannot parse (unknown
services, extended frame formats, incomplete bus monitor frames) still show their addresses,
priority and flags. The TPDU is decoded with xknx where it can be.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from xknx.exceptions import XKNXException
from xknx.telegram.apci import (
    APCI,
    GroupValueRead,
    GroupValueResponse,
    GroupValueWrite,
)
from xknx.telegram.tpci import TPCI, TAck, TConnect, TDataBroadcast, TDisconnect, TNak

from editor_gui.plugins.connection.busmonitor import (
    L_BUSMON_IND,
    CEMIBusmonitorStatus,
    CEMIInfo,
    TP1Acknowledgement,
    TP1DataFrame,
    busmon_frame_from_knx,
    parse_tp1_frame,
)

L_DATA_REQ = 0x11
L_DATA_CON = 0x2E
L_DATA_IND = 0x29

MESSAGE_CODE_NAMES = {
    L_DATA_REQ: "L_Data.req",
    L_DATA_CON: "L_Data.con",
    L_DATA_IND: "L_Data.ind",
    L_BUSMON_IND: "L_Busmon.ind",
}

# APCI code of A_SecureService (KNX Data Secure).
APCI_SECURE_SERVICE = 0x03F1

_CONTROL_TPDU_NAMES: dict[type[TPCI], str] = {
    TConnect: "T_Connect",
    TDisconnect: "T_Disconnect",
    TAck: "T_Ack",
    TNak: "T_Nak",
}
_ACK_NAMES = {
    TP1Acknowledgement.ACK: "ACK",
    TP1Acknowledgement.NAK: "NAK",
    TP1Acknowledgement.BUSY: "BUSY",
    TP1Acknowledgement.NAK_BUSY: "NAK+BUSY",
}


def ack_name(ack: TP1Acknowledgement) -> str:
    return _ACK_NAMES[ack]


@dataclass(frozen=True, slots=True)
class FrameInfo:
    """The fields of one recorded frame."""

    message_code: int
    # Link-layer fields in cEMI L_Data form; ``None`` for acknowledgements and unknown frames.
    ctrl1: int | None = None
    ctrl2: int | None = None
    source: int | None = None
    destination: int | None = None
    # TPCI and APDU octets.
    tpdu: bytes = b""
    tpci: TPCI | None = None
    apci: APCI | None = None
    apci_code: int | None = None
    # Bus monitor details.
    busmon_info: CEMIInfo | None = None
    tp1: TP1DataFrame | None = None
    ack: TP1Acknowledgement | None = None
    # The frame could not be decoded (truncated or unknown).
    malformed: bool = False
    # A KNX Data Secure frame shown with its decrypted (or verified) APDU.
    decrypted: bool = False

    @property
    def busmonitor_status(self) -> CEMIBusmonitorStatus | None:
        if self.busmon_info is None:
            return None
        try:
            return self.busmon_info.busmonitor_status
        except Exception:
            return None

    @property
    def is_data(self) -> bool:
        return self.ctrl1 is not None

    @property
    def group(self) -> bool:
        return self.ctrl2 is not None and bool(self.ctrl2 & 0x80)

    @property
    def broadcast(self) -> bool:
        return self.group and self.destination == 0

    @property
    def group_value(self) -> bool:
        """A group telegram (group address other than the broadcast address)."""
        return self.group and self.destination != 0

    @property
    def priority(self) -> int | None:
        """0 system, 1 normal, 2 urgent, 3 low."""
        return None if self.ctrl1 is None else (self.ctrl1 >> 2) & 0x03

    @property
    def hop_count(self) -> int | None:
        return None if self.ctrl2 is None else (self.ctrl2 >> 4) & 0x07

    @property
    def standard_frame(self) -> bool | None:
        return None if self.ctrl1 is None else bool(self.ctrl1 & 0x80)

    @property
    def repeated(self) -> bool:
        """A repetition on the medium; only received frames say so."""
        if self.ctrl1 is None or self.message_code not in (L_DATA_IND, L_BUSMON_IND):
            return False
        return not self.ctrl1 & 0x20

    @property
    def system_broadcast(self) -> bool:
        return (
            self.ctrl1 is not None
            and self.broadcast
            and not self.ctrl1 & 0x10
            and isinstance(self.tpci, TDataBroadcast)
        )

    @property
    def confirm_error(self) -> bool:
        return (
            self.message_code == L_DATA_CON
            and self.ctrl1 is not None
            and bool(self.ctrl1 & 0x01)
        )

    @property
    def data_secure(self) -> bool:
        return self.apci_code == APCI_SECURE_SERVICE

    @property
    def checksum_error(self) -> bool:
        return self.tp1 is not None and not self.tp1.checksum_valid

    @property
    def is_group_value_service(self) -> bool:
        return isinstance(
            self.apci, (GroupValueWrite, GroupValueResponse, GroupValueRead)
        )

    @property
    def value_payload(self) -> Any:
        """The group value of a write or response (DPTBinary or DPTArray), else ``None``."""
        if isinstance(self.apci, (GroupValueWrite, GroupValueResponse)):
            return self.apci.value
        return None

    @property
    def apdu_data(self) -> bytes:
        """The APDU octets after the APCI (empty for 6-bit data and control frames)."""
        if self.tpci is not None and self.tpci.control:
            return b""
        return self.tpdu[2:]

    @property
    def type_name(self) -> str:
        """The service, e.g. ``GroupValueWrite``, ``T_Connect`` or ``ACK``."""
        if self.ack is not None:
            return ack_name(self.ack)
        if self.tpci is not None and self.tpci.control:
            return _CONTROL_TPDU_NAMES.get(type(self.tpci), type(self.tpci).__name__)
        if self.data_secure and not self.decrypted:
            return "SecureService"
        if self.apci is not None:
            return type(self.apci).__name__
        if self.apci_code is not None:
            return f"APCI ${self.apci_code:03X}"
        return ""

    @property
    def link_flags(self) -> str:
        """Bus monitor status (F frame, B bit, P parity error, O overflow, L lost, then
        ``S=`` the sequence number), then E extended frame (H if LTE), R repeated and
        C not confirmed, separated by a space."""
        parts: list[str] = []
        status = self.busmonitor_status
        if status is not None:
            letters = "".join(
                letter
                for letter, set_ in (
                    ("F", status.frame_error),
                    ("B", status.bit_error),
                    ("P", status.parity_error),
                    ("O", status.overflow),
                    ("L", status.lost),
                )
                if set_
            )
            sequence = f"S={status.sequence_number}"
            parts.append(f"{letters} {sequence}" if letters else sequence)
        letters = ""
        if self.standard_frame is False and self.ctrl2 is not None:
            frame_format = self.ctrl2 & 0x0F
            letters += "E" if frame_format == 0 else "H" if frame_format == 4 else ""
        if self.repeated:
            letters += "R"
        if self.ctrl1 is not None and self.ctrl1 & 0x01:
            letters += "C"
        if letters:
            parts.append(letters)
        return " ".join(parts)


#: The keys of the secured group addresses (raw address -> key), to show their telegrams
#: decrypted; set by the monitor from the open project or the loaded keyring.
secure_group_keys: Callable[[], Mapping[int, bytes]] | None = None


def _plain_apdu(
    apci: APCI | None,
    tpci: TPCI | None,
    ctrl1: int,
    ctrl2: int,
    source: int,
    destination: int,
) -> APCI | None:
    """The APDU of a KNX Data Secure group telegram, decrypted (or verified) with its group
    address's key; ``None`` when it is none, the key is unknown or the check fails."""
    from xknx.telegram.apci import SecureAPDU

    if not isinstance(apci, SecureAPDU) or tpci is None or secure_group_keys is None:
        return None
    key = secure_group_keys().get(destination)
    if key is None:
        return None
    try:
        plain = apci.secured_data.get_plain_apdu(
            key=key,
            scf=apci.scf,
            address_fields_raw=source.to_bytes(2, "big")
            + destination.to_bytes(2, "big"),
            frame_flags=(ctrl1 << 8) | ctrl2,
            tpci=tpci,
        )
        return APCI.from_knx(plain)
    except Exception:
        return None


def decode_cemi(raw: bytes) -> FrameInfo | None:
    """Decode a recorded cEMI frame; ``None`` if it is not one the monitors record."""
    if not raw:
        return None
    code = raw[0]
    try:
        if code == L_BUSMON_IND:
            return _decode_busmon(raw)
        if code in (L_DATA_REQ, L_DATA_CON, L_DATA_IND):
            _, body = CEMIInfo.from_knx(raw[1:])
            return _decode_ldata(code, body)
    except (XKNXException, ValueError, IndexError):
        return FrameInfo(message_code=code, malformed=True)
    return None


def _decode_busmon(raw: bytes) -> FrameInfo:
    busmon = busmon_frame_from_knx(raw)
    try:
        decoded = parse_tp1_frame(busmon.data.raw_frame)
    except XKNXException:
        return FrameInfo(
            message_code=L_BUSMON_IND, busmon_info=busmon.info, malformed=True
        )
    if isinstance(decoded, TP1Acknowledgement):
        return FrameInfo(
            message_code=L_BUSMON_IND, busmon_info=busmon.info, ack=decoded
        )
    info = _decode_ldata(L_BUSMON_IND, decoded.cemi_ldata_raw())
    return FrameInfo(
        message_code=L_BUSMON_IND,
        ctrl1=info.ctrl1,
        ctrl2=info.ctrl2,
        source=info.source,
        destination=info.destination,
        tpdu=info.tpdu,
        tpci=info.tpci,
        apci=info.apci,
        apci_code=info.apci_code,
        busmon_info=busmon.info,
        tp1=decoded,
        decrypted=info.decrypted,
    )


def _decode_ldata(code: int, body: bytes) -> FrameInfo:
    """Decode cEMI L_Data service fields: Ctrl1 Ctrl2 SA DA L TPDU."""
    if len(body) < 7:
        raise ValueError("L_Data too short")
    ctrl1, ctrl2 = body[0], body[1]
    source = int.from_bytes(body[2:4], "big")
    destination = int.from_bytes(body[4:6], "big")
    length = body[6]
    tpdu = bytes(body[7 : 7 + length + 1])
    group = bool(ctrl2 & 0x80)
    tpci: TPCI | None = None
    apci: APCI | None = None
    apci_code: int | None = None
    if tpdu:
        try:
            tpci = TPCI.resolve(
                raw_tpci=tpdu[0],
                dst_is_group_address=group,
                dst_is_zero=destination == 0,
            )
        except Exception:
            tpci = None
        if len(tpdu) >= 2 and not (tpci is not None and tpci.control):
            apci_code = (tpdu[0] & 0x03) << 8 | tpdu[1]
            # The APCI field of non-extended services is 4 bits.
            if apci_code & 0x03C0 not in (0x02C0, 0x03C0):
                apci_code &= 0x03C0
            try:
                apci = APCI.from_knx(bytes((tpdu[0] & 0x03,)) + tpdu[1:])
            except Exception:
                apci = None
    plain = (
        _plain_apdu(apci, tpci, ctrl1, ctrl2, source, destination) if group else None
    )
    return FrameInfo(
        message_code=code,
        ctrl1=ctrl1,
        ctrl2=ctrl2,
        source=source,
        destination=destination,
        tpdu=tpdu,
        tpci=tpci,
        apci=plain or apci,
        apci_code=apci_code,
        decrypted=plain is not None,
    )


def individual_address_text(value: int) -> str:
    return f"{value >> 12}.{(value >> 8) & 0x0F}.{value & 0xFF}"
