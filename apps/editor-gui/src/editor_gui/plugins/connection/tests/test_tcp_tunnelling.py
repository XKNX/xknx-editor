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
