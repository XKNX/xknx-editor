"""Reading the maximum APDU length a tunnelling server reports."""

from __future__ import annotations

import asyncio

from xknx import XKNX
from xknx.io.transport import TCPTransport
from xknx.io.tunnel import TCPTunnel
from xknx.knxip import (
    HPAI,
    KNXIPFrame,
    TunnellingFeatureGet,
    TunnellingFeatureResponse,
    TunnellingFeatureType,
)

from editor_gui.plugins.connection.interface import read_tunnel_max_apdu_length


class _Transport(TCPTransport):
    def __init__(self, reply: bool) -> None:
        super().__init__(remote_addr=("127.0.0.1", 3671))
        self.reply = reply
        self.sent: list[KNXIPFrame] = []

    def send(self, knxipframe: KNXIPFrame, addr: tuple[str, int] | None = None) -> None:
        self.sent.append(knxipframe)
        if not self.reply:
            return
        response = TunnellingFeatureResponse(
            communication_channel_id=7,
            feature_type=TunnellingFeatureType.MAX_APDU_LENGTH,
            data=(248).to_bytes(2, "big"),
        )
        asyncio.get_running_loop().call_soon(
            self.handle_knxipframe, KNXIPFrame.init_from_body(response), HPAI()
        )


def _tunnel(transport: _Transport) -> TCPTunnel:
    tunnel = TCPTunnel(
        XKNX(),
        cemi_received_callback=lambda _raw: None,
        gateway_ip="127.0.0.1",
        gateway_port=3671,
    )
    tunnel.transport = transport
    tunnel.communication_channel = 7
    return tunnel


def test_reads_the_reported_length() -> None:
    async def run() -> tuple[int | None, list[KNXIPFrame]]:
        transport = _Transport(reply=True)
        return await read_tunnel_max_apdu_length(_tunnel(transport)), transport.sent

    length, sent = asyncio.run(run())
    assert length == 248
    assert isinstance(sent[0].body, TunnellingFeatureGet)
    assert sent[0].body.feature_type == TunnellingFeatureType.MAX_APDU_LENGTH


def test_no_answer_gives_none() -> None:
    async def run() -> int | None:
        return await read_tunnel_max_apdu_length(_tunnel(_Transport(reply=False)))

    assert asyncio.run(run()) is None
    assert asyncio.run(read_tunnel_max_apdu_length(None)) is None
