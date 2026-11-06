"""Home Assistant's telegrams rebuilt as the frames the monitors keep."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from xknx.cemi import CEMIFrame
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from editor_gui.plugins.home_assistant.telegrams import link_info, telegram_frame


def _telegram(**changes: Any) -> dict[str, Any]:
    return {
        "destination": "1/2/3",
        "source": "1.1.7",
        "telegramtype": "GroupValueWrite",
        "payload": 1,
        "direction": "Incoming",
        "timestamp": "2026-11-06T10:00:00.250000+01:00",
        **changes,
    }


@pytest.mark.parametrize(
    ("kind", "payload", "apci"),
    [
        ("GroupValueWrite", 1, GroupValueWrite(DPTBinary(1))),
        ("GroupValueResponse", [12, 34], GroupValueResponse(DPTArray((12, 34)))),
        ("GroupValueRead", None, GroupValueRead()),
    ],
)
def test_the_frame_of_a_telegram(kind: str, payload: Any, apci: Any) -> None:
    rebuilt = telegram_frame(_telegram(telegramtype=kind, payload=payload))
    assert rebuilt is not None
    raw, when = rebuilt
    data = CEMIFrame.from_knx(raw).data
    telegram = data.telegram()  # type: ignore[union-attr]
    assert telegram.destination_address == GroupAddress("1/2/3")
    assert telegram.source_address == IndividualAddress("1.1.7")
    assert telegram.payload == apci
    # Local time, as the monitors keep it.
    assert when.tzinfo is None
    assert when == datetime.fromisoformat(
        _telegram()["timestamp"]
    ).astimezone().replace(tzinfo=None)


def test_an_address_in_another_style() -> None:
    rebuilt = telegram_frame(_telegram(destination="1/515"))
    assert rebuilt is not None
    data = CEMIFrame.from_knx(rebuilt[0]).data
    assert data.telegram().destination_address == GroupAddress("1/2/3")  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "changes",
    [
        # An undecodable Data Secure telegram has no payload.
        {"payload": None},
        {"payload": "text"},
        {"telegramtype": "MemoryRead"},
        {"destination": "not an address"},
        {"timestamp": "yesterday"},
        {"source": None},
    ],
)
def test_a_telegram_that_cannot_be_rebuilt(changes: dict[str, Any]) -> None:
    assert telegram_frame(_telegram(**changes)) is None


def test_the_connection_named_in_recordings() -> None:
    routing = link_info({"type": "routing", "host": "224.0.23.12", "port": 3671})
    assert routing.name == "Home Assistant"
    assert routing.connector == "IpRouting"
    assert routing.host == "224.0.23.12"
    tunnel = link_info({"type": "tunneling", "host": "192.168.1.10"})
    assert tunnel.connector == "IpTunneling"
    assert tunnel.port == 3671
    assert link_info(None).connector == "IpTunneling"
