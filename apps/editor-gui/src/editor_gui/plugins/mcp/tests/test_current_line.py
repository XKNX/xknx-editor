"""A space's "Current Line" as the MCP tools read and change it, and a device added to a space,
placed on that line."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data


async def _lines(client: Client) -> dict[str, int]:
    """Every line by its address ("1.1")."""
    areas = (await _data(client, "project_topology"))["items"]
    return {
        f"{area['area_number']}.{line['line_number']}": line["id"]
        for area in areas
        for line in area["lines"]
    }


async def _spaces(client: Client, tmp_path: Path) -> dict[str, int]:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    building = (await _data(client, "project_spaces"))["items"][0]["id"]
    room = (
        await _data(
            client,
            "project_create_space",
            space_type="Room",
            name="Living",
            parent_id=building,
        )
    )["space_id"]
    return {"building": building, "room": room}


async def test_the_current_line_is_inherited_set_and_cleared(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        spaces = await _spaces(client, tmp_path)
        lines = await _lines(client)
        building, room = spaces["building"], spaces["room"]
        # A new project's building has line 1.1; the room takes it from there.
        assert await _data(client, "project_get_current_line", space_id=room) == {
            "space_id": room,
            "line_id": lines["1.1"],
            "line": "1.1 TP line",
            "set_by_space_id": building,
        }
        tree = (await _data(client, "project_spaces"))["items"][0]
        assert tree["current_line_id"] == lines["1.1"]
        assert "current_line_id" not in tree["children"][0]

        own = await _data(
            client, "project_set_current_line", space_id=room, line_id=lines["1.0"]
        )
        assert own["line_id"] == lines["1.0"]
        assert own["set_by_space_id"] == room
        cleared = await _data(client, "project_set_current_line", space_id=building)
        assert cleared == {
            "space_id": building,
            "line_id": None,
            "line": None,
            "set_by_space_id": None,
        }
        await _data(client, "project_undo")
        building_line = await _data(
            client, "project_get_current_line", space_id=building
        )
        assert building_line["line_id"] == lines["1.1"]


async def test_unknown_spaces_and_lines_are_errors(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        spaces = await _spaces(client, tmp_path)
        args: list[tuple[str, dict[str, Any]]] = [
            ("project_get_current_line", {"space_id": 999}),
            ("project_set_current_line", {"space_id": 999}),
            ("project_set_current_line", {"space_id": spaces["room"], "line_id": 999}),
        ]
        for tool, arguments in args:
            with pytest.raises(Exception, match=r"no (space|line) with id 999"):
                await client.call_tool(tool, arguments)


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_a_device_added_to_a_space_goes_on_its_current_line(
    tmp_path: Path,
) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        spaces = await _spaces(client, tmp_path)
        area = (await _data(client, "project_topology"))["items"][1]["id"]
        await _data(client, "project_create_line", area_id=area, line_number=2)
        lines = await _lines(client)
        # A new line takes the medium of its area's main line (IP); make it a TP line.
        await _data(client, "project_set_medium", line_id=lines["1.2"], medium="TP")
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        # A TP device does not fit the IP line 1.0.
        await _data(
            client,
            "project_set_current_line",
            space_id=spaces["room"],
            line_id=lines["1.0"],
        )
        with pytest.raises(Exception, match="incompatible with line's medium type"):
            await client.call_tool(
                "project_add_device",
                {
                    "product_ref_id": product["product_ref_id"],
                    "space_id": spaces["room"],
                },
            )
        await _data(
            client,
            "project_set_current_line",
            space_id=spaces["room"],
            line_id=lines["1.2"],
        )
        device = await _data(
            client,
            "project_add_device",
            product_ref_id=product["product_ref_id"],
            space_id=spaces["room"],
        )
        assert device["individual_address"].startswith("1.2.")
        room = (await _data(client, "project_spaces"))["items"][0]["children"][0]
        assert [d["node_id"] for d in room["devices"]] == [device["node_id"]]

        # A room without a Current Line of its own takes the building's (line 1.1).
        kitchen = (
            await _data(
                client,
                "project_create_space",
                space_type="Room",
                name="Kitchen",
                parent_id=spaces["building"],
            )
        )["space_id"]
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "add_device",
                    "params": {
                        "product_ref_id": product["product_ref_id"],
                        "space_id": kitchen,
                    },
                }
            ],
        )
        node_id = batch["results"][0]["result"]["node_id"]
        added = await _data(client, "project_get_device", node_id=node_id)
        assert added["individual_address"].startswith("1.1.")
        # A building holds no devices.
        with pytest.raises(Exception, match="stairways, rooms, corridors and cabinets"):
            await client.call_tool(
                "project_add_device",
                {
                    "product_ref_id": product["product_ref_id"],
                    "space_id": spaces["building"],
                },
            )
        with pytest.raises(Exception, match="no space with id 999"):
            await client.call_tool(
                "project_add_device",
                {"product_ref_id": product["product_ref_id"], "space_id": 999},
            )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_a_device_goes_on_the_segment_asked_for(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        spaces = await _spaces(client, tmp_path)
        area = (await _data(client, "project_topology"))["items"][1]["id"]
        await _data(client, "project_create_line", area_id=area, line_number=2)
        lines = await _lines(client)
        await _data(client, "project_set_medium", line_id=lines["1.2"], medium="TP")
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        line = next(
            ln
            for a in (await _data(client, "project_topology"))["items"]
            for ln in a["lines"]
            if ln["id"] == lines["1.2"]
        )
        # The segment wins over the space's Current Line (1.1), as "Add → Devices…" does.
        device = await _data(
            client,
            "project_add_device",
            product_ref_id=product["product_ref_id"],
            space_id=spaces["room"],
            segment_id=line["main_segment_id"],
        )
        assert device["individual_address"].startswith("1.2.")
        with pytest.raises(Exception, match="no segment with id 999"):
            await client.call_tool(
                "project_add_device",
                {"product_ref_id": product["product_ref_id"], "segment_id": 999},
            )
