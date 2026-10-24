"""The "Status" of the project, areas, lines, devices, spaces and functions, a room's "Use" and a
function's details, as the MCP tools read and change them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


async def _project(client: Client, tmp_path: Path) -> dict[str, Any]:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    node_id = (
        await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
    )["node_id"]
    area = (await _data(client, "project_topology"))["items"][0]
    building = (
        await _data(client, "project_create_space", space_type="Building", name="House")
    )["space_id"]
    room = (
        await _data(
            client,
            "project_create_space",
            space_type="Room",
            name="Living",
            parent_id=building,
            usage="SU-4",
        )
    )["space_id"]
    function = (
        await _data(client, "project_create_function", space_id=room, name="Light")
    )["function_id"]
    return {
        "device": node_id,
        "area": area["id"],
        "line": area["lines"][0]["id"],
        "space": room,
        "function": function,
    }


async def test_every_kind_has_a_status(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        ids = await _project(client, tmp_path)
        project = await _data(client, "project_get_status", kind="project")
        assert project == {"kind": "project", "element_id": 0, "status": "Undefined"}
        for kind, element_id in ids.items():
            changed = await _data(
                client,
                "project_set_status",
                kind=kind,
                element_id=element_id,
                status="Tested",
            )
            assert changed["status"] == "Tested"
        device = await _data(client, "project_get_device", node_id=ids["device"])
        by_address = await _data(
            client,
            "project_get_status",
            kind="device",
            device_address=device["individual_address"],
        )
        assert by_address["element_id"] == ids["device"]
        assert by_address["status"] == "Tested"
        line = await _data(
            client, "project_get_element", kind="line", element_id=ids["line"]
        )
        assert line["status"] == "Tested"

        await _data(client, "project_undo")
        function = await _data(
            client, "project_get_status", kind="function", element_id=ids["function"]
        )
        assert function["status"] == "Undefined"

        await _error(
            client, "project_get_status", "no area", kind="area", element_id=999
        )
        await _error(client, "project_get_status", "element_id", kind="space")
        await _error(
            client,
            "project_get_status",
            "only for kind device",
            kind="space",
            element_id=ids["space"],
            device_address="1.1.1",
        )


async def test_a_batch_sets_the_status_of_many_elements(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        ids = await _project(client, tmp_path)
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_status",
                    "params": {
                        "kind": "device",
                        "node_id": ids["device"],
                        "status": "Accepted",
                    },
                },
                {"op": "set_status", "params": {"kind": "project", "status": "Locked"}},
                {
                    "op": "set_status",
                    "params": {
                        "kind": "area",
                        "element_id": ids["area"],
                        "status": "Done",
                    },
                },
            ],
            atomic=False,
        )
        assert result["ok"] == 2
        assert "status must be" in str(result["results"][2])
        assert (await _data(client, "project_get_status", kind="project"))[
            "status"
        ] == "Locked"


async def test_a_room_has_a_use(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        ids = await _project(client, tmp_path)
        usages = (await _data(client, "project_list_space_usages"))["items"]
        assert len(usages) == 27
        assert {"usage": "SU-4", "text": "Living room"} in usages
        texts = [u["text"] for u in usages]
        assert texts == sorted(texts, key=str.casefold)

        def _room(spaces: list[dict[str, Any]]) -> dict[str, Any] | None:
            for space in spaces:
                if space["id"] == ids["space"]:
                    return space
                found = _room(space.get("children", []))
                if found is not None:
                    return found
            return None

        room = _room((await _data(client, "project_spaces"))["items"])
        assert room is not None
        assert (room["usage"], room["usage_text"]) == ("SU-4", "Living room")

        changed = await _data(
            client, "project_set_space_usage", space_id=ids["space"], usage="SU-1"
        )
        assert changed["usage_text"] == "Kitchen"
        cleared = await _data(
            client, "project_set_space_usage", space_id=ids["space"], usage=""
        )
        assert cleared["usage"] == ""
        await _data(client, "project_undo")
        room = _room((await _data(client, "project_spaces"))["items"])
        assert room is not None and room["usage"] == "SU-1"

        await _error(
            client,
            "project_set_space_usage",
            "unknown usage",
            space_id=ids["space"],
            usage="SU-99",
        )
        building = (await _data(client, "project_spaces"))["items"][-1]["id"]
        await _error(
            client,
            "project_set_space_usage",
            "only a Room",
            space_id=building,
            usage="SU-1",
        )
        await _error(
            client,
            "project_create_space",
            "Room only",
            space_type="Floor",
            name="F",
            usage="SU-1",
        )


async def test_a_function_has_details(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        ids = await _project(client, tmp_path)
        await _data(
            client,
            "project_set_description",
            kind="function",
            element_id=ids["function"],
            description="Ceiling",
        )
        details = await _data(
            client, "project_get_element", kind="function", element_id=ids["function"]
        )
        assert details == {
            "kind": "function",
            "element_id": ids["function"],
            "type": "function",
            "name": "Light",
            "description": "Ceiling",
            "status": "Undefined",
        }
