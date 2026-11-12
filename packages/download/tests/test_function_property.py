"""Empty function property responses and the distinction from a successful result."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.cemi import CEMILData
from xknx.exceptions import ConversionError
from xknx.telegram import IndividualAddress, Telegram, TelegramDirection
from xknx.telegram.apci import (
    APCI,
    FunctionPropertyCommand,
    FunctionPropertyStateRead,
    FunctionPropertyStateResponse,
)
from xknx.telegram.tpci import TAck, TDataConnected

from xknxeditor.download import function_property
from xknxeditor.download.errors import VerificationError
from xknxeditor.download.function_property import EmptyFunctionPropertyStateResponse
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.response_matching import answers
from xknxeditor.download.session import XknxBusConnection

from .conftest import FakeDevice


def test_empty_scan_response_roundtrips_cemi() -> None:
    raw = bytes.fromhex("b06010271008034ac9a001")
    frame = CEMILData.from_knx(raw)
    assert frame.src_addr == IndividualAddress("1.0.39")
    assert frame.dst_addr == IndividualAddress("1.0.8")
    assert frame.tpci == TDataConnected(sequence_number=2)
    assert isinstance(frame.payload, EmptyFunctionPropertyStateResponse)
    assert (frame.payload.object_index, frame.payload.property_id) == (160, 1)
    assert frame.payload.calculated_length() == 3
    assert frame.to_knx() == raw
    assert "return_code" not in str(frame.payload)


@pytest.mark.parametrize(
    "result", [b"\x00", b"\x01", b"\x00\x02", b"\x01\x12\x34\x56\x07"]
)
def test_nonempty_responses_keep_all_result_octets(result: bytes) -> None:
    raw = b"\x02\xc9\xa0\x01" + result
    payload = APCI.from_knx(raw)
    assert type(payload) is FunctionPropertyStateResponse
    assert payload.return_code == result[0]
    assert payload.data == result[1:]
    assert payload.to_knx() == raw
    assert payload.calculated_length() == len(raw) - 1


@pytest.mark.parametrize("raw", [b"", b"\x02", b"\x02\xc9", b"\x02\xc9\xa0"])
def test_truncated_response_still_fails(raw: bytes) -> None:
    with pytest.raises(ConversionError):
        FunctionPropertyStateResponse.from_knx(raw)


def test_repeated_install_keeps_normal_parser() -> None:
    function_property.install()
    function_property.install()
    assert isinstance(
        APCI.from_knx(bytes.fromhex("02c9a001")), EmptyFunctionPropertyStateResponse
    )
    assert (
        type(APCI.from_knx(bytes.fromhex("02c9a00100")))
        is FunctionPropertyStateResponse
    )


@pytest.mark.parametrize(
    "request_type", [FunctionPropertyCommand, FunctionPropertyStateRead]
)
def test_empty_response_matches_only_its_object_and_property(
    request_type: type[FunctionPropertyCommand | FunctionPropertyStateRead],
) -> None:
    payload = APCI.from_knx(bytes.fromhex("02c9a001"))
    assert answers(request_type(160, 1), payload)
    assert not answers(request_type(159, 1), payload)
    assert not answers(request_type(160, 2), payload)


class _EmptyDevice(FakeDevice):
    def _function_property_response(
        self, payload: FunctionPropertyCommand | FunctionPropertyStateRead
    ) -> FunctionPropertyStateResponse:
        return FunctionPropertyStateResponse.from_knx(
            bytes((2, 0xC9, payload.object_index, payload.property_id))
        )


@pytest.mark.parametrize("command", [False, True])
async def test_raw_function_property_preserves_empty_result(command: bool) -> None:
    programmer = DeviceProgrammer(_EmptyDevice())
    assert (
        await programmer.function_property_raw(160, 1, b"\x03", command=command) == b""
    )


@pytest.mark.parametrize("command", [False, True])
async def test_checked_function_property_requires_a_return_code(command: bool) -> None:
    programmer = DeviceProgrammer(_EmptyDevice())
    with pytest.raises(VerificationError, match="no function property return code"):
        if command:
            await programmer.invoke_function_property(160, 1, b"\x03")
        else:
            await programmer.read_function_property(160, 1)


async def test_empty_ack_allows_connected_scan_to_poll() -> None:
    xknx = XKNX()
    device = IndividualAddress("1.0.39")
    sent: list[Telegram] = []
    sequence = 0

    async def send(telegram: Telegram) -> None:
        nonlocal sequence
        sent.append(telegram)
        if not isinstance(telegram.tpci, TDataConnected):
            return
        xknx.management.process(
            Telegram(
                source_address=device,
                destination_address=xknx.current_address,
                direction=TelegramDirection.INCOMING,
                tpci=TAck(telegram.tpci.sequence_number),
            )
        )
        result = (
            b"" if isinstance(telegram.payload, FunctionPropertyCommand) else b"\x01"
        )
        raw = (
            bytes.fromhex("b06010271008")
            + bytes((3 + len(result), 0x42 | sequence << 2, 0xC9, 160, 1))
            + result
        )
        frame = CEMILData.from_knx(raw)
        xknx.management.process(
            Telegram(
                source_address=frame.src_addr,
                destination_address=frame.dst_addr,
                direction=TelegramDirection.INCOMING,
                tpci=frame.tpci,
                payload=frame.payload,
            )
        )
        sequence += 1

    xknx.cemi_handler = AsyncMock(send_telegram=send)
    connection = await xknx.management.connect(device)
    try:
        programmer = DeviceProgrammer(XknxBusConnection(connection))
        assert (
            await programmer.function_property_raw(
                160, 1, b"\x03\x01\x00\x00\x00", command=True
            )
            == b""
        )
        assert (
            await programmer.function_property_raw(160, 1, b"\x03", command=False)
            == b"\x01"
        )
        await asyncio.sleep(0)
        assert [t.tpci.sequence_number for t in sent if isinstance(t.tpci, TAck)] == [
            0,
            1,
        ]
    finally:
        await connection.disconnect()
