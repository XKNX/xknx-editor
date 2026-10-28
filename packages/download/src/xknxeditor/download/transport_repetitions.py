"""Repeat an unacknowledged point-to-point telegram as often as the transport layer does.

xknx 4.0 repeats a ``T_Data_Connected`` the device does not acknowledge only once. The
transport layer repeats it up to ``max_rep_count`` = 3 times, each after the acknowledgement
timeout of 3 s (KNX v01.02.03 - Transport Layer 03.03.04 - §4, events E17/E18 of §5.3), so on
a path that loses a telegram now and then two losses in a row broke a download off halfway.

:func:`install` gives every ``P2PConnection`` that behaviour until xknx has it itself
(XKNX/xknx#2004, which adds ``MANAGEMENT_MAX_REPETITIONS``); with such an xknx it does nothing.
"""

from __future__ import annotations

import asyncio
import logging

from xknx.exceptions import (
    CommunicationError,
    ConfirmationError,
    ManagementConnectionError,
    ManagementConnectionRefused,
    ManagementConnectionTimeout,
)
from xknx.management import management
from xknx.management.management import MANAGEMENT_ACK_TIMEOUT, P2PConnection
from xknx.telegram import Telegram
from xknx.telegram.apci import APCI
from xknx.telegram.tpci import TAck, TDataConnected, TNak

# How often a telegram without an ACK is repeated (max_rep_count).
MAX_REPETITIONS = 3

# The messages read like xknx's own, under its logger.
logger = logging.getLogger("xknx.management")


class _RepeatingP2PConnection(P2PConnection):
    """Holds the ``send_data`` that :func:`install` puts on ``P2PConnection``."""

    # ``self`` is any P2PConnection: install() puts the method on that class.
    async def send_data(
        self: P2PConnection, payload: APCI, wait_for_ack: bool = True
    ) -> None:
        """Send a payload to the KNX device.

        If ``wait_for_ack`` is True (default), waits for the device to ACK the telegram -
        repeating it up to ``MAX_REPETITIONS`` times, each after ``MANAGEMENT_ACK_TIMEOUT``
        without an ACK - and validates the ACK's sequence number.
        """
        if not self._connected:
            raise ManagementConnectionRefused(
                "Management connection disconnected by the peer."
            )
        seq_num = next(self.sequence_number)
        telegram = Telegram(
            destination_address=self.address,
            source_address=self.xknx.current_address,
            payload=payload,
            tpci=TDataConnected(sequence_number=seq_num),
        )
        if not wait_for_ack:
            await self.xknx.cemi_handler.send_telegram(telegram)
            return

        ack: TAck | TNak | None = None
        try:
            for repetition in range(MAX_REPETITIONS + 1):
                if repetition:
                    logger.info(
                        "%s: timeout while waiting for ACK. Resending Telegram (%s/%s).",
                        self.address,
                        repetition,
                        MAX_REPETITIONS,
                    )
                # on a timeout the Future is cancelled, so each send gets a new one
                self._ack_waiter = asyncio.get_running_loop().create_future()
                await self.xknx.cemi_handler.send_telegram(telegram)
                try:
                    async with asyncio.timeout(MANAGEMENT_ACK_TIMEOUT):
                        ack = await self._ack_waiter
                    break
                except TimeoutError:
                    continue
        except ConfirmationError as exc:
            raise ManagementConnectionError(
                f"Error while sending Telegram: {exc}"
            ) from exc
        except CommunicationError as exc:
            raise ManagementConnectionError("Error while sending Telegram") from exc
        finally:
            self._ack_waiter = None

        if ack is None:
            raise ManagementConnectionTimeout("No ACK received for repeated telegram.")
        if isinstance(ack, TNak):
            raise ManagementConnectionError(
                f"Received no_ack from sending Telegram: {telegram}"
            )
        if ack.sequence_number != seq_num:
            raise ManagementConnectionError(
                f"Ack sequence number {ack.sequence_number} does not match request "
                f"sequence number of {telegram}"
            )


def install() -> bool:
    """Make ``P2PConnection`` repeat an unacknowledged telegram ``MAX_REPETITIONS`` times.

    Returns whether it did: not with an xknx that repeats that often itself.
    """
    if hasattr(management, "MANAGEMENT_MAX_REPETITIONS"):
        return False
    P2PConnection.send_data = _RepeatingP2PConnection.send_data
    return True
