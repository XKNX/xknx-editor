from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from xknx.cemi import CEMIFrame
from xknx.exceptions import XKNXException
from xknx.io.data_connection import SequenceVerdict
from xknx.io.interface import Interface
from xknx.io.knxip_interface import KNXIPInterface, KNXIPInterfaceThreaded
from xknx.io.transport.ip_transport import KNXIPTransport
from xknx.io.tunnel import TCPTunnel, UDPTunnel
from xknx.knxip import (
    HPAI,
    KNXIPFrame,
    KNXIPServiceType,
    TunnellingFeatureGet,
    TunnellingFeatureResponse,
    TunnellingFeatureType,
)
from xknx.telegram.apci import ReturnCode

# Seconds to wait for a tunnelling feature response.
_FEATURE_TIMEOUT = 2.0

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.io.connection import ConnectionConfig


def _notify_sent(callback: Callable[[bytes], None], cemi: CEMIFrame) -> None:
    """Pass a sent frame to ``callback``; a failing observer never stops the send."""
    with contextlib.suppress(Exception):
        callback(cemi.to_knx())


class ObservableKNXIPInterface(KNXIPInterface):
    __slots__ = ("_raw_cemi_callback", "_sent_cemi_callback")

    def __init__(
        self,
        xknx: XKNX,
        connection_config: ConnectionConfig | None = None,
        raw_cemi_callback: Callable[[bytes], None] | None = None,
        sent_cemi_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(xknx, connection_config)
        self._raw_cemi_callback = raw_cemi_callback
        self._sent_cemi_callback = sent_cemi_callback

    def cemi_received(self, raw_cemi: bytes) -> None:
        if self._raw_cemi_callback:
            self._raw_cemi_callback(raw_cemi)
        super().cemi_received(raw_cemi)

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        # Every frame the app sends passes here, whichever code path built it.
        if self._sent_cemi_callback:
            _notify_sent(self._sent_cemi_callback, cemi)
        await super().send_cemi(cemi)


class ObservableKNXIPInterfaceThreaded(KNXIPInterfaceThreaded):
    __slots__ = ("_raw_cemi_callback", "_sent_cemi_callback")

    def __init__(
        self,
        xknx: XKNX,
        connection_config: ConnectionConfig | None = None,
        raw_cemi_callback: Callable[[bytes], None] | None = None,
        sent_cemi_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(xknx, connection_config)
        self._raw_cemi_callback = raw_cemi_callback
        self._sent_cemi_callback = sent_cemi_callback

    def cemi_received(self, raw_cemi: bytes) -> None:
        if self._raw_cemi_callback:
            self._raw_cemi_callback(raw_cemi)
        super().cemi_received(raw_cemi)

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        # Every frame the app sends passes here, whichever code path built it.
        if self._sent_cemi_callback:
            _notify_sent(self._sent_cemi_callback, cemi)
        await super().send_cemi(cemi)

    async def read_max_apdu_length(self) -> int | None:
        """The maximum APDU length a tunnelling server reports, or ``None``."""
        if self._thread_loop is None:
            return None
        return await self._await_from_connection_thread(
            read_tunnel_max_apdu_length(self._interface)
        )


async def read_tunnel_max_apdu_length(interface: Interface | None) -> int | None:
    """Ask a KNXnet/IP tunnelling (v2) server for its maximum APDU length."""
    if not isinstance(interface, (UDPTunnel, TCPTunnel)):
        return None
    channel = interface.communication_channel
    if channel is None:
        return None
    answer: asyncio.Future[TunnellingFeatureResponse] = (
        asyncio.get_running_loop().create_future()
    )

    def received(frame: KNXIPFrame, _source: HPAI, _transport: KNXIPTransport) -> None:
        body = frame.body
        if (
            not isinstance(body, TunnellingFeatureResponse)
            or body.communication_channel_id != channel
            or body.feature_type != TunnellingFeatureType.MAX_APDU_LENGTH
        ):
            return
        if isinstance(interface, UDPTunnel):
            sequence = interface._sequence  # pyright: ignore[reportPrivateUsage]
            verdict = sequence.evaluate(body.sequence_counter)
            if verdict is SequenceVerdict.OUT_OF_ORDER:
                return
            interface._send_tunnelling_ack(  # pyright: ignore[reportPrivateUsage]
                channel, body.sequence_counter
            )
            if verdict is SequenceVerdict.REPEATED:
                return
        if not answer.done():
            answer.set_result(body)

    callback = interface.transport.register_callback(
        received, [KNXIPServiceType.TUNNELLING_FEATURE_RESPONSE]
    )
    try:
        async with interface._send_ready():  # pyright: ignore[reportPrivateUsage]
            request = TunnellingFeatureGet(
                communication_channel_id=channel,
                sequence_counter=interface.sequence_number,
                feature_type=TunnellingFeatureType.MAX_APDU_LENGTH,
            )
            try:
                await interface._send_tunnelling_request(  # pyright: ignore[reportPrivateUsage]
                    request  # type: ignore[arg-type]
                )
            finally:
                interface._increase_sequence_number()  # pyright: ignore[reportPrivateUsage]
        response = await asyncio.wait_for(answer, _FEATURE_TIMEOUT)
    except (TimeoutError, XKNXException):
        return None
    finally:
        interface.transport.unregister_callback(callback)
    if response.return_code != ReturnCode.E_SUCCESS or len(response.data) < 2:
        return None
    return int.from_bytes(response.data[:2], "big")
