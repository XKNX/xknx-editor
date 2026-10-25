"""MCP tools for removing and copying devices, building functions, group ranges, restarting and
resetting a device and commissioning a DALI gateway."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.strings import S as PROJECT_S
from xknxeditor.dali import DaliBusState
from xknxeditor.dali.model import GROUP_SINGLE, EcgState, GroupState

from .test_server import _FIXTURE, _build, _build_ctx, _data

_needs_fixture = pytest.mark.skipif(
    not _FIXTURE.exists(), reason="knxprod fixture missing"
)


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


async def _device(client: Client) -> int:
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    return (
        await _data(
            client, "project_add_device", product_ref_id=product["product_ref_id"]
        )
    )["node_id"]


def _done(result: Any = None) -> Future[Any]:
    future: Future[Any] = Future()
    future.set_result(result)
    return future


@_needs_fixture
async def test_copy_and_remove_devices(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)

        copies = (await _data(client, "project_copy_device", node_id=node_id, count=2))[
            "node_ids"
        ]
        assert len(copies) == 2
        assert len(project.devices) == 3
        await _error(
            client, "project_copy_device", "count must be", node_id=node_id, count=0
        )

        pasted = await _data(
            client,
            "project_copy_device_configuration",
            source_node_id=node_id,
            target_node_ids=copies,
        )
        assert pasted["count"] == 2
        assert all(item["ok"] for item in pasted["items"])

        removed = await _data(client, "project_remove_device", node_id=copies[0])
        assert removed == {"status": "removed", "node_id": copies[0]}
        assert len(project.devices) == 2
        await _data(client, "project_undo")
        assert len(project.devices) == 3
        await _error(client, "project_remove_device", "no device", node_id=999999)


async def test_building_functions_and_their_group_addresses(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        space_id = (
            await _data(
                client,
                "project_create_space",
                space_type="Room",
                name="Hall",
                parent_id=building,
            )
        )["space_id"]
        created = await _data(
            client, "project_create_function", space_id=space_id, name="Ceiling light"
        )
        function_id = created["function_id"]
        assert created["function_type"] == "FT-1"

        await _data(
            client,
            "project_set_function_type",
            function_id=function_id,
            function_type="FT-6",
        )
        ga_id = (await _data(client, "project_create_group_address", address="1/1/1"))[
            "group_address_id"
        ]
        link = await _data(
            client,
            "project_add_function_group_address",
            function_id=function_id,
            group_address_id=ga_id,
            role="Switch",
        )

        def _function(spaces: list[dict[str, Any]]) -> dict[str, Any] | None:
            for space in spaces:
                for function in space.get("functions", []):
                    if function["id"] == function_id:
                        return function
                found = _function(space.get("children", []))
                if found is not None:
                    return found
            return None

        function = _function((await _data(client, "project_spaces"))["items"])
        assert function is not None
        assert function["function_type"] == "FT-6"
        assert [g["group_address_id"] for g in function["group_addresses"]] == [ga_id]

        await _data(
            client, "project_remove_function_group_address", link_id=link["link_id"]
        )
        await _error(
            client,
            "project_remove_function_group_address",
            "no function group address",
            link_id=link["link_id"],
        )
        await _data(client, "project_remove_function", function_id=function_id)
        assert _function((await _data(client, "project_spaces"))["items"]) is None
        await _error(
            client, "project_remove_function", "no function", function_id=function_id
        )
        await _error(
            client,
            "project_create_function",
            "no space",
            space_id=999999,
            name="x",
        )


async def test_group_ranges_are_created_and_removed(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        main = await _data(client, "project_create_group_range", name="Lights")
        assert main["type"] == "main_group"
        middle = await _data(
            client,
            "project_create_group_range",
            name="Switching",
            parent_id=main["group_range_id"],
        )
        assert middle["type"] == "middle_group"
        assert middle["address"].startswith(f"{main['address']}/")

        await _data(
            client,
            "project_remove_group_range",
            group_range_id=main["group_range_id"],
        )
        assert project.element_details("group_range", middle["group_range_id"]) is None
        await _error(
            client,
            "project_remove_group_range",
            "no group range",
            group_range_id=main["group_range_id"],
        )
        await _error(
            client,
            "project_create_group_range",
            "no group range",
            name="x",
            parent_id=999999,
        )


@_needs_fixture
async def test_restart_and_master_reset_go_to_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _build_ctx(tmp_path)
    mcp = build_server(ctx)
    service = ctx.api.connection
    calls: list[tuple[str, Any]] = []
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _error(
            client, "connection_restart_device", "no KNX connection", node_id=node_id
        )

        monkeypatch.setattr(service, "_xknx", object())
        await _data(
            client, "project_set_individual_address", node_id=node_id, address="1.1.9"
        )
        monkeypatch.setattr(
            service,
            "restart_device",
            lambda device: calls.append(("restart", device.node_id)) or _done(),
        )
        monkeypatch.setattr(
            service,
            "master_reset_device",
            lambda device, code: calls.append(("reset", code)) or _done(),
        )
        recorded: list[int] = []
        monkeypatch.setattr(
            ctx.api.project,
            "record_factory_reset",
            lambda device: recorded.append(device.node_id),
        )
        assert (await _data(client, "connection_restart_device", node_id=node_id))[
            "status"
        ] == "restarted"
        await _data(
            client,
            "connection_master_reset_device",
            node_id=node_id,
            reset="RESET_PARAMETERS",
        )
        await _data(
            client,
            "connection_master_reset_device",
            node_id=node_id,
            reset="FACTORY_RESET",
        )
        assert calls == [("restart", node_id), ("reset", 5), ("reset", 2)]
        # Only a factory reset gives a secure device its certificate key back.
        assert recorded == [node_id]


class _Commissioner:
    """A stand-in for the DALI commissioner that records what the tools ask of it."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def _scan(self) -> DaliBusState:
        return DaliBusState(
            channel=0,
            firmware=(1, 2, 3),
            ecgs=[
                EcgState(slot=0, present=True, group_index=1, ets_ecg_index=0),
                EcgState(
                    slot=1, present=True, group_index=GROUP_SINGLE, ets_ecg_index=1
                ),
                EcgState(slot=2, present=False),
            ],
            groups=[GroupState(index=1, ecg_count=1)],
        )

    async def scan(self) -> DaliBusState:
        self.calls.append(("scan",))
        return self._scan()

    async def new_installation(self, *, on_progress: Any = None) -> DaliBusState:
        self.calls.append(("new",))
        return self._scan()

    async def blink_ecg(self, slot: int, start: bool) -> None:
        self.calls.append(("blink_ecg", slot, start))

    async def switch_group(self, group: int, on: bool) -> None:
        self.calls.append(("switch_group", group, on))

    async def broadcast(self, on: bool) -> None:
        self.calls.append(("broadcast", on))

    async def commission(self, assignments: list[Any]) -> bool:
        self.calls.append(
            ("commission", [(a.slot, a.ets_index, a.group_index) for a in assignments])
        )
        return True

    async def write_scenes(self, scenes: list[Any]) -> None:
        self.calls.append(
            (
                "scenes",
                [
                    (
                        s.index,
                        s.knx_scene,
                        [(v.target_index, v.level) for v in s.values],
                    )
                    for s in scenes
                ],
            )
        )


