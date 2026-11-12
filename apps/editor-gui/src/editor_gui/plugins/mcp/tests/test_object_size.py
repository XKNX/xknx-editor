"""The group object size the MCP tools give is the text the Group Objects tab shows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.device import ComObject, Device
from editor_gui.plugins.mcp.server import build_server
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


async def test_variable_length_objects_read_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variable-length group object (size LegacyVarData in the product data) reads "Variable"
    in the object lists, a single object and the declared objects, as the GUI shows it; other
    sizes stay as they are."""
    visible = Device.get_visible_com_objects
    declared = DynamicUI.declared_com_objects

    def variable_first(self: Device) -> list[ComObject]:
        objects = visible(self)
        objects[0].object_size = "LegacyVarData"
        return objects

    def declared_variable_first(self: DynamicUI) -> list[dict[str, object]]:
        items = declared(self)
        items[0]["object_size"] = "LegacyVarData"
        return items

    monkeypatch.setattr(Device, "get_visible_com_objects", variable_first)
    monkeypatch.setattr(DynamicUI, "declared_com_objects", declared_variable_first)
    ctx = _build_ctx(tmp_path)
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_product", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        node_id = (
            await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
        )["node_id"]

        items: list[dict[str, Any]] = (
            await _data(client, "project_list_com_objects", node_id=node_id)
        )["items"]
        assert len(items) > 1
        assert items[0]["object_size"] == "Variable"
        assert all(item["object_size"] != "LegacyVarData" for item in items)
        assert items[1]["object_size"].endswith(("Bit", "Bits", "Byte", "Bytes"))

        single = await _data(
            client,
            "project_get_com_object",
            node_id=node_id,
            com_object_ref_id=items[0]["ref_id"],
        )
        assert single["object_size"] == "Variable"

        potential = (
            await _data(client, "find_potential_com_objects", node_id=node_id)
        )["items"]
        assert potential[0]["object_size"] == "Variable"
        assert all(item["object_size"] != "LegacyVarData" for item in potential)
