"""Segments and the media of lines and segments as the MCP tools read and change them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data


async def _line(client: Client, address: str) -> dict[str, Any]:
    """The line "1.1" as project_topology lists it."""
    area_number, line_number = (int(part) for part in address.split("."))
    areas = (await _data(client, "project_topology"))["items"]
    return next(
        line
        for area in areas
        if area["area_number"] == area_number
        for line in area["lines"]
        if line["line_number"] == line_number
    )


async def test_segments_are_added_renamed_and_removed(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        line = await _line(client, "1.1")
        assert (line["medium"], line["segments"]) == ("TP", [])
        assert (await _line(client, "1.0"))["medium"] == "IP"

        tp = await _data(client, "project_add_segment", line_id=line["id"])
        assert (tp["number"], tp["name"], tp["medium"]) == (1, "segment", "TP")
        rf = await _data(
            client,
            "project_add_segment",
            line_id=line["id"],
            name="Garden",
            medium="RF",
        )
        assert rf["medium"] == "RF" and rf["domain_address"]
        renamed = await _data(
            client, "project_rename_segment", segment_id=tp["id"], name="Cellar"
        )
        assert renamed["name"] == "Cellar"
        line = await _line(client, "1.1")
        assert [s["name"] for s in line["segments"]] == ["Cellar", "Garden"]

        element = await _data(
            client, "project_get_element", kind="segment", element_id=rf["id"]
        )
        assert element["type"] == "segment"
        assert element["medium"] == "RF" and element["status"] == "Undefined"
        assert "address" not in element

        with pytest.raises(Exception, match="main segment cannot be deleted"):
            await client.call_tool(
                "project_remove_segment", {"segment_id": line["main_segment_id"]}
            )
        removed = await _data(client, "project_remove_segment", segment_id=tp["id"])
        assert removed == {"status": "removed", "segment_id": tp["id"]}
        with pytest.raises(Exception, match="no segment with id"):
            await client.call_tool("project_remove_segment", {"segment_id": tp["id"]})
        with pytest.raises(Exception, match="no line with id 999"):
            await client.call_tool("project_add_segment", {"line_id": 999})
        with pytest.raises(Exception, match="IoT"):
            await client.call_tool(
                "project_add_segment", {"line_id": line["id"], "medium": "IoT"}
            )


async def test_the_medium_follows_the_rules(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        main = await _line(client, "1.0")
        changed = await _data(
            client, "project_set_medium", line_id=main["id"], medium="PL"
        )
        assert changed == {
            "kind": "line",
            "element_id": main["id"],
            "medium": "PL",
            "domain_address": "1",
        }
        line = await _line(client, "1.1")
        segment = await _data(client, "project_add_segment", line_id=line["id"])
        with pytest.raises(Exception, match="no longer be changed to RF"):
            await client.call_tool(
                "project_set_medium", {"line_id": line["id"], "medium": "RF"}
            )
        rf = await _data(
            client, "project_set_medium", segment_id=segment["id"], medium="RF"
        )
        assert rf["medium"] == "RF" and ":" in rf["domain_address"]
        for args in ({"medium": "TP"}, {"medium": "TP", "line_id": 1, "segment_id": 1}):
            with pytest.raises(
                Exception, match="exactly one of line_id and segment_id"
            ):
                await client.call_tool("project_set_medium", args)
        with pytest.raises(Exception, match="no segment with id 999"):
            await client.call_tool(
                "project_set_medium", {"segment_id": 999, "medium": "TP"}
            )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_devices_move_onto_segments_of_their_medium(tmp_path: Path) -> None:
    mcp, _project_service = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        device = await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
        address = device["individual_address"]
        assert address.startswith("1.1.")
        line = await _line(client, "1.1")
        tp = await _data(client, "project_add_segment", line_id=line["id"])
        rf = await _data(client, "project_add_segment", line_id=line["id"], medium="RF")

        with pytest.raises(Exception, match="incompatible with segment's medium type"):
            await client.call_tool(
                "project_move_device_to_segment",
                {"segment_id": rf["id"], "device_address": address},
            )
        moved = await _data(
            client,
            "project_move_device_to_segment",
            segment_id=tp["id"],
            node_id=device["node_id"],
        )
        # Within its line, a device keeps its address.
        assert moved["individual_address"] == address
        with pytest.raises(Exception, match="only change while it holds no device"):
            await client.call_tool(
                "project_set_medium", {"segment_id": tp["id"], "medium": "RF"}
            )
        # Into the IP line 1.0 the TP device does not fit.
        with pytest.raises(Exception, match="incompatible with line's medium type"):
            await client.call_tool(
                "project_set_individual_address",
                {"node_id": device["node_id"], "address": "1.0.7"},
            )
        with pytest.raises(Exception, match="no segment with id 999"):
            await client.call_tool(
                "project_move_device_to_segment",
                {"segment_id": 999, "node_id": device["node_id"]},
            )
        back = await _data(
            client,
            "project_move_device_to_segment",
            segment_id=line["main_segment_id"],
            node_id=device["node_id"],
        )
        assert back["individual_address"] == address
