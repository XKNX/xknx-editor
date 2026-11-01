"""The Operations panel through MCP: what runs, waits and has ended, the bus operations without a
queue entry, and the panel's actions (cancel, Cancel All, Force stop, the overwrite question,
Clear History)."""

from __future__ import annotations

import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, cast

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import ProgramQueue, QueueItem, ScriptRun

from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


async def _devices(client: Client, tmp_path: Path, *addresses: str) -> list[int]:
    await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
    await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
    product = (await _data(client, "catalog_list_products"))["items"][0]
    node_ids: list[int] = []
    for address in addresses:
        node_id = (
            await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
        )["node_id"]
        await _data(
            client, "project_set_individual_address", node_id=node_id, address=address
        )
        node_ids.append(node_id)
    return node_ids


def _setup(tmp_path: Path) -> tuple[Any, Any, ProjectPlugin, dict[int, Future[Any]]]:
    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    started: dict[int, Future[Any]] = {}

    def start(item: QueueItem) -> Future[Any]:
        started[item.node_id] = Future()
        return started[item.node_id]

    plugin._program_queue = ProgramQueue(is_busy=lambda: False, start=start)  # pyright: ignore[reportPrivateUsage]
    return ctx, build_server(ctx), plugin, started


def _item(node_id: int, address: str, **fields: Any) -> QueueItem:
    return QueueItem(
        node_id=node_id, address=address, name="Dimmer", scope=None, **fields
    )


async def test_operations_show_the_queue_and_the_bus_operation(tmp_path: Path) -> None:
    ctx, mcp, plugin, started = _setup(tmp_path)
    async with Client(mcp) as client:
        first, second = await _devices(client, tmp_path, "1.1.5", "1.1.6")
        queue = plugin.program_queue
        queue.enqueue(_item(first, "1.1.5"))
        queue.enqueue(_item(second, "1.1.6", origin="MCP"))
        state = await _data(client, "connection_operations")
        assert state["bus_operation"] is None
        assert [(e["device_address"], e["state"]) for e in state["active"]] == [
            ("1.1.5", "running"),
            ("1.1.6", "waiting"),
        ]
        assert state["active"][0]["operation"] == "Download (Full)"
        assert state["active"][1]["origin"] == "MCP"
        assert state["history"] == []

        # The running download ends: it moves to the history, the next one runs.
        started[first].set_result(None)
        state = await _data(client, "connection_operations")
        assert [e["state"] for e in state["active"]] == ["running"]
        assert [(e["device_address"], e["state"]) for e in state["history"]] == [
            ("1.1.5", "finished")
        ]
        assert "history" not in await _data(
            client, "connection_operations", history=False
        )

        # A bus operation without a queue entry, with its progress.
        started[second].set_result(None)
        connection = ctx.api.connection
        assert connection.begin_operation("recover", "1.0.1..1.0.20")
        connection._on_program_progress(3, 20)  # pyright: ignore[reportPrivateUsage]
        state = await _data(client, "connection_operations")
        assert state["bus_operation"] == {
            "kind": "recover",
            "address": "1.0.1..1.0.20",
            "text": "Recover Project from Bus 1.0.1..1.0.20",
            "progress": [3, 20],
        }
        connection.end_operation()

        assert await _data(client, "connection_clear_operations_history") == {
            "cleared": 2
        }
        assert (await _data(client, "connection_operations"))["history"] == []


async def test_cancel_operations_of_devices_or_all(tmp_path: Path) -> None:
    _ctx, mcp, plugin, _started = _setup(tmp_path)
    async with Client(mcp) as client:
        first, second, third = await _devices(
            client, tmp_path, "1.1.5", "1.1.6", "1.1.7"
        )
        queue = plugin.program_queue
        for node_id, address in ((first, "1.1.5"), (second, "1.1.6"), (third, "1.1.7")):
            queue.enqueue(_item(node_id, address))
        # A waiting one by its address ends at once.
        assert await _data(
            client, "connection_cancel_operations", device_addresses=["1.1.6"]
        ) == {"canceled": 1}
        history = (await _data(client, "connection_operations"))["history"]
        assert [(e["device_address"], e["state"]) for e in history] == [
            ("1.1.6", "canceled")
        ]
        # Cancel All: the waiting one ends, the running one is stopped.
        assert await _data(client, "connection_cancel_operations") == {"canceled": 2}
        state = await _data(client, "connection_operations")
        assert [e["state"] for e in state["history"]][:1] == ["canceled"]
        assert all(e["canceled_by_user"] for e in state["history"])
        # Nothing is left to cancel.
        assert await _data(
            client, "connection_cancel_operations", node_ids=[first]
        ) == {"canceled": 0}


