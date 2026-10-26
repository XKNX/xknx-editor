"""Group addresses and middle groups move as their "Move to" does."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from .test_server import _build, _data


async def test_group_address_and_middle_group_move(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        for address, name in (("1/1/5", "Light"), ("2/1/5", "Other")):
            await _data(
                client, "project_create_group_address", address=address, name=name
            )
        ranges = (await _data(client, "project_list_group_ranges"))["items"]
        by_address = {r["address"]: r["id"] for r in ranges}
        moved = await _data(
            client,
            "project_move_group_address",
            group_address="1/1/5",
            group_range_id=by_address["2/1"],
        )
        assert moved["address"] == "2/1/0"
        group = await _data(
            client,
            "project_move_group_range",
            group_range_id=by_address["1/1"],
            parent_id=by_address["2"],
        )
        assert group["address"] == "2/0"
        # A main group holds no group addresses in the three-level style.
        with pytest.raises(Exception, match="holds no group addresses"):
            await client.call_tool(
                "project_move_group_address",
                {"group_address": "2/1/0", "group_range_id": by_address["2"]},
            )
        await _data(client, "project_undo")
        ranges = (await _data(client, "project_list_group_ranges"))["items"]
        assert "1/1" in [r["address"] for r in ranges]
