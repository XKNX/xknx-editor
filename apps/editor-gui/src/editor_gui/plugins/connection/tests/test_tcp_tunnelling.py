"""Plain TCP tunnelling (KNXnet/IP Tunnelling v2): the transport is chosen per gateway, preferring
TCP when the interface offers it, and flows through to the xknx ConnectionConfig and the recording.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknx.io.connection import ConnectionType

from editor_gui.plugins.connection.plugin import (
    ConnectionPlugin,
    connection_type_name,
    default_transport,
    parse_connection_type,
)


def _gateway(*, tunnelling: bool, tcp: bool, routing: bool) -> Any:
    return SimpleNamespace(
        ip_addr="192.168.1.10",
        port=3671,
        supports_tunnelling=tunnelling,
        supports_tunnelling_tcp=tcp,
        supports_routing=routing,
        multicast_address="224.0.23.12",
    )


def test_default_transport_prefers_tcp_then_udp_then_routing() -> None:
    v2 = _gateway(tunnelling=True, tcp=True, routing=True)
    assert default_transport(v2) == ConnectionType.TUNNELING_TCP
    v1 = _gateway(tunnelling=True, tcp=False, routing=True)
    assert default_transport(v1) == ConnectionType.TUNNELING
    router = _gateway(tunnelling=False, tcp=False, routing=True)
    assert default_transport(router) == ConnectionType.ROUTING


def test_connection_type_name_and_parse_round_trip() -> None:
    for name in ("tunneling", "tunneling_tcp", "routing"):
        assert (
            connection_type_name(parse_connection_type(name) or ConnectionType.ROUTING)
            == name
        )
    assert parse_connection_type("nonsense") is None
    assert parse_connection_type(" Tunneling_TCP ") == ConnectionType.TUNNELING_TCP
    # the secure variants report their plain transport
    assert connection_type_name(ConnectionType.TUNNELING_TCP_SECURE) == "tunneling_tcp"
    assert connection_type_name(ConnectionType.ROUTING_SECURE) == "routing"


def _plugin(connection_type: ConnectionType) -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._connection_type = connection_type
    plugin._controller_ip = "192.168.1.10"
    plugin._multicast_group = "224.0.23.12"
    plugin._gateway_info = None
    # Only the routing / secure branches read the connection service; the plain tunnel path
    # (gateway=None) never reaches it.
    plugin._api = SimpleNamespace(connection=SimpleNamespace())
    return plugin


def test_connection_config_opens_tcp_when_selected() -> None:
    config = _plugin(ConnectionType.TUNNELING_TCP)._connection_config(None)
    assert config.connection_type == ConnectionType.TUNNELING_TCP
    assert config.gateway_ip == "192.168.1.10"


def test_connection_config_stays_udp_otherwise() -> None:
    config = _plugin(ConnectionType.TUNNELING)._connection_config(None)
    assert config.connection_type == ConnectionType.TUNNELING


def test_link_info_records_tcp_protocol() -> None:
    assert _plugin(ConnectionType.TUNNELING_TCP)._link_info().protocol == "Tcp"
    assert _plugin(ConnectionType.TUNNELING)._link_info().protocol == "Udp"


def test_manual_tcp_support_follows_a_discovered_gateway() -> None:
    plugin = _plugin(ConnectionType.TUNNELING)
    plugin._manual_probe_ip = ""
    plugin._manual_tcp = None
    # The entered IP matches a discovered v1 interface (no TCP): the probe result is irrelevant.
    plugin._gateways = [_gateway(tunnelling=True, tcp=False, routing=False)]
    plugin._controller_ip = "192.168.1.10"
    assert plugin._manual_tcp_support() is False
    plugin._gateways = [_gateway(tunnelling=True, tcp=True, routing=False)]
    assert plugin._manual_tcp_support() is True
    # An IP no discovered gateway and no finished probe covers is unknown.
    plugin._gateways = []
    assert plugin._manual_tcp_support() is None
    # A finished probe for exactly this endpoint is used when nothing was discovered.
    plugin._manual_probe_ip = "192.168.1.10"
    plugin._manual_tcp = False
    assert plugin._manual_tcp_support() is False


def _switchable(state: Any, connection_type: ConnectionType) -> tuple[Any, list[Any]]:
    plugin = _plugin(connection_type)
    ran: list[Any] = []
    plugin._state = state
    plugin._session = 0
    plugin._run_async = lambda coro: (ran.append(coro), coro.close())[0]
    plugin._busmonitor_blocks = lambda _action: False
    plugin._save_current_settings = lambda: None
    return plugin, ran


def test_switch_connection_type_reconnects_only_on_a_real_change() -> None:
    from editor_gui.plugins.connection.plugin import ConnectionState

    plugin, ran = _switchable(ConnectionState.CONNECTED, ConnectionType.TUNNELING)
    plugin.switch_connection_type(ConnectionType.TUNNELING_TCP)
    assert plugin._connection_type == ConnectionType.TUNNELING_TCP
    assert plugin._state == ConnectionState.CONNECTING and len(ran) == 1

    # No-op when already on that type, or when not connected.
    plugin, ran = _switchable(ConnectionState.CONNECTED, ConnectionType.TUNNELING_TCP)
    plugin.switch_connection_type(ConnectionType.TUNNELING_TCP)
    assert not ran
    plugin, ran = _switchable(ConnectionState.DISCONNECTED, ConnectionType.TUNNELING)
    plugin.switch_connection_type(ConnectionType.TUNNELING_TCP)
    assert not ran
