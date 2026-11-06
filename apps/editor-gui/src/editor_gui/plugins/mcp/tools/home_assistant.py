# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Home Assistant tools: the open project as the KNX project of Home Assistant's KNX integration
(the Home Assistant menu, inside Home Assistant only)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui import host
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.home_assistant.service import HomeAssistantService

# Home Assistant stores a large project for a while.
_SEND_TIMEOUT = 120.0
_POLL_SECONDS = 0.25


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)

    def service() -> HomeAssistantService:
        if not host.home_assistant() or ctx.home_assistant is None:
            raise ToolError("the editor does not run inside Home Assistant")
        return ctx.home_assistant

    def status() -> dict[str, Any]:
        ha = service()
        return {
            "state": ha.state.value,
            "connected": ha.connected,
            "knx_loaded": ha.knx_loaded,
            "linked": ha.linked,
            "auto_update": ha.auto_update,
            "project_in_home_assistant": ha.knx_project,
            "error": ha.error,
            "home_assistant_version": ha.home_assistant_version or None,
        }

    @tool("read")
    def home_assistant_status() -> dict[str, Any]:
        """Whether Home Assistant's KNX integration uses the open project (the Home Assistant
        menu). Returns {state, connected, knx_loaded, linked, auto_update,
        project_in_home_assistant, error, home_assistant_version}.
        state: "unavailable" (the integration is not connected), "knx_missing" (the KNX
        integration is not set up), "no_project" (none open here), "not_linked" (the KNX
        integration holds no project or another one: project_in_home_assistant names it),
        "up_to_date", "pending" (changes not sent yet: they are sent a moment after the last
        change when auto_update is on), "sending" or "failed" (error says why). auto_update is
        the setting home_assistant_auto_update (settings_get/settings_set, on by default).
        project_in_home_assistant: {name, guid, tool_version, created_by, last_modified} of the
        project the KNX integration holds, or null. linked: it holds the open project."""
        return ctx.run_locked(status)

    @tool("destructive", open_world=True)
    def home_assistant_use_project() -> dict[str, Any]:
        """Send the open project to Home Assistant's KNX integration now (Use Project in Home
        Assistant, or Update Home Assistant Now): it replaces the project the KNX integration
        holds (one uploaded there, or another editor project). Its group monitor, project view,
        address pickers and entity suggestions then use this project; entities, automations and
        the KNX integration's settings stay as they are. Waits until Home Assistant took it and
        returns the state as home_assistant_status does."""
        require_project(ctx)

        def _send() -> bool:
            return service().send_now()

        if not ctx.run_locked(_send):
            current = ctx.run_locked(status)
            raise ToolError(
                f"the project cannot be sent now (state: {current['state']})"
            )
        deadline = time.monotonic() + _SEND_TIMEOUT
        while time.monotonic() < deadline:
            current = ctx.run_locked(status)
            if current["state"] != "sending":
                if current["state"] == "failed":
                    raise ToolError(
                        f"Home Assistant did not take the project: {current['error']}"
                    )
                return current
            time.sleep(_POLL_SECONDS)
        raise ToolError("Home Assistant did not answer in time")

    @tool("read")
    def home_assistant_group_address_usage(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, Any]:
        """What Home Assistant does with a group address (its details' Home Assistant section):
        identify it by ``group_address_id`` OR ``group_address`` (printed, e.g. "1/2/3"). Returns
        {group_address_id, address, known, sends, users}: known is false while the integration is
        not connected; sends is true when Home Assistant sends to it (an entity's command, an
        expose, the time server: then it has a sender without a device of the project); users
        are {kind ("entity", "expose", "time"), name, entity_id, platform, state, ui (false for an
        entity from YAML), disabled}."""
        require_project(ctx)
        from editor_gui.plugins.mcp.tools.project import require_group_address_id

        def _read() -> dict[str, Any]:
            ha = service()
            ga_id = require_group_address_id(
                ctx.api.project, group_address_id, group_address
            )
            ga = ctx.api.project.get_group_address(ga_id)
            assert ga is not None  # proven by require_group_address_id
            usage = ha.usage.get(ga.raw)
            return {
                "group_address_id": ga_id,
                "address": ga.address,
                "known": ha.connected,
                "sends": usage.sends if usage is not None else False,
                "users": list(usage.users) if usage is not None else [],
            }

        return ctx.run_locked(_read)

    @tool("destructive")
    def home_assistant_load_telegrams(hours: int = 24) -> dict[str, Any]:
        """Load the group telegrams Home Assistant's KNX integration recorded in the last
        ``hours`` hours into the Group Monitor, in place of its entries (Load Telegrams from
        Home Assistant; recording stops, as after loading a recording). Read them with the
        network_* tools. Waits until they are there and returns {loaded}. Without a bus
        connection of the editor, the Group Monitor shows Home Assistant's live telegrams
        anyway."""
        if hours < 1:
            raise ToolError("hours must be at least 1")
        loaded: list[int] = []

        def _ask() -> bool:
            ha = service()
            previous = ha.on_history

            def take(telegrams: list[dict[str, Any]]) -> None:
                if previous is not None:
                    previous(telegrams)
                loaded.append(len(telegrams))
                ha.on_history = previous

            ha.on_history = take
            if ha.request_history(hours):
                return True
            ha.on_history = previous
            return False

        if not ctx.run_locked(_ask):
            raise ToolError(
                "Home Assistant cannot be asked now (not connected, or busy)"
            )
        deadline = time.monotonic() + _SEND_TIMEOUT
        while time.monotonic() < deadline:
            if loaded:
                return {"loaded": loaded[0]}
            error = ctx.run_locked(lambda: service().history_error)
            if error is not None:
                raise ToolError(f"the telegrams could not be loaded: {error}")
            time.sleep(_POLL_SECONDS)
        raise ToolError("Home Assistant did not answer in time")

    @tool("read")
    def home_assistant_project_model(section: str | None = None) -> dict[str, Any]:
        """The open project as it is sent to Home Assistant's KNX integration (xknxproject's
        project format). Without ``section``: {info, counts} with the number of entries of each
        section. With ``section`` (one of communication_objects, devices, topology, locations,
        trades, group_addresses, group_ranges, functions, info): {section: its content}."""
        require_project(ctx)

        def _build() -> dict[str, Any]:
            from editor_gui import __version__
            from editor_gui.plugins.home_assistant.project_model import (
                build_project_model,
            )
            from editor_gui.plugins.home_assistant.service import CREATED_BY

            return dict(
                build_project_model(
                    ctx.api.project, tool_version=f"{CREATED_BY} {__version__}"
                )
            )

        model = ctx.run_locked(_build)
        if section is None:
            return {
                "info": model["info"],
                "counts": {
                    key: len(value) for key, value in model.items() if key != "info"
                },
            }
        if section not in model:
            raise ToolError(f"unknown section {section!r}; one of {', '.join(model)}")
        return {section: model[section]}
