"""DALI-2 tools use shared operations and the connection write guard."""

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.tests.test_siemens_dali2 import APP, inventory

from .test_server import _build_ctx, _data


def setup(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    gateway = SimpleNamespace(
        node_id=7, individual_address="1.1.20", app=SimpleNamespace(id=APP)
    )
    project = ctx.api.project
    monkeypatch.setattr(project, "find_device_by_node_id", lambda _: gateway)
    monkeypatch.setattr(type(project), "devices", property(lambda _: [gateway]))
    monkeypatch.setattr(ctx.api.connection, "can_reach_bus", lambda: True)
    commissioner = SimpleNamespace(
        read_cached=AsyncMock(return_value=inventory()),
        discover=AsyncMock(return_value=inventory()),
        identify=AsyncMock(),
        identify_legacy=AsyncMock(),
        change_short_address=AsyncMock(return_value=inventory()),
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    calls = []

    def run(device, op, *, writes=False):
        calls.append((device, op, writes))
        future = Future()
        try:
            future.set_result(asyncio.run(op(object())))
        except Exception as exc:
            future.set_exception(exc)
        return future

    monkeypatch.setattr(ctx.api.connection, "run_siemens_dali", run)
    return ctx, gateway, commissioner, calls


async def test_modern_tools_run_shared_operations_and_expose_inventory(
    tmp_path, monkeypatch
):
    ctx, gateway, commissioner, calls = setup(tmp_path, monkeypatch)
    async with Client(build_server(ctx)) as client:
        read = await _data(client, "connection_siemens_dali2_read", node_id=7)
        assert read["available"] and read["devices"][0]["short_address"] == 7
        assert read["devices"][0]["instances"] == [
            {"number": 3, "type": 4, "resolution": 12}
        ]
        assert read["raw_hex"] == "0801"
        await _data(
            client,
            "connection_siemens_dali2_discover",
            device_address="1.1.20",
            line=1,
            sensors=True,
        )
        await _data(
            client,
            "connection_siemens_dali2_identify",
            node_id=7,
            address=7,
            action="stop",
        )
        commissioner.identify.assert_awaited_once_with(
            7, sensors=False, start=False, mode=0, timeout=15
        )
        await _data(
            client,
            "connection_siemens_dali2_address",
            node_id=7,
            address=7,
            new_address=12,
            random_address=0x123456,
        )
        commissioner.change_short_address.assert_awaited_once_with(
            7, 12, 0x123456, sensors=False, timeout=300
        )
        assert [c[2] for c in calls] == [False, True, True, True]
        assert all(c[0] is gateway for c in calls)
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["connection_siemens_dali2_read"].annotations.read_only_hint
        assert tools["connection_siemens_dali2_address"].annotations.destructive_hint


async def test_invalid_inputs_fail_before_starting_bus_operation(tmp_path, monkeypatch):
    ctx, gateway, _, calls = setup(tmp_path, monkeypatch)
    async with Client(build_server(ctx)) as client:
        for tool, args in (
            ("read", {"line": 2}),
            ("discover", {"timeout": 0}),
            ("identify", {"address": 64}),
            ("address", {"address": 7, "new_address": 7, "random_address": 1}),
        ):
            with pytest.raises(ToolError):
                await _data(
                    client, "connection_siemens_dali2_" + tool, node_id=7, **args
                )
        gateway.app.id = "M-0001_A-9837-02-ABCD"
        with pytest.raises(ToolError, match="Unsupported"):
            await _data(client, "connection_siemens_dali2_read", node_id=7)
        assert not calls


async def test_version_one_reads_both_lines_and_identifies_through_direct_frames(
    tmp_path, monkeypatch
):
    ctx, gateway, commissioner, calls = setup(tmp_path, monkeypatch)
    gateway.app.id = "M-0001_A-9A1C-01-33CF"
    async with Client(build_server(ctx)) as client:
        for line in (0, 1):
            result = await _data(
                client,
                "connection_siemens_dali2_read",
                device_address="1.1.20",
                line=line,
            )
            assert result["available"]
        await _data(client, "connection_siemens_dali2_identify", node_id=7, address=7)
    assert [call[1].line for call in calls[:2]] == [0, 1]
    commissioner.identify.assert_not_awaited()
    commissioner.identify_legacy.assert_awaited_once()
    assert commissioner.identify_legacy.await_args.kwargs["lines"] == 2


async def test_unavailable_cache_is_not_an_empty_success(tmp_path, monkeypatch):
    ctx, _, commissioner, _ = setup(tmp_path, monkeypatch)
    commissioner.read_cached.return_value = None
    async with Client(build_server(ctx)) as client:
        result = await _data(client, "connection_siemens_dali2_read", node_id=7)
        assert result == {"node_id": 7, "available": False, "devices": []}


async def test_queue_receives_write_classification_and_shared_plan(
    tmp_path, monkeypatch
):
    ctx, _, _, calls = setup(tmp_path, monkeypatch)
    queued = []

    def queue(ctx, node_id, kind, timeout, **options):
        queued.append((node_id, kind, options))
        return SimpleNamespace(result=inventory())

    monkeypatch.setattr(
        "editor_gui.plugins.mcp.tools.siemens_dali2.run_queued_action", queue
    )
    async with Client(build_server(ctx)) as client:
        await _data(client, "connection_siemens_dali2_discover", node_id=7)
        await _data(client, "connection_siemens_dali2_read", node_id=7)
    assert not calls
    assert [q[2]["writes"] for q in queued] == [True, False]
    assert all(q[1] == "siemens_dali" and q[2]["op"].line == 0 for q in queued)


async def test_mcp_stop_waits_for_gui_session_cleanup_without_queuing_another_operation(
    tmp_path, monkeypatch
):
    ctx, gateway, _, calls = setup(tmp_path, monkeypatch)
    gateway.app.id = "M-0001_A-9A1C-01-33CF"
    control = ctx.api.connection.siemens_dali2_identification
    target = (ctx.api.project.generation, 7, gateway.app.id, "1.1.20", 1, True)
    session = control.start(target)
    async with Client(build_server(ctx)) as client:
        task = asyncio.create_task(
            _data(
                client,
                "connection_siemens_dali2_identify",
                node_id=7,
                address=7,
                action="stop",
                line=1,
                sensors=True,
            )
        )
        for _ in range(200):
            if session.stop.is_set():
                break
            await asyncio.sleep(0.005)
        assert session.stop.is_set() and not task.done() and not calls
        control.finish(target, session)
        result = await task
        assert result["action"] == "stop"
    assert not calls


async def test_legacy_sensor_modes_and_duration_rejected_before_bus(
    tmp_path, monkeypatch
):
    ctx, gateway, _, calls = setup(tmp_path, monkeypatch)
    gateway.app.id = "M-0001_A-9A1C-01-33CF"
    async with Client(build_server(ctx)) as client:
        for args in (
            {"sensors": True, "action": "on"},
            {"sensors": True, "action": "off"},
            {"duration": 0},
            {"duration": 301},
        ):
            with pytest.raises(ToolError):
                await _data(
                    client,
                    "connection_siemens_dali2_identify",
                    node_id=7,
                    address=7,
                    **args,
                )
    assert not calls
