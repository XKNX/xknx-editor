# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Home Assistant tools: the open project as the KNX project of Home Assistant's KNX integration
(the Home Assistant menu, inside Home Assistant only)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui import host
from editor_gui.plugins.home_assistant.areas import AreaRow, AreaState
from editor_gui.plugins.home_assistant.service import EntityRow
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.home_assistant.service import HomeAssistantService

# Home Assistant stores a large project for a while.
_SEND_TIMEOUT = 120.0
_POLL_SECONDS = 0.25


def _items(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The list envelope of the list tools."""
    return {"items": rows, "count": len(rows)}


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
            # How the KNX integration reaches the bus: {type, transport, host, port}.
            "knx_connection": ha.connection,
        }

    @tool("read")
    def home_assistant_status() -> dict[str, Any]:
        """Whether Home Assistant's KNX integration uses the open project (the Home Assistant
        menu). Returns {state, connected, knx_loaded, linked, auto_update,
        project_in_home_assistant, error, home_assistant_version, knx_connection}.
        state: "unavailable" (the integration is not connected), "knx_missing" (the KNX
        integration is not set up), "no_project" (none open here), "not_linked" (the KNX
        integration holds no project or another one: project_in_home_assistant names it),
        "up_to_date", "pending" (changes not sent yet: they are sent a moment after the last
        change when auto_update is on), "sending" or "failed" (error says why). auto_update is
        the setting home_assistant_auto_update (settings_get/settings_set, on by default).
        project_in_home_assistant: {name, guid, tool_version, created_by, last_modified} of the
        project the KNX integration holds, or null. linked: it holds the open project.
        knx_connection: how the KNX integration reaches the bus, {type ("tunneling" or
        "routing"), transport ("tunneling", "tunneling_tcp", their "_secure" variants,
        "routing", "routing_secure" or "automatic"), host (the gateway, or the multicast group),
        port}, or null; connection_configure + connection_connect with them connect the editor
        the same way (not a secure or automatic one). While the editor has no connection of its
        own, monitor_send_read / monitor_send_write go over the KNX integration's."""
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

    def area_row(row: AreaRow) -> dict[str, Any]:
        planned = row.planned
        return {
            "space_id": planned.space_id,
            "kind": planned.kind,
            "name": planned.name,
            "path": planned.path,
            "floor_space_id": (
                int(planned.floor_key.rsplit(":", 1)[1]) if planned.floor_key else None
            ),
            "level": planned.level,
            "state": row.state.value,
            "name_in_home_assistant": row.current_name or None,
        }

    @tool("read")
    def home_assistant_areas() -> dict[str, Any]:
        """The project's floors and areas as Home Assistant gets them (Floors and Areas…): a
        floor of a building becomes a floor, a room, corridor or stairway an area on its floor.
        Returns {items, count}; each item has {space_id, kind ("floor" or "area"), name (in Home
        Assistant; with several buildings a floor's starts with its building's), path,
        floor_space_id (an area's floor), level (a floor's, from its number), state,
        name_in_home_assistant}. state: "new" (Home Assistant has none of this name), "match"
        (it has one, not linked yet), "linked", or "renamed" (linked, but named otherwise
        there)."""
        require_project(ctx)
        return _items(
            ctx.run_locked(lambda: [area_row(r) for r in service().area_rows()])
        )

    @tool("write", open_world=True)
    def home_assistant_apply_areas(
        space_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Create the floors and areas of the spaces ``space_ids`` in Home Assistant, link them
        to ones of the same name, or rename linked ones as here (Floors and Areas…, Apply);
        without space_ids every one not linked as it is here. Nothing in Home Assistant is
        removed. Waits until Home Assistant took them; returns {changed} and the rows as
        home_assistant_areas gives them."""
        require_project(ctx)
        applied: list[int] = []

        def _apply() -> bool:
            ha = service()
            rows = [
                r
                for r in ha.area_rows()
                if r.state is not AreaState.LINKED
                and (space_ids is None or r.planned.space_id in space_ids)
            ]
            if not rows:
                raise ToolError("nothing to apply: every space is linked as it is here")
            previous = ha.on_areas_applied

            def take(changed: int) -> None:
                applied.append(changed)
                ha.on_areas_applied = previous
                if previous is not None:
                    previous(changed)

            ha.on_areas_applied = take
            if ha.apply_areas(rows):
                return True
            ha.on_areas_applied = previous
            return False

        if not ctx.run_locked(_apply):
            raise ToolError(
                "Home Assistant cannot be asked now (not connected, or busy)"
            )
        deadline = time.monotonic() + _SEND_TIMEOUT
        while time.monotonic() < deadline:
            if applied:
                rows = ctx.run_locked(
                    lambda: [area_row(r) for r in service().area_rows()]
                )
                return {"changed": applied[0], **_items(rows)}
            error = ctx.run_locked(lambda: service().areas_error)
            if error is not None:
                raise ToolError(f"Home Assistant did not take them: {error}")
            time.sleep(_POLL_SECONDS)
        raise ToolError("Home Assistant did not answer in time")

    def entity_row(row: EntityRow) -> dict[str, Any]:
        candidate = row.candidate
        state = (
            ("changed" if row.downloaded else "changed_not_downloaded")
            if row.created is not None and row.changed
            else "created"
            if row.created is not None
            else "existing"
            if row.existing
            else "unchecked"
            if row.check is None
            else "not_possible"
            if row.check
            else "new"
        )
        return {
            "function_id": candidate.function_id,
            "name": candidate.name,
            "path": candidate.path,
            "platform": candidate.platform,
            "knx": candidate.knx,
            "group_addresses": list(candidate.group_addresses),
            "area_id": row.area_id,
            "state": state,
            "entity_id": row.created,
            "existing_entity_ids": list(row.existing),
            "reason": row.check or None,
        }

    def checked_rows() -> list[EntityRow]:
        """The rows once Home Assistant has checked them."""
        ctx.run_locked(lambda: service().check_entities(service().entity_rows()))
        deadline = time.monotonic() + _SEND_TIMEOUT
        while time.monotonic() < deadline:
            rows = ctx.run_locked(lambda: service().entity_rows())
            if not ctx.run_locked(lambda: service().entities_checking) and all(
                r.check is not None for r in rows
            ):
                return rows
            time.sleep(_POLL_SECONDS)
        raise ToolError("Home Assistant did not answer in time")

    @tool("read", open_world=True)
    def home_assistant_entities() -> dict[str, Any]:
        """The entities the project's building functions become in Home Assistant's KNX
        integration (Entities from Functions…), each checked by Home Assistant: a switchable or
        dimmable light, a switch or sun protection becomes a light, switch or cover with the
        group addresses of its roles, named like the function, in the area its room became
        (home_assistant_areas). Returns {items, count}; each item has {function_id, name, path,
        platform, knx (the configuration), group_addresses, area_id, state, entity_id,
        existing_entity_ids, reason}. state: "new" (Home Assistant takes it), "existing" (an
        entity uses its address already), "created" (entity_id), "not_possible" (reason)."""
        require_project(ctx)
        return _items([entity_row(r) for r in checked_rows()])

    @tool("write", open_world=True)
    def home_assistant_create_entities(
        function_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Create the entities of the functions ``function_ids`` in Home Assistant (Entities from
        Functions…, Create); without function_ids every "new" one of home_assistant_entities.
        Only "new" ones are created. Waits until Home Assistant created them; returns {created}
        and the rows as home_assistant_entities gives them, with a reason for each one that was
        not created."""
        require_project(ctx)
        rows = [
            r
            for r in checked_rows()
            if r.check == ""
            and r.created is None
            and not r.existing
            and (function_ids is None or r.candidate.function_id in function_ids)
        ]
        if not rows:
            raise ToolError("nothing to create: no new entity among them")
        done: list[int] = []

        def _create() -> bool:
            ha = service()
            previous = ha.on_entities_created

            def take(count: int) -> None:
                done.append(count)
                ha.on_entities_created = previous
                if previous is not None:
                    previous(count)

            ha.on_entities_created = take
            if ha.create_entities(rows):
                return True
            ha.on_entities_created = previous
            return False

        if not ctx.run_locked(_create):
            raise ToolError(
                "Home Assistant cannot be asked now (not connected, or busy)"
            )
        deadline = time.monotonic() + _SEND_TIMEOUT
        while time.monotonic() < deadline:
            if done:
                errors = ctx.run_locked(lambda: dict(service().entity_errors))
                current = ctx.run_locked(lambda: service().entity_rows())
                items = [entity_row(r) for r in current]
                for item, row in zip(items, current, strict=True):
                    if (error := errors.get(row.candidate.key)) is not None:
                        item["reason"] = error
                return {"created": done[0], **_items(items)}
            time.sleep(_POLL_SECONDS)
        raise ToolError("Home Assistant did not answer in time")

    def wait_for_requests() -> None:
        """Until Home Assistant answered the follow-up requests; raises its error."""
        deadline = time.monotonic() + _SEND_TIMEOUT
        while time.monotonic() < deadline:
            if not ctx.run_locked(lambda: service().requesting):
                if error := ctx.run_locked(lambda: service().request_error):
                    raise ToolError(f"Home Assistant: {error}")
                return
            time.sleep(_POLL_SECONDS)
        raise ToolError("Home Assistant did not answer in time")

    @tool("write", open_world=True)
    def home_assistant_update_entities(
        function_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Give the entities created for building functions the group addresses their functions
        have now (Entities from Functions…, Apply, for "changed" rows of
        home_assistant_entities); only entities whose devices have their links downloaded
        ("changed_not_downloaded": download those devices first), without function_ids all of
        them. Their names and the rest stay. Returns the rows as home_assistant_entities."""
        require_project(ctx)

        def _update() -> bool:
            rows = [
                r
                for r in service().entity_rows()
                if r.changed
                and r.downloaded
                and (function_ids is None or r.candidate.function_id in function_ids)
            ]
            if not rows:
                raise ToolError(
                    "nothing to update: no changed entity with downloaded devices"
                )
            return service().update_entities(rows)

        if not ctx.run_locked(_update):
            raise ToolError("Home Assistant cannot be asked now (not connected)")
        wait_for_requests()
        return _items(
            [entity_row(r) for r in ctx.run_locked(lambda: service().entity_rows())]
        )

    @tool("read")
    def home_assistant_orphan_entities() -> dict[str, Any]:
        """The entities created for building functions the project no longer has (or that no
        longer make an entity): {items: [{entity_id}], count}."""
        require_project(ctx)
        orphans = ctx.run_locked(lambda: service().orphans())
        return _items([{"entity_id": entity_id} for _key, entity_id in orphans])

    @tool("destructive", open_world=True)
    def home_assistant_remove_entities(entity_ids: list[str]) -> dict[str, Any]:
        """Remove entities of home_assistant_orphan_entities from Home Assistant (Entities from
        Functions…, Entities of Removed Functions); only those. Returns the orphans left."""
        require_project(ctx)

        def _remove() -> bool:
            keys = [key for key, eid in service().orphans() if eid in entity_ids]
            if not keys:
                raise ToolError("none of them is an entity of a removed function")
            return service().remove_entities(keys)

        if not ctx.run_locked(_remove):
            raise ToolError("Home Assistant cannot be asked now (not connected)")
        wait_for_requests()
        orphans = ctx.run_locked(lambda: service().orphans())
        return _items([{"entity_id": entity_id} for _key, entity_id in orphans])

    def served() -> dict[str, Any]:
        current = ctx.run_locked(lambda: service().time_server)
        if current is None:
            raise ToolError("the KNX integration is not set up or not connected")
        return dict(current)

    @tool("read")
    def home_assistant_time_server() -> dict[str, Any]:
        """Where the KNX integration's time server sends the time, the date and the date with
        time (Time Server…): {time, date, datetime}, each a printed group address or null."""
        return served()

    @tool("write", open_world=True)
    def home_assistant_set_time_server(
        time_address: str | None = None,
        date_address: str | None = None,
        datetime_address: str | None = None,
    ) -> dict[str, Any]:
        """Let the KNX integration's time server send the time, the date and the date with time
        to these printed group addresses (Time Server…, Apply); null for none. The addresses
        need the data type of a time (10.001), a date (11.001) or a date with time (19.001).
        Returns the time server as home_assistant_time_server."""
        addresses = {
            "time": time_address,
            "date": date_address,
            "datetime": datetime_address,
        }
        if not ctx.run_locked(lambda: service().set_time_server(addresses)):
            raise ToolError("Home Assistant cannot be asked now (not connected)")
        wait_for_requests()
        return served()

    @tool("destructive", open_world=True)
    def home_assistant_send_keys() -> dict[str, Any]:
        """Make the keys of the project's Data Secure group addresses the keyring of Home
        Assistant's KNX integration (Send Keys to Home Assistant…), replacing its keyring; the
        KNX integration is loaded again. Refused while it connects with KNX Secure (its keyring
        holds that connection's keys). Returns {sent: true}."""
        require_project(ctx)

        def _send() -> bool:
            ha = service()
            if ha.keyring_refused is not None:
                raise ToolError(ha.keyring_refused)
            return ha.send_keys()

        if not ctx.run_locked(_send):
            error = ctx.run_locked(lambda: service().request_error)
            raise ToolError(
                error or "Home Assistant cannot be asked now (not connected)"
            )
        wait_for_requests()
        return {"sent": True}

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
