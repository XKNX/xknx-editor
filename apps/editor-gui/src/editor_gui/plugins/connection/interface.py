from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from xknx.cemi.cemi_frame import CEMIFrame
from xknx.exceptions import XKNXException
from xknx.io.data_connection import SequenceVerdict
from xknx.io.device_management_connection import (
    TCPDeviceManagementConnection,
    UDPDeviceManagementConnection,
)
from xknx.io.interface import Interface
from xknx.io.knxip_interface import KNXIPInterface, KNXIPInterfaceThreaded
from xknx.io.transport.ip_transport import KNXIPTransport
from xknx.io.tunnel import SecureTunnel, TCPTunnel, UDPTunnel
from xknx.knxip import (
    HPAI,
    KNXIPFrame,
    KNXIPServiceType,
    TunnellingFeatureGet,
    TunnellingFeatureResponse,
    TunnellingFeatureType,
)
from xknx.profile.const import ResourceDevicePropertyId, ResourceObjectType
from xknx.telegram.apci import ReturnCode

from editor_gui.cemi_diagnostics import current_knxip_packet, handle_cemi_with_packet
from editor_gui.plugins.connection.diagnostic_transport import DiagnosticTCPTunnel
from editor_gui.plugins.connection.write_guard import BusWritesBlocked, is_bus_write

# Seconds to wait for a tunnelling feature response.
_FEATURE_TIMEOUT = 2.0
# Seconds a device management connection may take to answer the read of the maximum APDU
# length, connecting and disconnecting included.
_DEVICE_MANAGEMENT_TIMEOUT = 3.0
# APDU length every KNX medium and interface has to carry (a standard frame). A tunnelling
# server that does not report its maximum (e.g. knxd) is limited to it.
STANDARD_FRAME_APDU_LENGTH = 15
_knx_logger = logging.getLogger("xknx.log")

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.io.connection import ConnectionConfig


def _refuse_write(writes_blocked: Callable[[], bool] | None, cemi: CEMIFrame) -> None:
    """Raise :class:`BusWritesBlocked` for a write while Block Bus Writes is on."""
    if writes_blocked is not None and writes_blocked() and is_bus_write(cemi):
        raise BusWritesBlocked(cemi)


def _notify_sent(callback: Callable[[bytes], None], cemi: CEMIFrame) -> None:
    """Pass a sent frame to ``callback``; a failing observer never stops the send."""
    with contextlib.suppress(Exception):
        callback(cemi.to_knx())


class _DiagnosticKNXIPInterface(KNXIPInterface):
    async def _start_tunnelling_tcp(self, gateway_ip: str, gateway_port: int) -> None:
        tunnel_address = self.connection_config.individual_address
        _knx_logger.debug(
            "Starting tunnel to %s:%s over TCP%s",
            gateway_ip,
            gateway_port,
            f" requesting individual address {tunnel_address}"
            if tunnel_address
            else "",
        )
        self._interface = DiagnosticTCPTunnel(
            self.xknx,
            gateway_ip=gateway_ip,
            gateway_port=gateway_port,
            individual_address=tunnel_address,
            cemi_received_callback=self.cemi_received,
            auto_reconnect=self.connection_config.auto_reconnect,
            auto_reconnect_wait=self.connection_config.auto_reconnect_wait,
        )
        await self._interface.connect()


