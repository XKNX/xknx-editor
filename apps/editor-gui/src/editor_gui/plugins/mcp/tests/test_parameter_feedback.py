"""After a parameter edit the result names what it showed and hid; linking a com-object the
device's parameters hide says so."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")

# The Gira push-button's "combine K1/K2" selector: 1 replaces the two single-key channels.
_COMBINE = "M-0008_A-7072-21-5CC3-O000A_P-106_R-153"


async def _device(client: Client, tmp_path: Path) -> int:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    return (
        await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
    )["node_id"]


async def _visible(client: Client, node_id: int) -> tuple[set[str], set[str]]:
    params = (await _data(client, "project_list_parameters", node_id=node_id))["items"]
    objects = (await _data(client, "project_list_com_objects", node_id=node_id))[
        "items"
    ]
    return {p["ref_id"] for p in params}, {co["ref_id"] for co in objects}


async def test_a_selector_change_names_what_it_shows_and_hides(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        params_before, objects_before = await _visible(client, node_id)
        # A link on an object the change hides is reported as lost.
        doomed = sorted(objects_before)[0]
        await _data(client, "project_create_group_address", address="0/0/1")
        await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=doomed,
            group_address="0/0/1",
        )

        result = await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=_COMBINE,
            value="1",
        )
        params_after, objects_after = await _visible(client, node_id)

        assert {p["ref_id"] for p in result["shown_parameters"]} == (
            params_after - params_before
        )
        assert set(result["hidden_parameters"]) == params_before - params_after
        assert {co["ref_id"] for co in result["shown_com_objects"]} == (
            objects_after - objects_before
        )
        assert set(result["hidden_com_objects"]) == objects_before - objects_after
        # The new object comes from the rebuilt device: instantiated, so it can be linked.
        assert all(co["linkable"] for co in result["shown_com_objects"])
        assert set(result["shown_parameters"][0]) <= {
            "ref_id",
            "label",
            "value",
            "value_label",
        }
        assert [link["com_object_ref_id"] for link in result["lost_links"]] == [doomed]

        # An edit that shows or hides nothing adds none of the keys.
        same = await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=_COMBINE,
            value="1",
        )
        assert not [k for k in same if k.startswith(("shown_", "hidden_"))]

        # The batch op reports the same.
        await _data(client, "project_undo")
        await _data(client, "project_undo")
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_parameter",
                    "params": {
                        "node_id": node_id,
                        "parameter_ref_id": _COMBINE,
                        "value": "1",
                    },
                }
            ],
        )
        op = batch["results"][0]["result"]
        assert set(op["hidden_com_objects"]) == set(result["hidden_com_objects"])
        assert {co["ref_id"] for co in op["shown_com_objects"]} == {
            co["ref_id"] for co in result["shown_com_objects"]
        }


async def test_linking_a_hidden_com_object_says_so(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        node_id = await _device(client, tmp_path)
        _params, objects = await _visible(client, node_id)
        await _data(client, "project_create_group_address", address="0/0/1")
        await _data(client, "project_create_group_address", address="0/0/2")

        def _link(ref: str, ga: str) -> Any:
            return _data(
                client,
                "project_link_com_object",
                node_id=node_id,
                com_object_ref_id=ref,
                group_address=ga,
            )

        visible, hidden = sorted(objects)[:2]
        assert "hidden" not in await _link(visible, "0/0/1")

        # An instantiated object the parameters hide (as an imported project can hold one).
        device = project.find_device_by_node_id(node_id)
        assert device is not None
        device._cached_visible_cos = [  # pyright: ignore[reportPrivateUsage]
            co for co in device.get_visible_com_objects() if co.id != hidden
        ]
        linked = await _link(hidden, "0/0/2")
        assert linked["hidden"] is True
        assert linked["already_linked"] is False
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "link_com_object",
                    "params": {
                        "node_id": node_id,
                        "com_object_ref_id": hidden,
                        "group_address": "0/0/2",
                    },
                }
            ],
        )
        assert batch["results"][0]["result"]["hidden"] is True
