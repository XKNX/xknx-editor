"""One frame at a time: the next is sent only after the confirmation (L_Data.con) of the one
before arrived, or the wait for it timed out. A T_Ack sent in the background therefore no longer
overlaps the following request."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.cemi import CEMIFrame, CEMIMessageCode
from xknx.exceptions import ConfirmationError
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import MemoryRead
from xknx.telegram.tpci import TAck, TDataConnected

from editor_gui.plugins.connection.interface import SequentialCEMIHandler


class _Interface:
    """Records the frames handed to the interface."""

    def __init__(self) -> None:
        self.sent: list[CEMIFrame] = []

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        self.sent.append(cemi)


def _handler() -> tuple[SequentialCEMIHandler, _Interface]:
    xknx = XKNX()
    handler = SequentialCEMIHandler(xknx)
    xknx.cemi_handler = handler
    interface = _Interface()
    xknx.knxip_interface = cast(Any, interface)
    return handler, interface


def _confirm(handler: SequentialCEMIHandler, cemi: CEMIFrame) -> None:
    handler.handle_cemi_frame(
        CEMIFrame(code=CEMIMessageCode.L_DATA_CON, data=cemi.data)
    )


def _ack() -> Telegram:
    return Telegram(IndividualAddress("1.0.11"), tpci=TAck(sequence_number=1))


def _read() -> Telegram:
    return Telegram(
        IndividualAddress("1.0.11"),
        tpci=TDataConnected(sequence_number=2),
        payload=MemoryRead(address=0x4509, count=2),
    )


async def test_the_next_frame_waits_for_the_confirmation() -> None:
    handler, interface = _handler()
    ack = asyncio.create_task(handler.send_telegram(_ack()))
    read = asyncio.create_task(handler.send_telegram(_read()))
    await asyncio.sleep(0.05)
    assert len(interface.sent) == 1
    _confirm(handler, interface.sent[0])
    await ack
    await asyncio.sleep(0.05)
    assert len(interface.sent) == 2
    _confirm(handler, interface.sent[1])
    await read


async def test_a_missing_confirmation_releases_the_next_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import xknx.cemi.cemi_handler as cemi_handler

    monkeypatch.setattr(cemi_handler, "REQUEST_TO_CONFIRMATION_TIMEOUT", 0.05)
    handler, interface = _handler()
    ack = asyncio.create_task(handler.send_telegram(_ack()))
    read = asyncio.create_task(handler.send_telegram(_read()))
    while not interface.sent:
        await asyncio.sleep(0)
    # The acknowledgement waits with the short timeout, the read sent after it with a long one,
    # so that a busy test machine still confirms the read in time.
    monkeypatch.setattr(cemi_handler, "REQUEST_TO_CONFIRMATION_TIMEOUT", 10)
    with pytest.raises(ConfirmationError):
        await ack
    async with asyncio.timeout(5):
        while len(interface.sent) < 2:
            await asyncio.sleep(0.01)
    _confirm(handler, interface.sent[1])
    await read
