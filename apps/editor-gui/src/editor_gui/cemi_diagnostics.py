"""Keep the received frame with errors logged while xknx handles it."""

from __future__ import annotations

from threading import local
from typing import TYPE_CHECKING

from xknx.cemi.cemi_handler import CEMIHandler
from xknx.cemi.const import CEMIMessageCode
from xknx.telegram import GroupAddress, IndividualAddress

if TYPE_CHECKING:
    from xknx import XKNX


class _ReceiveContext(local):
    frame: tuple[bytes, str, str] | None = None


_received = _ReceiveContext()


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
