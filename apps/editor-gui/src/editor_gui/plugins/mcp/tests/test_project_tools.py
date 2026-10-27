"""MCP tools for the Tools window, bulk group addresses, the Mass Linker and plugin parameters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin

from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


def _server(tmp_path: Path, *, with_plugin: bool = True) -> tuple[Any, Any]:
    ctx = _build_ctx(tmp_path)
    if with_plugin:
        ctx.project_plugin = ProjectPlugin(ctx.api)
    return build_server(ctx), ctx.api.project


async def _device(client: Client, address: str | None = None) -> int:
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
    return node_id


async def test_extended_copy_replace_and_shift(tmp_path: Path) -> None:
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client, "1.1.10")
        await _data(client, "project_rename_device", node_id=node_id, name="Hall")
        copy = await _data(
            client,
            "project_extended_copy",
            node_id=node_id,
            count=2,
            find="Hall",
            replace="Kitchen",
            create_group_addresses=True,
        )
        assert copy["copied"] == 2
        assert len(copy["node_ids"]) == 2
        assert copy["errors"] == []
        names = [project.find_device_by_node_id(n).name for n in copy["node_ids"]]
        assert all(name.startswith("Kitchen") for name in names)
        assert project.group_addresses
        await _error(
            client, "project_extended_copy", "count must be", node_id=node_id, count=0
        )

        template = await _device(client, "1.1.20")
        replaced = await _data(
            client,
            "project_replace_device",
            node_id=node_id,
            template_node_id=template,
        )
        assert replaced["errors"] == []
        assert project.find_device_by_node_id(node_id) is None
        await _error(
            client,
            "project_replace_device",
            "cannot replace itself",
            node_id=template,
            template_node_id=template,
        )

        shifted = await _data(
            client, "project_shift_individual_addresses", node_ids=[template], offset=5
        )
        assert shifted == {"changed": 1, "errors": []}
        assert project.find_device_by_node_id(template).individual_address == "1.1.25"
        clash = await _data(
            client,
            "project_shift_individual_addresses",
            node_ids=[template],
            offset=300,
        )
        assert clash["changed"] == 0
        assert clash["errors"]


async def test_group_addresses_shift_create_and_mass_link(tmp_path: Path) -> None:
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        ga = (await _data(client, "project_create_group_address", address="1/1/1"))[
            "group_address_id"
        ]
        shifted = await _data(
            client, "project_shift_group_addresses", group_address_ids=[ga], offset=4
        )
        assert shifted == {"changed": 1, "errors": []}
        assert project.get_group_address(ga).address == "1/1/5"

        source = await _device(client, "1.1.30")
        target = await _device(client, "1.1.31")
        objects = [
            co["ref_id"]
            for co in (await _data(client, "project_list_com_objects", node_id=source))[
                "items"
            ]
            if co["linkable"]
        ]
        created = await _data(
            client,
            "project_create_group_addresses_for_com_objects",
            node_id=source,
            com_object_ref_ids=objects[:2],
            start_address="2/0/1",
            name_template="Light {n}",
        )
        assert created["created"] == 2
        names = {
            project.get_group_address(g).name for g in created["group_address_ids"]
        }
        assert names == {"Light 1", "Light 2"}
        await _error(
            client,
            "project_create_group_addresses_for_com_objects",
            "no visible group objects",
            node_id=source,
            com_object_ref_ids=["nope"],
        )

        linked = await _data(
            client,
            "project_link_com_objects",
            pairs=[
                {
                    "source_node_id": source,
                    "source_com_object_ref_id": objects[2],
                    "target_node_id": target,
                    "target_com_object_ref_id": objects[2],
                    "address": "3/0/1",
                    "name": "Pair",
                }
            ],
        )
        assert linked == {"linked": 1, "errors": []}
        links = (await _data(client, "project_list_links"))["items"]
        pair = {
            link["node_id"]
            for link in links
            if project.get_group_address(link["group_address_id"]).address == "3/0/1"
        }
        assert pair == {source, target}
        await _error(
            client,
            "project_link_com_objects",
            "needs source_node_id",
            pairs=[{"source_node_id": source}],
        )


async def test_history_steps_carry_their_time(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    mcp, _project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _device(client, "1.1.40")
        steps = (await _data(client, "project_history"))["items"]
        assert steps
        for step in steps:
            assert step["timestamp"].endswith("Z")
            moment = datetime.fromisoformat(step["timestamp"])
            assert abs(datetime.now(UTC) - moment) < timedelta(minutes=5)
        newest = steps[0]["timestamp"]
        since = await _data(client, "project_history", since=newest)
        assert since["count"] >= 1
        assert all(s["timestamp"] >= newest for s in since["items"])
        later = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
        none = await _data(client, "project_history", since=later.isoformat())
        assert none["count"] == 0
        await _error(client, "project_history", "no ISO 8601", since="yesterday")


async def test_compare_projects_lists_what_changed_in_a_device(tmp_path: Path) -> None:
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "old.xknx"))
        node_id = await _device(client, "1.1.40")
        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        enum = next(
            p
            for p in params
            if p["widget"].get("type") == "enum"
            and p["widget"].get("choice_count", 0) > 1
            and p.get("access") == "READ_WRITE"
        )
        full = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=enum["ref_id"],
        )
        value = next(
            str(c["value"])
            for c in full["widget"]["choices"]
            if str(c["value"]) != str(enum["value"])
        )
        # Continue in a copy, so the old file keeps the device as it was.
        assert project.save_as(tmp_path / "new.xknx") is not None
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=enum["ref_id"],
            value=value,
        )
        await _data(
            client, "project_set_individual_address", node_id=node_id, address="1.1.41"
        )
        result = await _data(
            client, "project_compare_projects", path=str(tmp_path / "old.xknx")
        )
    # One device: the same one with another address, not one removed and one added.
    (device,) = result["devices"]
    assert (device["key"], device["kind"]) == ("1.1.41", "changed")
    assert "address '1.1.41' -> '1.1.40'" in device["detail"]
    labels = [p["label"] for p in device["parameters"] if p["kind"] == "changed"]
    assert any(enum["label"] in label for label in labels)


async def test_compare_projects(tmp_path: Path) -> None:
    mcp, _project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _device(client, "1.1.40")
        other = tmp_path / "other.xknx"
        missing = await _data(client, "project_compare_projects", path=str(other))
        assert missing["error"]
        assert missing["needs_password"] is False
        same = await _data(
            client, "project_compare_projects", path=str(tmp_path / "p.xknx")
        )
        assert same == {"devices": [], "group_addresses": []}


async def test_plugin_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui import plugin_params

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _error(
            client,
            "project_plugin_parameters",
            "no plugin parameters",
            node_id=node_id,
        )

        # The fixture's application has no plugin; any mapping groups its parameters.
        mapping = plugin_params.mapping_for("M-0008_A-C030-21-AA50-O000A")
        monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: mapping)
        listed = await _data(client, "project_plugin_parameters", node_id=node_id)
        assert listed["title"] == mapping.title
        parameter = next(
            p
            for s in listed["sections"]
            for p in s["parameters"]
            if p["label"].startswith("Delay after bus voltage return")
        )
        assert parameter["writable"]
        other = "5"
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node_id,
            ref_id=parameter["ref_id"],
            value=other,
        )
        assert result == {
            "ref_id": parameter["ref_id"],
            "value": other,
            "changed": True,
        }
        assert project.find_device_by_node_id(node_id) is not None
        await _error(
            client,
            "project_set_plugin_parameter",
            "no plugin parameter",
            node_id=node_id,
            ref_id="nope",
            value="1",
        )
        section = listed["sections"][0]["section"]
        await _error(
            client,
            "project_add_plugin_item",
            "is not a list",
            node_id=node_id,
            section=section,
        )
        await _error(
            client,
            "project_remove_plugin_item",
            "no plugin parameter section",
            node_id=node_id,
            section="nope",
            item_id="1",
        )


async def test_the_actions_need_the_project_plugin(tmp_path: Path) -> None:
    mcp, _project = _server(tmp_path, with_plugin=False)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _error(
            client,
            "project_shift_group_addresses",
            "not available",
            group_address_ids=[],
            offset=1,
        )
