"""Filters that let a client read just what it needs: group objects by text or link, group
addresses by address or group, an enum's choices by text, one section's parameters, a device's
space, and a group address created with its datapoint type."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data, _ids

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


async def _project_with_device(client: Client, tmp_path: Path) -> int:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0][
        "product_ref_id"
    ]
    return (await _data(client, "project_add_device", product_ref_id=product))[
        "node_id"
    ]


async def test_the_device_names_its_space(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _project_with_device(client, tmp_path)
        assert (await _data(client, "project_get_device", node_id=node_id))[
            "space"
        ] is None
        building = (await _data(client, "project_spaces"))["items"][0]
        floor = (
            await _data(
                client,
                "project_create_space",
                space_type="Floor",
                name="EG",
                parent_id=building["id"],
            )
        )["space_id"]
        room = (
            await _data(
                client,
                "project_create_space",
                space_type="Room",
                name="Küche",
                parent_id=floor,
            )
        )["space_id"]
        await _data(client, "project_set_device_space", node_id=node_id, space_id=room)
        device = await _data(client, "project_get_device", node_id=node_id)
        assert device["space"] == {
            "space_id": room,
            "name": "Küche",
            "path": f"{building['name']} > EG > Küche",
        }


async def test_group_objects_by_link_and_text(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _project_with_device(client, tmp_path)
        every = (await _data(client, "project_list_com_objects", node_id=node_id))[
            "items"
        ]
        linked, other = [o for o in every if o["linkable"]][:2]
        await _data(
            client, "project_create_group_address", address="1/1/1", name="Licht Küche"
        )
        await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=linked["ref_id"],
            group_address="1/1/1",
        )

        only_linked = (
            await _data(
                client, "project_list_com_objects", node_id=node_id, linked_only=True
            )
        )["items"]
        assert [o["ref_id"] for o in only_linked] == [linked["ref_id"]]
        assert only_linked[0]["links"][0]["address"] == "1/1/1"

        # The text of a linked group address finds its object, the address too.
        for text in ("KÜCHE", "1/1/1"):
            found = await _data(
                client, "project_list_com_objects", node_id=node_id, search=text
            )
            assert [o["ref_id"] for o in found["items"]] == [linked["ref_id"]]
        by_name = await _data(
            client,
            "project_list_com_objects",
            node_id=node_id,
            search=other["name"][-6:].lower(),
        )
        assert other["ref_id"] in [o["ref_id"] for o in by_name["items"]]
        assert all(
            other["name"][-6:].lower() in (o["name"] + o["description"]).lower()
            or o["links"]
            for o in by_name["items"]
        )
        nothing = await _data(
            client, "project_list_com_objects", node_id=node_id, search="no such text"
        )
        assert nothing == {"items": [], "count": 0}

        compact = (
            await _data(
                client, "project_list_com_objects", node_id=node_id, compact=True
            )
        )["items"]
        assert len(compact) == len(every)
        assert {tuple(o) for o in compact} == {
            ("ref_id", "number", "name", "dpt", "links")
        }


async def test_group_addresses_by_address_and_group(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        ids = {
            address: (
                await _data(client, "project_create_group_address", address=address)
            )["group_address_id"]
            for address in ("1/1/1", "1/1/2", "1/2/1", "2/0/1")
        }

        def _addresses(result: dict[str, Any]) -> list[str]:
            return sorted(g["address"] for g in result["items"])

        plain = await _data(client, "project_list_group_addresses")
        assert "not_found" not in plain
        listed = await _data(
            client,
            "project_list_group_addresses",
            group_addresses=["1/1/1", "9/9/9"],
            group_address_ids=[ids["2/0/1"], 99999],
        )
        assert _addresses(listed) == ["1/1/1", "2/0/1"]
        assert listed["not_found"] == [99999, "9/9/9"]
        everything_found = await _data(
            client, "project_list_group_addresses", group_addresses=["1/2/1"]
        )
        assert everything_found["not_found"] == []

        ranges = {
            r["address"]: r["id"]
            for r in (await _data(client, "project_list_group_ranges"))["items"]
        }
        main = await _data(
            client, "project_list_group_addresses", group_range_id=ranges["1"]
        )
        assert _addresses(main) == ["1/1/1", "1/1/2", "1/2/1"]
        middle = await _data(
            client, "project_list_group_addresses", group_range_id=ranges["1/1"]
        )
        assert _addresses(middle) == ["1/1/1", "1/1/2"]
        # Filters combine.
        both = await _data(
            client,
            "project_list_group_addresses",
            group_range_id=ranges["1/1"],
            group_addresses=["1/1/2", "2/0/1"],
        )
        assert _addresses(both) == ["1/1/2"]
        with pytest.raises(Exception, match="no group range with id"):
            await _data(client, "project_list_group_addresses", group_range_id=99999)

        compact = await _data(client, "project_list_group_addresses", compact=True)
        assert {tuple(g) for g in compact["items"]} == {
            ("id", "address", "name", "datapoint_type")
        }


async def test_a_group_address_is_created_with_its_datapoint_type(
    tmp_path: Path,
) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        created = await _data(
            client,
            "project_create_group_address",
            address="1/1/1",
            name="Licht",
            dpt="1.001",
        )
        assert created["datapoint_type"] == "DPST-1-1"
        # The address and its type are one undo step.
        await _data(client, "project_undo")
        assert (await _data(client, "project_list_group_addresses"))["count"] == 0

        # A type that cannot be parsed creates nothing.
        with pytest.raises(Exception, match="unrecognized datapoint type"):
            await _data(
                client, "project_create_group_address", address="1/1/2", dpt="nonsense"
            )
        assert (await _data(client, "project_list_group_addresses"))["count"] == 0

        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "create_group_address",
                    "params": {"address": "1/1/3", "dpt": "DPST-5-1"},
                },
                {"op": "create_group_address", "params": {"address": "x/y"}},
            ],
            atomic=False,
        )
        assert [r["status"] for r in batch["results"]] == ["ok", "error"]
        assert "could not create group address" in batch["results"][1]["error"]
        made = await _data(client, "project_get_group_address", group_address="1/1/3")
        assert made["datapoint_type"] == "DPST-5-1"


async def _enum(client: Client, node_id: int) -> dict[str, Any]:
    """A parameter of the device with an enum of more than two choices."""
    rows = (await _data(client, "project_list_parameters", node_id=node_id))["items"]
    ref_id = next(
        r["ref_id"]
        for r in rows
        if r["widget"]["type"] == "enum" and r["widget"]["choice_count"] > 2
    )
    return await _data(
        client, "project_get_parameter", node_id=node_id, parameter_ref_id=ref_id
    )


async def test_an_enums_choices_by_text(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _project_with_device(client, tmp_path)
        full = await _enum(client, node_id)
        choices = full["widget"]["choices"]
        assert "choice_count" not in full["widget"]
        wanted = choices[1]["label"]
        found = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=full["ref_id"],
            choice_search=wanted.upper(),
        )
        assert found["widget"]["choice_count"] == len(choices)
        assert choices[1] in found["widget"]["choices"]
        assert all(
            wanted.casefold() in c["label"].casefold()
            for c in found["widget"]["choices"]
        )
        assert len(found["widget"]["choices"]) < len(choices)
        assert {k: v for k, v in found.items() if k != "widget"} == {
            k: v for k, v in full.items() if k != "widget"
        }


async def test_the_parameters_of_one_section(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _project_with_device(client, tmp_path)
        outline = await _data(
            client, "project_parameter_tree", node_id=node_id, outline=True
        )
        section = _ids(outline["items"])[0]
        every = {
            r["ref_id"]
            for r in (await _data(client, "project_list_parameters", node_id=node_id))[
                "items"
            ]
        }
        with pytest.raises(Exception, match="requires tree_token"):
            await _data(
                client, "project_list_parameters", node_id=node_id, subtree_id=section
            )
        with pytest.raises(Exception, match="stale tree_token"):
            await _data(
                client,
                "project_list_parameters",
                node_id=node_id,
                subtree_id=section,
                tree_token="0" * 16,
            )
        scoped = await _data(
            client,
            "project_list_parameters",
            node_id=node_id,
            subtree_id=section,
            tree_token=outline["tree_token"],
            compact=True,
        )
        refs = {r["ref_id"] for r in scoped["items"]}
        assert refs
        assert refs <= every
        # The same parameters as the section's tree, flat.
        tree = await _data(
            client,
            "project_parameter_tree",
            node_id=node_id,
            subtree_id=section,
            tree_token=outline["tree_token"],
        )

        def _refs(nodes: list[dict[str, Any]]) -> set[str]:
            out: set[str] = set()
            for nd in nodes:
                if nd["kind"] == "parameter":
                    out.add(nd["ref_id"])
                out |= _refs(nd.get("children", []))
            return out

        assert refs == _refs(tree["items"])
        with pytest.raises(Exception, match="without subtree_id"):
            await _data(
                client,
                "project_list_parameters",
                node_id=node_id,
                subtree_id=section,
                tree_token=outline["tree_token"],
                include_hidden=True,
            )
