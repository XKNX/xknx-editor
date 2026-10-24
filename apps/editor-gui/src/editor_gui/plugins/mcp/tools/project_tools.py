# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""MCP tools for the actions of the Tools window (extended copy, replace device, shifting
individual and group addresses, comparing projects), the bulk creation of group addresses for a
device's group objects and the Mass Linker. They run the project plugin's own implementations,
so they behave exactly as in the GUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import require_device

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.project.plugin import ProjectPlugin

_MAX_COPIES = 100


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    def _plugin() -> ProjectPlugin:
        if ctx.project_plugin is None:
            raise ToolError("the project actions are not available here")
        return ctx.project_plugin

    def _result(count: int, errors: list[str], key: str) -> dict[str, Any]:
        return {key: count, "errors": errors}

    @tool
    def project_extended_copy(
        node_id: int,
        count: int = 1,
        find: str = "",
        replace: str = "",
        create_group_addresses: bool = False,
    ) -> dict[str, Any]:
        """The Tools window's extended copy: add ``count`` copies (1-100) of a device (node_id from
        project_list_devices) with its parameter values. ``find``/``replace`` rewrite each copy's
        name; ``create_group_addresses`` creates and links a group address for every group object
        of each copy. Each step is undoable with project_undo. Returns {copied, node_ids,
        errors}."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")
        plugin = _plugin()

        def _copy() -> dict[str, Any]:
            require_device(project, node_id)
            before = {d.node_id for d in project.devices}
            copied, errors = plugin.tools_extended_copy(
                node_id, count, find, replace, create_group_addresses
            )
            new_ids = sorted(
                d.node_id for d in project.devices if d.node_id not in before
            )
            return {"copied": copied, "node_ids": new_ids, "errors": errors}

        return ctx.run_locked(_copy, timeout=300.0)

    @tool(destructive=True)
    def project_replace_device(node_id: int, template_node_id: int) -> dict[str, Any]:
        """The Tools window's replace device: replace the device ``node_id`` by a copy of the
        device ``template_node_id`` (both from project_list_devices). The copy gets the replaced
        device's name and individual address, and its group objects the group address links of
        the replaced device's objects with the same number and size. Undoable with project_undo
        (several steps). Returns {mapped, errors}: mapped counts the objects whose links moved."""
        require_project(ctx)
        plugin = _plugin()

        def _replace() -> dict[str, Any]:
            require_device(project, node_id)
            require_device(project, template_node_id)
            if node_id == template_node_id:
                raise ToolError("a device cannot replace itself")
            return _result(
                *plugin.tools_replace_device(node_id, template_node_id), key="mapped"
            )

        return ctx.run_locked(_replace, timeout=300.0)

    @tool
    def project_shift_individual_addresses(
        node_ids: list[int], offset: int
    ) -> dict[str, Any]:
        """The Tools window's address shift: add ``offset`` to the device number of the
        individual address of each device (node_ids from project_list_devices). All or nothing:
        when an address would leave its line or collide with another device, nothing changes and
        errors says why. Undoable with project_undo. Returns {changed, errors}."""
        require_project(ctx)
        plugin = _plugin()
        return ctx.run_locked(
            lambda: _result(
                *plugin.tools_shift_addresses(node_ids, offset), key="changed"
            )
        )

    @tool
    def project_shift_group_addresses(
        group_address_ids: list[int], offset: int
    ) -> dict[str, Any]:
        """The Tools window's group address shift: add ``offset`` to each group address (ids from
        project_list_group_addresses); its links move with it. All or nothing: when an address
        would leave the valid range or collide with another group address, nothing changes and
        errors says why. Undoable with project_undo. Returns {changed, errors}."""
        require_project(ctx)
        plugin = _plugin()
        return ctx.run_locked(
            lambda: _result(
                *plugin.tools_shift_group_addresses(group_address_ids, offset),
                key="changed",
            )
        )

    @tool(read_only=True)
    def project_compare_projects(
        path: str, password: str | None = None
    ) -> dict[str, Any]:
        """The Tools window's project comparison: compare the open project with the project at
        ``path`` (an .xknx or .knxproj file on the editor's machine, opened read-only; the open
        project is not changed), ``password`` for a protected one. Returns {devices,
        group_addresses}, each a list of {key, kind, detail} with kind added, removed or changed
        (key: the individual or group address); or {error, needs_password} when the other project
        cannot be opened (needs_password: it needs a (different) password)."""
        require_project(ctx)
        plugin = _plugin()
        outcome = ctx.run_locked(
            lambda: plugin.tools_diff_projects(path, password), timeout=300.0
        )
        if outcome.result is None:
            return {"error": outcome.error, "needs_password": outcome.needs_password}

        def _entries(entries: list[Any]) -> list[dict[str, str]]:
            return [{"key": e.key, "kind": e.kind, "detail": e.detail} for e in entries]

        return {
            "devices": _entries(outcome.result.devices),
            "group_addresses": _entries(outcome.result.group_addresses),
        }

    @tool
    def project_create_group_addresses_for_com_objects(
        com_object_ref_ids: list[str] | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        start_address: str = "",
        name_template: str | None = None,
    ) -> dict[str, Any]:
        """Create a group address for each of a device's group objects and link it (as the
        sending address when the object has none yet), like Create Group Addresses in the Group
        Objects tab. Identify the device by node_id OR device_address; com_object_ref_ids (ref_id
        from project_list_com_objects) limits it to those objects, else every visible one.
        ``start_address`` numbers them from that address on, else each gets the next free one.
        ``name_template`` names them (placeholders as in the GUI, e.g. {object}, {room}, {device},
        {n}); omitted, the program-wide template is used. Each step is undoable with project_undo.
        Returns {created, group_address_ids}."""
        require_project(ctx)
        plugin = _plugin()

        def _create() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            objects = list(device.get_visible_com_objects())
            if com_object_ref_ids is not None:
                wanted = set(com_object_ref_ids)
                objects = [co for co in objects if co.id in wanted]
                missing = wanted - {co.id for co in objects}
                if missing:
                    raise ToolError(
                        f"no visible group objects {sorted(missing)} on device "
                        f"{device.node_id}"
                    )
            before = {g.id for g in project.group_addresses}
            plugin.auto_create_gas(
                device,
                objects,
                start_address.strip(),
                name_template
                if name_template is not None
                else plugin.ga_name_template(),
            )
            created = sorted(
                g.id for g in project.group_addresses if g.id not in before
            )
            return {"created": len(created), "group_address_ids": created}

        return ctx.run_locked(_create, timeout=300.0)

    @tool
    def project_link_com_objects(pairs: list[dict[str, Any]]) -> dict[str, Any]:
        """The Mass Linker's object-to-object links: for each pair create a group address and
        link both group objects to it, the source sending and the target receiving. Each pair is
        {source_node_id, source_com_object_ref_id, target_node_id, target_com_object_ref_id,
        address?, name?}: address (in the project's style) or empty for the next free one, name
        or empty for the source object's name. Each step is undoable with project_undo. Returns
        {linked, errors}."""
        require_project(ctx)
        plugin = _plugin()

        def _link() -> dict[str, Any]:
            specs: list[tuple[Any, Any, str, str]] = []
            for pair in pairs:
                objects: list[Any] = []
                for side in ("source", "target"):
                    node = pair.get(f"{side}_node_id")
                    ref = pair.get(f"{side}_com_object_ref_id")
                    if not isinstance(node, int) or not isinstance(ref, str):
                        raise ToolError(
                            f"every pair needs {side}_node_id and {side}_com_object_ref_id"
                        )
                    co = require_device(project, node).find_com_object(ref)
                    if co is None:
                        raise ToolError(f"no group object {ref!r} on device {node}")
                    objects.append(co)
                specs.append(
                    (
                        objects[0],
                        objects[1],
                        str(pair.get("address") or ""),
                        str(pair.get("name") or ""),
                    )
                )
            return _result(*plugin.mass_link_objects(specs), key="linked")

        return ctx.run_locked(_link, timeout=300.0)

    # --- plugin parameters ------------------------------------------------------

    def _plugin_sections(device: Any) -> tuple[Any, list[Any]]:
        from editor_gui.plugin_params import collect_from_device, mapping_for

        mapping = mapping_for(device.app.id if device.app is not None else None)
        if mapping is None:
            raise ToolError(
                f"device {device.node_id} has no plugin parameters the editor can show"
            )
        return mapping, collect_from_device(device, mapping)

    def _parameter_dict(parameter: Any, in_collection: bool) -> dict[str, Any]:
        from editor_gui.plugins.project.ui.plugin_params_panel import (
            collection_item_id,
        )

        data: dict[str, Any] = {
            "ref_id": parameter.ref_id,
            "label": parameter.label,
            "value": parameter.value,
            "default_value": parameter.default_value,
            "writable": parameter.writable,
        }
        if parameter.choices:
            data["choices"] = [
                {"value": value, "label": label} for value, label in parameter.choices
            ]
        if in_collection:
            data["item_id"] = collection_item_id(parameter.label)
        return data

    def _section_dict(section: Any) -> dict[str, Any]:
        binding = section.collection
        data: dict[str, Any] = {
            "section": section.section,
            "parameters": [
                _parameter_dict(p, binding is not None) for p in section.parameters
            ],
        }
        if binding is not None:
            data["collection"] = {
                "label": binding.display_label,
                "item_types": [
                    {"id": type_id, "label": label}
                    for type_id, label in binding.item_types
                ],
                "needs_name": binding.needs_name,
            }
        return data

    @tool(read_only=True)
    def project_plugin_parameters(
        node_id: int | None = None,
        device_address: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """The parameters a device's manufacturer plugin manages, as its Plugin Parameters tab
        shows them (devices with such a tab only, e.g. the Siemens KNX/DALI Gateway plus
        N 141/03). Identify the device by node_id OR device_address; ``search`` keeps the
        sections or parameters whose name contains it. Returns {title, sections}: each section
        has {section, parameters} and, for a list the user can add items to, {collection:
        {label, item_types, needs_name}}; each parameter has {ref_id, label, value,
        default_value, writable, choices?, item_id?} (item_id: for project_remove_plugin_item)."""
        require_project(ctx)

        def _read() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            mapping, sections = _plugin_sections(device)
            needle = (search or "").lower().strip()
            shown: list[dict[str, Any]] = []
            for section in sections:
                data = _section_dict(section)
                if needle and needle not in section.section.lower():
                    data["parameters"] = [
                        p for p in data["parameters"] if needle in p["label"].lower()
                    ]
                    if not data["parameters"] and "collection" not in data:
                        continue
                shown.append(data)
            return {"title": mapping.title, "sections": shown}

        return ctx.run_locked(_read)

    def _find_section(device: Any, section_name: str) -> Any:
        _mapping, sections = _plugin_sections(device)
        found = next((s for s in sections if s.section == section_name), None)
        if found is None:
            raise ToolError(f"no plugin parameter section {section_name!r}")
        return found

    @tool
    def project_set_plugin_parameter(
        ref_id: str,
        value: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a plugin parameter (ref_id from project_plugin_parameters, writable ones only) as
        the Plugin Parameters tab does; for a parameter with choices pass a choice's value.
        Identify the device by node_id OR device_address. Returns {ref_id, value, changed}:
        value as the device holds it afterwards."""
        require_project(ctx)
        plugin = _plugin()

        def _set() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            _mapping, sections = _plugin_sections(device)
            parameter = next(
                (p for s in sections for p in s.parameters if p.ref_id == ref_id), None
            )
            if parameter is None:
                raise ToolError(
                    f"no plugin parameter {ref_id!r} on device {device.node_id}"
                )
            if not parameter.writable:
                raise ToolError(f"plugin parameter {ref_id!r} cannot be changed")
            plugin.handle_param_change(device, ref_id, value)
            # A parameter edit can rebuild the device: read the value back from the new one.
            _mapping, after = _plugin_sections(require_device(project, device.node_id))
            now = next(
                (p.value for s in after for p in s.parameters if p.ref_id == ref_id),
                parameter.value,
            )
            return {"ref_id": ref_id, "value": now, "changed": now != parameter.value}

        return ctx.run_locked(_set)

    @tool
    def project_add_plugin_item(
        section: str,
        name: str = "",
        item_type: str = "",
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Add an item to a plugin parameter list (a section with ``collection`` in
        project_plugin_parameters), as its Add button does: ``item_type`` is one of the
        collection's item_types ids when it has any, ``name`` is needed when needs_name is true.
        Returns {section, added, count}; count is the number of items afterwards."""
        require_project(ctx)
        plugin = _plugin()

        def _add() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            found = _find_section(device, section)
            binding = found.collection
            if binding is None:
                raise ToolError(f"section {section!r} is not a list")
            types = {type_id for type_id, _label in binding.item_types}
            if types and item_type not in types:
                raise ToolError("item_type must be one of: " + ", ".join(sorted(types)))
            before = len(found.parameters)
            plugin.add_plugin_collection_item(device, binding, item_type, name)
            count = len(
                _find_section(
                    require_device(project, device.node_id), section
                ).parameters
            )
            return {"section": section, "added": count > before, "count": count}

        return ctx.run_locked(_add)

    @tool(destructive=True)
    def project_remove_plugin_item(
        section: str,
        item_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Remove an item (item_id from project_plugin_parameters) from a plugin parameter list,
        as its Remove button does. Returns {section, removed, count}."""
        require_project(ctx)
        plugin = _plugin()

        def _remove() -> dict[str, Any]:
            from editor_gui.plugins.project.ui.plugin_params_panel import (
                collection_item_id,
            )

            device = require_device(project, node_id, device_address)
            found = _find_section(device, section)
            if found.collection is None:
                raise ToolError(f"section {section!r} is not a list")
            if item_id not in {collection_item_id(p.label) for p in found.parameters}:
                raise ToolError(f"no item {item_id!r} in {section!r}")
            before = len(found.parameters)
            plugin.remove_plugin_collection_item(device, found.collection, item_id)
            count = len(
                _find_section(
                    require_device(project, device.node_id), section
                ).parameters
            )
            return {"section": section, "removed": count < before, "count": count}

        return ctx.run_locked(_remove)
