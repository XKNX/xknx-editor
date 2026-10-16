"""The editor's own connection with KNX IP Secure and KNX Data Secure."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from xknx.io.connection import ConnectionType
from xknx.knxip.dib import TunnelingSlotStatus
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.plugin import ConnectionPlugin
from editor_gui.plugins.connection.service import ConnectionService, SecureTunnel
from editor_gui.plugins.keyring.service import KeyringIpSecrets

_TUNNELS = [
    SecureTunnel(2, "1.1.250", "one", "auth"),
    SecureTunnel(3, "1.1.251", "two", "auth"),
]


def test_a_free_tunnel_of_the_project_first() -> None:
    service = ConnectionService()
    service.project_tunnels = lambda host: _TUNNELS if host == "1.1.10" else []
    assert service.secure_tunnel("1.1.10") == _TUNNELS[0]
    assert service.secure_tunnel("1.1.10", ["1.1.251"]) == _TUNNELS[1]
    assert service.secure_tunnel("1.1.11") is None


def test_the_keyring_fills_in() -> None:
    service = ConnectionService()
    service.project_tunnels = lambda host: []
    service.keyring = cast(
        Any,
        SimpleNamespace(
            ip_secrets=lambda host: KeyringIpSecrets(
                "auth", "mgmt", [(4, "1.1.252", "ring")]
            )
        ),
    )
    assert service.secure_tunnel("1.1.10") == SecureTunnel(4, "1.1.252", "ring", "auth")


def test_data_secure_from_the_project() -> None:
    service = ConnectionService()
    service.project_data_secure = lambda: ({0x0801: bytes(16)}, {0x1105: 41})
    data_secure = service.data_secure()
    assert data_secure is not None
    assert service.secure_group_keys() == {0x0801: bytes(16)}
    service.project_data_secure = lambda: None
    assert service.data_secure() is None
    assert service.secure_group_keys() == {}


def _plugin(gateway: Any, *, routing: bool = False, **connection: Any) -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._connection_type = (
        ConnectionType.ROUTING if routing else ConnectionType.TUNNELING
    )
    plugin._selected_gateway = gateway
    plugin._controller_ip = "192.0.2.1"
    plugin._multicast_group = "224.0.23.12"
    plugin._api = SimpleNamespace(connection=SimpleNamespace(**connection))
    return plugin


def _gateway(**fields: Any) -> Any:
    values: dict[str, Any] = {
        "individual_address": IndividualAddress("1.1.10"),
        "tunnelling_requires_secure": False,
        "routing_requires_secure": False,
        "tunnelling_slots": {},
    }
    return SimpleNamespace(**(values | fields))


def test_a_secure_tunnel_when_the_interface_requires_it() -> None:
    gateway = _gateway(
        tunnelling_requires_secure=True,
        tunnelling_slots={
            IndividualAddress("1.1.250"): TunnelingSlotStatus(True, True, False),
            IndividualAddress("1.1.251"): TunnelingSlotStatus(True, True, True),
        },
    )
    service = ConnectionService()
    service.project_tunnels = lambda host: _TUNNELS
    plugin = _plugin(gateway, secure_tunnel=service.secure_tunnel)
    config = asyncio.run(ConnectionPlugin._connection_config(plugin))  # pyright: ignore[reportPrivateUsage]
    assert config.connection_type is ConnectionType.TUNNELING_TCP_SECURE
    assert config.individual_address == IndividualAddress("1.1.251")
    assert config.secure_config is not None
    assert (config.secure_config.user_id, config.secure_config.user_password) == (
        3,
        "two",
    )


def test_plain_tunnel_and_secure_routing() -> None:
    plugin = _plugin(_gateway(), secure_tunnel=lambda *a: None)
    config = asyncio.run(ConnectionPlugin._connection_config(plugin))  # pyright: ignore[reportPrivateUsage]
    assert config.connection_type is ConnectionType.TUNNELING
    plugin = _plugin(
        _gateway(routing_requires_secure=True),
        routing=True,
        secure_backbone=lambda: (bytes(range(16)), 1500),
    )
    config = asyncio.run(ConnectionPlugin._connection_config(plugin))  # pyright: ignore[reportPrivateUsage]
    assert config.connection_type is ConnectionType.ROUTING_SECURE
    assert config.secure_config is not None
    assert config.secure_config.backbone_key == bytes(range(16))
    assert config.secure_config.latency_ms == 1500
