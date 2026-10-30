"""Connectionless point-to-point communication (T_Data_Individual, no T_Connect).

xknx sends T_Data_Individual for any individual destination, but drops incoming
connectionless frames when no connection to the sender is open. While a manager is
open, ``Management.process`` routes the device's frames to its channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionTimeout
from xknx.management.management import Management
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.tpci import TDataIndividual

from .response_matching import answers

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.apci import APCI

# Same response timeout as xknx' connection-oriented P2PConnection.
RESPONSE_TIMEOUT = 6.0


class ConnectionlessConnection:
    """A :class:`~xknxeditor.download.programmer.BusConnection` without transport connection."""

    def __init__(self, xknx: XKNX, address: IndividualAddress) -> None:
        self._xknx = xknx
        self._address = address
        self._received: asyncio.Queue[Telegram] = asyncio.Queue()

    def process(self, telegram: Telegram) -> None:
        self._received.put_nowait(telegram)

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        await self._xknx.cemi_handler.send_telegram(
            Telegram(
                destination_address=self._address,
                payload=payload,
                tpci=TDataIndividual(),
            )
        )

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        while not self._received.empty():
            self._received.get_nowait()
        await self.send_data(payload)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESPONSE_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ManagementConnectionTimeout(
                    f"No response from {self._address} for {payload}"
                )
            try:
                telegram = await asyncio.wait_for(self._received.get(), remaining)
            except TimeoutError as exc:
                raise ManagementConnectionTimeout(
                    f"No response from {self._address} for {payload}"
                ) from exc
            if (expected is None or isinstance(telegram.payload, expected)) and answers(
                payload, telegram.payload
            ):
                return telegram


# The open channels by the ``Management`` they receive through (its id: ``Management`` has
# ``__slots__``, so it can neither carry them nor be referenced weakly) and the device.
_CHANNELS: dict[int, dict[IndividualAddress, ConnectionlessConnection]] = {}
_management_process: Callable[[Management, Telegram], None] | None = None


def _process(management: Management, telegram: Telegram) -> None:
    """``Management.process`` that hands a device's connectionless frames to its channel."""
    channels = _CHANNELS.get(id(management), {})
    channel = channels.get(telegram.source_address)
    if channel is not None and isinstance(telegram.tpci, TDataIndividual):
        channel.process(telegram)
        return
    assert _management_process is not None
    _management_process(management, telegram)


def _route_connectionless_frames() -> None:
    """Put :func:`_process` on ``Management`` once."""
    global _management_process
    if _management_process is None:
        _management_process = Management.process
        Management.process = _process  # type: ignore[method-assign]


class ConnectionlessManager:
    """Open/close a connectionless channel; same contract as the other connection managers."""

    def __init__(self, xknx: XKNX, address: IndividualAddress) -> None:
        self._xknx = xknx
        self._address = address
        self._management: Management | None = None

    async def open(self) -> ConnectionlessConnection:
        _route_connectionless_frames()
        connection = ConnectionlessConnection(self._xknx, self._address)
        self._management = self._xknx.management
        _CHANNELS.setdefault(id(self._management), {})[self._address] = connection
        return connection

    async def close(self) -> None:
        if self._management is None:
            return
        channels = _CHANNELS.get(id(self._management), {})
        channels.pop(self._address, None)
        if not channels:
            _CHANNELS.pop(id(self._management), None)
        self._management = None
