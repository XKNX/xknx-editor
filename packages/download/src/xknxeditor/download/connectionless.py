"""Connectionless point-to-point communication (T_Data_Individual, no T_Connect).

A :class:`~xknxeditor.download.programmer.BusConnection` on xknx's ``P2PConnectionless``:
while a manager is open, ``Management.connectionless()`` hands the device's connectionless
frames to it instead of dropping them.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionTimeout

from .response_matching import answers

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.management.management import P2PConnectionless
    from xknx.telegram import IndividualAddress, Telegram
    from xknx.telegram.apci import APCI

# Same response timeout as xknx' connection-oriented P2PConnection.
RESPONSE_TIMEOUT = 6.0


class ConnectionlessConnection:
    """A :class:`~xknxeditor.download.programmer.BusConnection` without transport connection."""

    def __init__(self, connectionless: P2PConnectionless) -> None:
        self._connectionless = connectionless

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        await self._connectionless.send(payload)

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        """Send ``payload`` and return the device's answer to it: a telegram of ``expected``
        that answers this request, not one to an earlier request (see ``answers``)."""
        await self._connectionless.send(payload)
        async with contextlib.aclosing(
            self._connectionless.receive(expected, timeout=RESPONSE_TIMEOUT)
        ) as telegrams:
            async for telegram in telegrams:
                if answers(payload, telegram.payload):
                    return telegram
        raise ManagementConnectionTimeout(
            f"No response from {self._connectionless.address} for {payload}"
        )


class ConnectionlessManager:
    """Open/close a connectionless channel; same contract as the other connection managers."""

    def __init__(self, xknx: XKNX, address: IndividualAddress) -> None:
        self._xknx = xknx
        self._address = address
        self._context: contextlib.AsyncExitStack | None = None

    async def open(self) -> ConnectionlessConnection:
        context = contextlib.AsyncExitStack()
        # No rate limit: it only paces xknx's own request(), which isn't used here.
        connectionless = await context.enter_async_context(
            self._xknx.management.connectionless(self._address, rate_limit=0)
        )
        self._context = context
        return ConnectionlessConnection(connectionless)

    async def close(self) -> None:
        context, self._context = self._context, None
        if context is not None:
            await context.aclose()
