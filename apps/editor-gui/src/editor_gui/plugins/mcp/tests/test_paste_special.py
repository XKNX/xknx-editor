"""project_paste_special: copies as a drop with Ctrl held makes them, with the options of the
Paste Special dialog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from .test_server import _FIXTURE, _build, _data

_needs_fixture = pytest.mark.skipif(
    not _FIXTURE.exists(), reason="knxprod fixture missing"
)


async def _error(client: Client, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool("project_paste_special", args)


@_needs_fixture
async def test_devices_are_copied_by_their_address(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        node_id = (
            await _data(
                client,
                "project_add_device",
                product_ref_id=product["product_ref_id"],
                name="Switch",
            )
        )["node_id"]
        address = (await _data(client, "project_get_device", node_id=node_id))[
            "individual_address"
        ]
        segment = project.device_segments()[node_id]

        pasted = await _data(
            client,
            "project_paste_special",
            kind="device",
            device_addresses=[address],
            target_kind="segment",
            target_id=segment,
            copies=2,
            links="keep",
        )

        assert len(pasted["created"]) == 2
        assert pasted["label"].startswith("Copied 'Switch' to ")
        assert len(project.devices) == 3
        await _data(client, "project_undo")
        assert len(project.devices) == 1
        await _error(
            client,
            "copies must be",
            kind="device",
            ids=[node_id],
            target_kind="segment",
            target_id=segment,
            copies=0,
        )
        await _error(
            client,
            "no group for the new group addresses",
            kind="device",
            ids=[node_id],
            target_kind="segment",
            target_id=segment,
            links="create_new",
        )


async def test_group_addresses_are_copied_by_their_printed_address(
    tmp_path: Path,
) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(
            client, "project_create_group_address", address="1/1/1", name="Light"
        )
        group = project.get_group_range_tree()[0].children[0]

        pasted = await _data(
            client,
            "project_paste_special",
            kind="group_address",
            group_addresses=["1/1/1"],
            target_kind="group_range",
            target_id=group.id,
            allocation="start_with",
            start_with=20,
        )

        (copy,) = pasted["created"]
        info = project.get_group_address(copy)
        assert info is not None
        assert (info.address, info.name) == ("1/1/20", "Light")
        await _error(
            client,
            "pass ids",
            kind="group_address",
            target_kind="group_range",
            target_id=group.id,
        )
