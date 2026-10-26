# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""The Operations panel: running, waiting and ended bus operations, read and acted on as the
panel does (cancel, Cancel All, Force stop, the overwrite question, Clear History)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.plugins.mcp.tools.project import require_device

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.project.plugin import ProjectPlugin
    from editor_gui.plugins.project.program_queue import QueueItem


def item_dict(
    item: QueueItem, progress: tuple[int, int] | None, connecting: bool = False
) -> dict[str, Any]:
    """An entry of the Operations panel; ``progress`` is the running download's (done, total),
    ``connecting`` whether it still waits for the connection opened for it."""
    from editor_gui.plugins.project.program_queue import OperationState
    from editor_gui.plugins.project.ui.program_queue import (
        FORCE_STOP_AFTER,
        operation_text,
        state_text,
    )

    run = item.script
    question = item.full_download_question
    entry: dict[str, Any] = {
        "node_id": item.node_id,
        "device_address": item.address,
        "name": item.name,
        "operation": operation_text(item),
        "state": item.state.value,
        "state_text": state_text(item, connecting),
        "origin": item.origin,
        "message": item.message,
        "details": [
            {"section": title, "rows": [{"label": k, "value": v} for k, v in rows]}
            for title, rows in item.details
        ],
        "connection": item.connection,
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "ended_at": item.ended_at.isoformat() if item.ended_at else None,
        "canceled_by_user": item.canceled_by_user,
        "awaiting_overwrite_answer": question is not None and not question.done(),
    }
    if item.state is OperationState.RUNNING:
        if run is not None:
            entry["progress_percent"] = run.progress
            entry["script_text"] = run.text
            entry["force_stop_available"] = (
                run.canceled and run.ignored_cancel_for() >= FORCE_STOP_AFTER
            )
        elif progress is not None and progress[1] > 0:
            entry["progress"] = list(progress)
    return entry


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    def _plugin() -> ProjectPlugin:
        if ctx.project_plugin is None:
            raise ToolError("the Operations panel is not available here")
        return ctx.project_plugin

    def _active_of(
        plugin: ProjectPlugin, node_id: int | None, device_address: str | None
    ) -> list[QueueItem]:
        nid = require_device(ctx.api.project, node_id, device_address).node_id
        return [i for i in plugin.program_queue.active if i.node_id == nid]

    @tool("read")
    def connection_operations(history: bool = True) -> dict[str, Any]:
        """What the Operations panel shows. Returns {bus_operation, active, history}:
        ``bus_operation`` is the running bus operation without a queue entry (a device test,
        restart, reset, DALI or DCA commissioning, the programming mode scan, the project
        recovery) as {kind, address, text, progress?: [done, total]}, or null; ``active`` the
        running operation followed by the waiting ones, ``history`` the ended ones, newest first
        (left out with history=false). Each entry: {node_id, device_address, name, operation (e.g.
        "Download (Parameters)"), state (waiting, running, finished, canceled, failed),
        state_text (e.g. "Connecting" while a running one waits for the connection opened for
        it through the backbone's Bus Connection), origin ("MCP" when an MCP client started it), message (the error), details
        (the result sections of a device info), connection, started_at, ended_at,
        canceled_by_user, awaiting_overwrite_answer (a download asks whether to overwrite the
        application in the device: answer with connection_answer_overwrite), and while running
        progress [done, total] or, for a parameter script, progress_percent, script_text and
        force_stop_available}."""
        plugin = _plugin()

        def _read() -> dict[str, Any]:
            from editor_gui.plugins.project.ui.program_queue import bus_operation_text

            progress = ctx.api.connection.busy_progress
            operation = plugin.unqueued_bus_operation()
            bus: dict[str, Any] | None = None
            if operation is not None:
                kind, address = operation
                bus = {
                    "kind": kind,
                    "address": address,
                    "text": bus_operation_text(kind, address),
                }
                if progress is not None and progress[1] > 0:
                    bus["progress"] = list(progress)
            queue = plugin.program_queue
            result: dict[str, Any] = {
                "bus_operation": bus,
                "active": [
                    item_dict(
                        i,
                        progress,
                        ctx.api.connection.waiting_for_connection(i.future),
                    )
                    for i in queue.active
                ],
            }
            if history:
                result["history"] = [item_dict(i, None) for i in queue.history]
            return result

        return ctx.run_on_ui(_read)

    @tool("write", open_world=True)
    def connection_cancel_operations(
        node_ids: list[int] | None = None,
        device_addresses: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Cancel operations in the Operations panel as its cancel buttons do: those of the
        devices given by ``node_ids`` and/or ``device_addresses`` (individual addresses like
        "1.0.30"), or all of them as Cancel All does when neither is given. A waiting operation
        ends at once; a running download stops at its next bus exchange (the device then needs a
        new download); a parameter script is asked to stop. ``force`` is the panel's Force stop:
        it aborts a parameter script that has ignored its cancel for 10 s. Returns {canceled}:
        how many operations were canceled (or force-stopped)."""
        plugin = _plugin()

        def _cancel() -> dict[str, Any]:
            queue = plugin.program_queue
            if not node_ids and not device_addresses:
                items = queue.active
            else:
                items = [
                    item
                    for nid in node_ids or ()
                    for item in _active_of(plugin, nid, None)
                ] + [
                    item
                    for address in device_addresses or ()
                    for item in _active_of(plugin, None, address)
                ]
            count = 0
            for item in {id(i): i for i in items}.values():
                if force:
                    count += _force_stop(item)
                elif not item.canceled_by_user and not (
                    item.script is not None and item.script.canceled
                ):
                    queue.cancel_item(item)
                    count += 1
            return {"canceled": count}

        return ctx.run_locked(_cancel)

    def _force_stop(item: QueueItem) -> int:
        from editor_gui.plugins.project.ui.program_queue import FORCE_STOP_AFTER

        run = item.script
        if run is None or not run.canceled:
            raise ToolError(
                f"the operation of {item.address or item.node_id} is not a canceled parameter "
                "script: cancel it first (force=false)"
            )
        if run.ignored_cancel_for() < FORCE_STOP_AFTER:
            raise ToolError(
                f"Force stop is offered once a script has ignored its cancel for "
                f"{FORCE_STOP_AFTER:.0f} s; it has for {run.ignored_cancel_for():.0f} s"
            )
        run.abort.request()
        return 1

    @tool("destructive", open_world=True)
    def connection_answer_overwrite(
        overwrite: bool,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Answer the question a running download of the device (``node_id`` or
        ``device_address``) asks in the Operations panel when the device holds another
        application (awaiting_overwrite_answer in connection_operations): ``overwrite`` true
        is its Yes (a full download overwrites the application), false its No. Returns
        {answered: true}."""
        plugin = _plugin()

        def _answer() -> dict[str, Any]:
            for item in _active_of(plugin, node_id, device_address):
                question = item.full_download_question
                if question is not None and not question.done():
                    question.set_result(overwrite)
                    return {"answered": True}
            raise ToolError("no download of this device is waiting for an answer")

        return ctx.run_locked(_answer)

    @tool("write")
    def connection_clear_operations_history() -> dict[str, Any]:
        """Empty the History tab of the Operations panel, as its Clear History button does.
        Returns {cleared}: how many ended operations were removed."""
        plugin = _plugin()

        def _clear() -> dict[str, Any]:
            count = len(plugin.program_queue.history)
            plugin.program_queue.clear_history()
            return {"cleared": count}

        return ctx.run_on_ui(_clear)
