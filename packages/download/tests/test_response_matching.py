"""A point-to-point request takes only the response that answers it."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.exceptions import ManagementConnectionRefused, ManagementConnectionTimeout
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import IndividualAddress, Telegram, TelegramDirection, apci, tpci

from xknxeditor.download import response_matching, transport_repetitions
from xknxeditor.download.connectionless import ConnectionlessConnection
from xknxeditor.download.response_matching import answer_type, answers

_DEVICE = IndividualAddress("1.1.16")

_PROPERTY_WRITE = apci.PropertyValueWrite(
    object_index=1, property_id=5, count=1, start_index=1, data=bytes(10)
)
_PROPERTY_ANSWER = apci.PropertyValueResponse(
    object_index=1, property_id=5, count=1, start_index=1, data=b"\x02"
)
# The second answer to a repeated read of memory 0x4003 in the log of issue #170.
_LATE_MEMORY_ANSWER = apci.MemoryResponse(
    address=0x4003, data=bytes.fromhex("088c088d088e088f4801")
)


@pytest.fixture(autouse=True)
def _short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_repetitions, "MANAGEMENT_ACK_TIMEOUT", 0.01)
    monkeypatch.setattr(management, "MANAGEMENT_CONNECTION_TIMEOUT", 0.05)


class _Device:
    """The device end of a connection: acknowledges each request, then sends ``replies``."""

    def __init__(self) -> None:
        self.xknx = XKNX()
        self.send = AsyncMock(side_effect=self._on_send)
        self.xknx.cemi_handler = AsyncMock(send_telegram=self.send)
        self.replies: list[apci.APCI] = []
        self.on_send: Callable[[], None] | None = None
        self._sequence = 0

    async def connect(self) -> P2PConnection:
        connection = await self.xknx.management.connect(_DEVICE)
        self.send.reset_mock()
        return connection

    def _on_send(self, telegram: Telegram) -> None:
        if not isinstance(telegram.tpci, tpci.TDataConnected):
            return
        self.control(tpci.TAck(telegram.tpci.sequence_number))
        if self.on_send is not None:
            self.on_send()
        for payload in self.replies:
            self.answer(payload)
        self.replies = []

    def control(self, control: tpci.TPCI) -> None:
        self.xknx.management.process(
            Telegram(
                source_address=_DEVICE,
                destination_address=self.xknx.current_address,
                direction=TelegramDirection.INCOMING,
                tpci=control,
            )
        )

    def answer(self, payload: apci.APCI) -> None:
        self.xknx.management.process(
            Telegram(
                source_address=_DEVICE,
                destination_address=self.xknx.current_address,
                direction=TelegramDirection.INCOMING,
                payload=payload,
                tpci=tpci.TDataConnected(sequence_number=self._sequence),
            )
        )
        self._sequence = self._sequence + 1 & 0xF


def test_installed_for_every_connection() -> None:
    matching = response_matching._MatchingP2PConnection  # pyright: ignore[reportPrivateUsage]
    assert P2PConnection.request is matching.answered_request
    assert P2PConnection.process is matching.queueing_process
    # installing again changes nothing
    assert response_matching.install() is True
    assert matching.xknx_process is not matching.queueing_process


def test_not_installed_with_an_xknx_that_matches_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        apci.APCIRequest, "is_response", lambda self, payload: True, raising=False
    )
    original = P2PConnection.request
    assert response_matching.install() is False
    assert P2PConnection.request is original


async def test_back_to_back_responses_are_both_kept() -> None:
    # e.g. two frames of one TCP segment, handled before the request runs again
    device = _Device()
    connection = await device.connect()
    device.replies = [_LATE_MEMORY_ANSWER, _PROPERTY_ANSWER]
    telegram = await connection.request(_PROPERTY_WRITE)
    assert telegram.payload == _PROPERTY_ANSWER
    assert id(connection) not in response_matching._QUEUED  # pyright: ignore[reportPrivateUsage]


async def test_all_responses_from_before_the_request_are_ignored() -> None:
    device = _Device()
    connection = await device.connect()
    device.answer(_LATE_MEMORY_ANSWER)
    device.answer(_PROPERTY_ANSWER)
    fresh = apci.PropertyValueResponse(
        object_index=1, property_id=5, count=1, start_index=1, data=b"\x03"
    )
    device.replies = [fresh]
    telegram = await connection.request(_PROPERTY_WRITE)
    assert telegram.payload == fresh


async def test_a_capped_memory_response_answers() -> None:
    # A device that caps its replies answers a 10-octet read with fewer octets.
    device = _Device()
    connection = await device.connect()
    capped = apci.MemoryResponse(address=0x4003, data=bytes(4))
    device.replies = [capped]
    telegram = await connection.request(apci.MemoryRead(address=0x4003, count=10))
    assert telegram.payload == capped


async def test_a_response_from_before_the_request_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    device = _Device()
    connection = await device.connect()
    # The repeated read was answered twice; the second answer arrives while nothing waits.
    device.answer(_LATE_MEMORY_ANSWER)
    device.replies = [_PROPERTY_ANSWER]
    with caplog.at_level(logging.DEBUG, logger="xknx.management"):
        telegram = await connection.request(_PROPERTY_WRITE)
    assert telegram.payload == _PROPERTY_ANSWER
    assert "arrived before" in caplog.text


async def _request_answered_after(
    device: _Device, connection: P2PConnection, first: apci.APCI
) -> Telegram:
    """``_PROPERTY_WRITE``, answered by ``first`` and a moment later by its answer."""
    device.replies = [first]

    async def answer_later() -> None:
        await asyncio.sleep(0.01)
        device.answer(_PROPERTY_ANSWER)

    task = asyncio.create_task(answer_later())
    telegram = await connection.request(_PROPERTY_WRITE)
    await task
    return telegram


async def test_a_response_to_another_request_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The second answer to the repeated read arrives after the next request was sent.
    device = _Device()
    connection = await device.connect()
    with caplog.at_level(logging.DEBUG, logger="xknx.management"):
        telegram = await _request_answered_after(
            device, connection, _LATE_MEMORY_ANSWER
        )
    assert telegram.payload == _PROPERTY_ANSWER
    assert "does not answer" in caplog.text


async def test_a_response_about_another_object_is_ignored() -> None:
    device = _Device()
    connection = await device.connect()
    other_object = apci.PropertyValueResponse(
        object_index=2, property_id=5, count=1, start_index=1, data=b"\x02"
    )
    telegram = await _request_answered_after(device, connection, other_object)
    assert telegram.payload == _PROPERTY_ANSWER


async def test_a_response_during_the_acknowledgement_wait_answers() -> None:
    device = _Device()
    connection = await device.connect()
    device.on_send = lambda: device.answer(_PROPERTY_ANSWER)
    telegram = await connection.request(_PROPERTY_WRITE)
    assert telegram.payload == _PROPERTY_ANSWER


async def test_without_an_answer_it_times_out() -> None:
    device = _Device()
    connection = await device.connect()
    device.replies = [_LATE_MEMORY_ANSWER]
    with pytest.raises(ManagementConnectionTimeout, match="PropertyValueResponse"):
        await connection.request(_PROPERTY_WRITE)


async def test_the_next_request_still_gets_its_answer() -> None:
    device = _Device()
    connection = await device.connect()
    device.replies = [_PROPERTY_ANSWER]
    await connection.request(_PROPERTY_WRITE)
    read = apci.MemoryRead(address=0x4003, count=10)
    device.replies = [_LATE_MEMORY_ANSWER]
    telegram = await connection.request(read)
    assert telegram.payload == _LATE_MEMORY_ANSWER


async def test_a_disconnect_ends_the_wait() -> None:
    device = _Device()
    connection = await device.connect()
    device.on_send = lambda: device.control(tpci.TDisconnect())
    with pytest.raises(ManagementConnectionRefused):
        await connection.request(_PROPERTY_WRITE)


async def test_nothing_is_sent_on_a_closed_connection() -> None:
    device = _Device()
    connection = await device.connect()
    await connection.disconnect()
    device.send.reset_mock()
    with pytest.raises(ManagementConnectionRefused):
        await connection.request(_PROPERTY_WRITE)
    device.send.assert_not_called()


async def test_the_rate_limit_spaces_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    device = _Device()
    connection = await device.connect()
    connection.rate_limit = 1000
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    device.replies = [_PROPERTY_ANSWER]
    await connection.request(_PROPERTY_WRITE)
    device.replies = [_PROPERTY_ANSWER]
    await connection.request(_PROPERTY_WRITE)
    assert len(slept) == 1
    assert 0 < slept[0] <= 0.001


async def test_connectionless_ignores_a_response_to_another_request() -> None:
    xknx = XKNX()
    connection = ConnectionlessConnection(xknx, _DEVICE)

    def reply(payload: apci.APCI) -> None:
        connection.process(
            Telegram(
                source_address=_DEVICE,
                destination_address=xknx.current_address,
                direction=TelegramDirection.INCOMING,
                payload=payload,
                tpci=tpci.TDataIndividual(),
            )
        )

    async def send(telegram: Telegram) -> None:
        reply(_LATE_MEMORY_ANSWER)
        reply(_PROPERTY_ANSWER)

    xknx.cemi_handler = AsyncMock(send_telegram=send)
    telegram = await connection.request(_PROPERTY_WRITE, apci.PropertyValueResponse)
    assert telegram.payload == _PROPERTY_ANSWER


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (apci.MemoryWrite(address=1, data=b"\x00"), apci.MemoryResponse),
        (apci.UserMemoryWrite(address=1, data=b"\x00"), apci.UserMemoryResponse),
        (apci.FunctionPropertyCommand(1, 2), apci.FunctionPropertyStateResponse),
        (
            apci.FunctionPropertyExtCommand(1, 1, 2),
            apci.FunctionPropertyExtStateResponse,
        ),
        (apci.MemoryRead(address=1), apci.MemoryResponse),
        (apci.Restart(), None),
    ],
)
def test_answer_type(request_: apci.APCI, expected: type[apci.APCI] | None) -> None:
    assert answer_type(request_) is expected


@pytest.mark.parametrize(
    ("request_", "response", "answered"),
    [
        # memory: the same address; the number of octets is the caller's
        (
            apci.MemoryRead(address=0x4003, count=10),
            apci.MemoryResponse(address=0x4003, data=bytes(10)),
            True,
        ),
        (
            apci.MemoryRead(address=0x4000, count=10),
            apci.MemoryResponse(address=0x4003, data=bytes(10)),
            False,
        ),
        (
            apci.MemoryRead(address=0x4003, count=10),
            apci.MemoryResponse(address=0x4003, data=bytes(4)),
            True,
        ),
        (
            apci.MemoryRead(address=0x4003, count=10),
            apci.MemoryResponse(address=0x4003, data=b""),
            True,
        ),
        (
            apci.MemoryWrite(address=0x4003, data=bytes(2)),
            apci.MemoryResponse(address=0x4003, data=bytes(2)),
            True,
        ),
        (
            apci.MemoryWrite(address=0x4003, data=bytes(2)),
            apci.UserMemoryResponse(address=0x4003, data=bytes(2)),
            False,
        ),
        (
            apci.UserMemoryRead(address=0x10000, count=2),
            apci.UserMemoryResponse(address=0x10000, data=bytes(2)),
            True,
        ),
        (
            apci.UserMemoryWrite(address=0x10000, data=bytes(2)),
            apci.UserMemoryResponse(address=0x10001, data=bytes(2)),
            False,
        ),
        (
            apci.MemoryExtendedRead(count=4, address=0x20000),
            apci.MemoryExtendedReadResponse(0, 0x20000, bytes(4)),
            True,
        ),
        (
            apci.MemoryExtendedRead(count=4, address=0x20000),
            apci.MemoryExtendedReadResponse(0, 0x20000, bytes(2)),
            True,
        ),
        (
            apci.MemoryExtendedRead(count=4, address=0x20000),
            apci.MemoryExtendedReadResponse(0, 0x20004, bytes(4)),
            False,
        ),
        (
            apci.MemoryExtendedRead(count=4, address=0x20000),
            apci.MemoryExtendedReadResponse(3, 0x20000),
            True,
        ),
        (
            apci.MemoryExtendedWrite(address=0x20000, data=bytes(4)),
            apci.MemoryExtendedWriteResponse(0, 0x20000),
            True,
        ),
        (
            apci.MemoryExtendedWrite(address=0x20000, data=bytes(4)),
            apci.MemoryExtendedWriteResponse(0, 0x20004),
            False,
        ),
        # property values: the same object, property and start index
        (_PROPERTY_WRITE, _PROPERTY_ANSWER, True),
        (_PROPERTY_WRITE, _LATE_MEMORY_ANSWER, False),
        (
            _PROPERTY_WRITE,
            apci.PropertyValueResponse(
                object_index=1, property_id=5, start_index=1, count=0
            ),
            True,
        ),
        (
            _PROPERTY_WRITE,
            apci.PropertyValueResponse(object_index=1, property_id=6, start_index=1),
            False,
        ),
        (
            _PROPERTY_WRITE,
            apci.PropertyValueResponse(object_index=1, property_id=5, start_index=2),
            False,
        ),
        (
            apci.PropertyValueRead(object_index=0, property_id=11, count=2),
            apci.PropertyValueResponse(
                object_index=0, property_id=11, count=1, start_index=1
            ),
            True,
        ),
        (
            apci.PropertyExtValueRead(11, 1, 52, 1, 1),
            apci.PropertyExtValueResponse(11, 1, 52, 1, 1, b"\x00"),
            True,
        ),
        (
            apci.PropertyExtValueRead(11, 1, 52, 1, 1),
            apci.PropertyExtValueResponse(11, 2, 52, 1, 1, b"\x00"),
            False,
        ),
        (
            apci.PropertyExtValueWriteCon(11, 1, 52, 1, 1, b"\x00"),
            apci.PropertyExtValueWriteConRes(
                11, 1, 52, apci.ReturnCode.E_SUCCESS, 1, 1
            ),
            True,
        ),
        # property descriptions: by property id, or by index when the id is 0
        (
            apci.PropertyDescriptionRead(object_index=1, property_id=5),
            apci.PropertyDescriptionResponse(
                object_index=1, property_id=5, property_index=3
            ),
            True,
        ),
        (
            apci.PropertyDescriptionRead(object_index=1, property_id=5),
            apci.PropertyDescriptionResponse(object_index=2, property_id=5),
            False,
        ),
        (
            apci.PropertyDescriptionRead(
                object_index=1, property_id=0, property_index=3
            ),
            apci.PropertyDescriptionResponse(
                object_index=1, property_id=5, property_index=3
            ),
            True,
        ),
        (
            apci.PropertyDescriptionRead(
                object_index=1, property_id=0, property_index=3
            ),
            apci.PropertyDescriptionResponse(
                object_index=1, property_id=5, property_index=4
            ),
            False,
        ),
        (
            apci.PropertyExtDescriptionRead(11, 1, 52),
            apci.PropertyExtDescriptionResponse(11, 1, 52),
            True,
        ),
        (
            apci.PropertyExtDescriptionRead(11, 1, 0, property_index=2),
            apci.PropertyExtDescriptionResponse(11, 1, 52, property_index=3),
            False,
        ),
        # function properties: the same object and property
        (
            apci.FunctionPropertyCommand(object_index=1, property_id=52),
            apci.FunctionPropertyStateResponse(object_index=1, property_id=52),
            True,
        ),
        (
            apci.FunctionPropertyStateRead(object_index=1, property_id=52),
            apci.FunctionPropertyStateResponse(object_index=1, property_id=53),
            False,
        ),
        (
            apci.FunctionPropertyExtCommand(11, 1, 52),
            apci.FunctionPropertyExtStateResponse(11, 1, 52, apci.ReturnCode.E_SUCCESS),
            True,
        ),
        (
            apci.FunctionPropertyExtStateRead(11, 1, 52),
            apci.FunctionPropertyExtStateResponse(12, 1, 52, apci.ReturnCode.E_SUCCESS),
            False,
        ),
        # A/D converter: the same channel
        (apci.ADCRead(channel=1, count=2), apci.ADCResponse(channel=1, count=2), True),
        (apci.ADCRead(channel=1, count=2), apci.ADCResponse(channel=2, count=2), False),
        # services answered by their response type alone
        (
            apci.DeviceDescriptorRead(descriptor=0),
            apci.DeviceDescriptorResponse(descriptor=0, value=0x07B0),
            True,
        ),
        (apci.DeviceDescriptorRead(descriptor=0), _PROPERTY_ANSWER, False),
        (apci.AuthorizeRequest(key=0xFFFFFFFF), apci.AuthorizeResponse(level=0), True),
        # a service without a response takes anything
        (apci.Restart(), _PROPERTY_ANSWER, True),
    ],
)
def test_answers(request_: apci.APCI, response: apci.APCI, answered: bool) -> None:
    assert answers(request_, response) is answered