async def test_answer_overwrite_question(tmp_path: Path) -> None:
    _ctx, mcp, plugin, _started = _setup(tmp_path)
    async with Client(mcp) as client:
        (node_id,) = await _devices(client, tmp_path, "1.1.5")
        with pytest.raises(Exception, match="no download of this device is waiting"):
            await client.call_tool(
                "connection_answer_overwrite", {"overwrite": True, "node_id": node_id}
            )
        item = _item(node_id, "1.1.5")
        plugin.program_queue.enqueue(item)
        question: Future[bool] = Future()
        item.full_download_question = question
        entry = (await _data(client, "connection_operations"))["active"][0]
        assert entry["awaiting_overwrite_answer"] is True
        assert entry["state_text"] == "Waiting for confirmation"
        assert await _data(
            client,
            "connection_answer_overwrite",
            overwrite=False,
            device_address="1.1.5",
        ) == {"answered": True}
        assert question.result() is False


async def test_force_stop_a_script_that_ignores_its_cancel(tmp_path: Path) -> None:
    _ctx, mcp, plugin, _started = _setup(tmp_path)
    async with Client(mcp) as client:
        (node_id,) = await _devices(client, tmp_path, "1.1.5")
        run = ScriptRun(button=cast(Any, None), progress=40.0, text="Reading")
        plugin.program_queue.enqueue(_item(node_id, "1.1.5", script=run))
        entry = (await _data(client, "connection_operations"))["active"][0]
        assert (entry["progress_percent"], entry["script_text"]) == (40.0, "Reading")
        assert entry["force_stop_available"] is False
        with pytest.raises(Exception, match="not a canceled parameter script"):
            await client.call_tool(
                "connection_cancel_operations", {"node_ids": [node_id], "force": True}
            )
        # A cancel asks the script to stop; a second one does nothing.
        for expected in (1, 0):
            assert await _data(
                client, "connection_cancel_operations", node_ids=[node_id]
            ) == {"canceled": expected}
        assert run.canceled
        with pytest.raises(Exception, match="ignored its cancel for 10 s"):
            await client.call_tool(
                "connection_cancel_operations", {"node_ids": [node_id], "force": True}
            )
        run.cancel_requested_at = time.monotonic() - 11
        entry = (await _data(client, "connection_operations"))["active"][0]
        assert entry["force_stop_available"] is True
        assert await _data(
            client, "connection_cancel_operations", node_ids=[node_id], force=True
        ) == {"canceled": 1}
        assert run.abort.requested


async def test_read_device_info_runs_in_the_queue_and_returns_its_rows(
    tmp_path: Path,
) -> None:
    from editor_gui.plugins.project.device_info_view import Toned

    _ctx, mcp, plugin, _started = _setup(tmp_path)
    read: list[QueueItem] = []

    def start(item: QueueItem) -> Future[Any]:
        read.append(item)
        future: Future[Any] = Future()
        future.set_result("info")
        return future

    def show(item: QueueItem, _result: Any) -> None:
        item.details = [
            (
                "Group Communication",
                [("Obj#45", Toned("-", "differs")), ("Obj#133 (1 bit)", "3/0/32")],
            )
        ]

    plugin._program_queue = ProgramQueue(  # pyright: ignore[reportPrivateUsage]
        is_busy=lambda: False, start=start, on_finished=show
    )
    async with Client(mcp) as client:
        (node_id,) = await _devices(client, tmp_path, "1.1.47")
        result = await _data(
            client, "connection_read_device_info", device_address="1.1.47"
        )
    ((item),) = read
    assert (item.node_id, item.device_info, item.origin) == (node_id, True, "MCP")
    assert result["state"] == "finished"
    assert result["details"] == [
        {
            "section": "Group Communication",
            "rows": [
                {"label": "Obj#45", "value": "-", "tone": "differs"},
                {"label": "Obj#133 (1 bit)", "value": "3/0/32"},
            ],
        }
    ]
