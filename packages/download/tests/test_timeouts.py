"""The waits of the bus exchanges, set for a slow connection, and a probe that tells a frame the
interface did not confirm from a device that is not there."""

from __future__ import annotations

import asyncio
import importlib
import time
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.exceptions import (
    ConfirmationError,
    ManagementConnectionError,
    ManagementConnectionTimeout,
)
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.tpci import TConnect

from xknxeditor.download.errors import InterfaceNotConfirming
from xknxeditor.download.timeouts import (
    BusTimeouts,
    bus_timeouts,
    confirmation_timed_out,
    set_bus_timeouts,
)

# The package re-exports ``download``, which shadows the submodule on attribute access.
dl = importlib.import_module("xknxeditor.download.download")
DEVICE = IndividualAddress("1.1.21")


@pytest.fixture(autouse=True)
def _defaults() -> Iterator[None]:
    yield
    set_bus_timeouts(BusTimeouts())


def test_the_defaults_are_xknxs() -> None:
    assert bus_timeouts() == BusTimeouts(confirmation=3, acknowledgement=3, response=6)


def test_set_timeouts_are_the_ones_in_use() -> None:
    from xknx.cemi import cemi_handler
    from xknx.management import management

    set_bus_timeouts(BusTimeouts(confirmation=10, acknowledgement=5, response=20))
    assert bus_timeouts() == BusTimeouts(10, 5, 20)
    assert cemi_handler.REQUEST_TO_CONFIRMATION_TIMEOUT == 10
    assert management.MANAGEMENT_ACK_TIMEOUT == 5
    assert management.MANAGEMENT_CONNECTION_TIMEOUT == 20


async def test_a_frame_waits_for_its_confirmation_as_long_as_set() -> None:
    xknx = XKNX()
    xknx.knxip_interface = cast(Any, AsyncMock())
    set_bus_timeouts(BusTimeouts(confirmation=0.05))
    started = time.monotonic()
    with pytest.raises(ConfirmationError):
        await xknx.cemi_handler.send_telegram(
            Telegram(destination_address=DEVICE, tpci=TConnect())
        )
    assert time.monotonic() - started < 2


def test_a_confirmation_timeout_is_found_behind_the_errors_it_caused() -> None:
    sent = ManagementConnectionError("error sending sequence 13")
    sent.__cause__ = ConfirmationError("L_DATA_CON timed out")
    wrapped = RuntimeError("Programming failed")
    wrapped.__context__ = sent
    assert confirmation_timed_out(sent)
    assert confirmation_timed_out(wrapped)
    assert not confirmation_timed_out(ManagementConnectionTimeout("No ACK received"))
    # A chain that refers back to itself ends.
    loop = RuntimeError("a")
    loop.__context__ = RuntimeError("b")
    loop.__context__.__context__ = loop
    assert not confirmation_timed_out(loop)


class _Manager:
    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.closed = False

    async def open(self) -> object:
        raise self._error

    async def close(self) -> None:
        self.closed = True


def _probe(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> bool:
    manager = _Manager(error)
    monkeypatch.setattr(dl, "_connection_manager", lambda *_args: manager)
    try:
        return asyncio.run(dl._device_present(cast(Any, None), DEVICE, None))  # pyright: ignore[reportPrivateUsage]
    finally:
        assert manager.closed


def test_a_device_that_does_not_answer_is_not_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A full download then assigns the address to the device in programming mode.
    assert not _probe(monkeypatch, ManagementConnectionTimeout("No ACK received"))


def test_a_connection_the_interface_did_not_confirm_tells_nothing_about_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = ManagementConnectionError(f"Connection to {DEVICE} failed")
    refused.__cause__ = ConfirmationError("L_DATA_CON timed out")
    set_bus_timeouts(BusTimeouts(confirmation=4))
    with pytest.raises(
        InterfaceNotConfirming, match=r"1\.1\.21: .* within 4 s"
    ) as info:
        _probe(monkeypatch, refused)
    assert info.value.__cause__ is refused
    assert confirmation_timed_out(info.value)