class ObservableKNXIPInterface(_DiagnosticKNXIPInterface):
    __slots__ = ("_raw_cemi_callback", "_sent_cemi_callback", "_writes_blocked")

    def __init__(
        self,
        xknx: XKNX,
        connection_config: ConnectionConfig | None = None,
        raw_cemi_callback: Callable[[bytes], None] | None = None,
        sent_cemi_callback: Callable[[bytes], None] | None = None,
        writes_blocked: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(xknx, connection_config)
        self._raw_cemi_callback = raw_cemi_callback
        self._sent_cemi_callback = sent_cemi_callback
        # Block Bus Writes (write_guard.py): asked for every frame, so a change applies at once.
        self._writes_blocked = writes_blocked

    def cemi_received(self, raw_cemi: bytes) -> None:
        if self._raw_cemi_callback:
            self._raw_cemi_callback(raw_cemi)
        super().cemi_received(raw_cemi)

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        # Every frame the app sends passes here, whichever code path built it.
        _refuse_write(self._writes_blocked, cemi)
        if self._sent_cemi_callback:
            _notify_sent(self._sent_cemi_callback, cemi)
        await super().send_cemi(cemi)


class ObservableKNXIPInterfaceThreaded(
    KNXIPInterfaceThreaded, _DiagnosticKNXIPInterface
):
    __slots__ = ("_raw_cemi_callback", "_sent_cemi_callback", "_writes_blocked")

    def __init__(
        self,
        xknx: XKNX,
        connection_config: ConnectionConfig | None = None,
        raw_cemi_callback: Callable[[bytes], None] | None = None,
        sent_cemi_callback: Callable[[bytes], None] | None = None,
        writes_blocked: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(xknx, connection_config)
        self._raw_cemi_callback = raw_cemi_callback
        self._sent_cemi_callback = sent_cemi_callback
        # Block Bus Writes (write_guard.py): asked for every frame, so a change applies at once.
        self._writes_blocked = writes_blocked

    def cemi_received(self, raw_cemi: bytes) -> None:
        if self._raw_cemi_callback:
            self._raw_cemi_callback(raw_cemi)
        self._main_loop.call_soon_threadsafe(
            handle_cemi_with_packet,
            super(KNXIPInterfaceThreaded, self).cemi_received,
            raw_cemi,
            current_knxip_packet(),
        )

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        # Every frame the app sends passes here, whichever code path built it.
        _refuse_write(self._writes_blocked, cemi)
        if self._sent_cemi_callback:
            _notify_sent(self._sent_cemi_callback, cemi)
        await super().send_cemi(cemi)

    async def read_max_apdu_length(self) -> int | None:
        """The maximum APDU length a tunnelling server reports, or ``None``."""
        if self._thread_loop is None:
            return None
        return await self._await_from_connection_thread(
            read_interface_max_apdu_length(self._interface)
        )


def interface_apdu_limit(reported: int | None, *, tunnelling: bool) -> int | None:
    """The maximum APDU length to send through the connected interface.

    ``reported`` is what the tunnelling server reported (see
    :func:`read_interface_max_apdu_length`), or ``None``. A tunnelling server that does not
    report it (such as knxd) may sit in front of a TP interface without long frame support:
    a longer frame is then lost, or (knxd with a TPUART) takes the link down. Such a
    connection stays at the standard frame length, the only one every interface carries.
    Routing is left unlimited (``None``).
    """
    if reported is not None:
        return reported
    return STANDARD_FRAME_APDU_LENGTH if tunnelling else None


async def read_interface_max_apdu_length(interface: Interface | None) -> int | None:
    """The maximum APDU length a tunnelling server reports, or ``None``: a tunnelling v2
    server answers the feature query; a tunnelling v1 server, which has none, may answer the
    read of its device object's ``PID_MAX_APDU_LENGTH`` over a device management connection."""
    length = await read_tunnel_max_apdu_length(interface)
    if length is None:
        length = await read_device_management_max_apdu_length(interface)
    return length


async def read_device_management_max_apdu_length(
    interface: Interface | None,
) -> int | None:
    """Read the maximum APDU length of a tunnelling server's device object (object 0,
    ``PID_MAX_APDU_LENGTH``) over a KNXnet/IP device management connection to it.

    ``None`` when the server has no device management connection, or does not answer. Secure
    tunnels are left out: they would need a secure session of their own, and their servers
    answer the feature query (tunnelling v2).
    """
    connection: UDPDeviceManagementConnection | TCPDeviceManagementConnection
    if isinstance(interface, UDPTunnel):
        connection = UDPDeviceManagementConnection(
            gateway_ip=interface.gateway_ip,
            gateway_port=interface.gateway_port,
            local_ip=interface.local_ip,
            route_back=interface.route_back,
        )
    elif isinstance(interface, TCPTunnel) and not isinstance(interface, SecureTunnel):
        connection = TCPDeviceManagementConnection(
            gateway_ip=interface.gateway_ip, gateway_port=interface.gateway_port
        )
    else:
        return None
    try:
        async with asyncio.timeout(_DEVICE_MANAGEMENT_TIMEOUT), connection:
            data = await connection.read_property(
                ResourceObjectType.OBJECT_DEVICE,
                ResourceDevicePropertyId.PID_MAX_APDU_LENGTH,
            )
    except (TimeoutError, XKNXException, OSError):
        return None
    if len(data) < 2:
        return None
    return int.from_bytes(data[:2], "big") or None


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
