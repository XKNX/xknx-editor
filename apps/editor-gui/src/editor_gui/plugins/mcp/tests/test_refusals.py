"""The tools of the topology, the buildings and the group security refuse what the editor refuses,
with its reason, and name the ids they do not know."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_segments import _line
from .test_server import _build, _data


async def _refused(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


async def test_unknown_ids_are_named(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _refused(
            client, "project_add_segment", "no line with id 999", line_id=999
        )
        await _refused(
            client,
            "project_create_space",
            "no space with id 999",
            parent_id=999,
            space_type="Room",
            name="Kitchen",
        )
        await _refused(
            client,
            "project_set_space_usage",
            "no space with id 999",
            space_id=999,
            usage="SU-4",
        )
        await _refused(
            client,
            "project_move_space",
            "no space with id 999",
            space_id=999,
            new_parent_id=None,
        )


async def test_space_rules_are_kept(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        # Only a building goes to the top level, and only a room has a use.
        await _refused(
            client,
            "project_create_space",
            "cannot be added here",
            parent_id=None,
            space_type="Room",
            name="Kitchen",
        )
        floor = (
            await _data(
                client,
                "project_create_space",
                parent_id=building,
                space_type="Floor",
                name="Ground floor",
            )
        )["space_id"]
        await _refused(
            client,
            "project_set_space_usage",
            "only a Room has a use",
            space_id=floor,
            usage="SU-4",
        )
        # A floor does not go below itself, nor to the top level.
        await _refused(
            client, "project_move_space", ".", space_id=floor, new_parent_id=floor
        )
        await _refused(
            client, "project_move_space", ".", space_id=floor, new_parent_id=None
        )


async def test_segments_and_security_are_refused_with_the_reason(
    tmp_path: Path,
) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        line = await _line(client, "1.1")
        # An RF line has no sub-segments.
        await _data(client, "project_set_medium", line_id=line["id"], medium="RF")
        line = await _line(client, "1.1")
        assert line["medium"] == "RF" and line["domain_address"]
        await _refused(client, "project_add_segment", "sub-segment", line_id=line["id"])
        # Securing a group needs the project password.
        await _data(
            client, "project_create_group_address", address="1/1/1", name="Light"
        )
        group = (await _data(client, "project_list_group_ranges"))["items"][0]["id"]
        await _refused(
            client,
            "project_set_group_range_security",
            "project password",
            group_range_id=group,
            mode="On",
        )
