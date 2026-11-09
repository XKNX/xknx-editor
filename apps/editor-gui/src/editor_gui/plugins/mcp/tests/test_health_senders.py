"""Health uses the same effective sender flags through MCP and the GUI service."""

from dataclasses import asdict
from pathlib import Path

from fastmcp import Client

from editor_gui.plugins.health.service import HealthService

from .test_server import _FIXTURE, _build, _data


async def test_health_follows_flag_edits_and_undo(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    try:
        async with Client(mcp) as client:
            await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
            await _data(client, "catalog_import_product", path=str(_FIXTURE))
            product = (await _data(client, "catalog_list_products"))["items"][0]
            device = await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
            node_id = device["node_id"]
            objects = (
                await _data(client, "project_list_com_objects", node_id=node_id)
            )["items"]
            transmitter = next(co for co in objects if co["flags"]["transmit"])
            receiver = next(co for co in objects if not co["flags"]["transmit"])
            await _data(
                client,
                "project_create_group_address",
                address="1/1/1",
                name="Light",
                dpt="1.001",
            )
            for co in (transmitter, receiver):
                await _data(
                    client,
                    "project_link_com_object",
                    node_id=node_id,
                    com_object_ref_id=co["ref_id"],
                    group_address="1/1/1",
                    is_sending=True,
                )
            assert (await _data(client, "project_health", category="group-address"))[
                "count"
            ] == 0
            await _data(
                client,
                "project_set_com_object_flag",
                node_id=node_id,
                com_object_ref_id=receiver["ref_id"],
                flag="transmit",
                value=True,
            )
            findings = await _data(client, "project_health", category="group-address")
            assert findings["count"] == 1
            assert findings["items"] == [
                {**asdict(f), "severity": f.severity.value}
                for f in HealthService(project).findings()
                if f.category == "group-address"
            ]
            assert "2 transmit-capable group objects" in findings["items"][0]["message"]
            await _data(client, "project_undo")
            assert (await _data(client, "project_health", category="group-address"))[
                "count"
            ] == 0
    finally:
        project.close()
