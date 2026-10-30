"""Take only the response that answers a point-to-point request.

xknx 4.0 hands a request whatever the device sends next on the connection: also a response
that arrived while no request waited for one, and a second response to an earlier request
that was sent again after its acknowledgement got lost. A download then took the memory
response of a repeated read as the answer to the next property write and stopped. It also
drops a telegram that arrives while it still holds an earlier one.

:func:`answers` compares a response with its request as the application layer does: the
response service of the request, for memory services the same address, for property and
function property services the same object and property (and start index). The number of
octets or elements is left to the caller, which takes a shorter memory response from a
device that caps it. :func:`install` makes ``P2PConnection`` wait for such an answer, ignore
the rest and keep telegrams that arrive back to back, until xknx does it itself
(XKNX/xknx#2006, which adds ``APCIRequest.is_response``); with such an xknx it does nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
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
from xknx.telegram.tpci import TDataConnected

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

# Telegrams a connection received while its response waiter already held one, in order, by
# the connection (its id: ``P2PConnection`` has ``__slots__``, so it cannot carry them).
_QUEUED: dict[int, deque[Telegram]] = {}


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
        return response.address == request.address
    if isinstance(response, UserMemoryResponse) and isinstance(
        request, UserMemoryRead | UserMemoryWrite
    ):
        return response.address == request.address
    if isinstance(
        response, MemoryExtendedReadResponse | MemoryExtendedWriteResponse
    ) and isinstance(request, MemoryExtendedRead | MemoryExtendedWrite):
        return response.address == request.address
    if isinstance(response, PropertyValueResponse) and isinstance(
        request, PropertyValueRead | PropertyValueWrite
    ):
        return (
            response.object_index == request.object_index
            and response.property_id == request.property_id
            and response.start_index == request.start_index
        )
    if isinstance(
        response, PropertyExtValueResponse | PropertyExtValueWriteConRes
    ) and isinstance(request, PropertyExtValueRead | PropertyExtValueWriteCon):
        return (
            response.interface_object_type == request.interface_object_type
            and response.object_instance == request.object_instance
            and response.property_id == request.property_id
            and response.start_index == request.start_index
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
        return response.channel == request.channel
    return True


class _MatchingP2PConnection(P2PConnection):
    """Holds what :func:`install` puts on ``P2PConnection``."""

    # ``self`` is any P2PConnection: install() puts the methods on that class.
    def queueing_process(self: P2PConnection, telegram: Telegram) -> None:
        """Process an incoming telegram; one the response waiter has no room for is queued."""
        held = self._response_waiter
        if not (held.done() and isinstance(telegram.tpci, TDataConnected)):
            _MatchingP2PConnection.xknx_process(self, telegram)
            return
        # xknx acknowledges it and keeps the sequence; only the payload needs a place
        self._response_waiter = asyncio.get_running_loop().create_future()
        try:
            _MatchingP2PConnection.xknx_process(self, telegram)
        finally:
            received, self._response_waiter = self._response_waiter, held
        if (
            received.done()
            and not received.cancelled()
            and received.exception() is None
        ):
            _QUEUED.setdefault(id(self), deque()).append(received.result())

    # xknx's own ``P2PConnection.process``, set by install()
    xknx_process = P2PConnection.process

    def renew_response_waiter(self: P2PConnection) -> None:
        """Set up the Future for the next response, holding a queued one if any."""
        self._response_waiter = asyncio.get_running_loop().create_future()
        queued = _QUEUED.get(id(self))
        if queued:
            self._response_waiter.set_result(queued.popleft())
        if not queued:
            _QUEUED.pop(id(self), None)

    async def answered_request(self: P2PConnection, payload: APCI) -> Telegram:
        """Send a payload to the KNX device and wait for the response that answers it.

        The responses that arrived before the request was sent, and those that do not
        answer it (see :func:`answers`), are ignored; the wait goes on until
        ``MANAGEMENT_CONNECTION_TIMEOUT`` after the device acknowledged the request.
        """
        if not self._connected:
            _QUEUED.pop(id(self), None)
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
        unclaimed = list(_QUEUED.pop(id(self), ()))
        held = self._response_waiter
        if held.done() and not held.cancelled() and held.exception() is None:
            unclaimed.insert(0, held.result())
        for telegram in unclaimed:
            logger.debug(
                "%s: ignored %s, it arrived before %s",
                self.address,
                telegram.payload,
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
                _MatchingP2PConnection.renew_response_waiter(self)
            if answers(payload, telegram.payload):
                self._last_response_time = time.time()
                return telegram
            logger.debug(
                "%s: ignored %s, it does not answer %s",
                self.address,
                telegram.payload,
                payload,
            )


def install() -> bool:
    """Make ``P2PConnection`` take only the response that answers a request.

    Returns whether it did: not with an xknx that does it itself.
    """
    if hasattr(APCIRequest, "is_response"):
        return False
    if P2PConnection.request is not _MatchingP2PConnection.answered_request:
        P2PConnection.process = _MatchingP2PConnection.queueing_process  # type: ignore[method-assign]
        P2PConnection.request = _MatchingP2PConnection.answered_request  # type: ignore[method-assign]
    return True
