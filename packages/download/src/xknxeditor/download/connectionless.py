"""Connectionless point-to-point communication (T_Data_Individual, no T_Connect).

xknx sends T_Data_Individual for any individual destination, but drops incoming
connectionless frames when no connection to the sender is open. The manager
routes them to the waiting request while it is open.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionTimeout
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


class ConnectionlessManager:
    """Open/close a connectionless channel; same contract as the other connection managers."""

    def __init__(self, xknx: XKNX, address: IndividualAddress) -> None:
        self._xknx = xknx
        self._address = address
        self._installed = False
        self._own: Callable[[Telegram], None] | None = None

    async def open(self) -> ConnectionlessConnection:
        connection = ConnectionlessConnection(self._xknx, self._address)
        management = self._xknx.management
        previous = vars(management).get("process")
        forward: Callable[[Telegram], None] = management.process

        def process(telegram: Telegram) -> None:
            if telegram.source_address == self._address and isinstance(
                telegram.tpci, TDataIndividual
            ):
                connection.process(telegram)
                return
            forward(telegram)

        self._own = previous
        management.process = process  # type: ignore[method-assign]
        self._installed = True
        return connection

    async def close(self) -> None:
        if not self._installed:
            return
        management = self._xknx.management
        if self._own is None:
            vars(management).pop("process", None)
        else:
            management.process = self._own  # type: ignore[method-assign]
        self._installed = False
