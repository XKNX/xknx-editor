"""Original KNX/IP packets stay associated with cEMI errors over TCP."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from xknx import XKNX
from xknx.cemi.cemi_handler import CEMIHandler
from xknx.io.connection import ConnectionConfig, SecureConfig
from xknx.io.gateway_scanner import GatewayDescriptor, GatewayScanner
from xknx.io.tunnel import SecureTunnel, TCPTunnel, UDPTunnel
from xknx.knxip import HPAI, KNXIPFrame, TunnellingRequest

from editor_gui.cemi_diagnostics import (
    DiagnosticCEMIHandler,
    cemi_error_details,
    current_knxip_packet,
)
from editor_gui.log_file import session_log, set_session_log
from editor_gui.plugins.connection.diagnostic_transport import DiagnosticTCPTransport
from editor_gui.plugins.connection.interface import (
    ObservableKNXIPInterface,
    ObservableKNXIPInterfaceThreaded,
    read_device_management_max_apdu_length,
)
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.logger.ui import LogPanel

_MALFORMED = bytes.fromhex("290005006a21bce010e68045276000")
_VALID = bytes.fromhex("2900bce0101f0801010081")


def _packet(cemi: bytes, sequence: int = 9) -> bytes:
    body = bytes((4, 7, sequence, 0xA5)) + cemi
    return b"\x06\x10\x04\x20" + (6 + len(body)).to_bytes(2, "big") + body


async def _interface(
    *, threaded: bool = False, target: str = "192.0.2.1"
) -> tuple[XKNX, ObservableKNXIPInterface | ObservableKNXIPInterfaceThreaded]:
    xknx = XKNX()
    xknx.cemi_handler = DiagnosticCEMIHandler(xknx, mode="tunneling_tcp", target=target)
    interface = (
        ObservableKNXIPInterfaceThreaded(xknx)
        if threaded
        else ObservableKNXIPInterface(xknx)
    )
    with patch.object(TCPTunnel, "connect", new_callable=AsyncMock):
        await interface._start_tunnelling_tcp(target, 3671)
    assert isinstance(interface._interface, TCPTunnel)
    interface._interface.communication_channel = 7
    return xknx, interface


def _transport(
    interface: ObservableKNXIPInterface | ObservableKNXIPInterfaceThreaded,
) -> DiagnosticTCPTransport:
    assert isinstance(interface._interface, TCPTunnel)
    transport = interface._interface.transport
    assert isinstance(transport, DiagnosticTCPTransport)
    return transport


async def test_original_packet_reaches_warning_copy_and_file() -> None:
    disk = io.StringIO()
    previous = session_log()
    set_session_log(disk)
    try:
        log = LogService()
        xknx, interface = await _interface()
        wire = _packet(_MALFORMED)
        _transport(interface).data_received_callback(wire)
    finally:
        set_session_log(previous)
    (record,) = log.get_records()
    assert "APDU LEN should be 16 but is 5" in record.event
    assert (
        record.payload.items()
        >= {
            "knxip_raw": wire.hex(),
            "knxip_frame_length": "25",
            "knxip_length_declared": "25",
            "knxip_raw_truncated": "False",
            "knxip_peer": "192.0.2.1:3671",
            "knxip_header_length": "6",
            "knxip_protocol_version": "0x10",
            "knxip_service": "0x0420",
            "tunnel_header_length": "4",
            "tunnel_channel": "7",
            "tunnel_sequence": "9",
            "tcp_chunk_length": "25",
            "tcp_buffered_length": "0",
            "tcp_frame_offset": "0",
            "tcp_bytes_after_frame": "0",
        }.items()
    )
    assert xknx.connection_manager.cemi_count_incoming_error == 1
    assert xknx.telegrams.empty()
    panel = LogPanel(log.get_records, log.records_version, log.clear)
    assert wire.hex() in panel._record_as_text(record)
    assert wire.hex() in disk.getvalue()
    assert current_knxip_packet() is None
    assert cemi_error_details() == {}


async def test_all_tcp_split_positions_preserve_packets_and_delivery() -> None:
    log = LogService()
    xknx, interface = await _interface()
    transport = _transport(interface)
    good, bad = _packet(_VALID), _packet(_MALFORMED)
    stream = good + bad + good
    for split in range(1, len(stream)):
        log.clear()
        transport.data_received_callback(stream[:split])
        transport.data_received_callback(stream[split:])
        (record,) = log.get_records()
        assert record.payload["knxip_raw"] == bad.hex(), split
        assert record.payload["knxip_frame_length"] == str(len(bad))
        assert record.payload["tcp_buffered_length"] == str(
            split
            if split < len(good)
            else split - len(good)
            if split < len(good) + len(bad)
            else 0
        )
    assert xknx.telegrams.qsize() == 2 * (len(stream) - 1)


async def test_coalesced_frames_keep_original_offsets_after_skipped_frame() -> None:
    log = LogService()
    _, interface = await _interface()
    unknown = bytes.fromhex("0610ffff0008aabb")
    first, second = _packet(_MALFORMED, 1), _packet(_MALFORMED, 2)
    _transport(interface).data_received_callback(unknown + first + second)
    records = log.get_records()
    assert len(records) == 2
    for record, packet in zip(records, (first, second), strict=True):
        assert record.payload["knxip_raw"] == packet.hex()
    assert records[0].payload["tcp_frame_offset"] == "8"
    assert records[0].payload["tcp_bytes_after_frame"] == str(len(second))
    assert records[1].payload["tcp_frame_offset"] == str(8 + len(first))
    assert records[1].payload["tcp_bytes_after_frame"] == "0"


async def test_threaded_delivery_keeps_each_connections_packet() -> None:
    log = LogService()
    _, first = await _interface(threaded=True, target="192.0.2.1")
    _, second = await _interface(threaded=True, target="192.0.2.2")
    frames = [_packet(_MALFORMED, n) for n in (1, 2, 3)]

    def receive() -> None:
        _transport(first).data_received_callback(frames[0] + frames[1])
        _transport(second).data_received_callback(frames[2])
        assert current_knxip_packet() is None

    await asyncio.to_thread(receive)
    await asyncio.sleep(0)
    records = log.get_records()
    assert [r.payload["knxip_raw"] for r in records] == [f.hex() for f in frames]
    assert [r.payload["knxip_peer"] for r in records] == [
        "192.0.2.1:3671",
        "192.0.2.1:3671",
        "192.0.2.2:3671",
    ]
    logging.getLogger("xknx.cemi").warning("Unrelated warning")
    assert log.get_records()[-1].payload == {}


async def test_stop_discards_partial_frame_and_diagnostics() -> None:
    log = LogService()
    _, interface = await _interface()
    transport = _transport(interface)
    transport.data_received_callback(_packet(_MALFORMED)[:12])
    assert not log.get_records()
    transport.stop()
    fresh = _packet(_MALFORMED, 2)
    transport.data_received_callback(fresh)
    (record,) = log.get_records()
    assert record.payload["knxip_raw"] == fresh.hex()
    assert record.payload["tcp_buffered_length"] == "0"


async def test_callback_exceptions_clear_packet_context() -> None:
    transport = DiagnosticTCPTransport(("192.0.2.1", 3671))

    def fail(*_args: object) -> None:
        assert current_knxip_packet() is not None
        raise RuntimeError("observer failed")

    transport.register_callback(fail)
    with pytest.raises(RuntimeError, match="observer failed"):
        transport.data_received_callback(_packet(_MALFORMED))
    assert current_knxip_packet() is None
    assert transport._receive_data is None


async def test_scheduled_logs_do_not_inherit_packet_context() -> None:
    log = LogService()
    _, interface = await _interface(threaded=True)
    loop = asyncio.get_running_loop()

    def later(_handler: CEMIHandler, _raw: bytes) -> None:
        assert cemi_error_details()["knxip_raw"] == _packet(_MALFORMED).hex()
        loop.call_soon(logging.getLogger("xknx.cemi").warning, "Later warning")

    with patch.object(CEMIHandler, "handle_raw_cemi", later):
        await asyncio.to_thread(
            _transport(interface).data_received_callback, _packet(_MALFORMED)
        )
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    (record,) = log.get_records()
    assert record.payload == {}


async def test_large_invalid_packet_is_explicitly_capped() -> None:
    log = LogService()
    _, interface = await _interface()
    wire = _packet(_MALFORMED + bytes(2000))
    _transport(interface).data_received_callback(wire)
    (record,) = log.get_records()
    assert record.payload["knxip_raw"] == wire[:1024].hex()
    assert record.payload["knxip_raw_truncated"] == "True"
    assert record.payload["knxip_frame_length"] == str(len(wire))
    assert record.payload["knxip_length_declared"] == str(len(wire))


async def test_non_socket_cemi_has_no_stale_packet() -> None:
    log = LogService()
    _, interface = await _interface()
    transport = _transport(interface)
    transport.data_received_callback(_packet(_MALFORMED))
    frame = KNXIPFrame.init_from_body(
        TunnellingRequest(communication_channel_id=7, raw_cemi=_MALFORMED)
    )
    transport.handle_knxipframe(frame, HPAI())
    assert "knxip_raw" in log.get_records()[0].payload
    assert "knxip_raw" not in log.get_records()[1].payload


@pytest.mark.parametrize("threaded", [False, True])
async def test_automatic_tcp_selection_uses_diagnostics(threaded: bool) -> None:
    gateway = GatewayDescriptor(
        ip_addr="192.0.2.1", port=3671, supports_tunnelling_tcp=True
    )

    async def scan(_scanner: GatewayScanner) -> AsyncIterator[GatewayDescriptor]:
        yield gateway

    xknx = XKNX()
    interface = (
        ObservableKNXIPInterfaceThreaded(xknx)
        if threaded
        else ObservableKNXIPInterface(xknx)
    )
    with (
        patch.object(GatewayScanner, "async_scan", scan),
        patch.object(TCPTunnel, "connect", new_callable=AsyncMock),
    ):
        await interface._start_automatic(local_ip=None, keyring=None)
    assert isinstance(_transport(interface), DiagnosticTCPTransport)


@pytest.mark.parametrize("secure", [False, True])
async def test_other_tunnels_keep_transport_and_threaded_delivery(secure: bool) -> None:
    log = LogService()
    xknx = XKNX()
    xknx.cemi_handler = DiagnosticCEMIHandler(xknx, mode="test", target="192.0.2.1")
    interface = ObservableKNXIPInterfaceThreaded(
        xknx,
        ConnectionConfig(secure_config=SecureConfig(user_id=2, user_password="test")),
    )
    if secure:
        with patch.object(SecureTunnel, "connect", new_callable=AsyncMock):
            await interface._start_secure_tunnelling_tcp("192.0.2.1", 3671)
        assert isinstance(interface._interface, SecureTunnel)
    else:
        with patch.object(UDPTunnel, "connect", new_callable=AsyncMock):
            await interface._start_tunnelling_udp("192.0.2.1", 3671, "192.0.2.2")
        assert isinstance(interface._interface, UDPTunnel)
    assert not isinstance(interface._interface.transport, DiagnosticTCPTransport)
    await asyncio.to_thread(interface._interface.cemi_received_callback, _MALFORMED)
    await asyncio.sleep(0)
    (record,) = log.get_records()
    assert record.payload["cemi_raw"] == _MALFORMED.hex()
    assert "knxip_raw" not in record.payload


async def test_diagnostic_tunnel_still_supports_device_management() -> None:
    _, interface = await _interface()
    management = AsyncMock()
    management.__aenter__.return_value = management
    management.read_property.return_value = b"\x00\xf8"
    factory = Mock(return_value=management)
    with patch(
        "editor_gui.plugins.connection.interface.TCPDeviceManagementConnection", factory
    ):
        length = await read_device_management_max_apdu_length(interface._interface)
    assert length == 248
    factory.assert_called_once_with(gateway_ip="192.0.2.1", gateway_port=3671)
    management.read_property.assert_awaited_once_with(0, 56)
