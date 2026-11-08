"""Keep failed point-to-point exchanges from reusing a broken connection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from xknx.exceptions import (
    CommunicationError,
    ConfirmationError,
    ManagementConnectionError,
    ManagementConnectionRefused,
)
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import Telegram
from xknx.telegram.tpci import TAck, TDataConnected, TDisconnect, TNak

from .errors import ManagementAckTimeout

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.apci import APCI

logger = logging.getLogger("xknx.management")
_RECONNECT_DELAY = 2.5
_FAILED_AT: dict[tuple[XKNX, int], float] = {}
_PENDING_SEQUENCE: dict[int, int] = {}


def _expire_failure(key: tuple[XKNX, int], failed_at: float) -> None:
    if _FAILED_AT.get(key) == failed_at:
        _FAILED_AT.pop(key)


def _mark_failed(connection: P2PConnection) -> None:
    key = (connection.xknx, connection.address.raw)
    failed_at = time.monotonic()
    _FAILED_AT[key] = failed_at
    asyncio.get_running_loop().call_later(
        _RECONNECT_DELAY, _expire_failure, key, failed_at
    )


class _TransportP2PConnection(P2PConnection):
    """Holds the transport guards installed on xknx's connections."""

    xknx_process = P2PConnection.process
    xknx_connect = P2PConnection.connect

    async def delayed_connect(self: P2PConnection) -> None:
        """Let traffic from a failed exchange drain before reconnecting to that peer."""
        key = (self.xknx, self.address.raw)
        if (failed_at := _FAILED_AT.get(key)) is not None:
            delay = failed_at + _RECONNECT_DELAY - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            _expire_failure(key, failed_at)
        await _TransportP2PConnection.xknx_connect(self)

    def guarded_process(self: P2PConnection, telegram: Telegram) -> None:
        """Wake both waits on disconnect and tolerate already delivered ACKs."""
        if isinstance(telegram.tpci, TDisconnect):
            logger.info("%s disconnected management session.", self.address)
            if self._connected:
                _mark_failed(self)
                self._connected = False
            for waiter in (self._ack_waiter, self._response_waiter):
                if waiter is not None and not waiter.done():
                    waiter.set_exception(
                        ManagementConnectionRefused(
                            f"Management connection to {self.address} disconnected by the peer."
                        )
                    )
                    # A send without a response has no task awaiting the response future.
                    waiter.exception()
            return
        if not self._connected:
            return
        if (
            isinstance(telegram.tpci, TNak)
            and (sequence := _PENDING_SEQUENCE.get(id(self))) is not None
            and telegram.tpci.sequence_number != sequence
        ):
            logger.debug(
                "%s: ignored NAK sequence %d, expected %d",
                self.address,
                telegram.tpci.sequence_number,
                sequence,
            )
            return
        if (
            isinstance(telegram.tpci, TAck | TNak)
            and self._ack_waiter is not None
            and self._ack_waiter.done()
        ):
            logger.debug("%s: ignored an ACK/NAK after the wait ended", self.address)
            return
        _TransportP2PConnection.xknx_process(self, telegram)

    async def checked_send_data(
        self: P2PConnection, payload: APCI, wait_for_ack: bool = True
    ) -> None:
        """Repeat the same frame until acknowledged or the retry budget is exhausted."""
        if not self._connected:
            raise ManagementConnectionRefused(
                f"Management connection to {self.address} is closed."
            )
        sequence = next(self.sequence_number)
        _PENDING_SEQUENCE[id(self)] = sequence
        telegram = Telegram(
            destination_address=self.address,
            source_address=self.xknx.current_address,
            payload=payload,
            tpci=TDataConnected(sequence_number=sequence),
        )
        try:
            if not wait_for_ack:
                await self.xknx.cemi_handler.send_telegram(telegram)
                return
            for repetition in range(management.MANAGEMENT_MAX_REPETITIONS + 1):
                if repetition:
                    logger.info(
                        "%s: no positive ACK for sequence %d; repeating telegram (%d/%d)",
                        self.address,
                        sequence,
                        repetition,
                        management.MANAGEMENT_MAX_REPETITIONS,
                    )
                self._ack_waiter = asyncio.get_running_loop().create_future()
                await self.xknx.cemi_handler.send_telegram(telegram)
                try:
                    async with asyncio.timeout(management.MANAGEMENT_ACK_TIMEOUT):
                        ack = await self._ack_waiter
                except TimeoutError:
                    continue
                if not self._connected:
                    raise ManagementConnectionRefused(
                        f"Management connection to {self.address} disconnected by the peer."
                    )
                if ack.sequence_number != sequence:
                    raise ManagementConnectionError(
                        f"{self.address}: received {type(ack).__name__} sequence "
                        f"{ack.sequence_number}, expected ACK sequence {sequence} "
                        f"for {payload}"
                    )
                if isinstance(ack, TNak):
                    if repetition < management.MANAGEMENT_MAX_REPETITIONS:
                        continue
                    raise ManagementConnectionError(
                        f"{self.address}: repeated NAK for sequence {sequence} for {payload}"
                    )
                return
            raise ManagementAckTimeout(
                f"{self.address}: no ACK received for sequence {sequence} "
                f"after {management.MANAGEMENT_MAX_REPETITIONS + 1} transmissions of {payload}"
            )
        except (ConfirmationError, CommunicationError) as exc:
            raise ManagementConnectionError(
                f"{self.address}: error sending sequence {sequence} for {payload}: {exc}"
            ) from exc
        finally:
            _PENDING_SEQUENCE.pop(id(self), None)
            if self._ack_waiter is not None:
                self._ack_waiter.cancel()
            self._ack_waiter = None

    async def guarded_send_data(
        self: P2PConnection, payload: APCI, wait_for_ack: bool = True
    ) -> None:
        """Clean up a failed exchange before allowing another connection."""
        try:
            await _TransportP2PConnection.checked_send_data(self, payload, wait_for_ack)
        except (ManagementConnectionError, asyncio.CancelledError):
            self._response_waiter.cancel()
            if not self._response_waiter.cancelled():
                self._response_waiter.exception()
            if self._connected:
                _mark_failed(self)
                self._connected = False
                with contextlib.suppress(ConfirmationError, CommunicationError):
                    await self.xknx.cemi_handler.send_telegram(
                        Telegram(
                            destination_address=self.address,
                            source_address=self.xknx.current_address,
                            tpci=TDisconnect(),
                        )
                    )
            raise


def install() -> None:
    """Install transport guards independently of upstream response matching."""
    if P2PConnection.send_data is _TransportP2PConnection.guarded_send_data:
        return
    P2PConnection.process = _TransportP2PConnection.guarded_process  # type: ignore[method-assign]
    P2PConnection.send_data = _TransportP2PConnection.guarded_send_data  # type: ignore[method-assign]
    P2PConnection.connect = _TransportP2PConnection.delayed_connect  # type: ignore[method-assign]
