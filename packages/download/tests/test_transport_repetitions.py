"""A point-to-point telegram without an ACK is repeated as often as the transport layer does."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.exceptions import (
    ConfirmationError,
    ManagementConnectionError,
    ManagementConnectionRefused,
    ManagementConnectionTimeout,
)
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import IndividualAddress, Telegram, TelegramDirection, apci, tpci

from xknxeditor.download import transport_repetitions
from xknxeditor.download.transport_repetitions import MAX_REPETITIONS

_DEVICE = IndividualAddress("1.1.16")


@pytest.fixture(autouse=True)
def _short_ack_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_repetitions, "MANAGEMENT_ACK_TIMEOUT", 0.01)


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


def _from_device(xknx: XKNX, control: tpci.TAck | tpci.TNak) -> None:
    xknx.management.process(
        Telegram(
            source_address=_DEVICE,
            destination_address=xknx.current_address,
            direction=TelegramDirection.INCOMING,
            tpci=control,
        )
    )


def test_the_editor_repeats_with_this_xknx() -> None:
    # Installed on import of the download package, for every P2PConnection.
    assert (
        P2PConnection.send_data
        is transport_repetitions._RepeatingP2PConnection.send_data
    )  # pyright: ignore[reportPrivateUsage]


def test_not_installed_with_an_xknx_that_repeats_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(management, "MANAGEMENT_MAX_REPETITIONS", 3, raising=False)
    original = P2PConnection.send_data
    assert transport_repetitions.install() is False
    assert P2PConnection.send_data is original


async def test_repeated_three_times_without_an_ack() -> None:
    _, send, connection = await _connect()
    with pytest.raises(
        ManagementConnectionTimeout, match="No ACK received for repeated telegram"
    ):
        await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))
    sent = _sent_data(send)
    assert len(sent) == MAX_REPETITIONS + 1
    # the same telegram each time, with the same sequence number
    assert all(t == sent[0] for t in sent)


async def test_an_ack_to_the_last_repetition_completes_it() -> None:
    xknx, send, connection = await _connect()

    def acknowledge_the_last(telegram: Telegram) -> None:
        if len(_sent_data(send)) == MAX_REPETITIONS + 1:
            _from_device(xknx, tpci.TAck(0))

    send.side_effect = acknowledge_the_last
    await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))
    assert len(_sent_data(send)) == MAX_REPETITIONS + 1


async def test_an_ack_stops_the_repetitions() -> None:
    xknx, send, connection = await _connect()
    send.side_effect = lambda telegram: _from_device(xknx, tpci.TAck(0))
    await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))
    assert len(_sent_data(send)) == 1


async def test_a_nak_fails_it() -> None:
    xknx, send, connection = await _connect()
    send.side_effect = lambda telegram: _from_device(xknx, tpci.TNak(0))
    with pytest.raises(ManagementConnectionError, match="Received no_ack"):
        await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))


async def test_an_ack_with_another_sequence_number_fails_it() -> None:
    xknx, send, connection = await _connect()
    send.side_effect = lambda telegram: _from_device(xknx, tpci.TAck(5))
    with pytest.raises(ManagementConnectionError, match="does not match"):
        await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))


async def test_sent_once_without_waiting_for_an_ack() -> None:
    _, send, connection = await _connect()
    await connection.send_data(apci.Restart(), wait_for_ack=False)
    assert len(_sent_data(send)) == 1


async def test_a_telegram_the_interface_does_not_confirm_fails_it() -> None:
    _, send, connection = await _connect()
    send.side_effect = ConfirmationError("no L_Data.con")
    with pytest.raises(ManagementConnectionError, match=r"no L_Data\.con"):
        await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))


async def test_nothing_is_sent_on_a_closed_connection() -> None:
    _, send, connection = await _connect()
    await connection.disconnect()
    with pytest.raises(ManagementConnectionRefused):
        await connection.send_data(apci.DeviceDescriptorRead(descriptor=0))
    assert _sent_data(send) == []