async def test_dali_commissioning_of_a_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    ctx = _build_ctx(tmp_path)
    mcp = build_server(ctx)
    service = ctx.api.connection
    project = ctx.api.project
    commissioner = _Commissioner()
    gateway = SimpleNamespace(
        node_id=7,
        individual_address="1.1.20",
        app=SimpleNamespace(id="M-0083_A-0153-11-ABCD"),
    )
    other = SimpleNamespace(
        node_id=8, individual_address="1.1.21", app=SimpleNamespace(id="M-0083_A-0001")
    )
    monkeypatch.setattr(service, "_xknx", object())
    monkeypatch.setattr(
        project,
        "find_device_by_node_id",
        lambda node_id: {7: gateway, 8: other}.get(node_id),
    )
    monkeypatch.setattr(
        project,
        "get_device_info",
        lambda node_id: SimpleNamespace(order_number="SCN-DA641.03"),
    )

    def run_dali(device: Any, op: Any, *, channel: int = 0) -> Future[Any]:
        return _done(asyncio.run(op(commissioner)))

    monkeypatch.setattr(service, "run_dali", run_dali)
    saved: list[tuple[Any, ...]] = []

    def persist(
        project: Any,
        device: Any,
        scan: DaliBusState,
        assignments: list[Any],
        scenes: list[Any],
        channel: int,
        *,
        label: str,
    ) -> None:
        saved.append(
            (
                device.node_id,
                [(a.slot, a.group_index) for a in assignments],
                len(scenes),
                channel,
            )
        )

    monkeypatch.setattr("editor_gui.programming_dali.persist_dali_backup", persist)

    async with Client(mcp) as client:
        await _error(
            client,
            "connection_dali_program_groups",
            "scan the channel first",
            node_id=7,
            groups=[],
        )
        scan = await _data(client, "connection_dali_scan", node_id=7)
        assert scan["firmware"] == "1.2.3"
        assert [(e["slot"], e["ecg_number"], e["group"]) for e in scan["ecgs"]] == [
            (0, 0, 1),
            (1, 1, "single"),
        ]
        assert scan["groups"][0]["index"] == 1
        await _error(
            client, "connection_dali_scan", "channel 0 to 0", node_id=7, channel=1
        )
        await _error(client, "connection_dali_scan", "not a DALI gateway", node_id=8)

        await _data(
            client, "connection_dali_identify", node_id=7, action="blink", slot=1
        )
        await _data(
            client, "connection_dali_identify", node_id=7, action="off", group=3
        )
        await _data(client, "connection_dali_identify", node_id=7, action="on")
        await _error(
            client,
            "connection_dali_identify",
            "blink needs a slot or a group",
            node_id=7,
            action="blink",
        )

        programmed = await _data(
            client,
            "connection_dali_program_groups",
            node_id=7,
            groups=[{"slot": 1, "group": 4}],
        )
        assert programmed == {
            "node_id": 7,
            "channel": 0,
            "programmed": 2,
            "saved": True,
        }
        await _error(
            client,
            "connection_dali_program_groups",
            "no ECG at slot",
            node_id=7,
            groups=[{"slot": 2, "group": 4}],
        )
        await _error(
            client,
            "connection_dali_program_groups",
            "group must be",
            node_id=7,
            groups=[{"slot": 1, "group": 16}],
        )

        scenes = await _data(
            client,
            "connection_dali_program_scenes",
            node_id=7,
            scenes=[{"scene": 2, "levels": [{"group": 4, "level": 128}]}],
        )
        assert scenes["programmed"] == 1
        installed = await _data(
            client, "connection_dali_installation", node_id=7, installation="new"
        )
        assert len(installed["ecgs"]) == 2

    assert ("blink_ecg", 1, True) in commissioner.calls
    assert ("switch_group", 3, False) in commissioner.calls
    assert ("broadcast", True) in commissioner.calls
    assert ("commission", [(0, 0, 1), (1, 1, 4)]) in commissioner.calls
    assert ("scenes", [(2, 3, [(4, 128)])]) in commissioner.calls
    assert ("new",) in commissioner.calls
    # Each groups/scenes write saves the whole channel: the new group, then the scenes too.
    assert saved == [(7, [(0, 1), (1, 4)], 0, 0), (7, [(0, 1), (1, 4)], 1, 0)]


