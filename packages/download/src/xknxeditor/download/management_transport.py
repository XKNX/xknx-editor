"""Keep failed point-to-point exchanges from reusing a broken connection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.cemi_handler import CEMIHandler
from xknx.cemi.const import CEMIMessageCode
from xknx.exceptions import (
    CommunicationError,
    ConfirmationError,
    ManagementConnectionError,
    ManagementConnectionRefused,
)
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import Telegram
from xknx.telegram.tpci import TAck, TConnect, TDataConnected, TDisconnect, TNak

from .errors import ManagementAckTimeout

if TYPE_CHECKING:
    from collections.abc import Generator

    from xknx import XKNX
    from xknx.telegram.address import IndividualAddress
    from xknx.telegram.apci import APCI

logger = logging.getLogger("xknx.management")
_RECONNECT_DELAY = 2.5
_FAILED_AT: dict[tuple[XKNX, int], float] = {}
_PENDING_SEQUENCE: dict[int, int] = {}
# Connections closed here after a failed exchange (T_Disconnect sent), by id until they are
# closed (P2PConnection has slots without weak references): closing them again ends them quietly.
# xknx's disconnect() raises for a closed connection, which suits a peer that disconnected, but
# would turn every unanswered NM_IndividualAddress_Check (a timeout: the address is free) into
# "occupied", as that procedure takes a refusal on closing for an answer.
_CLOSED_HERE: dict[int, P2PConnection] = {}
# Connections no device took (a negative L_Data.con of their T_Connect), by id until their first
# exchange: it fails at once as unanswered (ManagementAckTimeout), as xknx's procedures take a
# timeout for "no device there" (NM_IndividualAddress_Check: the address is free), instead of
# waiting for every repetition of its frame to go unacknowledged.
_UNCONFIRMED: dict[int, P2PConnection] = {}


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
    xknx_disconnect = P2PConnection.disconnect

    async def delayed_connect(self: P2PConnection) -> None:
        """Let traffic from a failed exchange drain before reconnecting to that peer, and note
        when the link layer reports that no device took the T_Connect (a negative L_Data.con),
        unless the device is one that gives it (see :func:`negative_connect_confirmations`):
        the first exchange then fails at once, as unanswered (see ``_UNCONFIRMED``)."""
        key = (self.xknx, self.address.raw)
        if (failed_at := _FAILED_AT.get(key)) is not None:
            delay = failed_at + _RECONNECT_DELAY - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            _expire_failure(key, failed_at)
        handler = self.xknx.cemi_handler
        watch = _ConnectWatch(self.address.raw)
        _CONNECT_WATCHES[id(handler)] = watch
        try:
            await _TransportP2PConnection.xknx_connect(self)
        finally:
            if _CONNECT_WATCHES.get(id(handler)) is watch:
                del _CONNECT_WATCHES[id(handler)]
        if watch.negative and not _ACCEPTING.get((id(self.xknx), self.address.raw)):
            logger.info(
                "%s: no device on the bus took the connection (negative L_Data.con)",
                self.address,
            )
            _UNCONFIRMED[id(self)] = self

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
        # Like ETS, ignore an ACK/NAK of another frame: a device acknowledges a repeated
        # frame again, and that late duplicate arrives while the next frame waits (#242).
        if (
            isinstance(telegram.tpci, TAck | TNak)
            and (sequence := _PENDING_SEQUENCE.get(id(self))) is not None
            and telegram.tpci.sequence_number != sequence
        ):
            logger.debug(
                "%s: ignored %s sequence %d, expected %d",
                self.address,
                "ACK" if isinstance(telegram.tpci, TAck) else "NAK",
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
        if _UNCONFIRMED.pop(id(self), None) is self:
            raise ManagementAckTimeout(
                f"{self.address}: no device on the bus took the connection "
                "(negative L_Data.con)"
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
                _CLOSED_HERE[id(self)] = self
                with contextlib.suppress(ConfirmationError, CommunicationError):
                    await self.xknx.cemi_handler.send_telegram(
                        Telegram(
                            destination_address=self.address,
                            source_address=self.xknx.current_address,
                            tpci=TDisconnect(),
                        )
                    )
            raise

    async def guarded_disconnect(self: P2PConnection) -> None:
        """End a connection closed here after a failed exchange without another T_Disconnect
        and without raising (see ``_CLOSED_HERE``); otherwise as xknx does."""
        if _UNCONFIRMED.get(id(self)) is self:
            del _UNCONFIRMED[id(self)]
        if _CLOSED_HERE.get(id(self)) is self:
            del _CLOSED_HERE[id(self)]
            self.disconnect_hook()
            return
        await _TransportP2PConnection.xknx_disconnect(self)


@dataclass(slots=True)
class _ConnectWatch:
    """The link layer confirmation of a T_Connect to ``destination`` being sent."""

    destination: int
    negative: bool = False


# The T_Connect being sent, by id of the cEMI handler sending it (one at a time: its send lock
# keeps the confirmation of each frame apart from the next).
_CONNECT_WATCHES: dict[int, _ConnectWatch] = {}
# Devices whose negative confirmation of a connection is ignored, by id of their xknx and
# address: the number of operations that asked for it.
_ACCEPTING: dict[tuple[int, int], int] = {}
# Applications whose devices take a connection but may not confirm it on the link layer.
_NEGATIVE_CONNECT_CONFIRMATIONS = ("M-0009_A-2077", "M-0009_A-2078")


def _watched_handle_cemi_frame(self: CEMIHandler, cemi: CEMIFrame) -> None:
    """Note the link layer confirmation of a T_Connect being sent, then handle the frame as
    xknx does."""
    watch = _CONNECT_WATCHES.get(id(self))
    data = cemi.data
    if (
        watch is not None
        and cemi.code is CEMIMessageCode.L_DATA_CON
        and isinstance(data, CEMILData)
        and isinstance(data.tpci, TConnect)
        and data.dst_addr.raw == watch.destination
    ):
        watch.negative = data.flags.confirm_error
    _XKNX_HANDLE_CEMI_FRAME(self, cemi)


_XKNX_HANDLE_CEMI_FRAME = CEMIHandler.handle_cemi_frame


@contextlib.contextmanager
def negative_connect_confirmations(
    xknx: XKNX, address: IndividualAddress, application_id: str | None
) -> Generator[None]:
    """While open, a connection to ``address`` the link layer does not confirm (a negative
    L_Data.con of its T_Connect) is used as any other when the device's application
    (``application_id``) is one whose devices take a connection without confirming it; for
    every other device its first exchange fails at once, as no device on the bus took it."""
    if not application_id or not application_id.startswith(
        _NEGATIVE_CONNECT_CONFIRMATIONS
    ):
        yield
        return
    key = (id(xknx), address.raw)
    _ACCEPTING[key] = _ACCEPTING.get(key, 0) + 1
    try:
        yield
    finally:
        _ACCEPTING[key] -= 1
        if not _ACCEPTING[key]:
            del _ACCEPTING[key]


def install() -> None:
    """Install transport guards independently of upstream response matching."""
    if P2PConnection.send_data is _TransportP2PConnection.guarded_send_data:
        return
    CEMIHandler.handle_cemi_frame = _watched_handle_cemi_frame  # type: ignore[method-assign]
    P2PConnection.process = _TransportP2PConnection.guarded_process  # type: ignore[method-assign]
    P2PConnection.send_data = _TransportP2PConnection.guarded_send_data  # type: ignore[method-assign]
    P2PConnection.connect = _TransportP2PConnection.delayed_connect  # type: ignore[method-assign]
    P2PConnection.disconnect = _TransportP2PConnection.guarded_disconnect  # type: ignore[method-assign]
