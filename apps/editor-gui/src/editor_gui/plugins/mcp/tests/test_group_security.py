"""The Security of main and middle groups and a group address's Central mark, as the MCP tools
read and change them."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from .test_server import _build, _data


async def test_a_group_forces_its_security_below_it(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        ga = await _data(
            client, "project_create_group_address", address="1/1/1", name="Light"
        )
        ranges = (await _data(client, "project_list_group_ranges"))["items"]
        main = next(r for r in ranges if r["depth"] == 0)
        assert (main["security"], main["security_forced_by"]) == ("Auto", None)

        changed = await _data(
            client,
            "project_set_group_range_security",
            group_range_id=main["id"],
            mode="Off",
        )
        assert changed["security"] == "Off" and changed["secured"] == []
        listed = await _data(client, "project_get_group_address", group_address="1/1/1")
        assert listed["security"] == "Auto"
        assert listed["security_forced_by"] == {
            "group_range_id": main["id"],
            "name": main["name"],
            "mode": "Off",
        }
        middle = next(
            r
            for r in (await _data(client, "project_list_group_ranges"))["items"]
            if r["depth"] == 1
        )
        assert middle["security_forced_by"]["mode"] == "Off"
        element = await _data(
            client,
            "project_get_element",
            kind="group_address",
            element_id=ga["group_address_id"],
        )
        assert element["security_forced_by"]["group_range_id"] == main["id"]
        assert element["central"] is False

        with pytest.raises(Exception, match="project password"):
            await client.call_tool(
                "project_set_group_range_security",
                {"group_range_id": main["id"], "mode": "On"},
            )
        with pytest.raises(Exception, match="no group range with id 999"):
            await client.call_tool(
                "project_set_group_range_security",
                {"group_range_id": 999, "mode": "On"},
            )


async def test_a_group_address_is_marked_central(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "project_create_group_address", address="1/1/1", name="A")
        await _data(client, "project_create_group_address", address="1/1/2", name="B")
        marked = await _data(
            client,
            "project_set_group_address_central",
            central=True,
            group_address="1/1/1",
        )
        assert marked["central"] is True
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_group_address_central",
                    "params": {"group_address": "1/1/2", "central": True},
                },
                {
                    "op": "set_group_address_central",
                    "params": {"group_address": "1/1/1", "central": False},
                },
            ],
        )
        assert batch["ok"] == 2
        listed = {
            g["address"]: g["central"]
            for g in (await _data(client, "project_list_group_addresses"))["items"]
        }
        assert listed == {"1/1/1": False, "1/1/2": True}
