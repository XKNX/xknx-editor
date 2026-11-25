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


def _row(label: str, value: str) -> dict[str, Any]:
    """A row of a result section; a value the readout compares carries its ``tone``: "ok"
    (as the project has it) or "differs"."""
    row: dict[str, Any] = {"label": label, "value": str(value)}
    tone = getattr(value, "tone", None)
    if tone is not None:
        row["tone"] = tone
    return row


def item_dict(
    item: QueueItem,
    progress: tuple[int, int] | None,
    connecting: bool = False,
    memory_progress: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """An entry of the Operations panel; ``progress`` is the running download's (done, total),
    ``connecting`` whether it still waits for the connection opened for it,
    ``memory_progress`` the (done, total) octets its current load control wrote of its memory."""
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
            {"section": title, "rows": [_row(k, v) for k, v in rows]}
            for title, rows in item.details
        ],
        "connection": item.connection,
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "ended_at": item.ended_at.isoformat() if item.ended_at else None,
        "canceled_by_user": item.canceled_by_user,
        "awaiting_overwrite_answer": question is not None and not question.done(),
    }
    serial_question = item.serial_number_question
    if (
        serial_question is not None
        and not serial_question.done()
        and item.serial_number_asked is not None
    ):
        found, stored = item.serial_number_asked
        entry["awaiting_serial_number_answer"] = {
            "found": found.hex().upper(),
            "stored": stored.hex().upper(),
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
            if memory_progress is not None and memory_progress[1] > 0:
                entry["progress_bytes"] = list(memory_progress)
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
        ``bus_operation`` is the running bus operation without a queue entry (a restart, reset, DALI or DCA commissioning, the programming mode scan, the project
        recovery) as {kind, address, text, progress?: [done, total]}, or null; ``active`` the
        running operation followed by the waiting ones, ``history`` the ended ones, newest first
        (left out with history=false). Each entry: {node_id, device_address, name, operation (e.g.
        "Download (Parameters)"), state (waiting, running, finished, canceled, failed),
        state_text (e.g. "Connecting" while a running one waits for the connection opened for
        it through the backbone's Bus Connection), origin ("MCP" when an MCP client started it), message (the error), details
        (the result sections of a device info: [{section, rows: [{label, value, tone?}]}], tone
        "ok" or "differs" where the value is compared with the project), connection, started_at, ended_at,
        canceled_by_user, awaiting_overwrite_answer (a download asks whether to overwrite the
        application in the device: answer with connection_answer_overwrite), when asked
        awaiting_serial_number_answer {found, stored} (the secured device in programming mode has
        another serial number: answer with connection_answer_serial_number), and while running
        progress [done, total] (load controls) with progress_bytes [done, total] (octets the
        current control wrote of its memory, while it writes memory) or, for a parameter script, progress_percent, script_text and
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
                        ctx.api.connection.busy_memory_progress,
                    )
                    for i in queue.active
                ],
            }
            if history:
                result["history"] = [item_dict(i, None) for i in queue.history]
            return result

        return ctx.run_on_ui(_read)

    @tool("read", open_world=True)
    def connection_read_device_info(
        node_id: int | None = None,
        device_address: str | None = None,
        group_communication: bool = True,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Read what the device on the bus reports about itself, as "Read from Device" does.
        Identify the project device by ``node_id`` OR ``device_address`` (its individual address
        like "1.0.30"); pass exactly one. Needs an open connection. With ``group_communication``
        (default) it also reads the group address, association and group object tables and
        compares each linked object's group addresses with the project. It runs in the
        editor's operations queue, labelled MCP: it waits behind the operations already queued
        (the wait counts toward ``timeout`` seconds) and the user can cancel it. Writes nothing.

        Returns the ended operation as connection_operations lists it; its ``details`` are the
        readout's sections ("General", "Application Program", "Group Communication"), each
        {section, rows: [{label, value, tone?}]}; ``tone`` is "ok" or "differs" where the value
        is compared with the project. A group object row is labelled "Obj#<number> (<size>,
        <flags>)" with the device's group addresses as value; an object the project links but the
        device holds no group address for is labelled "Obj#<number>" with value "-"."""
        from editor_gui.plugins.mcp.tools.connection import finished, wait_for_operation
        from editor_gui.plugins.project.program_queue import ORIGIN_MCP

        plugin = _plugin()
        nid = require_device(ctx.api.project, node_id, device_address).node_id
        item = ctx.run_locked(
            lambda: plugin.queue_device_info(
                nid, group_communication, origin=ORIGIN_MCP
            )
        )
        if item is None or item.ended is None:
            raise ToolError(f"no device with node_id {nid}")
        ended = finished(wait_for_operation(ctx, item, timeout))
        return ctx.run_on_ui(lambda: item_dict(ended, None))

    @tool("read")
    def project_memory_segments(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """List the memory preview's segments without accessing the bus.

        Identify a device by node_id OR device_address. Returns segments with id,
        address (absolute address or object-relative offset), object_index (null
        for absolute memory), user_memory, size and planned bytes as hex. Use a
        segment id with connection_read_memory to read the same range from the device.
        """
        from editor_gui.memory_read import preview_segments

        def read() -> dict[str, Any]:
            project = ctx.api.project
            device = require_device(project, node_id, device_address)
            segments = preview_segments(device, project.prepared_download(device))
            return {
                "segments": [
                    {
                        "id": key,
                        "address": segment.request.address,
                        "object_index": segment.request.object_index,
                        "user_memory": segment.request.user_memory,
                        "size": len(segment.data),
                        "planned": segment.data.hex(),
                    }
                    for key, segment in segments.items()
                ]
            }

        return ctx.run_locked(read)

    @tool("read", open_world=True)
    def connection_read_memory(
        segment: str,
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Read a segment of the memory preview from the device, without writing.

        Identify the device by node_id OR device_address; segment is an id from
        project_memory_segments. Reads the complete displayed range, including
        holes, resolving relative memory through the device's table reference.
        Uses the GUI's cancellable operations queue and secure connection. Changes
        to project parameters do not prevent reading. Returns segment, actual
        address, size, object_index, user_memory and the bytes read as hex in data.
        A refusal or incomplete read fails instead of returning fabricated bytes.
        Does not change parameters, stored blobs or download status.
        """
        from editor_gui.memory_read import MemoryReadResult
        from editor_gui.plugins.mcp.tools.connection import finished, wait_for_operation
        from editor_gui.plugins.project.program_queue import ORIGIN_MCP

        plugin = _plugin()

        def queue() -> QueueItem:
            device = require_device(ctx.api.project, node_id, device_address)
            return plugin.queue_memory_read(device.node_id, segment, origin=ORIGIN_MCP)

        item = ctx.run_locked(queue)
        ended = finished(wait_for_operation(ctx, item, timeout))
        result = ended.result
        if not isinstance(result, MemoryReadResult):
            raise ToolError("Memory read returned no data")
        return {
            "segment": result.request.segment,
            "address": result.address,
            "size": len(result.data),
            "object_index": result.request.object_index,
            "user_memory": result.request.user_memory,
            "data": result.data.hex(),
        }

    @tool("read", open_world=True)
    def connection_read_plugin_memory(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Read device parameter memory directly into Plugin Config's comparison.

        Identify the device by node_id OR device_address. For supported Siemens
        plugin layouts, automatically selects the full parameter memory range.
        Uses the same cancellable queue and connection as the GUI. Returns address,
        size and counts of matching, mismatching and unchecked parameters. The
        comparison stays in this session; no configuration blob, parameter, group
        link or download status is overwritten. Inspect parameter differences with
        project_generic_plugin_parameters after this read. No memory preview opens.
        """
        from editor_gui.plugins.mcp.tools.connection import finished, wait_for_operation
        from editor_gui.plugins.project.program_queue import ORIGIN_MCP
        from xknxeditor.vendor.siemens_generic import MemoryCheck

        plugin = _plugin()

        def queue() -> QueueItem:
            device = require_device(ctx.api.project, node_id, device_address)
            return plugin.queue_plugin_memory_read(device.node_id, origin=ORIGIN_MCP)

        item = ctx.run_locked(queue)
        finished(wait_for_operation(ctx, item, timeout))

        def result() -> dict[str, Any]:
            device = require_device(ctx.api.project, node_id, device_address)
            memory = plugin.generic_plugin_memory_result(device)
            view = plugin.generic_plugin_view(device)
            if (
                memory is None
                or view is None
                or plugin.generic_plugin_memory_operation(device) is not item
            ):
                raise ToolError("Device changed during memory comparison")
            return {
                "address": memory.address,
                "size": len(memory.data),
                "counts": {check.value: view.count(check) for check in MemoryCheck},
            }

        return ctx.run_locked(result)

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

    @tool("destructive", open_world=True)
    def connection_answer_serial_number(
        take_over: bool,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Answer the question a running download of the device (``node_id`` or
        ``device_address``) asks in the Operations panel when the secured device in programming
        mode has another serial number than the one stored for it
        (awaiting_serial_number_answer in connection_operations: {found, stored}): ``take_over``
        true is its Yes (the serial number found is stored for the device and the download
        starts again with it, asking for that device's certificate unless the project has it),
        false its No (the download is canceled). Returns {answered: true}."""
        plugin = _plugin()

        def _answer() -> dict[str, Any]:
            for item in _active_of(plugin, node_id, device_address):
                question = item.serial_number_question
                if question is not None and not question.done():
                    question.set_result(take_over)
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
