"""Hidden parameters through MCP: listed with their section, the edits that show one (as
project_batch operations), and a set on a hidden parameter that names those edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from .test_server import _FIXTURE, _build, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")

_APP = "M-0008_A-7072-21-5CC3-O000A"
# "Type of blind/shutter" of the first channel: shown once its "Function" is blind/shutter.
_BLIND_TYPE = f"{_APP}_MD-1_M-100_MI-1_P-361_R-577"
# A parameter no user ever sees (Access None).
_INTERNAL = f"{_APP}_P-1_R-2"


async def _device(client: Client, tmp_path: Path) -> int:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    return (
        await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
    )["node_id"]


async def _visible(client: Client, node_id: int) -> dict[str, dict[str, Any]]:
    items = (await _data(client, "project_list_parameters", node_id=node_id))["items"]
    return {item["ref_id"]: item for item in items}


async def test_hidden_parameters_are_listed_with_their_section(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        visible = await _visible(client, node_id)
        assert _BLIND_TYPE not in visible
        assert all("visible" not in item for item in visible.values())

        items = (
            await _data(
                client, "project_list_parameters", node_id=node_id, include_hidden=True
            )
        )["items"]
        by_ref = {item["ref_id"]: item for item in items}
        hidden = by_ref[_BLIND_TYPE]
        assert hidden["visible"] is False
        assert "Type of blind/shutter" in hidden["label"]
        assert hidden["section"]
        assert all(by_ref[ref]["visible"] is True for ref in visible)
        # Parameters no user ever sees are not listed, and each one is listed once.
        assert _INTERNAL not in by_ref
        assert len(by_ref) == len(items)

        found = (
            await _data(
                client,
                "project_list_parameters",
                node_id=node_id,
                include_hidden=True,
                search="type of blind",
                compact=True,
            )
        )["items"]
        assert found
        assert all(
            set(item) >= {"ref_id", "label", "visible", "section"} for item in found
        )


async def test_a_plan_shows_the_parameter_in_one_batch(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        plan = await _data(
            client,
            "project_plan_parameter",
            node_id=node_id,
            parameter_ref_id=_BLIND_TYPE,
        )
        assert [(s["label"].strip(), s["value"]) for s in plan["steps"]] == [
            ("Function", "2")
        ]
        step = plan["steps"][0]
        assert step["section"]
        assert step["current_value"] != "2"
        assert "value_label" in step
        assert plan["also_shown"] > 0
        # Planning changes nothing.
        assert _BLIND_TYPE not in await _visible(client, node_id)

        applied = await _data(client, "project_batch", operations=plan["operations"])
        assert applied["applied"] is True
        assert _BLIND_TYPE in await _visible(client, node_id)

        # Shown now: nothing left to do.
        again = await _data(
            client,
            "project_plan_parameter",
            node_id=node_id,
            parameter_ref_id=_BLIND_TYPE,
        )
        assert again["steps"] == [] and again["operations"] == []


async def test_a_plan_with_a_value_ends_with_setting_it(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        # Learn a choice other than the current one from the shown parameter, then go back.
        plan = await _data(
            client,
            "project_plan_parameter",
            node_id=node_id,
            parameter_ref_id=_BLIND_TYPE,
        )
        await _data(client, "project_batch", operations=plan["operations"])
        shown = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=_BLIND_TYPE,
        )
        other = next(
            c for c in shown["widget"]["choices"] if str(c["value"]) != shown["value"]
        )
        await _data(client, "project_undo")
        assert _BLIND_TYPE not in await _visible(client, node_id)

        plan = await _data(
            client,
            "project_plan_parameter",
            node_id=node_id,
            parameter_ref_id=_BLIND_TYPE,
            value_label=other["label"],
        )
        last = plan["operations"][-1]["params"]
        assert (last["parameter_ref_id"], last["value"]) == (
            _BLIND_TYPE,
            str(other["value"]),
        )
        await _data(client, "project_batch", operations=plan["operations"])
        assert (await _visible(client, node_id))[_BLIND_TYPE]["value"] == str(
            other["value"]
        )

        with pytest.raises(ToolError, match="no choice labelled"):
            await client.call_tool(
                "project_plan_parameter",
                {
                    "node_id": node_id,
                    "parameter_ref_id": _BLIND_TYPE,
                    "value_label": "no such choice",
                },
            )


async def test_setting_a_hidden_parameter_names_the_switches(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        with pytest.raises(ToolError) as raised:
            await client.call_tool(
                "project_set_parameter",
                {"node_id": node_id, "parameter_ref_id": _BLIND_TYPE, "value": "0"},
            )
        message = str(raised.value)
        assert "is not visible" in message
        assert "to show it, set first: " in message
        assert "Function" in message and "project_plan_parameter" in message


async def test_a_parameter_no_plan_shows(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        with pytest.raises(ToolError, match="never shown to users"):
            await client.call_tool(
                "project_plan_parameter",
                {"node_id": node_id, "parameter_ref_id": _INTERNAL},
            )
        with pytest.raises(ToolError, match="not a parameter of this device"):
            await client.call_tool(
                "project_plan_parameter",
                {"node_id": node_id, "parameter_ref_id": f"{_APP}_P-99999_R-99999"},
            )
