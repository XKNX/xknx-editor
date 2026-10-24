# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""MCP tools for removing and copying devices, building functions and group ranges: the edits of
the device tree's context menu, the Buildings view and the group address folders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import (
    require_device,
    require_group_address_id,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.project.service import ProjectService

# The function types the Buildings view offers (KNX FunctionType codes): custom, switchable light,
# dimmable light, sun protection, heating (switching), heating (continuous), socket.
FunctionType = Literal["FT-0", "FT-1", "FT-6", "FT-7", "FT-8", "FT-9", "FT-10"]

_MAX_COPIES = 100


def _functions(project: ProjectService) -> dict[int, Any]:
    """Every building function by id."""
    found: dict[int, Any] = {}

    def walk(spaces: list[Any]) -> None:
        for space in spaces:
            for function in space.functions:
                found[function.id] = function
            walk(space.children)

    walk(project.get_space_tree())
    return found


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    # --- devices --------------------------------------------------------------

    @tool("destructive")
    def project_remove_device(node_id: int) -> dict[str, Any]:
        """Remove a device (node_id from project_list_devices) from the project, with its group
        objects and their links. Undoable with project_undo; the device on the bus is unchanged.
        Returns {status: "removed", node_id}."""
        require_project(ctx)

        def _remove() -> dict[str, Any]:
            require_device(project, node_id)
            project.remove_device(node_id)
            if node_id in project.selected_node_ids:
                project.set_multi_selection(None, [])
            return {"status": "removed", "node_id": node_id}

        return ctx.run_locked(_remove)

    @tool("write")
    def project_copy_device(
        node_id: int,
        count: int = 1,
        include_parameters: bool = True,
        include_links: bool = False,
    ) -> dict[str, Any]:
        """Add ``count`` copies (1-100) of a device (node_id from project_list_devices), with the
        same product and application. ``include_parameters`` carries the parameter values over,
        ``include_links`` the group address links. The copies get no individual address. Undoable
        with project_undo. Returns {node_ids} of the copies."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")

        def _copy() -> dict[str, Any]:
            require_device(project, node_id)
            new_ids = project.clone_device(
                node_id,
                count,
                include_params=include_parameters,
                include_links=include_links,
            )
            if not new_ids:
                raise ToolError(
                    f"device {node_id} cannot be copied (its application is not loaded)"
                )
            return {"node_ids": new_ids}

        return ctx.run_locked(_copy)

    @tool("destructive")
    def project_copy_device_configuration(
        source_node_id: int,
        target_node_ids: list[int],
        include_parameters: bool = True,
        include_links: bool = True,
    ) -> dict[str, Any]:
        """Copy a device's configuration onto other devices with the same application: its
        parameter values (``include_parameters``) and group address links (``include_links``),
        like Copy and Paste Configuration in the device tree. Each target is one undo step.
        Returns {items, count}; each item has {node_id, ok, parameters_changed, links_mapped}, ok
        false for a target with another application."""
        require_project(ctx)
        if not target_node_ids:
            raise ToolError("pass at least one target node id")

        def _paste() -> list[dict[str, Any]]:
            require_device(project, source_node_id)
            clip = project.copy_device_config(source_node_id)
            if clip is None:
                raise ToolError(
                    f"device {source_node_id} has no configuration to copy (no application)"
                )
            results: list[dict[str, Any]] = []
            for target in target_node_ids:
                require_device(project, target)
                ok, params, links = project.paste_device_config(
                    target,
                    clip,
                    include_params=include_parameters,
                    include_links=include_links,
                )
                results.append(
                    {
                        "node_id": target,
                        "ok": ok,
                        "parameters_changed": params,
                        "links_mapped": links,
                    }
                )
            return results

        rows = ctx.run_locked(_paste)
        return {"items": rows, "count": len(rows)}

    # --- building functions ---------------------------------------------------

    @tool("write")
    def project_create_function(
        space_id: int, name: str, function_type: FunctionType = "FT-1"
    ) -> dict[str, Any]:
        """Add a building function to a space (id from project_spaces). function_type: FT-0
        custom, FT-1 switchable light (default), FT-6 dimmable light, FT-7 sun protection, FT-8
        heating (switching), FT-9 heating (continuous), FT-10 socket. Undoable with project_undo.
        Returns {function_id, space_id, name, function_type}; the function shows in
        project_spaces."""
        require_project(ctx)

        def _create() -> dict[str, Any]:
            if project.find_space(space_id) is None:
                raise ToolError(f"no space with id {space_id}")
            function_id = project.create_function(space_id, function_type, name)
            if function_id is None:
                raise ToolError("the function could not be created")
            return {
                "function_id": function_id,
                "space_id": space_id,
                "name": name,
                "function_type": function_type,
            }

        return ctx.run_locked(_create)

    def _require_function(function_id: int) -> Any:
        function = _functions(project).get(function_id)
        if function is None:
            raise ToolError(
                f"no function with id {function_id}; project_spaces lists the functions"
            )
        return function

    @tool("destructive")
    def project_remove_function(function_id: int) -> dict[str, Any]:
        """Remove a building function (id from project_spaces). Undoable with project_undo.
        Returns {status: "removed", function_id}."""
        require_project(ctx)

        def _remove() -> dict[str, Any]:
            _require_function(function_id)
            project.remove_function(function_id)
            return {"status": "removed", "function_id": function_id}

        return ctx.run_locked(_remove)

    @tool("write")
    def project_set_function_type(
        function_id: int, function_type: FunctionType
    ) -> dict[str, Any]:
        """Change the type of a building function (id from project_spaces); the types as for
        project_create_function. Undoable with project_undo. Returns {function_id,
        function_type}."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            _require_function(function_id)
            project.set_function_type(function_id, function_type)
            return {"function_id": function_id, "function_type": function_type}

        return ctx.run_locked(_set)

    @tool("write")
    def project_add_function_group_address(
        function_id: int,
        group_address_id: int | None = None,
        group_address: str | None = None,
        role: str = "",
    ) -> dict[str, Any]:
        """Add a group address to a building function (id from project_spaces), with an optional
        free-text ``role``. Identify the group address by ``group_address_id`` (id from
        project_list_group_addresses) OR ``group_address`` (a printed address like "10/1/2" in the
        project's style); pass exactly one. Undoable with project_undo. Returns {link_id,
        function_id, group_address_id, role}; pass link_id to
        project_remove_function_group_address."""
        require_project(ctx)

        def _add() -> dict[str, Any]:
            _require_function(function_id)
            ga_id = require_group_address_id(project, group_address_id, group_address)
            if project.get_group_address(ga_id) is None:
                raise ToolError(f"no group address with id {ga_id}")
            link_id = project.add_function_group_address(function_id, ga_id, role)
            if link_id is None:
                raise ToolError("the group address could not be added")
            return {
                "link_id": link_id,
                "function_id": function_id,
                "group_address_id": ga_id,
                "role": role,
            }

        return ctx.run_locked(_add)

    @tool("destructive")
    def project_remove_function_group_address(link_id: int) -> dict[str, Any]:
        """Remove a group address from a building function: link_id is the ``id`` of the entry
        in the function's group_addresses (project_spaces) or a returned link_id. Undoable with
        project_undo. Returns {status: "removed", link_id}."""
        require_project(ctx)

        def _remove() -> dict[str, Any]:
            known = {
                ref.id
                for function in _functions(project).values()
                for ref in function.group_addresses
            }
            if link_id not in known:
                raise ToolError(f"no function group address with id {link_id}")
            project.remove_function_group_address(link_id)
            return {"status": "removed", "link_id": link_id}

        return ctx.run_locked(_remove)

    # --- group ranges ---------------------------------------------------------

    @tool("write")
    def project_create_group_range(
        name: str, parent_id: int | None = None
    ) -> dict[str, Any]:
        """Add a group address folder: a main group, or a middle group in the main group
        ``parent_id`` (a group range id, see project_get_element), with the next free number.
        Undoable with project_undo. Returns {group_range_id, type, address, name}."""
        require_project(ctx)

        def _create() -> dict[str, Any]:
            if (
                parent_id is not None
                and project.element_details("group_range", parent_id) is None
            ):
                raise ToolError(f"no group range with id {parent_id}")
            range_id = project.create_group_range(parent_id, name)
            if range_id is None:
                raise ToolError(
                    "no folder can be added there (the group address style has none, or all "
                    "numbers are used)"
                )
            details = project.element_details("group_range", range_id)
            return {
                "group_range_id": range_id,
                "type": details.title_kind if details is not None else "",
                "address": (
                    f"{details.prefix}{details.address}" if details is not None else ""
                ),
                "name": name,
            }

        return ctx.run_locked(_create)

    @tool("destructive")
    def project_remove_group_range(group_range_id: int) -> dict[str, Any]:
        """Remove a group address folder (main or middle group) with everything in it: its middle
        groups, their group addresses and the links of those. Undoable with project_undo.
        Returns {status: "removed", group_range_id}."""
        require_project(ctx)

        def _remove() -> dict[str, Any]:
            if project.element_details("group_range", group_range_id) is None:
                raise ToolError(f"no group range with id {group_range_id}")
            project.remove_group_range(group_range_id)
            return {"status": "removed", "group_range_id": group_range_id}

        return ctx.run_locked(_remove)
