"""Take only the response that answers a point-to-point request.

xknx 4.0 hands a request whatever the device sends next on the connection: also a response
that arrived while no request waited for one, and a second response to an earlier request
that was sent again after its acknowledgement got lost. A download then took the memory
response of a repeated read as the answer to the next property write and stopped.

:func:`answers` compares a response with its request as the application layer does: the
response service of the request, for memory services the same address, for property and
function property services the same object and property (and start index), and the same
number of bytes or elements, or none, which is how a device refuses a request.
:func:`install` makes ``P2PConnection.request`` wait for such an answer and ignore the rest.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import cast

from xknx.exceptions import ManagementConnectionRefused, ManagementConnectionTimeout
from xknx.management import management
from xknx.management.management import P2PConnection
from xknx.telegram import Telegram
from xknx.telegram.apci import (
    APCI,
    ADCRead,
    ADCResponse,
    APCIRequest,
    FunctionPropertyCommand,
    FunctionPropertyExtCommand,
    FunctionPropertyExtStateRead,
    FunctionPropertyExtStateResponse,
    FunctionPropertyStateRead,
    FunctionPropertyStateResponse,
    MemoryExtendedRead,
    MemoryExtendedReadResponse,
    MemoryExtendedWrite,
    MemoryExtendedWriteResponse,
    MemoryRead,
    MemoryResponse,
    MemoryWrite,
    PropertyDescriptionRead,
    PropertyDescriptionResponse,
    PropertyExtDescriptionRead,
    PropertyExtDescriptionResponse,
    PropertyExtValueRead,
    PropertyExtValueResponse,
    PropertyExtValueWriteCon,
    PropertyExtValueWriteConRes,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
    UserMemoryRead,
    UserMemoryResponse,
    UserMemoryWrite,
)

# The messages read like xknx's own, under its logger.
logger = logging.getLogger("xknx.management")

# Requests xknx declares no response for, answered all the same: a memory write by the
# echo of the written bytes (verify mode), a function property command by its state.
_ANSWER_TYPES: dict[type[APCI], type[APCI]] = {
    MemoryWrite: MemoryResponse,
    UserMemoryWrite: UserMemoryResponse,
    FunctionPropertyCommand: FunctionPropertyStateResponse,
    FunctionPropertyExtCommand: FunctionPropertyExtStateResponse,
}


def answer_type(request: APCI) -> type[APCI] | None:
    """The service that answers ``request``, or None when any telegram does."""
    if isinstance(request, APCIRequest):
        return cast("APCIRequest[APCI]", request).RESPONSE_TYPE
    return _ANSWER_TYPES.get(type(request))


def answers(request: APCI, response: APCI | None) -> bool:
    """Whether ``response`` answers ``request``."""
    expected = answer_type(request)
    if expected is not None and not isinstance(response, expected):
        return False
    if isinstance(response, MemoryResponse) and isinstance(
        request, MemoryRead | MemoryWrite
    ):
        return response.address == request.address and response.count in (
            request.count,
            0,
        )
    if isinstance(response, UserMemoryResponse) and isinstance(
        request, UserMemoryRead | UserMemoryWrite
    ):
        return response.address == request.address and response.count in (
            request.count,
            0,
        )
    if isinstance(response, MemoryExtendedReadResponse) and isinstance(
        request, MemoryExtendedRead
    ):
        return response.address == request.address and len(response.data) in (
            request.count,
            0,
        )
    if isinstance(response, MemoryExtendedWriteResponse) and isinstance(
        request, MemoryExtendedWrite
    ):
        return response.address == request.address
    if isinstance(response, PropertyValueResponse) and isinstance(
        request, PropertyValueRead | PropertyValueWrite
    ):
        return (
            response.object_index == request.object_index
            and response.property_id == request.property_id
            and response.start_index == request.start_index
            and response.count in (request.count, 0)
        )
    if isinstance(
        response, PropertyExtValueResponse | PropertyExtValueWriteConRes
    ) and (isinstance(request, PropertyExtValueRead | PropertyExtValueWriteCon)):
        return (
            response.interface_object_type == request.interface_object_type
            and response.object_instance == request.object_instance
            and response.property_id == request.property_id
            and response.start_index == request.start_index
            and response.nr_of_elem in (request.nr_of_elem, 0)
        )
    if isinstance(response, PropertyDescriptionResponse) and isinstance(
        request, PropertyDescriptionRead
    ):
        return response.object_index == request.object_index and (
            response.property_id == request.property_id
            if request.property_id
            else response.property_index == request.property_index
        )
    if isinstance(response, PropertyExtDescriptionResponse) and isinstance(
        request, PropertyExtDescriptionRead
    ):
        return (
            response.interface_object_type == request.interface_object_type
            and response.object_instance == request.object_instance
            and (
                response.property_id == request.property_id
                if request.property_id
                else response.property_index == request.property_index
            )
        )
    if isinstance(response, FunctionPropertyStateResponse) and isinstance(
        request, FunctionPropertyCommand | FunctionPropertyStateRead
    ):
        return (
            response.object_index == request.object_index
            and response.property_id == request.property_id
        )
    if isinstance(response, FunctionPropertyExtStateResponse) and isinstance(
        request, FunctionPropertyExtCommand | FunctionPropertyExtStateRead
    ):
        return (
            response.interface_object_type == request.interface_object_type
            and response.object_instance == request.object_instance
            and response.property_id == request.property_id
        )
    if isinstance(response, ADCResponse) and isinstance(request, ADCRead):
        return response.channel == request.channel and response.count in (
            request.count,
            0,
        )
    return True


class _MatchingP2PConnection(P2PConnection):
    """Holds the ``request`` that :func:`install` puts on ``P2PConnection``."""

    # ``self`` is any P2PConnection: install() puts the method on that class.
    async def answered_request(self: P2PConnection, payload: APCI) -> Telegram:
        """Send a payload to the KNX device and wait for the response that answers it.

        A response that arrived before the request was sent, and one that does not
        answer it (see :func:`answers`), is ignored; the wait goes on until
        ``MANAGEMENT_CONNECTION_TIMEOUT`` after the device acknowledged the request.
        """
        if not self._connected:
            raise ManagementConnectionRefused(
                "Management connection disconnected by the peer."
            )
        if self.rate_limit:
            # time in seconds since the last request operation
            time_diff = time.time() - self._last_response_time
            wait_time = 1 / self.rate_limit
            if time_diff < wait_time:
                await asyncio.sleep(wait_time - time_diff)

        loop = asyncio.get_running_loop()
        if self._response_waiter.done():
            stale = self._response_waiter
            if not stale.cancelled() and stale.exception() is None:
                logger.debug(
                    "%s: ignored %s, it arrived before %s",
                    self.address,
                    stale.result().payload,
                    payload,
                )
        self._response_waiter = loop.create_future()
        await self.send_data(payload)

        deadline = loop.time() + management.MANAGEMENT_CONNECTION_TIMEOUT
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    telegram = await self._response_waiter
            except TimeoutError:
                raise ManagementConnectionTimeout(
                    f"Timeout while waiting for {answer_type(payload)}"
                ) from None
            finally:
                # set up new Future for the next response
                self._response_waiter = loop.create_future()
            if answers(payload, telegram.payload):
                self._last_response_time = time.time()
                return telegram
            logger.debug(
                "%s: ignored %s, it does not answer %s",
                self.address,
                telegram.payload,
                payload,
            )


def install() -> None:
    """Make ``P2PConnection.request`` take only the response that answers the request."""
    P2PConnection.request = _MatchingP2PConnection.answered_request  # type: ignore[method-assign]
