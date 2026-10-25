"""Trades as the MCP tools read and change them."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data


async def test_trades_are_created_changed_moved_and_removed(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        assert await _data(client, "project_trades") == {"items": [], "count": 0}
        light = await _data(client, "project_create_trade", name="Lighting")
        assert light == {
            "id": light["id"],
            "name": "Lighting",
            "trade_ids": [light["id"]],
        }
        heat = await _data(client, "project_create_trade", name="Heating")
        inside = await _data(
            client, "project_create_trade", name="Inside", parent_id=light["id"]
        )
        changed = await _data(
            client,
            "project_set_trade",
            trade_id=light["id"],
            number="01",
            description="All lights",
        )
        assert (changed["number"], changed["description"]) == ("01", "All lights")
        assert changed["children"] == [{"id": inside["id"], "name": "Inside"}]

        await _data(
            client,
            "project_set_status",
            kind="trade",
            element_id=light["id"],
            status="Tested",
        )
        element = await _data(
            client, "project_get_element", kind="trade", element_id=light["id"]
        )
        assert (element["type"], element["number"], element["status"]) == (
            "trade",
            "01",
            "Tested",
        )
        await _data(
            client,
            "project_set_description",
            kind="trade",
            element_id=heat["id"],
            description="Radiators",
        )

        moved = await _data(
            client, "project_move_trade", trade_id=inside["id"], parent_id=heat["id"]
        )
        assert moved["name"] == "Inside"
        with pytest.raises(Exception, match="below it"):
            await client.call_tool(
                "project_move_trade",
                {"trade_id": heat["id"], "parent_id": inside["id"]},
            )
        trades = await _data(client, "project_trades")
        assert [t["name"] for t in trades["items"]] == ["Lighting", "Heating"]
        assert trades["items"][1]["description"] == "Radiators"
        assert trades["items"][1]["children"][0]["name"] == "Inside"

        removed = await _data(client, "project_remove_trade", trade_id=heat["id"])
        assert removed == {"status": "removed", "trade_id": heat["id"]}
        assert (await _data(client, "project_trades"))["count"] == 1
        for tool, args in (
            ("project_set_trade", {"trade_id": 999, "name": "x"}),
            ("project_create_trade", {"name": "x", "parent_id": 999}),
            ("project_remove_trade", {"trade_id": 999}),
        ):
            with pytest.raises(Exception, match="no trade with id 999"):
                await client.call_tool(tool, args)


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_devices_go_into_trades(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        device = await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
        trade = await _data(client, "project_create_trade", name="Lighting")
        with_device = await _data(
            client,
            "project_set_trade_device",
            trade_id=trade["id"],
            device_address=device["individual_address"],
        )
        assert [d["node_id"] for d in with_device["devices"]] == [device["node_id"]]
        without = await _data(
            client,
            "project_set_trade_device",
            trade_id=trade["id"],
            node_id=device["node_id"],
            member=False,
        )
        assert "devices" not in without


async def test_several_spaces_and_trades_at_once_with_default_names(
    tmp_path: Path,
) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        rooms = await _data(
            client,
            "project_create_space",
            space_type="Room",
            parent_id=building,
            count=3,
        )
        assert rooms["name"] == "New room" and len(rooms["space_ids"]) == 3
        assert rooms["space_id"] == rooms["space_ids"][0]
        tree = (await _data(client, "project_spaces"))["items"][0]
        assert [c["name"] for c in tree["children"]] == ["New room"] * 3
        trades = await _data(client, "project_create_trade", count=2)
        assert trades["name"] == "New trade" and len(trades["trade_ids"]) == 2
        listed = (await _data(client, "project_trades"))["items"]
        assert [t["name"] for t in listed] == ["New trade", "New trade"]
        # Each one is an undo step, as in the dialog.
        await _data(client, "project_undo")
        assert len((await _data(client, "project_trades"))["items"]) == 1
        for tool, args in (
            ("project_create_space", {"space_type": "Room", "parent_id": building}),
            ("project_create_trade", {}),
        ):
            for count in (0, 101):
                with pytest.raises(Exception, match="count must be 1 to 100"):
                    await client.call_tool(tool, {**args, "count": count})
