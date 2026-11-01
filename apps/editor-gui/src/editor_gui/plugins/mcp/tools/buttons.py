# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Buttons of a device's parameter view, pressed as a click there does: an offline handler runs
right away, an online one as a "Parameter Script" operation of the Operations panel."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import hidden_block, require_device

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import Device
    from editor_gui.plugins.project.button_runner import ButtonOutcome
    from editor_gui.plugins.project.plugin import ProjectPlugin
    from editor_gui.plugins.project.program_queue import QueueItem
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.prod.parser_v2.ui import UiButton


def _nodes(nodes: list[Any], *, shown: bool) -> Iterator[Any]:
    """Every node of a device UI tree, depth first; ``shown`` leaves out the blocks the
    parameter view never shows, with their content."""
    from xknxeditor.prod.parser_v2.ui import UiParameterBlock, UiTab

    for node in nodes:
        if shown and hidden_block(node):
            continue
        yield node
        if isinstance(node, (UiTab, UiParameterBlock)):
            yield from _nodes(list(node.children), shown=shown)


def shown_button(device: Device, button_id: str) -> UiButton | None:
    """The button ``button_id`` as the device's parameter view shows it now (None: it shows no
    such button)."""
    from xknxeditor.prod.parser_v2.ui import UiButton

    for node in _nodes(list(device.get_ui()), shown=True):
        if isinstance(node, UiButton) and node.id == button_id:
            return node
    return None


def _changed_parameters(
    device: Device | None, changes: ChangeSet
) -> list[dict[str, Any]]:
    from xknxeditor.prod.parser_v2.ui import UiParameter

    labels: dict[str, str] = {}
    if device is not None:
        for node in _nodes(list(device.get_ui()), shown=False):
            if isinstance(node, UiParameter):
                labels.setdefault(node.ref_id, node.label)
    return [
        {
            "ref_id": ref,
            "label": labels.get(ref),
            "old_value": old,
            "new_value": new,
        }
        for ref, (old, new) in changes.items()
    ]


def _log(entries: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"level": level, "message": message} for level, message in entries]


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    def _plugin() -> ProjectPlugin:
        if ctx.project_plugin is None:
            raise ToolError(
                "buttons are pressed in the editor; it is not available here"
            )
        return ctx.project_plugin

    def _offline(
        node_id: int, button: UiButton, outcome: Future[ButtonOutcome], wait: float
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "button_id": button.id,
            "text": button.text,
            "online": None,
        }
        try:
            ended = outcome.result(timeout=wait)
        except FutureTimeout:
            return {
                **result,
                "state": "running",
                "error": None,
                "changed_parameters": [],
                "log": [],
            }
        device = ctx.run_locked(lambda: ctx.api.project.find_device_by_node_id(node_id))
        return {
            **result,
            "state": "failed" if ended.error is not None else "finished",
            "error": ended.error,
            "changed_parameters": _changed_parameters(device, ended.changes),
            "log": _log(ended.log),
        }

    def _online(item: QueueItem, button: UiButton, wait: float) -> dict[str, Any]:
        from editor_gui.plugins.mcp.tools.operations import item_dict
        from editor_gui.plugins.project.program_queue import OperationState

        assert item.ended is not None and item.script is not None
        run = item.script
        with contextlib.suppress(FutureTimeout):
            item.ended.result(timeout=wait)

        def _read() -> dict[str, Any]:
            device = ctx.api.project.find_device_by_node_id(item.node_id)
            progress = ctx.api.connection.busy_progress
            error = item.message
            if item.state is OperationState.FAILED and error is None and device:
                error = device.param_errors.get(button.id)
            return {
                "button_id": button.id,
                "text": button.text,
                "online": button.online,
                "state": item.state.value,
                "error": error,
                "changed_parameters": _changed_parameters(device, dict(run.changes)),
                "log": _log(list(run.log)),
                "operation": item_dict(item, progress),
            }

        return ctx.run_locked(_read)

    @tool("destructive", open_world=True)
    def project_press_button(
        button_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
        wait: float = 60.0,
    ) -> dict[str, Any]:
        """Press a Button of a device's parameter view, as a click there does. Identify the device
        by ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass exactly
        one. ``button_id`` is the ``id`` of a "button" node of project_parameter_tree (search finds
        buttons by their text); only a button the parameter view shows now can be pressed.

        The button runs the handler script of the device's application. Handlers read and write
        the device's parameters; online ones (``online`` "ConnectionOriented"/"ConnectionLess" in
        the tree) also talk to the device on the bus, e.g. read calibration data, start a
        measurement or enrollment on the device, or erase data in it, so press one only when the
        user wants what its text says.
        - Offline handler (``online`` null): it runs right away while the editor waits, as one
          undo step ("Button … executed."); one that fails stores nothing.
        - Online handler: it is queued as a "Parameter Script" operation in the Operations panel
          (origin "MCP"), behind the operations already queued. It needs a KNX connection and a
          device whose individual address has been downloaded. Each parameter it writes is its
          own undo step, kept also when it fails or is canceled later.
        Refused when the button is read-only, another handler runs on the device, or, for an
        online one, the device's individual address has not been downloaded or there is no KNX
        connection.

        ``wait`` (seconds) is how long to wait for the handler to end. There is no time limit
        on the handler itself: when ``wait`` runs out, the result has state "running" (or
        "waiting" for a queued online one) and the handler goes on. Some wait for the user at
        the device (e.g. a finger on a sensor). Follow an online one with connection_operations
        and stop it with connection_cancel_operations (Cancel lets the handler stop at its next
        check; Force Stop follows when it ignores that). The editor offers Force Stop for an
        offline one that runs longer than 10 s.

        Returns {button_id, text, online, state, error, changed_parameters, log, operation?}:
        ``state`` "finished", "failed", "canceled", "running" or "waiting"; ``error`` the
        handler's message when it failed (also shown under the button); ``changed_parameters``
        the parameters stored so far, each {ref_id, label, old_value, new_value}; ``log`` what the
        handler logged, each {level, message}; ``operation`` (online only) the operation's entry as
        connection_operations lists it. A handler may show or hide parameters: re-read the tree
        (its ``tree_token`` may change)."""
        plugin = _plugin()
        wait = max(0.0, wait)

        def _press() -> tuple[Device, UiButton, Future[ButtonOutcome] | QueueItem]:
            from editor_gui.plugins.project.program_queue import ORIGIN_MCP

            require_project(ctx)
            device = require_device(ctx.api.project, node_id, device_address)
            button = shown_button(device, button_id)
            if button is None:
                raise ToolError(
                    f"device {device.node_id} shows no button {button_id!r}; read the button "
                    "ids with project_parameter_tree"
                )
            if button.read_only:
                raise ToolError(f"button {button_id!r} is read-only")
            enabled, reason = plugin.button_state(device, button)
            if not enabled:
                raise ToolError(reason or f"button {button_id!r} cannot be pressed now")
            if button.online is not None and not ctx.api.connection.can_reach_bus():
                raise ToolError(
                    "no KNX connection; connect first with connection_connect (configured "
                    "gateway) or connection_scan + connection_connect_gateway"
                )
            pressed = plugin.press_button(device, button, origin=ORIGIN_MCP)
            if pressed is None:
                raise ToolError(
                    "another button's handler is running; retry once it has ended"
                )
            return device, button, pressed

        device, button, pressed = ctx.run_locked(_press)
        if isinstance(pressed, Future):
            return _offline(device.node_id, button, pressed, wait)
        return _online(pressed, button, wait)
