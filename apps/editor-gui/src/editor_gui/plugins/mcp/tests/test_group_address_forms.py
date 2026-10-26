"""Every tool on a group address takes it by its id or by its printed address; a link can be
removed by the linked pair; the parameter list can hold only the changed parameters, compactly."""

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


async def _device(client: Client, tmp_path: Path) -> int:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    return (
        await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
    )["node_id"]


async def _linkable(client: Client, node_id: int) -> str:
    objects = (await _data(client, "project_list_com_objects", node_id=node_id))[
        "items"
    ]
    return next(co["ref_id"] for co in objects if co["linkable"])


async def test_a_batch_links_by_the_address_it_just_created(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        ref = await _linkable(client, node_id)
        result = await _data(
            client,
            "project_batch",
            operations=[
                {"op": "create_group_address", "params": {"address": "0/0/5"}},
                {
                    "op": "link_com_object",
                    "params": {
                        "node_id": node_id,
                        "com_object_ref_id": ref,
                        "group_address": "0/0/5",
                        "is_sending": True,
                    },
                },
            ],
        )
        assert result["ok"] == 2
        created = result["results"][0]["result"]["group_address_id"]
        assert result["results"][1]["result"]["group_address_id"] == created


async def test_group_address_tools_take_the_printed_address(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        ga_id = (await _data(client, "project_create_group_address", address="0/0/7"))[
            "group_address_id"
        ]
        ref = await _linkable(client, node_id)
        await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=ref,
            group_address="0/0/7",
        )

        got = await _data(client, "project_get_group_address", group_address="0/0/7")
        assert got["id"] == ga_id
        assignments = await _data(
            client, "project_get_ga_assignments", group_address="0/0/7"
        )
        assert [a["node_id"] for a in assignments["items"]] == [node_id]
        links = await _data(client, "project_list_links", group_address="0/0/7")
        assert [link["group_address_id"] for link in links["items"]] == [ga_id]

        renamed = await _data(
            client, "project_rename_group_address", group_address="0/0/7", name="Hall"
        )
        assert (renamed["id"], renamed["name"]) == (ga_id, "Hall")
        typed = await _data(
            client, "project_set_group_address_dpt", group_address="0/0/7", dpt="1.001"
        )
        assert typed["id"] == ga_id and typed["datapoint_type"]
        secured = await _data(
            client,
            "project_set_group_address_security",
            group_address="0/0/7",
            mode="Off",
        )
        assert (secured["id"], secured["secured"]) == (ga_id, False)

        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        space_id = (
            await _data(
                client,
                "project_create_space",
                space_type="Room",
                name="Hall",
                parent_id=building,
            )
        )["space_id"]
        function_id = (
            await _data(
                client, "project_create_function", space_id=space_id, name="Light"
            )
        )["function_id"]
        added = await _data(
            client,
            "project_add_function_group_address",
            function_id=function_id,
            group_address="0/0/7",
        )
        assert added["group_address_id"] == ga_id

        removed = await _data(
            client, "project_remove_group_address", group_address="0/0/7"
        )
        assert removed["group_address_id"] == str(ga_id)
        await _error(
            client,
            "project_get_group_address",
            "no group address",
            group_address="0/0/7",
        )
        # Exactly one of the two forms.
        await _error(client, "project_get_group_address", "exactly one")
        await _error(
            client,
            "project_get_group_address",
            "exactly one",
            group_address_id=ga_id,
            group_address="0/0/7",
        )


async def test_a_link_is_removed_by_the_linked_pair(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        address = (await _data(client, "project_get_device", node_id=node_id))[
            "individual_address"
        ]
        ref = await _linkable(client, node_id)
        for ga in ("0/0/1", "0/0/2"):
            await _data(client, "project_create_group_address", address=ga)
            await _data(
                client,
                "project_link_com_object",
                node_id=node_id,
                com_object_ref_id=ref,
                group_address=ga,
            )

        def _linked() -> Any:
            return _data(client, "project_list_links", node_id=node_id)

        unlinked = await _data(
            client,
            "project_unlink_com_object",
            device_address=address,
            com_object_ref_id=ref,
            group_address="0/0/1",
        )
        remaining = (await _linked())["items"]
        assert len(remaining) == 1
        assert unlinked["status"] == "unlinked"

        # Not linked (any more): a clear error, nothing removed.
        await _error(
            client,
            "project_unlink_com_object",
            "not linked",
            node_id=node_id,
            com_object_ref_id=ref,
            group_address="0/0/1",
        )
        # The id and the pair are alternatives.
        await _error(client, "project_unlink_com_object", "assignment_id")
        await _error(
            client,
            "project_unlink_com_object",
            "assignment_id",
            assignment_id=1,
            com_object_ref_id=ref,
        )

        # The batch op takes the pair as well.
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "unlink_com_object",
                    "params": {
                        "node_id": node_id,
                        "com_object_ref_id": ref,
                        "group_address": "0/0/2",
                    },
                }
            ],
        )
        assert result["ok"] == 1
        assert (await _linked())["items"] == []
        checked = await _data(
            client,
            "project_batch",
            operations=[{"op": "unlink_com_object", "params": {"node_id": node_id}}],
            dry_run=True,
        )
        assert "assignment_id" in checked["results"][0]["issue"]


async def test_the_changed_parameters_compactly(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        listed = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        assert (
            await _data(
                client, "project_list_parameters", node_id=node_id, modified_only=True
            )
        )["items"] == []

        enum = next(
            p
            for p in listed
            if p["widget"]["type"] == "enum" and p["widget"]["choice_count"] > 1
        )
        full = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=enum["ref_id"],
        )
        other = next(
            str(c["value"])
            for c in full["widget"]["choices"]
            if str(c["value"]) != str(enum["value"])
        )
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=enum["ref_id"],
            value=other,
        )

        changed = (
            await _data(
                client,
                "project_list_parameters",
                node_id=node_id,
                modified_only=True,
                compact=True,
            )
        )["items"]
        item = next(p for p in changed if p["ref_id"] == enum["ref_id"])
        assert set(item) == {"ref_id", "label", "value", "value_label"}
        assert str(item["value"]) == other
        assert all(
            set(p) <= {"ref_id", "label", "value", "value_label"} for p in changed
        )
