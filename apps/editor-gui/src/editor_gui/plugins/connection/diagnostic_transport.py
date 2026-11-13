"""Preserve original TCP packet bytes for cEMI parse warnings."""

from __future__ import annotations

from collections.abc import Callable

from xknx.io.transport.tcp_transport import TCPTransport
from xknx.io.tunnel import TCPTunnel
from xknx.knxip import HPAI, KNXIPFrame

from editor_gui.cemi_diagnostics import KNXIPReceiveContext, knxip_receive_context

_MAX_PACKET_BYTES = 1024


class DiagnosticTCPTransport(TCPTransport):
    """Observe xknx's framing without changing how it parses or buffers data."""

    def __init__(
        self,
        remote_addr: tuple[str, int],
        connection_lost_cb: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(remote_addr, connection_lost_cb)
        self._receive_data: bytes | None = None
        self._receive_offset = 0
        self._chunk_length = 0
        self._buffered_length = 0

    def data_received_callback(self, raw: bytes) -> None:
        buffered = self._buffer
        self._receive_data = buffered + raw
        self._receive_offset = 0
        self._chunk_length = len(raw)
        self._buffered_length = len(buffered)
        try:
            super().data_received_callback(raw)
        finally:
            self._receive_data = None

    def _skip_frame(self, raw: bytes) -> bytes:
        rest = super()._skip_frame(raw)
        self._receive_offset += len(raw) - len(rest)
        return rest

    def handle_knxipframe(self, knxipframe: KNXIPFrame, source: HPAI) -> None:
        if self._receive_data is None:
            with knxip_receive_context(None):
                super().handle_knxipframe(knxipframe, source)
            return
        offset = self._receive_offset
        length = knxipframe.header.total_length
        self._receive_offset += length
        frame_length = min(length, len(self._receive_data) - offset)
        packet = KNXIPReceiveContext(
            raw=self._receive_data[offset : offset + min(length, _MAX_PACKET_BYTES)],
            frame_length=frame_length,
            peer=f"{source.ip_addr}:{source.port}",
            chunk_length=self._chunk_length,
            buffered_length=self._buffered_length,
            frame_offset=offset,
            trailing_length=len(self._receive_data) - self._receive_offset,
        )
        with knxip_receive_context(packet):
            super().handle_knxipframe(knxipframe, source)


class DiagnosticTCPTunnel(TCPTunnel):
    """Use receive diagnostics for plain TCP tunnels."""

    def _init_transport(self) -> None:
        self.transport = DiagnosticTCPTransport(
            remote_addr=(self.gateway_ip, self.gateway_port),
            connection_lost_cb=self._connection_lost,
        )
