"""The IP settings of an IP device as the MCP tools read and change them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_details import _device
from .test_server import _FIXTURE, _build, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


def _ip_device(
    monkeypatch: pytest.MonkeyPatch, project: Any, ip_config: str = "Tool"
) -> None:
    """Make the fixture's product an IP device whose application leaves its IP settings to the
    tool (or sets them itself, ``ip_config`` "Custom")."""
    monkeypatch.setattr(project._catalog, "hardware_is_ip_enabled", lambda _ref: True)
    monkeypatch.setattr(project._catalog, "ip_config", lambda _ref: ip_config)


async def test_other_devices_have_none(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node = await _device(client)
        for tool, args in (
            ("project_get_ip_settings", {"node_id": node}),
            ("project_set_ip_settings", {"node_id": node, "assign": "Fixed"}),
        ):
            with pytest.raises(Exception, match="is no IP device"):
                await client.call_tool(tool, args)


async def test_static_address_and_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, project = _build(tmp_path)
    _ip_device(monkeypatch, project)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node = await _device(client)
        assert await _data(client, "project_get_ip_settings", node_id=node) == {
            "node_id": node,
            "tool_managed": True,
            "assign": "Auto",
            "ip_address": None,
            "subnet_mask": None,
            "default_gateway": None,
            "mac_address": None,
            "multicast_address": None,
        }
        fixed = await _data(
            client,
            "project_set_ip_settings",
            node_id=node,
            assign="Fixed",
            ip_address="192.168.1.10",
            subnet_mask="255.255.255.0",
            default_gateway="192.168.1",
        )
        assert fixed["assign"] == "Fixed"
        assert fixed["default_gateway"] == "192.168.0.1"
        # Back to automatic keeps the addresses.
        auto = await _data(
            client, "project_set_ip_settings", node_id=node, assign="Auto"
        )
        assert (auto["assign"], auto["ip_address"]) == ("Auto", "192.168.1.10")
        await _data(client, "project_undo")
        undone = await _data(client, "project_get_ip_settings", node_id=node)
        assert undone["assign"] == "Fixed"


async def test_unreadable_addresses_change_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, project = _build(tmp_path)
    _ip_device(monkeypatch, project, "Custom")
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node = await _device(client)
        with pytest.raises(Exception, match="An invalid IP address was specified"):
            await client.call_tool(
                "project_set_ip_settings", {"node_id": node, "ip_address": "1.2.3."}
            )
        settings = await _data(client, "project_get_ip_settings", node_id=node)
        assert settings["tool_managed"] is False
        assert settings["ip_address"] is None
