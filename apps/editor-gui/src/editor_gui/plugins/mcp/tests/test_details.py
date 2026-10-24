"""MCP tools for comments, installation hints, descriptions, numbers, group object properties,
the project name, all links and catalog product details/export."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data

_needs_fixture = pytest.mark.skipif(
    not _FIXTURE.exists(), reason="knxprod fixture missing"
)


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


def _find_space(spaces: list[dict[str, Any]], space_id: int) -> dict[str, Any]:
    for space in spaces:
        if space["id"] == space_id:
            return space
        with contextlib.suppress(LookupError):
            return _find_space(space.get("children", []), space_id)
    raise LookupError(space_id)


async def _device(client: Client) -> int:
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    return (
        await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
    )["node_id"]


async def test_project_comment_as_text_and_paragraphs(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        empty = await _data(client, "project_get_comment", kind="project")
        assert (empty["text"], empty["paragraphs"]) == ("", [])

        written = await _data(
            client, "project_set_comment", kind="project", text="First\nSecond"
        )
        assert written["text"] == "First\nSecond"
        assert written["paragraphs"] == [[{"text": "First"}], [{"text": "Second"}]]
        # Stored as RTF, the way the Comment tab stores it.
        assert (project.comment("project", 0) or "").startswith("{\\rtf1")

        formatted = await _data(
            client,
            "project_set_comment",
            kind="project",
            paragraphs=[[{"text": "Bold", "bold": True}, {"text": " plain"}]],
        )
        assert formatted["paragraphs"] == [
            [{"text": "Bold", "bold": True}, {"text": " plain"}]
        ]
        assert formatted["text"] == "Bold plain"

        await _data(client, "project_undo")
        again = await _data(client, "project_get_comment", kind="project")
        assert again["text"] == "First\nSecond"

        cleared = await _data(client, "project_set_comment", kind="project", text="")
        assert cleared["text"] == ""

        await _error(
            client,
            "project_set_comment",
            "exactly one of text or paragraphs",
            kind="project",
        )
        await _error(
            client,
            "project_set_comment",
            "unknown run fields",
            kind="project",
            paragraphs=[[{"text": "x", "colour": "red"}]],
        )


@_needs_fixture
async def test_device_comment_and_installation_hints(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _data(
            client,
            "project_set_comment",
            kind="device",
            element_id=node_id,
            field="installation_hints",
            text="Mount 1.5 m above the floor",
        )
        await _data(
            client,
            "project_set_comment",
            kind="device",
            element_id=node_id,
            text="Replaced in 2026",
        )
        hints = await _data(
            client,
            "project_get_comment",
            kind="device",
            element_id=node_id,
            field="installation_hints",
        )
        comment = await _data(
            client, "project_get_comment", kind="device", element_id=node_id
        )
        assert hints["text"] == "Mount 1.5 m above the floor"
        assert comment["text"] == "Replaced in 2026"

        await _error(
            client,
            "project_get_comment",
            "no device with id",
            kind="device",
            element_id=999999,
        )
        await _error(
            client,
            "project_get_comment",
            "only a device has installation hints",
            kind="project",
            field="installation_hints",
        )

        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_comment",
                    "params": {
                        "kind": "device",
                        "element_id": node_id,
                        "text": "From a batch",
                    },
                },
                {"op": "set_comment", "params": {"kind": "nothing", "text": "x"}},
            ],
            atomic=False,
        )
        assert [r["status"] for r in batch["results"]] == ["ok", "error"]
        comment = await _data(
            client, "project_get_comment", kind="device", element_id=node_id
        )
        assert comment["text"] == "From a batch"


async def test_descriptions_and_numbers_of_topology_and_groups(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        area_id = (
            await _data(client, "project_create_area", area_number=6, name="Ground")
        )["area_id"]
        line_id = (
            await _data(
                client, "project_create_line", area_id=area_id, line_number=1, name="L"
            )
        )["line_id"]

        await _data(
            client,
            "project_set_description",
            kind="area",
            element_id=area_id,
            description="Ground floor",
        )
        await _data(
            client,
            "project_set_description",
            kind="line",
            element_id=line_id,
            description="Main line",
        )
        areas = (await _data(client, "project_topology"))["items"]
        area = next(a for a in areas if a["id"] == area_id)
        assert area["description"] == "Ground floor"
        assert area["lines"][0]["description"] == "Main line"

        element = await _data(
            client, "project_set_number", kind="line", element_id=line_id, number="3"
        )
        assert (element["type"], element["address"], element["number"]) == (
            "line",
            "6.3",
            "3",
        )
        await _data(
            client, "project_set_number", kind="area", element_id=area_id, number="7"
        )
        details = await _data(
            client, "project_get_element", kind="line", element_id=line_id
        )
        assert details["address"] == "7.3"
        assert details["description"] == "Main line"
        await _error(
            client,
            "project_set_number",
            ".",
            kind="area",
            element_id=area_id,
            number="16",
        )

        main = project.create_group_range(None, "Lights")
        assert main is not None
        middle = project.create_group_range(main, "Switching")
        assert middle is not None
        await _data(
            client,
            "project_set_description",
            kind="group_range",
            element_id=middle,
            description="All switching",
        )
        changed = await _data(
            client,
            "project_set_number",
            kind="group_range",
            element_id=middle,
            number="5",
        )
        assert changed["type"] == "middle_group"
        assert changed["number"] == "5"
        assert changed["description"] == "All switching"

        ga_id = (
            await _data(
                client, "project_create_group_address", address="1/1/1", name="Hall"
            )
        )["group_address_id"]
        renumbered = await _data(
            client,
            "project_set_number",
            kind="group_address",
            element_id=ga_id,
            number="1/1/7",
        )
        assert renumbered["address"] == "1/1/7"
        await _data(
            client,
            "project_set_description",
            kind="group_address",
            element_id=ga_id,
            description="Hall light",
        )
        ga = await _data(client, "project_get_group_address", group_address_id=ga_id)
        assert ga["description"] == "Hall light"

        space_id = (
            await _data(
                client, "project_create_space", space_type="Room", name="Kitchen"
            )
        )["space_id"]
        await _data(
            client,
            "project_set_number",
            kind="space",
            element_id=space_id,
            number="0.12",
        )
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_description",
                    "params": {
                        "kind": "space",
                        "element_id": space_id,
                        "description": "Next to the hall",
                    },
                }
            ],
        )
        assert batch["ok"] == 1
        space = _find_space((await _data(client, "project_spaces"))["items"], space_id)
        assert (space["number"], space["description"]) == ("0.12", "Next to the hall")

        await _error(
            client,
            "project_set_description",
            "no area with id",
            kind="area",
            element_id=999999,
            description="x",
        )


@_needs_fixture
async def test_com_object_properties_links_and_project_name(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        objects = (await _data(client, "project_list_com_objects", node_id=node_id))[
            "items"
        ]
        co = next(o for o in objects if o["linkable"])
        assert "description" in co
        assert "priority" in co

        updated = await _data(
            client,
            "project_set_com_object_properties",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            description="Hall light",
            priority="High",
        )
        assert (updated["description"], updated["priority"]) == ("Hall light", "High")
        cleared = await _data(
            client,
            "project_set_com_object_properties",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            description="",
        )
        assert cleared["description"] == ""
        await _error(
            client,
            "project_set_com_object_properties",
            "pass description, priority, datapoint_type or several",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
        )

        ga_id = (await _data(client, "project_create_group_address", address="1/1/1"))[
            "group_address_id"
        ]
        await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            group_address_id=ga_id,
        )
        links = await _data(client, "project_list_links")
        assert links["count"] == 1
        (link,) = links["items"]
        assert (link["node_id"], link["group_address_id"]) == (node_id, ga_id)
        assert set(link["flags"]) == {
            "communication",
            "read",
            "write",
            "transmit",
            "update",
        }
        await _error(client, "project_list_links", "no device", node_id=node_id + 1)

        renamed = await _data(client, "project_set_name", name="  House  ")
        assert renamed["name"] == "House"
        assert (await _data(client, "project_info"))["name"] == "House"


@_needs_fixture
async def test_catalog_product_details_and_export(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        program = product["hardware2program_ref_id"]

        details = await _data(
            client, "catalog_get_product_details", hardware2program_ref_id=program
        )
        assert details["hardware2program_ref_id"] == program
        assert details["mask_version"]
        assert isinstance(details["mediums"], list)

        dest = tmp_path / "export.knxprod"
        await _data(
            client,
            "catalog_export_knxprod",
            hardware2program_ref_id=program,
            dest=str(dest),
        )
        assert dest.read_bytes() == _FIXTURE.read_bytes()

        await _error(
            client,
            "catalog_get_product_details",
            "not in the local catalog",
            hardware2program_ref_id="M-0000_H-x_HP-0000-00-0000",
        )
        await _error(
            client,
            "catalog_export_knxprod",
            "no stored .knxprod archive",
            hardware2program_ref_id="M-0000_H-x_HP-0000-00-0000",
            dest=str(dest),
        )
