"""project_link_graph: which devices send on and receive from each group address, as the Bus
Diagram's Links view shows it, for the whole project or one part of it."""

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


async def _device(
    client: Client, product_ref_id: str, **flags: bool
) -> tuple[int, str]:
    """A new device and its first linkable group object, with ``flags`` set on it."""
    node_id = (
        await _data(client, "project_add_device", product_ref_id=product_ref_id)
    )["node_id"]
    objects = (await _data(client, "project_list_com_objects", node_id=node_id))[
        "items"
    ]
    ref_id = next(o for o in objects if o["linkable"])["ref_id"]
    for flag, value in flags.items():
        await _data(
            client,
            "project_set_com_object_flag",
            node_id=node_id,
            com_object_ref_id=ref_id,
            flag=flag,
            value=value,
        )
    return node_id, ref_id


async def _link(
    client: Client, device: tuple[int, str], address: str, *, sending: bool = False
) -> None:
    await _data(
        client,
        "project_link_com_object",
        node_id=device[0],
        com_object_ref_id=device[1],
        group_address=address,
        is_sending=sending,
    )


def _shown(graph: dict[str, Any]) -> list[tuple[str, list[int], list[int]]]:
    return [
        (
            item["group_address"],
            [d["node_id"] for d in item["senders"]],
            [d["node_id"] for d in item["receivers"]],
        )
        for item in graph["items"]
    ]


async def test_senders_and_receivers_per_group_address(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        sender = await _device(
            client,
            product,
            communication=True,
            transmit=True,
            write=False,
            update=False,
        )
        receiver = await _device(
            client, product, communication=True, transmit=False, write=True
        )
        # Communication off: the device takes part nowhere.
        silent = await _device(
            client, product, communication=False, transmit=True, write=True
        )
        for address in ("1/1/1", "1/1/2", "2/1/1"):
            await _data(client, "project_create_group_address", address=address)
        await _link(client, sender, "1/1/2", sending=True)
        await _link(client, receiver, "1/1/2")
        await _link(client, sender, "2/1/1", sending=True)
        await _link(client, receiver, "2/1/1")
        await _link(client, silent, "2/1/1", sending=True)
        # Only the receiver: one device links nothing.
        await _link(client, receiver, "1/1/1")

        whole = await _data(client, "project_link_graph")
        assert _shown(whole) == [
            ("1/1/2", [sender[0]], [receiver[0]]),
            ("2/1/1", [sender[0]], [receiver[0]]),
        ]
        first = whole["items"][0]["senders"][0]
        assert set(first) == {"node_id", "individual_address", "name"}

        assert _shown(
            await _data(client, "project_link_graph", node_id=receiver[0])
        ) == [
            ("1/1/2", [sender[0]], [receiver[0]]),
            ("2/1/1", [sender[0]], [receiver[0]]),
        ]
        assert _shown(await _data(client, "project_link_graph", main_group=2)) == [
            ("2/1/1", [sender[0]], [receiver[0]])
        ]
        # A single group address is shown also with one device on it.
        assert _shown(
            await _data(client, "project_link_graph", group_address="1/1/1")
        ) == [("1/1/1", [], [receiver[0]])]
        assert (await _data(client, "project_link_graph", node_id=silent[0]))[
            "count"
        ] == 0

        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        room = (
            await _data(
                client,
                "project_create_space",
                space_type="Room",
                name="Living",
                parent_id=building,
            )
        )["space_id"]
        assert (await _data(client, "project_link_graph", space_id=room))["count"] == 0
        await _data(
            client, "project_set_device_space", node_id=sender[0], space_id=room
        )
        assert [
            item["group_address"]
            for item in (await _data(client, "project_link_graph", space_id=room))[
                "items"
            ]
        ] == ["1/1/2", "2/1/1"]

        await _error(
            client,
            "project_link_graph",
            "at most one scope",
            node_id=sender[0],
            main_group=1,
        )
        await _error(client, "project_link_graph", "no space", space_id=room + 1000)
        await _error(client, "project_link_graph", "no device", node_id=10_000)
