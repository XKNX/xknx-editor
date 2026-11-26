"""The MCP tools of the configured connections (#351): listed, added, changed, deleted, imported
and exported, one made the open project's connection (its backbone's Bus Connection)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastmcp import Client

from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionState
from editor_gui.plugins.mcp.context import McpContext
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


def _ctx(tmp_path: Path) -> tuple[McpContext, list[int]]:
    """A context with a connection plugin keeping its configured connections in memory; the
    list counts the saves."""
    ctx = _build_ctx(tmp_path)
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._api = ctx.api
    plugin._configured = ()
    plugin._gateways = []
    plugin._state = ConnectionState.DISCONNECTED
    plugin._log = SimpleNamespace(info=lambda *_a, **_k: None)
    saves: list[int] = []
    plugin._save_current_settings = lambda: saves.append(len(plugin._configured))
    ctx.connection_plugin = plugin
    return ctx, saves


async def test_add_change_and_delete(tmp_path: Path) -> None:
    ctx, saves = _ctx(tmp_path)
    async with Client(build_server(ctx)) as client:
        listed = await _data(client, "connection_list_configured")
        assert listed == {"connections": [], "project_connection": None}
        listed = await _data(client, "connection_add_configured", host="192.168.0.221")
        assert listed["connections"] == [
            {
                "index": 0,
                "name": "IP Tunneling",
                "host": "192.168.0.221",
                "port": 3671,
                "protocol": "Auto",
                "use_nat": False,
                "parameter": 'Type=IpTunneling;HostAddress=192.168.0.221;Name="IP Tunneling"',
                "project_connection": False,
            }
        ]
        listed = await _data(
            client,
            "connection_add_configured",
            host="192.168.97.24",
            name="Brücktal",
            protocol="Tcp",
            use_nat=True,
        )
        second = listed["connections"][1]
        assert (second["name"], second["protocol"], second["use_nat"]) == (
            "Brücktal",
            "Tcp",
            False,
        )
        assert saves == [1, 2]
        with pytest.raises(Exception, match="neither an IP address"):
            await client.call_tool("connection_add_configured", {"host": "0.0.0.0"})
        listed = await _data(
            client,
            "connection_update_configured",
            name="Brücktal",
            port=3700,
            protocol="Udp",
            use_nat=True,
        )
        assert listed["connections"][1]["parameter"] == (
            "Type=IpTunneling;HostAddress=192.168.97.24;IpPort=3700;ProtocolType=Udp;"
            "UseNat=True;Name=Brücktal"
        )
        listed = await _data(
            client, "connection_update_configured", index=0, new_name="Brücktal"
        )
        with pytest.raises(Exception, match="several connections"):
            await client.call_tool("connection_update_configured", {"name": "Brücktal"})
        with pytest.raises(Exception, match="exactly one of index or name"):
            await client.call_tool("connection_delete_configured", {})
        listed = await _data(client, "connection_delete_configured", index=0)
        assert [c["port"] for c in listed["connections"]] == [3700]


async def test_the_project_connection(tmp_path: Path) -> None:
    ctx, _saves = _ctx(tmp_path)
    async with Client(build_server(ctx)) as client:
        await _data(
            client, "connection_add_configured", host="192.168.0.221", name="Home"
        )
        with pytest.raises(Exception, match="no project is open"):
            await client.call_tool(
                "connection_set_project_connection", {"name": "Home"}
            )
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        backbone = await _data(client, "project_get_backbone")
        assert backbone["configured_connections"] == [
            {
                "parameter": "Type=IpTunneling;HostAddress=192.168.0.221;Name=Home",
                "text": "Home (192.168.0.221)",
            }
        ]
        listed = await _data(client, "connection_set_project_connection", name="Home")
        assert listed["connections"][0]["project_connection"] is True
        assert listed["project_connection"] == (
            "Type=IpTunneling;HostAddress=192.168.0.221;Name=Home"
        )
        backbone = await _data(client, "project_get_backbone")
        assert backbone["bus_connection"]["parameter"] == listed["project_connection"]
        # A change to it is the project's Bus Connection's too.
        listed = await _data(
            client, "connection_update_configured", name="Home", host="192.168.0.30"
        )
        assert listed["project_connection"] == (
            "Type=IpTunneling;HostAddress=192.168.0.30;Name=Home"
        )
        listed = await _data(
            client, "connection_set_project_connection", automatic=True
        )
        assert listed["project_connection"] is None
        # The backbone's Bus Connection takes a configured connection too.
        backbone = await _data(
            client,
            "project_set_backbone",
            bus_connection="Type=IpTunneling;HostAddress=192.168.0.30;Name=Home",
        )
        assert backbone["bus_connection"]["text"] == "Home (192.168.0.30:3671)"


async def test_export_and_import(tmp_path: Path) -> None:
    ctx, _saves = _ctx(tmp_path)
    async with Client(build_server(ctx)) as client:
        await _data(
            client, "connection_add_configured", host="192.168.0.221", name="Home"
        )
        exported = await _data(client, "connection_export_configured")
        assert "BusConnection ConnectionString=" in exported["xml"]
        await _data(client, "connection_delete_configured", name="Home")
        listed = await _data(
            client, "connection_import_configured", xml=exported["xml"]
        )
        assert listed["added"] == 1
        assert [c["name"] for c in listed["connections"]] == ["Home"]
        # The same again adds nothing.
        listed = await _data(
            client, "connection_import_configured", xml=exported["xml"]
        )
        assert listed["added"] == 0
        with pytest.raises(Exception, match="no BusConnections XML"):
            await client.call_tool("connection_import_configured", {"xml": "nope"})
