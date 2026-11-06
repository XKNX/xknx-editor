"""The connected gateway as the Gateway menu shows it, and switching the open connection's type,
as its radio buttons do."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import Client
from xknx.io.connection import ConnectionType

from editor_gui.plugins.connection.plugin import ConnectionState
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


class _Connected:
    """A connection to a gateway that offers TCP and UDP tunnelling."""

    def __init__(self) -> None:
        self.state = ConnectionState.CONNECTED
        self.connected = True
        self.controller_ip = "192.168.1.10"
        self.multicast_group = "224.0.23.12"
        self.is_routing = False
        self.connection_type = "tunneling_tcp"
        self.gateways: tuple[Any, ...] = ()
        self.gateway_info = SimpleNamespace(
            name="IP Interface",
            individual_address="1.1.250",
            core_version=2,
            supports_tunnelling=True,
            supports_tunnelling_tcp=True,
            supports_routing=False,
            supports_secure=False,
        )
        self.switched: list[ConnectionType] = []

    def offered_transports(self) -> list[tuple[ConnectionType, str]]:
        return [
            (ConnectionType.TUNNELING_TCP, "TCP"),
            (ConnectionType.TUNNELING, "UDP"),
        ]

    def switch_connection_type(self, ctype: ConnectionType) -> None:
        self.switched.append(ctype)
        self.connection_type = "tunneling"


def _server(tmp_path: Path, conn: Any) -> Any:
    ctx = _build_ctx(tmp_path)
    ctx.connection_plugin = conn
    return build_server(ctx)


async def test_the_status_names_the_gateway_and_its_transports(tmp_path: Path) -> None:
    async with Client(_server(tmp_path, _Connected())) as client:
        status = await _data(client, "connection_status")
    assert status["gateway"] == {
        "name": "IP Interface",
        "individual_address": "1.1.250",
        "core_version": 2,
        "services": ["Tunneling", "TCP Tunneling"],
        "transports": ["tunneling_tcp", "tunneling"],
    }


async def test_the_connection_switches_to_an_offered_type(tmp_path: Path) -> None:
    conn = _Connected()
    async with Client(_server(tmp_path, conn)) as client:
        result = await _data(
            client, "connection_switch_type", connection_type="tunneling"
        )
        # The type in use already: nothing to do.
        again = await _data(
            client, "connection_switch_type", connection_type="tunneling"
        )
    assert conn.switched == [ConnectionType.TUNNELING]
    assert result["connected"] and again["connected"]


async def test_a_type_the_gateway_lacks_is_refused(tmp_path: Path) -> None:
    conn = _Connected()
    async with Client(_server(tmp_path, conn)) as client:
        with pytest.raises(Exception, match="offers only: tunneling_tcp, tunneling"):
            await client.call_tool(
                "connection_switch_type", {"connection_type": "routing"}
            )
        with pytest.raises(Exception, match="unknown connection_type"):
            await client.call_tool("connection_switch_type", {"connection_type": "usb"})
        conn.connected = False
        with pytest.raises(Exception, match="not connected"):
            await client.call_tool(
                "connection_switch_type", {"connection_type": "tunneling"}
            )
    assert conn.switched == []
