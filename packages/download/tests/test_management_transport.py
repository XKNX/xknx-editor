"""Transport retries preserve sequence numbers and stop when the connection fails."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.exceptions import (
    CommunicationError,
    ManagementConnectionError,
    ManagementConnectionRefused,
)
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import IndividualAddress, Telegram, TelegramDirection, apci, tpci

from xknxeditor.download import management_transport
from xknxeditor.download.errors import ManagementAckTimeout, MemoryWriteNotAcknowledged
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.session import XknxBusConnection, XknxConnectionManager

_ADDRESS = IndividualAddress("1.3.39")
_WRITE = apci.MemoryWrite(address=0x54, data=bytes(13))


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(management, "MANAGEMENT_ACK_TIMEOUT", 0.005)
    monkeypatch.setattr(management, "MANAGEMENT_CONNECTION_TIMEOUT", 0.01)
    monkeypatch.setattr(management, "MANAGEMENT_MAX_REPETITIONS", 3)
    monkeypatch.setattr("xknxeditor.download.programmer._TRANSIENT_RETRY_DELAY", 0.0)
    monkeypatch.setattr(management_transport, "_RECONNECT_DELAY", 0.0)


class _Peer:
    def __init__(self) -> None:
        self.xknx = XKNX()
        self.sent: list[Telegram] = []
        self.on_data: Callable[[Telegram], None] = lambda _: None
        self.xknx.cemi_handler = AsyncMock(send_telegram=self.send)

    async def connect(self) -> P2PConnection:
        return await self.xknx.management.connect(_ADDRESS, rate_limit=0)

    async def send(self, telegram: Telegram) -> None:
        self.sent.append(telegram)
        if isinstance(telegram.tpci, tpci.TDataConnected):
            self.on_data(telegram)

    def receive(self, control: tpci.TPCI, payload: apci.APCI | None = None) -> None:
        self.xknx.management.process(
            Telegram(
                source_address=_ADDRESS,
                destination_address=self.xknx.current_address,
                direction=TelegramDirection.INCOMING,
                tpci=control,
                payload=payload,
            )
        )

    def ack(self, telegram: Telegram) -> None:
        assert isinstance(telegram.tpci, tpci.TDataConnected)
        self.receive(tpci.TAck(telegram.tpci.sequence_number))

    @property
    def sequences(self) -> list[int]:
        return [
            telegram.tpci.sequence_number
            for telegram in self.sent
            if isinstance(telegram.tpci, tpci.TDataConnected)
        ]


async def _assert_closed(peer: _Peer, connection: P2PConnection) -> None:
    before = len(peer.sent)
    with pytest.raises(ManagementConnectionRefused):
        await connection.send_data(_WRITE)
    assert len(peer.sent) == before
    with pytest.raises(ManagementConnectionRefused):
        await peer.xknx.management.disconnect(_ADDRESS)
    fresh = await peer.connect()
    peer.on_data = peer.ack
    await fresh.send_data(_WRITE)
    assert peer.sequences[-1] == 0
    await fresh.disconnect()


@pytest.mark.parametrize("negative_ack", [False, True])
async def test_one_missing_or_negative_ack_repeats_the_same_frame(
    negative_ack: bool,
) -> None:
    peer = _Peer()
    connection = await peer.connect()

    def respond(telegram: Telegram) -> None:
        if len(peer.sequences) == 1:
            if negative_ack:
                peer.receive(tpci.TNak(0))
            return
        peer.ack(telegram)

    peer.on_data = respond
    await connection.send_data(_WRITE)
    await connection.send_data(_WRITE)
    assert peer.sequences == [0, 0, 1]
    data = [t for t in peer.sent if isinstance(t.tpci, tpci.TDataConnected)]
    assert data[0] is data[1]
    assert not any(isinstance(t.tpci, tpci.TDisconnect) for t in peer.sent)
    await connection.disconnect()


@pytest.mark.parametrize("use_request", [False, True])
async def test_exhausted_ack_retries_close_without_advancing(use_request: bool) -> None:
    peer = _Peer()
    connection = await peer.connect()
    with pytest.raises(ManagementAckTimeout, match="sequence 0 after 4 transmissions"):
        if use_request:
            await connection.request(_WRITE)
        else:
            await connection.send_data(_WRITE)
    assert peer.sequences == [0, 0, 0, 0]
    assert isinstance(peer.sent[-1].tpci, tpci.TDisconnect)
    await _assert_closed(peer, connection)


async def test_wrong_ack_sequence_closes_with_expected_and_received_sequence() -> None:
    peer = _Peer()
    connection = await peer.connect()
    peer.on_data = lambda _: peer.receive(tpci.TAck(7))
    with pytest.raises(
        ManagementConnectionError, match="sequence 7, expected ACK sequence 0"
    ):
        await connection.request(_WRITE)
    assert peer.sequences == [0]
    assert isinstance(peer.sent[-1].tpci, tpci.TDisconnect)
    await _assert_closed(peer, connection)


async def test_wrong_nak_sequence_is_ignored_while_waiting_for_ack() -> None:
    peer = _Peer()
    connection = await peer.connect()

    def respond(telegram: Telegram) -> None:
        peer.receive(tpci.TNak(7))
        peer.ack(telegram)

    peer.on_data = respond
    await connection.send_data(_WRITE)
    assert peer.sequences == [0]
    await connection.disconnect()


async def test_reconnect_waits_only_after_failure_on_the_same_bus_and_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _Peer()
    connection = await peer.connect()
    monkeypatch.setattr(management_transport, "_RECONNECT_DELAY", 2.5)
    peer.on_data = lambda _: peer.receive(tpci.TDisconnect())
    with pytest.raises(ManagementConnectionRefused):
        await connection.send_data(_WRITE)
    with pytest.raises(ManagementConnectionRefused):
        await peer.xknx.management.disconnect(_ADDRESS)
    sleep = AsyncMock()
    monkeypatch.setattr(management_transport.asyncio, "sleep", sleep)
    other_peer = await peer.xknx.management.connect(IndividualAddress("1.3.40"))
    await other_peer.disconnect()
    other_bus = _Peer()
    other_connection = await other_bus.connect()
    await other_connection.disconnect()
    sleep.assert_not_called()
    fresh = await peer.connect()
    sleep.assert_awaited_once()
    assert 0 < sleep.await_args.args[0] <= 2.5
    await fresh.disconnect()
    clean = await peer.connect()
    assert sleep.await_count == 1
    await clean.disconnect()


async def test_repeated_nak_exhausts_the_same_transport_budget() -> None:
    peer = _Peer()
    connection = await peer.connect()
    peer.on_data = lambda _: peer.receive(tpci.TNak(0))
    with pytest.raises(ManagementConnectionError, match="repeated NAK"):
        await connection.send_data(_WRITE)
    assert peer.sequences == [0, 0, 0, 0]
    await _assert_closed(peer, connection)


@pytest.mark.parametrize("before_disconnect", ["nothing", "ack", "response", "both"])
async def test_peer_disconnect_stops_pending_send_even_with_completed_waiters(
    before_disconnect: str,
) -> None:
    peer = _Peer()
    connection = await peer.connect()

    def respond(telegram: Telegram) -> None:
        if before_disconnect in ("ack", "both"):
            peer.ack(telegram)
        if before_disconnect in ("response", "both"):
            peer.receive(
                tpci.TDataConnected(0),
                apci.MemoryResponse(address=_WRITE.address, data=_WRITE.data),
            )
        peer.receive(tpci.TDisconnect())

    peer.on_data = respond
    with pytest.raises(ManagementConnectionRefused):
        await connection.request(_WRITE)
    assert peer.sequences == [0]
    await _assert_closed(peer, connection)


async def test_duplicate_ack_does_not_break_the_request() -> None:
    peer = _Peer()
    connection = await peer.connect()

    def respond(telegram: Telegram) -> None:
        peer.ack(telegram)
        peer.ack(telegram)
        peer.receive(
            tpci.TDataConnected(0),
            apci.MemoryResponse(address=_WRITE.address, data=_WRITE.data),
        )

    peer.on_data = respond
    response = await connection.request(_WRITE)
    assert response.payload == apci.MemoryResponse(
        address=_WRITE.address, data=_WRITE.data
    )
    await connection.disconnect()


async def test_lost_response_can_be_retried_on_the_acknowledged_connection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    peer = _Peer()
    connection = await peer.connect()

    def respond(telegram: Telegram) -> None:
        peer.ack(telegram)
        if len(peer.sequences) == 2:
            peer.receive(tpci.TDataConnected(0), apci.MemoryResponse(0x54, b"\x12"))

    peer.on_data = respond
    programmer = DeviceProgrammer(XknxBusConnection(connection))
    with caplog.at_level(logging.DEBUG):
        assert await programmer.read_memory(0x54, 1) == b"\x12"
    assert peer.sequences == [0, 1]
    assert "Cause: Timeout while waiting for" in caplog.text
    await connection.disconnect()


@pytest.mark.parametrize("wait_for_ack", [False, True])
async def test_send_failure_closes_connection(wait_for_ack: bool) -> None:
    peer = _Peer()
    connection = await peer.connect()

    def fail(_: Telegram) -> None:
        raise CommunicationError("interface unavailable")

    peer.on_data = fail
    with pytest.raises(ManagementConnectionError, match="interface unavailable"):
        await connection.send_data(_WRITE, wait_for_ack=wait_for_ack)
    await _assert_closed(peer, connection)


async def test_cancelled_send_closes_connection() -> None:
    peer = _Peer()
    connection = await peer.connect()
    sent = asyncio.Event()
    peer.on_data = lambda _: sent.set()
    task = asyncio.create_task(connection.request(_WRITE))
    await sent.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_closed(peer, connection)


@pytest.mark.parametrize("failure", ["timeout", "wrong_ack", "nak", "disconnect"])
async def test_memory_failure_allows_smaller_blocks_on_a_fresh_connection(
    failure: str,
) -> None:
    peer = _Peer()
    manager = XknxConnectionManager(peer.xknx, _ADDRESS)
    counts: list[int] = []

    def respond(telegram: Telegram) -> None:
        payload = telegram.payload
        if isinstance(payload, apci.PropertyValueRead):
            peer.ack(telegram)
            peer.receive(
                tpci.TDataConnected(0),
                apci.PropertyValueResponse(
                    object_index=0, property_id=14, count=1, start_index=1, data=b"\x02"
                ),
            )
        elif isinstance(payload, apci.MemoryWrite):
            counts.append(len(payload.data))
            if len(payload.data) <= 12:
                peer.ack(telegram)
            elif failure == "wrong_ack":
                peer.receive(tpci.TAck(15))
            elif failure == "nak":
                assert isinstance(telegram.tpci, tpci.TDataConnected)
                peer.receive(tpci.TNak(telegram.tpci.sequence_number))
            elif failure == "disconnect":
                peer.receive(tpci.TDisconnect())

    peer.on_data = respond
    connection = await manager.open()
    programmer = DeviceProgrammer(connection, max_apdu_length=55)
    with pytest.raises(MemoryWriteNotAcknowledged):
        await programmer.write_memory(_WRITE.address, _WRITE.data)
    repetitions = 4 if failure in ("timeout", "nak") else 1
    assert counts == [13] * repetitions
    await manager.close()
    connection = await manager.open()
    programmer = DeviceProgrammer(connection, max_apdu_length=55)
    programmer.max_memory_service_count = 12
    await programmer.write_memory(_WRITE.address, _WRITE.data)
    assert counts[-2:] == [12, 1]
    assert peer.sequences == [0, *([1] * repetitions), 0, 1, 2]
    await manager.close()
