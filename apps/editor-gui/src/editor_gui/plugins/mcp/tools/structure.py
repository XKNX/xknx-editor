# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""MCP tools for removing and copying devices, building functions and group ranges: the edits of
the device tree's context menu, the Buildings view and the group address folders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import (
    NumberAllocation,
    require_device,
    require_group_address_id,
)
from editor_gui.plugins.project.paste import (
    PasteError,
    PasteKind,
    PasteTarget,
    TargetKind,
    paste_error_text,
)
from editor_gui.plugins.project.space_text import default_function_name
from editor_gui.plugins.project.strings import S as PROJECT_S
from xknxeditor.proj.core.paste import (
    AddressPool,
    Allocation,
    GroupAddressCopy,
    PasteOptions,
    group_base,
    place_range,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.proj.core.service import GroupRangeInfo

# The function types the Buildings view offers (KNX FunctionType codes): custom, switchable light,
# dimmable light, sun protection, heating (switching), heating (continuous), socket.
FunctionType = Literal["FT-0", "FT-1", "FT-6", "FT-7", "FT-8", "FT-9", "FT-10"]

_MAX_COPIES = 100


@dataclass(frozen=True)
class _FoundGroup:
    """A group of the group address tree and the id of the main group it is in (None: it is
    a main group)."""

    node: GroupRangeInfo
    parent_id: int | None


def _group_range(project: ProjectService, range_id: int) -> _FoundGroup | None:
    """The group ``range_id`` of the group address tree (nested at any depth), None for
    none."""
    stack: list[tuple[GroupRangeInfo, int | None]] = [
        (node, None) for node in project.get_group_range_tree()
    ]
    while stack:
        node, parent_id = stack.pop()
        if node.id == range_id:
            return _FoundGroup(node, parent_id)
        stack.extend((child, node.id) for child in node.children)
    return None


def _used_addresses(project: ProjectService) -> set[int]:
    """Every group address of the project, as numbers."""
    used: set[int] = set()
    stack = list(project.get_group_range_tree())
    while stack:
        node = stack.pop()
        used.update(ga.address for ga in node.group_addresses)
        stack.extend(node.children)
    return used


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
    def project_remove_device(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Remove a device from the project, with its group objects and their links. Identify
        it by ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass
        exactly one. Undoable with project_undo; the device on the bus is unchanged. Returns
        {status: "removed", node_id}."""
        require_project(ctx)

        def _remove() -> dict[str, Any]:
            nid = require_device(project, node_id, device_address).node_id
            project.remove_device(nid)
            if nid in project.selected_node_ids:
                project.set_multi_selection(None, [])
            return {"status": "removed", "node_id": nid}

        return ctx.run_locked(_remove)

    @tool("write")
    def project_copy_device(
        node_id: int | None = None,
        device_address: str | None = None,
        count: int = 1,
        include_parameters: bool = True,
        include_links: bool = False,
    ) -> dict[str, Any]:
        """Add ``count`` copies (1-100) of a device, with the same product and application.
        Identify the device by ``node_id`` OR ``device_address`` (an individual address like
        "1.0.30"); pass exactly one. ``include_parameters`` carries the parameter values over,
        ``include_links`` the group address links. The copies get no individual address. Undoable
        with project_undo. Returns {node_ids} of the copies."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")

        def _copy() -> dict[str, Any]:
            nid = require_device(project, node_id, device_address).node_id
            new_ids = project.clone_device(
                nid,
                count,
                include_params=include_parameters,
                include_links=include_links,
            )
            if not new_ids:
                raise ToolError(
                    f"device {nid} cannot be copied (its application is not loaded)"
                )
            return {"node_ids": new_ids}

        return ctx.run_locked(_copy)

    @tool("destructive")
    def project_copy_device_configuration(
        source_node_id: int | None = None,
        source_device_address: str | None = None,
        target_node_ids: list[int] | None = None,
        target_device_addresses: list[str] | None = None,
        include_parameters: bool = True,
        include_links: bool = True,
    ) -> dict[str, Any]:
        """Copy a device's configuration onto other devices with the same application: its
        parameter values (``include_parameters``) and group address links (``include_links``),
        like Copy and Paste Configuration in the device tree. Identify the source by
        ``source_node_id`` OR ``source_device_address`` (an individual address like "1.0.30"),
        the targets by ``target_node_ids`` and/or ``target_device_addresses``. Each target is one
        undo step. Returns {items, count}; each item has {node_id, ok, parameters_changed,
        links_mapped}, ok false for a target with another application."""
        require_project(ctx)
        if not target_node_ids and not target_device_addresses:
            raise ToolError("pass at least one target node id or device address")

        def _paste() -> list[dict[str, Any]]:
            if (source_node_id is None) == (source_device_address is None):
                raise ToolError(
                    "pass exactly one of source_node_id or source_device_address"
                )
            source = require_device(project, source_node_id, source_device_address)
            clip = project.copy_device_config(source.node_id)
            if clip is None:
                raise ToolError(
                    f"device {source.node_id} has no configuration to copy (no application)"
                )
            targets = [
                *(require_device(project, n).node_id for n in target_node_ids or ()),
                *(
                    require_device(project, device_address=a).node_id
                    for a in target_device_addresses or ()
                ),
            ]
            results: list[dict[str, Any]] = []
            for target in targets:
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

    @tool("write")
    def project_paste_special(
        kind: PasteKind,
        target_kind: TargetKind,
        target_id: int | None = None,
        ids: list[int] | None = None,
        device_addresses: list[str] | None = None,
        group_addresses: list[str] | None = None,
        copies: int = 1,
        links: Literal["dont_copy", "keep", "create_new"] = "dont_copy",
        new_group: str = "",
        new_group_range_id: int | None = None,
        allocation: Literal["fill_up", "append", "start_with", "offset"] = "fill_up",
        start_with: int = 1,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Copy elements as dropping them with Ctrl held does in a tree (the Paste Special
        dialog): ``copies`` (1-100) copies of each, one undo step (project_undo).

        What: ``kind`` with ``ids`` (device node ids, building part, trade, building function,
        group address, group range, line or segment ids from project_list_devices,
        project_spaces, project_trades, project_list_group_ranges and project_topology);
        devices also by ``device_addresses`` ("1.0.30"), group addresses by ``group_addresses``
        (printed, e.g. "1/2/3").

        Where (``target_kind`` and ``target_id``): devices into a "segment" (a line's main
        segment for the line itself), a "space" (it has to hold devices) or a "trade"; building
        parts into a "space" (``target_id`` none: the top, for buildings); trades into a
        "trade" (none: the top); functions into a "space"; group addresses into a
        "group_range" that holds them; middle groups into a main "group_range", main groups to
        the top ("group_range", none); lines into an "area"; segments onto a "line".

        A copy keeps its source's name and texts. Device copies keep their device number in
        the target's line when it is free there, else get the lowest free one; a building part's
        devices are numbered from the lowest free one of its Current Line (else of the target's);
        devices copied where there is no line (a trade, a building part without Current Line)
        get no individual address. ``links`` says what copies of devices, building parts,
        trades, functions, lines and segments link: nothing ("dont_copy"), the same group
        addresses ("keep") or new ones ("create_new": one per group address the sources link,
        per copy, in the middle group ``new_group`` like "1/2" (three-level style) or main group
        "1" (two-level), made when it is missing; in free style the range
        ``new_group_range_id``). ``allocation`` places new group addresses, and copied group
        addresses and groups, in their group: "fill_up" the lowest free, "append" after the
        highest used, "start_with" the first free from ``start_with`` on, "offset" at the
        source's own place plus ``offset``.

        Returns {created: ids of the copies, label: the history entry}."""
        require_project(ctx)
        if not 1 <= copies <= _MAX_COPIES:
            raise ToolError(f"copies must be 1 to {_MAX_COPIES}")
        if start_with < 1 or offset < 0:
            raise ToolError("start_with must be at least 1, offset at least 0")
        options = PasteOptions(
            count=copies,
            group_addresses=GroupAddressCopy(links),
            target_range=new_group,
            target_range_id=new_group_range_id,
            allocation=Allocation(allocation),
            start_with=start_with,
            offset=offset,
        )

        def _paste() -> dict[str, Any]:
            sources = [
                *(ids or ()),
                *(
                    require_device(project, device_address=a).node_id
                    for a in device_addresses or ()
                ),
                *(
                    require_group_address_id(project, group_address=a)
                    for a in group_addresses or ()
                ),
            ]
            if not sources:
                raise ToolError("pass ids, device_addresses or group_addresses")
            try:
                created = project.paste(
                    kind, sources, PasteTarget(target_kind, target_id), options
                )
            except PasteError as exc:
                raise ToolError(paste_error_text(exc)) from exc
            except KeyError as exc:
                raise ToolError(f"no such element: {exc}") from exc
            return {"created": created, "label": project.history()[0].display_text}

        return ctx.run_locked(_paste)

    # --- building functions ---------------------------------------------------

    @tool("write")
    def project_create_function(
        space_id: int,
        name: str = "",
        function_type: FunctionType = "FT-1",
        count: int = 1,
    ) -> dict[str, Any]:
        """Add a building function to a space (id from project_spaces), or ``count`` of them (1
        to 100, one undo step) as the Add dialog does. function_type: FT-0 custom, FT-1
        switchable light (default), FT-6 dimmable light, FT-7 sun protection, FT-8 heating
        (switching), FT-9 heating (continuous), FT-10 socket. Without a ``name`` a function is
        named after its type ("New function" for a custom one), in the editor's language.
        Undoable with project_undo. Returns {function_id, function_ids, space_id, name,
        function_type}: function_id is the first one; the functions show in project_spaces."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")
        title = name.strip() or default_function_name(function_type)

        def _create() -> dict[str, Any]:
            space = project.find_space(space_id)
            if space is None:
                raise ToolError(f"no space with id {space_id}")
            label = (
                PROJECT_S.HISTORY_EV_ADDED_FUNCTIONS.format(
                    count=count,
                    target=project.paste_target_name(PasteTarget("space", space_id)),
                )
                if count > 1
                else None
            )
            ids: list[int] = []
            with project.one_undo_step(label):
                for _ in range(count):
                    function_id = project.create_function(
                        space_id, function_type, title
                    )
                    if function_id is None:
                        raise ToolError("the function could not be created")
                    ids.append(function_id)
            return {
                "function_id": ids[0],
                "function_ids": ids,
                "space_id": space_id,
                "name": title,
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
    def project_move_function(function_id: int, space_id: int) -> dict[str, Any]:
        """Move a building function (id from project_spaces) into another space (a space's
        ``id``), as its "Move to" does; it comes after the space's functions and keeps its group
        addresses. Undoable with project_undo. Returns {function_id, space_id}."""
        require_project(ctx)

        def _move() -> dict[str, Any]:
            _require_function(function_id)
            if project.find_space(space_id) is None:
                raise ToolError(f"no space with id {space_id}")
            project.move_function(function_id, space_id)
            return {"function_id": function_id, "space_id": space_id}

        return ctx.run_locked(_move)

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
        name: str = "",
        parent_id: int | None = None,
        number: int | None = None,
        count: int = 1,
        allocation: NumberAllocation = "fill_up",
        start_with: int = 0,
        capacity: int | None = None,
    ) -> dict[str, Any]:
        """Add a group address folder: a main group (0-31, at most 32), or a middle group (0-7,
        at most 8) in the main group ``parent_id`` (a group range id, see project_get_element),
        or ``count`` of them (1 to 100, one undo step) as the Add dialog does. Without a
        ``name`` it is called "New main group" / "New middle group" (in the editor's language).
        ``number`` asks for that number (count 1 only); without it the numbers are given as
        "Generate Addresses" does: ``allocation`` "fill_up" takes the lowest free one, "append"
        the one after the highest (then the lowest free), "start_with" the first free from
        ``start_with`` on. In the Free style a group range spans ``capacity`` addresses (1 to
        65535; 1024 at the top, a quarter of ``parent_id`` in one), is called "New group range"
        without a name, and is placed where it fits beside the others: the lowest place, right
        after the highest one, or the first place from the address ``start_with`` on. Undoable
        with project_undo. Returns {group_range_id, group_range_ids, type, address, name} of the
        first one; in the Free style ``address`` is its span ("5000-5099") and ``spans`` lists
        all."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")
        if number is not None and count != 1:
            raise ToolError("pass a number only with count 1")

        def _create() -> dict[str, Any]:
            from xknxeditor.proj.core.addressing import GroupAddressStyle

            if project.group_address_style is GroupAddressStyle.FREE:
                return _create_free()
            if capacity is not None:
                raise ToolError("capacity is for group ranges of the Free style only")
            if parent_id is None:
                used = {
                    node.range_start >> 11 for node in project.get_group_range_tree()
                }
                highest, default = 31, PROJECT_S.GA_DEFAULT_MAIN_GROUP
                several = PROJECT_S.HISTORY_EV_ADDED_MAIN_GROUPS
                target = PROJECT_S.PANEL_GROUP_ADDRESSES
            else:
                parent = next(
                    (n for n in project.get_group_range_tree() if n.id == parent_id),
                    None,
                )
                if parent is None:
                    raise ToolError(
                        f"no group range with id {parent_id} (a main group)"
                    )
                used = {(child.range_start >> 8) & 7 for child in parent.children}
                highest, default = 7, PROJECT_S.GA_DEFAULT_MIDDLE_GROUP
                several = PROJECT_S.HISTORY_EV_ADDED_MIDDLE_GROUPS
                target = f"{parent.address} {parent.name}".strip()
            pool = AddressPool(
                0,
                highest,
                used,
                Allocation(allocation),
                start_with=start_with,
                skip_zero=False,
            )
            title = name.strip() or default
            ids: list[int] = []
            label = several.format(count=count, target=target) if count > 1 else None
            with project.one_undo_step(label):
                for _ in range(count):
                    wanted = number if number is not None else pool.take()
                    range_id = (
                        project.create_group_range(parent_id, title, wanted)
                        if wanted is not None
                        else None
                    )
                    if range_id is None:
                        raise ToolError(
                            "no folder can be added there (the group address style has "
                            "none, or the number is used or all numbers are)"
                        )
                    ids.append(range_id)
            details = project.element_details("group_range", ids[0])
            return {
                "group_range_id": ids[0],
                "group_range_ids": ids,
                "type": details.title_kind if details is not None else "",
                "address": (
                    f"{details.prefix}{details.address}" if details is not None else ""
                ),
                "name": title,
            }

        def _create_free() -> dict[str, Any]:
            """Free style: ranges of ``capacity`` addresses, placed where they fit."""
            parent = None
            low, high = 1, 0xFFFF
            siblings = project.get_group_range_tree()
            if parent_id is not None:
                found = _group_range(project, parent_id)
                if found is None:
                    raise ToolError(f"no group range with id {parent_id}")
                parent = found.node
                low, high = parent.range_start, parent.range_end
                siblings = parent.children
            if number is not None:
                raise ToolError("Free style ranges have no number; pass start_with")
            size = capacity or (
                1024
                if parent is None
                else max(1, (parent.range_end - parent.range_start) // 4)
            )
            if not 1 <= size <= 0xFFFF:
                raise ToolError("capacity must be 1 to 65535")
            taken = [(n.range_start, n.range_end) for n in siblings]
            title = name.strip() or PROJECT_S.GA_DEFAULT_GROUP_RANGE
            target = PROJECT_S.PANEL_GROUP_ADDRESSES if parent is None else parent.name
            label = (
                PROJECT_S.HISTORY_EV_ADDED_GROUP_RANGES.format(
                    count=count, target=target
                )
                if count > 1
                else None
            )
            ids: list[int] = []
            spans: list[str] = []
            with project.one_undo_step(label):
                for _ in range(count):
                    start = place_range(
                        low, high, taken, size, Allocation(allocation), start_with
                    )
                    if start is None:
                        raise ToolError(PROJECT_S.ADD_LIMIT_REACHED)
                    end = start + size - 1
                    try:
                        range_id = project.create_free_group_range(
                            parent_id, title, start, end
                        )
                    except ValueError as error:
                        raise ToolError(str(error)) from error
                    assert range_id is not None
                    taken.append((start, end))
                    ids.append(range_id)
                    spans.append(f"{start}-{end}")
            return {
                "group_range_id": ids[0],
                "group_range_ids": ids,
                "type": "group_range",
                "address": spans[0],
                "spans": spans,
                "name": title,
            }

        return ctx.run_locked(_create)

    @tool("write")
    def project_add_group_addresses(
        group_range_id: int,
        count: int = 1,
        name: str = "",
        allocation: NumberAllocation = "fill_up",
        start_with: int = 0,
        function_id: int | None = None,
        role: str = "",
    ) -> dict[str, Any]:
        """Add ``count`` group addresses (1 to 100, one undo step) to a middle group (a main
        group in two-level style, any group range in the Free style; ``group_range_id`` from
        project_get_element or the group tree), as its "Add Group Addresses" dialog does.
        Without a ``name`` they are called "New group address" (in the editor's language).
        ``allocation`` "fill_up" takes the lowest free address of the group, "append" the one
        after the highest, "start_with" the first free from ``start_with`` on, counted within
        the group (10 in 1/2 is 1/2/10; in the Free style the address itself); the address
        0/0/0 is never given. With ``function_id`` (a building function, from project_spaces)
        each new group address is linked with that function, with ``role``, in the same undo
        step, as the function's "Add Group Addresses…" does. Undoable with project_undo.
        Returns {group_address_ids, addresses}: the printed addresses."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")

        def _add() -> dict[str, Any]:
            from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga

            style = project.group_address_style
            free = style is GroupAddressStyle.FREE
            group = _group_range(project, group_range_id)
            if group is None or (
                style is GroupAddressStyle.THREE_LEVEL and group.parent_id is None
            ):
                raise ToolError(
                    f"no group with id {group_range_id} that holds group addresses"
                )
            node = group.node
            if function_id is not None and function_id not in _functions(project):
                raise ToolError(f"no function with id {function_id}")
            pool = AddressPool(
                node.range_start,
                node.range_end,
                _used_addresses(project),
                Allocation(allocation),
                start_with=start_with,
                base=0 if free else group_base(node.range_start, style),
            )
            title = name.strip() or PROJECT_S.GA_DEFAULT_GROUP_ADDRESS
            target = f"{node.address} {node.name}".strip()
            label = (
                PROJECT_S.HISTORY_EV_ADDED_GROUP_ADDRESSES.format(
                    count=count, target=target
                )
                if count > 1
                else None
            )
            ids: list[int] = []
            addresses: list[str] = []
            with project.one_undo_step(label):
                for _ in range(count):
                    address = pool.take()
                    if address is None:
                        raise ToolError(PROJECT_S.ADD_LIMIT_REACHED)
                    text = format_ga(address, style)
                    try:
                        ga_id = project.create_group_address_in(node.id, address, title)
                    except ValueError as error:
                        raise ToolError(str(error)) from error
                    if ga_id is None:
                        raise ToolError(f"could not create group address {text}")
                    if function_id is not None:
                        project.add_function_group_address(function_id, ga_id, role)
                    ids.append(ga_id)
                    addresses.append(text)
            return {"group_address_ids": ids, "addresses": addresses}

        return ctx.run_locked(_add)

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
