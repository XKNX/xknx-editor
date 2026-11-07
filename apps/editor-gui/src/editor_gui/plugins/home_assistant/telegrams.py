"""Home Assistant's group telegrams as the frames the editor's monitors keep.

The KNX integration describes a telegram as a dict (``TelegramDict`` in
``homeassistant/components/knx/telegrams.py``): source, destination, the service
(``telegramtype``), the payload (an int for a small value, a list of octets otherwise), and an ISO
timestamp. It is rebuilt here as the L_Data.ind frame it was on the bus.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.dpt.payload import DPTArray, DPTBinary
from xknx.exceptions import XKNXException
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import APCI, GroupValueRead, GroupValueResponse, GroupValueWrite

from editor_gui.plugins.connection.service import LinkInfo


def link_info(connection: dict[str, Any] | None) -> LinkInfo:
    """The connection Home Assistant's telegrams come over, as recordings name it."""
    connection = connection or {}
    routing = connection.get("type") == "routing"
    return LinkInfo(
        name="Home Assistant",
        host=str(connection.get("host") or ""),
        port=int(connection.get("port") or 3671),
        connector="IpRouting" if routing else "IpTunneling",
    )


def _apci(kind: object, payload: object) -> APCI | None:
    if kind == "GroupValueRead":
        return GroupValueRead()
    if kind not in ("GroupValueWrite", "GroupValueResponse"):
        return None
    octets = (
        list(cast("list[object] | tuple[object, ...]", payload))
        if isinstance(payload, list | tuple)
        else None
    )
    if isinstance(payload, int):
        value: DPTBinary | DPTArray = DPTBinary(payload)
    elif octets is not None and all(isinstance(b, int) for b in octets):
        value = DPTArray(tuple(cast("list[int]", octets)))
    else:
        # An undecodable Data Secure telegram: Home Assistant has no payload.
        return None
    return (
        GroupValueWrite(value)
        if kind == "GroupValueWrite"
        else GroupValueResponse(value)
    )


def telegram_frame(telegram: dict[str, Any]) -> tuple[bytes, datetime] | None:
    """The frame of ``telegram`` and when it was on the bus (local time); None for one that
    cannot be rebuilt."""
    apci = _apci(telegram.get("telegramtype"), telegram.get("payload"))
    if apci is None:
        return None
    try:
        rebuilt = Telegram(
            destination_address=GroupAddress(str(telegram["destination"])),
            source_address=IndividualAddress(str(telegram["source"])),
            payload=apci,
        )
        when = datetime.fromisoformat(str(telegram["timestamp"]))
        frame = CEMIFrame(
            code=CEMIMessageCode.L_DATA_IND, data=CEMILData.init_from_telegram(rebuilt)
        )
        raw = frame.to_knx()
    except (KeyError, ValueError, XKNXException):
        return None
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return raw, when
