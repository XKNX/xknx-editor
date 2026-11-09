"""Pending download status through the real MCP tools, without bus operations."""

from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from editor_gui.plugins.project.tests.test_device_resolution import _project_with_device

from .test_server import _FIXTURE, _build, _data


async def test_pending_filter_health_and_device_details(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_product", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        device = await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
        node_id = device["node_id"]
        project.record_download(node_id, "full")
        assert (
            await _data(client, "project_list_devices", download_pending_only=True)
        )["count"] == 0

        project.set_device_commissioning(
            node_id, parameters_loaded=False, communication_part_loaded=False
        )
        listed = await _data(client, "project_list_devices", download_pending_only=True)
        assert listed["count"] == 1
        expected = ["parameters", "group_communication"]
        assert listed["items"][0]["pending_download_parts"] == expected
        info = await _data(client, "project_get_device", node_id=node_id)
        assert info["pending_download_parts"] == expected
        info = await _data(
            client, "project_get_device", device_address=info["individual_address"]
        )
        assert info["pending_download_parts"] == expected
        findings = await _data(client, "project_health", category="download-pending")
        assert findings["count"] == 1
        assert findings["items"][0]["device_node_id"] == node_id
        assert findings["items"][0]["severity"] == "warning"
        assert "Parameters, Group Addresses" in findings["items"][0]["message"]
        assert (
            await _data(
                client,
                "project_list_devices",
                name_contains="does not exist",
                download_pending_only=True,
            )
        )["count"] == 0
        project.record_download(node_id, "par,grp")
        assert (await _data(client, "project_health", category="download-pending"))[
            "count"
        ] == 0
        assert (
            await _data(client, "project_list_devices", download_pending_only=True)
        )["count"] == 0


async def test_pending_devices_with_missing_product_data_are_readable(
    tmp_path: Path,
) -> None:
    import sqlite3

    path, node_id, _ = _project_with_device(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM product_data")
    mcp, project = _build(tmp_path)
    project.open(path)
    assert project.devices == [] and project.unloaded_devices
    async with Client(mcp) as client:
        all_devices = await _data(client, "project_list_devices")
        assert all_devices["count"] == 1
        listed = await _data(client, "project_list_devices", download_pending_only=True)
        assert listed["count"] == 1
        assert listed == all_devices
        info = await _data(client, "project_get_device", node_id=node_id)
        assert (
            info["pending_download_parts"]
            == listed["items"][0]["pending_download_parts"]
        )
        by_address = await _data(
            client, "project_get_device", device_address=info["individual_address"]
        )
        assert by_address == info
        findings = await _data(client, "project_health", category="download-pending")
        assert findings["items"][0]["device_node_id"] == node_id
        with pytest.raises(ToolError, match="no device"):
            await _data(client, "project_list_parameters", node_id=node_id)
    project.close()
