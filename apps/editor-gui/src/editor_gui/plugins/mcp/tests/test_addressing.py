"""Every tool on a device takes it by node_id or by its individual address, every tool on a group
address by its id or its printed address; the parameter tree can be searched like the parameter
view."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin

from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")

# The id argument and the address argument that may replace it.
_ALTERNATIVES = (
    ("node_ids", "device_addresses"),
    ("node_id", "device_address"),
    ("group_address_ids", "group_addresses"),
    ("group_address_id", "group_address"),
)


def _server(tmp_path: Path) -> Any:
    ctx = _build_ctx(tmp_path)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    return build_server(ctx)


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


async def _device(client: Client, address: str | None = None) -> tuple[int, str]:
    products = (await _data(client, "catalog_list_products"))["items"]
    if not products:
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = (await _data(client, "catalog_list_products"))["items"]
    node_id = (
        await _data(
            client, "project_add_device", product_ref_id=products[0]["product_ref_id"]
        )
    )["node_id"]
    if address is not None:
        await _data(
            client, "project_set_individual_address", node_id=node_id, address=address
        )
    device = await _data(client, "project_get_device", node_id=node_id)
    return node_id, device["individual_address"]


async def _linkable(client: Client, address: str) -> str:
    objects = (await _data(client, "project_list_com_objects", device_address=address))[
        "items"
    ]
    return next(co["ref_id"] for co in objects if co["linkable"])


async def test_every_id_argument_has_its_address_form(tmp_path: Path) -> None:
    async with Client(_server(tmp_path)) as client:
        tools = await client.list_tools()
    missing: list[str] = []
    for t in tools:
        properties = set(t.input_schema.get("properties", {}))
        for name in properties:
            for id_suffix, address_suffix in _ALTERNATIVES:
                if name.endswith(id_suffix):
                    wanted = name.removesuffix(id_suffix) + address_suffix
                    if wanted not in properties:
                        missing.append(f"{t.name}: {name} without {wanted}")
                    break
    assert missing == []


async def test_device_tools_take_the_individual_address(tmp_path: Path) -> None:
    async with Client(_server(tmp_path)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id, _ = await _device(client, "1.1.10")

        renamed = await _data(
            client, "project_rename_device", device_address="1.1.10", name="Hall"
        )
        assert (renamed["node_id"], renamed["name"]) == (node_id, "Hall")
        moved = await _data(
            client,
            "project_set_individual_address",
            device_address="1.1.10",
            address="1.1.11",
        )
        assert (moved["node_id"], moved["individual_address"]) == (node_id, "1.1.11")
        serial = await _data(
            client,
            "project_set_serial_number",
            device_address="1.1.11",
            serial_number="0083:7AA2039C",
        )
        assert serial["node_id"] == node_id

        ref = await _linkable(client, "1.1.11")
        flagged = await _data(
            client,
            "project_set_com_object_flag",
            device_address="1.1.11",
            com_object_ref_id=ref,
            flag="read",
            value=True,
        )
        assert flagged["flags"]["read"] is True

        await _data(client, "project_create_group_address", address="0/0/3")
        await _data(
            client,
            "project_link_com_object",
            device_address="1.1.11",
            com_object_ref_id=ref,
            group_address="0/0/3",
        )
        links = await _data(client, "project_list_links", device_address="1.1.11")
        assert [link["node_id"] for link in links["items"]] == [node_id]
        # An unknown device or group address is an error, not an empty filter result.
        await _error(client, "project_list_links", "no device", device_address="9.9.9")
        await _error(client, "project_list_links", "no device", node_id=node_id + 100)
        await _error(
            client, "project_list_links", "no group address", group_address_id=999
        )

        copies = (await _data(client, "project_copy_device", device_address="1.1.11"))[
            "node_ids"
        ]
        await _data(
            client,
            "project_set_individual_address",
            node_id=copies[0],
            address="1.1.12",
        )
        compared = await _data(
            client,
            "project_compare_devices",
            device_address_a="1.1.11",
            device_address_b="1.1.12",
        )
        assert (compared["node_id_a"], compared["node_id_b"]) == (node_id, copies[0])
        await _error(
            client,
            "project_compare_devices",
            "node_id_b or device_address_b",
            device_address_a="1.1.11",
        )
        pasted = await _data(
            client,
            "project_copy_device_configuration",
            source_device_address="1.1.11",
            target_device_addresses=["1.1.12"],
        )
        assert [(r["node_id"], r["ok"]) for r in pasted["items"]] == [(copies[0], True)]
        await _error(
            client,
            "project_copy_device_configuration",
            "source_node_id or source_device_address",
            target_device_addresses=["1.1.12"],
        )

        removed = await _data(client, "project_remove_device", device_address="1.1.12")
        assert removed["node_id"] == copies[0]
        await _error(
            client,
            "project_get_device",
            "no device",
            device_address="1.1.12",
        )


async def test_batch_group_address_ops_resolve_the_address_once(tmp_path: Path) -> None:
    async with Client(_server(tmp_path)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        ga_id = (await _data(client, "project_create_group_address", address="0/0/4"))[
            "group_address_id"
        ]
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "rename_group_address",
                    "params": {"group_address": "0/0/4", "name": "Hall"},
                },
                {
                    "op": "set_group_address_dpt",
                    "params": {"group_address": "0/0/4", "dpt": "1.001"},
                },
                {"op": "remove_group_address", "params": {"group_address": "0/0/4"}},
            ],
        )
        assert result["ok"] == 3
        assert [r["result"]["group_address_id"] for r in result["results"]] == [
            ga_id
        ] * 3
        assert (await _data(client, "project_list_group_addresses"))["items"] == []

        unknown = await _data(
            client,
            "project_batch",
            operations=[
                {"op": "remove_group_address", "params": {"group_address_id": ga_id}}
            ],
            atomic=False,
        )
        assert unknown["ok"] == 0
        assert "no group address" in str(unknown["results"][0])


async def test_tools_window_actions_take_addresses(tmp_path: Path) -> None:
    async with Client(_server(tmp_path)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        replaced, _ = await _device(client, "1.1.20")
        template, _ = await _device(client, "1.1.21")

        await _data(
            client,
            "project_replace_device",
            device_address="1.1.20",
            template_device_address="1.1.21",
        )
        devices = (await _data(client, "project_list_devices"))["items"]
        assert replaced not in [d["node_id"] for d in devices]
        await _error(
            client,
            "project_replace_device",
            "template_node_id or template_device_address",
            device_address="1.1.20",
        )

        shifted = await _data(
            client,
            "project_shift_individual_addresses",
            device_addresses=["1.1.21"],
            offset=5,
        )
        assert shifted["errors"] == []
        assert (await _data(client, "project_get_device", node_id=template))[
            "individual_address"
        ] == "1.1.26"
        await _error(
            client, "project_shift_individual_addresses", "device_addresses", offset=1
        )

        ga_id = (await _data(client, "project_create_group_address", address="0/0/1"))[
            "group_address_id"
        ]
        moved = await _data(
            client,
            "project_shift_group_addresses",
            group_addresses=["0/0/1"],
            offset=4,
        )
        assert moved["errors"] == []
        assert (
            await _data(client, "project_get_group_address", group_address="0/0/5")
        )["id"] == ga_id

        source = await _linkable(client, "1.1.20")
        target = await _linkable(client, "1.1.26")
        linked = await _data(
            client,
            "project_link_com_objects",
            pairs=[
                {
                    "source_device_address": "1.1.20",
                    "source_com_object_ref_id": source,
                    "target_device_address": "1.1.26",
                    "target_com_object_ref_id": target,
                    "address": "0/0/9",
                }
            ],
        )
        assert linked["linked"] == 1
        await _error(
            client,
            "project_link_com_objects",
            "source_node_id or source_device_address",
            pairs=[
                {
                    "source_node_id": template,
                    "source_device_address": "1.1.26",
                    "source_com_object_ref_id": source,
                    "target_device_address": "1.1.26",
                    "target_com_object_ref_id": target,
                }
            ],
        )


def _flatten(nodes: list[dict[str, Any]], trail: tuple[str, ...] = ()) -> list[Any]:
    """Each parameter of ``nodes`` as (headings of the tabs/blocks around it, label)."""
    found: list[Any] = []
    for node in nodes:
        if node["kind"] == "parameter":
            found.append((trail, node["label"]))
        elif node["kind"] in ("tab", "block"):
            found.extend(_flatten(node["children"], (*trail, node["name"])))
    return found


async def test_the_parameter_tree_is_searched_like_the_parameter_view(
    tmp_path: Path,
) -> None:
    async with Client(_server(tmp_path)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _device(client, "1.1.30")

        found = await _data(
            client, "project_parameter_tree", device_address="1.1.30", search="FUNCTION"
        )
        hits = _flatten(found["items"])
        # Each "Function" comes with its channel, so the two can be told apart.
        functions = [trail[0] for trail, label in hits if label == "Function"]
        assert functions == ["Channel 1 (...)", "Channel 2 (...)"]
        assert all("function" in label.lower() for _trail, label in hits)
        assert found["outline"] is False and found["tree_token"]

        # A heading that matches brings the whole section along.
        channel = await _data(
            client,
            "project_parameter_tree",
            device_address="1.1.30",
            search="channel 2",
        )
        tabs = [n for n in channel["items"] if n["name"] == "Channel 2 (...)"]
        assert len(tabs) == 1
        assert ((("Channel 2 (...)", "K 2 - General (...)"), "Contact type")) in (
            _flatten(tabs)
        )

        # Scoped to one section, with the section's id from the same read.
        subtree = next(
            n["id"] for n in found["items"] if n["name"] == "Channel 1 (...)"
        )
        scoped = await _data(
            client,
            "project_parameter_tree",
            device_address="1.1.30",
            search="function",
            subtree_id=subtree,
            tree_token=found["tree_token"],
        )
        assert [n["id"] for n in scoped["items"]] == [subtree]
        assert {trail[0] for trail, _label in _flatten(scoped["items"])} == {
            "Channel 1 (...)"
        }

        nothing = await _data(
            client, "project_parameter_tree", device_address="1.1.30", search="zzz"
        )
        assert (nothing["items"], nothing["count"]) == ([], 0)
        await _error(
            client,
            "project_parameter_tree",
            "without outline",
            device_address="1.1.30",
            search="function",
            outline=True,
        )
