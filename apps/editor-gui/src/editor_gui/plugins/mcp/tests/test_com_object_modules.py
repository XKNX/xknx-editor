"""Module discovery and KO filtering through the public MCP tools."""

from pathlib import Path

from fastmcp import Client

from .test_lookup_filters import _project_with_device
from .test_server import _build, _data


async def test_modules_are_discoverable_and_filter_group_objects(
    tmp_path: Path,
) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _project_with_device(client, tmp_path)
        modules = await _data(
            client, "project_list_com_object_modules", node_id=node_id
        )
        assert [item["name"] for item in modules["items"]] == [
            "Channel 1 (...)",
            "Channel 2 (...)",
        ]
        second = modules["items"][1]["module_id"]
        assert [item["parent_id"] for item in modules["items"]] == [None, None]
        selected = await _data(
            client,
            "project_list_com_objects",
            node_id=node_id,
            module_ids=[second],
            compact=True,
        )
        assert [item["number"] for item in selected["items"]] == [262, 261]
        none = await _data(
            client, "project_list_com_objects", node_id=node_id, module_ids=[]
        )
        assert none == {"items": [], "count": 0}
        all_objects = await _data(client, "project_list_com_objects", node_id=node_id)
        assert all_objects["count"] == 4
        by_address = await _data(
            client, "project_list_com_object_modules", device_address="1.1.1"
        )
        assert by_address == modules
        combined = await _data(
            client,
            "project_list_com_objects",
            device_address="1.1.1",
            module_ids=[item["module_id"] for item in modules["items"]],
            search="K 2",
        )
        assert [item["number"] for item in combined["items"]] == [262, 261]
        without = await _data(
            client, "project_list_com_objects", node_id=node_id, module_ids=[""]
        )
        assert without == {"items": [], "count": 0}