async def test_a_function_moves_into_another_space(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        rooms = [
            (
                await _data(
                    client,
                    "project_create_space",
                    space_type="Room",
                    name=name,
                    parent_id=building,
                )
            )["space_id"]
            for name in ("Hall", "Kitchen")
        ]
        function_id = (
            await _data(
                client, "project_create_function", space_id=rooms[0], name="Light"
            )
        )["function_id"]
        moved = await _data(
            client, "project_move_function", function_id=function_id, space_id=rooms[1]
        )
        assert moved == {"function_id": function_id, "space_id": rooms[1]}
        children = (await _data(client, "project_spaces"))["items"][0]["children"]
        kitchen = next(c for c in children if c["id"] == rooms[1])
        assert [f["id"] for f in kitchen["functions"]] == [function_id]
        await _data(client, "project_undo")
        children = (await _data(client, "project_spaces"))["items"][0]["children"]
        hall = next(c for c in children if c["id"] == rooms[0])
        assert [f["id"] for f in hall["functions"]] == [function_id]
        with pytest.raises(Exception, match="no space with id"):
            await client.call_tool(
                "project_move_function", {"function_id": function_id, "space_id": 999}
            )


async def test_groups_and_group_addresses_are_added_as_the_add_dialog_does(
    tmp_path: Path,
) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        mains = await _data(
            client,
            "project_create_group_range",
            count=2,
            allocation="start_with",
            start_with=20,
        )
        assert [
            project.element_details("group_range", i).address  # type: ignore[union-attr]
            for i in mains["group_range_ids"]
        ] == ["20", "21"]
        assert mains["name"] == PROJECT_S.GA_DEFAULT_MAIN_GROUP
        middle = await _data(
            client,
            "project_create_group_range",
            parent_id=mains["group_range_id"],
            number=3,
            name="Blinds",
        )
        assert middle["address"] == "20/3"
        added = await _data(
            client,
            "project_add_group_addresses",
            group_range_id=middle["group_range_id"],
            count=3,
            allocation="start_with",
            start_with=10,
        )
        assert added["addresses"] == ["20/3/10", "20/3/11", "20/3/12"]
        await _data(client, "project_undo")
        listed = (await _data(client, "project_list_group_addresses"))["items"]
        assert "20/3/10" not in {ga["address"] for ga in listed}
        # A main group holds middle groups in three-level style, not group addresses.
        await _error(
            client,
            "project_add_group_addresses",
            "holds group addresses",
            group_range_id=mains["group_range_id"],
        )
        await _error(
            client,
            "project_create_group_range",
            "count 1",
            number=4,
            count=2,
        )
