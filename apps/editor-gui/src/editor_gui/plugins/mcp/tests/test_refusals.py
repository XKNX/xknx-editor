"""The tools of the topology, the buildings and the group security refuse what the editor refuses,
with its reason, and name the ids they do not know."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_segments import _line
from .test_server import _FIXTURE, _build, _data


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


async def test_operations_need_the_operations_panel(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _refused(
            client, "connection_operations", "Operations panel is not available"
        )


async def test_moves_and_unknown_elements_are_refused(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _refused(
            client,
            "project_rename_segment",
            "no segment with id 999",
            segment_id=999,
            name="Garden",
        )
        await _data(client, "project_create_group_address", address="1/1/1")
        await _data(client, "project_create_group_address", address="2/1/1")
        ranges = {
            r["address"]: r["id"]
            for r in (await _data(client, "project_list_group_ranges"))["items"]
        }
        # Only a middle group goes below a main group, and only to one that exists.
        await _refused(
            client,
            "project_move_group_range",
            "only a middle group",
            group_range_id=ranges["1"],
            parent_id=ranges["2"],
        )
        await _refused(
            client,
            "project_move_group_range",
            "No group range",
            group_range_id=ranges["1/1"],
            parent_id=999,
        )
        # A middle group's Security, found below its main group.
        middle = await _data(
            client,
            "project_set_group_range_security",
            group_range_id=ranges["1/1"],
            mode="Off",
        )
        assert (middle["security"], middle["secured"]) == ("Off", [])


async def test_a_space_moves_and_takes_only_known_spaces(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        floors = [
            (
                await _data(
                    client,
                    "project_create_space",
                    parent_id=building,
                    space_type="Floor",
                    name=name,
                )
            )["space_id"]
            for name in ("Ground floor", "First floor")
        ]
        room = (
            await _data(
                client,
                "project_create_space",
                parent_id=floors[0],
                space_type="Room",
                name="Kitchen",
            )
        )["space_id"]
        moved = await _data(
            client, "project_move_space", space_id=room, new_parent_id=floors[1]
        )
        assert moved == {"space_id": room, "new_parent_id": floors[1]}
        tree = (await _data(client, "project_spaces"))["items"][0]
        upper = next(c for c in tree["children"] if c["id"] == floors[1])
        assert [c["id"] for c in upper["children"]] == [room]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_a_device_only_goes_where_it_may(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        node_id = (
            await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
        )["node_id"]
        await _refused(
            client,
            "project_set_device_space",
            "no space with id 999",
            node_id=node_id,
            space_id=999,
        )
        # A device not commissioned securely keeps its group address from being secured.
        objects = (await _data(client, "project_list_com_objects", node_id=node_id))[
            "items"
        ]
        await _data(client, "project_create_group_address", address="1/1/1")
        await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=next(o for o in objects if o["linkable"])["ref_id"],
            group_address="1/1/1",
        )
        project.set_project_password("geheim")
        main = next(
            r["id"]
            for r in (await _data(client, "project_list_group_ranges"))["items"]
            if r["depth"] == 0
        )
        await _refused(
            client,
            "project_set_group_range_security",
            "commissioned securely",
            group_range_id=main,
            mode="On",
        )
