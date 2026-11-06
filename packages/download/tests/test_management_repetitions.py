"""A point-to-point telegram without an ACK is repeated as often as the transport layer does.

xknx does it from 4.1.1 on (``MANAGEMENT_MAX_REPETITIONS``); downloads rely on it on lossy
paths such as a busy KNXnet/IP tunnel."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionTimeout
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import IndividualAddress, Telegram, TelegramDirection, apci, tpci

_DEVICE = IndividualAddress("1.1.16")


@pytest.fixture(autouse=True)
def _short_ack_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, "MANAGEMENT_ACK_TIMEOUT", 0.01)


async def _connect() -> tuple[XKNX, AsyncMock, P2PConnection]:
    xknx = XKNX()
    send = AsyncMock()
    xknx.cemi_handler = AsyncMock(send_telegram=send)
    connection = await xknx.management.connect(_DEVICE)
    send.reset_mock()
    return xknx, send, connection


def _sent_data(send: AsyncMock) -> list[Telegram]:
    return [
        c.args[0]
        for c in send.call_args_list
        if isinstance(c.args[0].tpci, tpci.TDataConnected)
    ]


async def test_repeated_three_times_without_an_ack() -> None:
    assert management.MANAGEMENT_MAX_REPETITIONS == 3
    _, send, connection = await _connect()
    with pytest.raises(ManagementConnectionTimeout):
        await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))
    sent = _sent_data(send)
    assert len(sent) == 4
    # the same telegram each time, with the same sequence number
    assert all(t == sent[0] for t in sent)


async def test_an_ack_to_the_last_repetition_completes_it() -> None:
    xknx, send, connection = await _connect()

    def acknowledge_the_last(telegram: Telegram) -> None:
        if len(_sent_data(send)) == 4:
            xknx.management.process(
                Telegram(
                    source_address=_DEVICE,
                    destination_address=xknx.current_address,
                    direction=TelegramDirection.INCOMING,
                    tpci=tpci.TAck(0),
                )
            )

    send.side_effect = acknowledge_the_last
    await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))
    assert len(_sent_data(send)) == 4
