"""Reading the maximum APDU length a tunnelling server reports."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest
from xknx import XKNX
from xknx.exceptions import CommunicationError
from xknx.io.transport import TCPTransport
from xknx.io.tunnel import TCPTunnel, UDPTunnel
from xknx.knxip import (
    HPAI,
    KNXIPFrame,
    TunnellingFeatureGet,
    TunnellingFeatureResponse,
    TunnellingFeatureType,
)

from editor_gui.plugins.connection import interface
from editor_gui.plugins.connection.interface import (
    STANDARD_FRAME_APDU_LENGTH,
    interface_apdu_limit,
    read_device_management_max_apdu_length,
    read_interface_max_apdu_length,
    read_tunnel_max_apdu_length,
)


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


def test_reported_length_is_the_limit() -> None:
    assert interface_apdu_limit(248, tunnelling=True) == 248
    assert interface_apdu_limit(15, tunnelling=False) == 15


def test_tunnel_without_report_uses_standard_frames() -> None:
    # Tunnelling v1 (knxd) cannot report its maximum; a long frame can take its TP link down.
    assert (
        interface_apdu_limit(None, tunnelling=True) == STANDARD_FRAME_APDU_LENGTH == 15
    )


def test_routing_without_report_stays_unlimited() -> None:
    assert interface_apdu_limit(None, tunnelling=False) is None


class _DeviceManagement:
    """A device management connection to a server whose device object answers ``answer``
    (bytes), or fails with it (an exception)."""

    answer: bytes | Exception = b""
    opened: ClassVar[list[dict[str, Any]]] = []
    reads: ClassVar[list[tuple[int, int]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.opened.append(kwargs)

    async def __aenter__(self) -> _DeviceManagement:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def read_property(self, object_type: int, property_id: int) -> bytes:
        self.reads.append((int(object_type), int(property_id)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def device_management(monkeypatch: pytest.MonkeyPatch) -> type[_DeviceManagement]:
    _DeviceManagement.answer = b""
    _DeviceManagement.opened = []
    _DeviceManagement.reads = []
    monkeypatch.setattr(interface, "UDPDeviceManagementConnection", _DeviceManagement)
    monkeypatch.setattr(interface, "TCPDeviceManagementConnection", _DeviceManagement)
    return _DeviceManagement


def _udp_tunnel() -> UDPTunnel:
    return UDPTunnel(
        XKNX(),
        cemi_received_callback=lambda _raw: None,
        gateway_ip="192.0.2.10",
        gateway_port=3671,
        local_ip="192.0.2.20",
        route_back=True,
    )


def test_a_v1_server_answers_over_device_management(
    device_management: type[_DeviceManagement],
) -> None:
    # Object 0 (device), PID_MAX_APDU_LENGTH (56), a 2-octet unsigned value.
    device_management.answer = (254).to_bytes(2, "big")
    assert asyncio.run(read_device_management_max_apdu_length(_udp_tunnel())) == 254
    assert device_management.reads == [(0, 56)]
    assert device_management.opened == [
        {
            "gateway_ip": "192.0.2.10",
            "gateway_port": 3671,
            "local_ip": "192.0.2.20",
            "route_back": True,
        }
    ]
    # Over TCP, a connection of its own to the same server.
    device_management.opened.clear()
    tcp = _tunnel(_Transport(reply=False))
    assert asyncio.run(read_device_management_max_apdu_length(tcp)) == 254
    assert device_management.opened == [
        {"gateway_ip": "127.0.0.1", "gateway_port": 3671}
    ]


@pytest.mark.parametrize(
    "answer",
    [CommunicationError("no device management"), TimeoutError(), b"\x0f", b"\x00\x00"],
)
def test_no_device_management_answer_gives_none(
    device_management: type[_DeviceManagement], answer: bytes | Exception
) -> None:
    device_management.answer = answer
    assert asyncio.run(read_device_management_max_apdu_length(_udp_tunnel())) is None


def test_only_tunnels_are_asked_over_device_management(
    device_management: type[_DeviceManagement],
) -> None:
    assert asyncio.run(read_device_management_max_apdu_length(None)) is None
    assert device_management.opened == []


def test_the_feature_query_comes_first(
    monkeypatch: pytest.MonkeyPatch, device_management: type[_DeviceManagement]
) -> None:
    device_management.answer = (254).to_bytes(2, "big")

    async def feature(_interface: object) -> int | None:
        return 248

    monkeypatch.setattr(interface, "read_tunnel_max_apdu_length", feature)
    assert asyncio.run(read_interface_max_apdu_length(_udp_tunnel())) == 248
    assert device_management.opened == []

    async def no_feature(_interface: object) -> int | None:
        return None

    # A tunnelling v1 server has no feature query: its device object is read instead.
    monkeypatch.setattr(interface, "read_tunnel_max_apdu_length", no_feature)
    assert asyncio.run(read_interface_max_apdu_length(_udp_tunnel())) == 254
