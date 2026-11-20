"""The raw send and receive of the cEMI handler that the copy of xknx.io.server needs and xknx
4.1.1 lacks (``xknx_server.compat``), as the xknx fork tests them, and how it is put in place."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from xknx import XKNX
from xknx.cemi.cemi_frame import CEMIFrame
from xknx.cemi.cemi_handler import CEMIHandler
from xknx.dpt import DPTBinary
from xknx.exceptions import ConfirmationError
from xknx.telegram import GroupAddress, Telegram
from xknx.telegram.apci import GroupValueWrite

from editor_gui.cemi_diagnostics import DiagnosticCEMIHandler
from editor_gui.plugins.connection.xknx_server import compat
from editor_gui.plugins.connection.xknx_server.compat import (
    CEMIHandlerWithHooks,
    hooked_cemi_handler,
    usb_interface_info,
)

RAW_GROUP_WRITE = bytes.fromhex("2900bce0110a0a03010081")
REQUEST = bytes.fromhex("1100bce0110a0a03010081")
CONFIRMATION = bytes.fromhex("2e00bce0110a0a03010081")


def _xknx() -> tuple[XKNX, CEMIHandlerWithHooks]:
    xknx = XKNX()
    xknx.knxip_interface = AsyncMock()
    return xknx, hooked_cemi_handler(xknx)


async def test_send_cemi_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_cemi_frame returns the raw L_Data.con, which the callbacks don't get."""
    monkeypatch.setattr(compat, "REQUEST_TO_CONFIRMATION_TIMEOUT", 0.05)
    xknx, handler = _xknx()
    received = MagicMock(return_value=None)
    handler.register_cemi_received_cb(received)
    request = CEMIFrame.from_knx(REQUEST)

    task = asyncio.create_task(handler.send_cemi_frame(request))
    await asyncio.sleep(0)
    cast(Any, xknx.knxip_interface).send_cemi.assert_called_once_with(request)
    # an indication meanwhile is no confirmation
    handler.handle_raw_cemi(RAW_GROUP_WRITE)
    received.assert_called_once_with(RAW_GROUP_WRITE)
    assert not task.done()
    handler.handle_raw_cemi(CONFIRMATION)
    assert await task == CONFIRMATION
    assert received.call_count == 1
    # the confirmation of a telegram xknx sends goes to the callbacks
    handler.handle_raw_cemi(CONFIRMATION)
    received.assert_called_with(CONFIRMATION)

    with pytest.raises(ConfirmationError):
        await handler.send_cemi_frame(request)


async def test_sends_share_the_lock_of_send_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw frame waits until the telegram sent before it is confirmed (and vice versa)."""
    xknx, handler = _xknx()
    sent: list[str] = []

    async def send_cemi(cemi: CEMIFrame) -> None:
        sent.append(cemi.to_knx().hex())

    cast(Any, xknx.knxip_interface).send_cemi = send_cemi
    telegram = asyncio.create_task(
        handler.send_telegram(
            Telegram(
                destination_address=GroupAddress("1/2/3"),
                payload=GroupValueWrite(DPTBinary(1)),
            )
        )
    )
    await asyncio.sleep(0)
    raw = asyncio.create_task(handler.send_cemi_frame(CEMIFrame.from_knx(REQUEST)))
    await asyncio.sleep(0.01)
    # the raw frame waits for the telegram's confirmation
    assert len(sent) == 1
    handler.handle_raw_cemi(bytes.fromhex("2e") + bytes.fromhex(sent[0])[1:])
    await telegram
    await asyncio.sleep(0.01)
    assert len(sent) == 2 and sent[1] == REQUEST.hex()
    handler.handle_raw_cemi(CONFIRMATION)
    assert await raw == CONFIRMATION


async def test_cemi_received_callbacks() -> None:
    """Registering and unregistering callbacks; an error in one is logged."""
    _, handler = _xknx()
    failing = MagicMock(side_effect=RuntimeError("bug"))
    received = MagicMock(return_value=None)
    unregister = handler.register_cemi_received_cb(failing)
    handler.register_cemi_received_cb(received)
    with patch("logging.Logger.exception") as mock_exception:
        handler.handle_raw_cemi(RAW_GROUP_WRITE)
    mock_exception.assert_called_once()
    received.assert_called_once_with(RAW_GROUP_WRITE)
    # unparsable frames are passed on as well
    handler.handle_raw_cemi(b"\x00")
    received.assert_called_with(b"\x00")

    unregister()
    unregister()
    handler.unregister_cemi_received_cb(received)
    handler.handle_raw_cemi(RAW_GROUP_WRITE)
    assert failing.call_count == 2
    assert received.call_count == 2


async def test_a_callback_takes_a_frame() -> None:
    """A frame a callback takes is neither handled nor passed to later callbacks."""
    xknx, handler = _xknx()
    taking = MagicMock(return_value=True)
    later = MagicMock(return_value=None)
    handler.register_cemi_received_cb(taking)
    handler.register_cemi_received_cb(later)
    handler.handle_raw_cemi(RAW_GROUP_WRITE)
    taking.assert_called_once_with(RAW_GROUP_WRITE)
    later.assert_not_called()
    assert xknx.telegrams.empty()
    taking.return_value = False
    handler.handle_raw_cemi(RAW_GROUP_WRITE)
    later.assert_called_once_with(RAW_GROUP_WRITE)
    assert not xknx.telegrams.empty()


def test_put_in_place_of_a_plain_handler() -> None:
    xknx = XKNX()
    plain = xknx.cemi_handler
    assert type(plain) is CEMIHandler
    plain.data_secure = cast(Any, "keys")
    hooked = hooked_cemi_handler(xknx)
    assert xknx.cemi_handler is hooked and hooked is not plain
    assert hooked.data_secure == "keys"
    # once
    assert hooked_cemi_handler(xknx) is hooked


def test_the_editors_handler_has_them() -> None:
    xknx = XKNX()
    diagnostic = DiagnosticCEMIHandler(xknx, mode="usb", target="x")
    xknx.cemi_handler = diagnostic
    assert hooked_cemi_handler(xknx) is diagnostic

    class Other(CEMIHandler):
        __slots__ = ()

    xknx.cemi_handler = Other(xknx)
    with pytest.raises(TypeError, match="no raw send and receive"):
        hooked_cemi_handler(xknx)


def test_usb_interface_info() -> None:
    xknx = XKNX()
    assert usb_interface_info(xknx) is None
    info = object()
    xknx.knxip_interface = cast(Any, SimpleNamespace(usb_info=lambda: info))
    assert usb_interface_info(xknx) is info
