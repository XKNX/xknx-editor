# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""MCP tools for the actions of the Tools window (extended copy, replace device, shifting
individual and group addresses, comparing projects), the bulk creation of group addresses for a
device's group objects and the Mass Linker. They run the project plugin's own implementations,
so they behave exactly as in the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import (
    require_device,
    require_group_address_id,
)

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

    @tool("read", open_world=True)
    def project_compare_mdt_symbols(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 120,
    ) -> dict[int, bool]:
        """Read device CRCs and compare all symbols including defaults, without writing or restarting.
        Address by node_id OR device_address. Missing default pixels must be available in
        the attached app library; otherwise the operation fails before bus access.
        Returns zero-based slot -> equal.
        """
        return _run_symbols(
            node_id, device_address, timeout, False, include_defaults=True
        )

    @tool("write", open_world=True)
    def project_read_mdt_symbols(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 300,
    ) -> dict[str, Any]:
        """Read all MDT device symbol pixels into the project in one undo step.

        Address by node_id OR device_address. Requires no existing symbol library.
        Reads the application identity, every pixel and CRC, never writes to or restarts
        the device. Refused, incomplete or inconsistent reads leave the project unchanged.
        Replaces the selected device's stored symbols; the default library selection is
        unknown after read-back. Retains parameters, links, flags and download status.
        Refuses to apply if the device or its symbols changed during the read. Returns
        the current symbol view. Reading can take several minutes. Call only when the
        user requests it; the GUI asks before starting a read for missing symbols.
        """
        from editor_gui.plugins.mcp.tools.connection import (
            await_operation,
            finished,
            not_started,
            wait_for_operation,
        )
        from editor_gui.plugins.project.program_queue import ORIGIN_MCP

        require_project(ctx)

        def start() -> tuple[Any, Any, Any]:
            device = require_device(project, node_id, device_address)
            target = project.prepare_mdt_symbol_read(device.node_id)
            if ctx.api.queue_action is not None:
                return (
                    target,
                    ctx.api.queue_action(
                        device.node_id, "read_mdt_symbols", origin=ORIGIN_MCP
                    ),
                    None,
                )
            return target, None, ctx.api.connection.read_mdt_symbols(device)

        target, item, future = ctx.run_locked(start)
        if item is not None:
            finished(wait_for_operation(ctx, item, timeout))
        else:
            if future is None:
                raise not_started(ctx.api.connection, "the symbol read")
            symbols = await_operation(
                ctx.api.connection, future, timeout, "the symbol read"
            )
            ctx.run_locked(lambda: project.apply_mdt_symbol_read(target, symbols))
        return ctx.run_locked(lambda: project.mdt_symbols(target.node_id))

    @tool("destructive", open_world=True)
    def project_program_mdt_symbols(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 180,
        include_defaults: bool = False,
    ) -> int:
        """Write every stored custom MDT symbol, verify CRCs, then restart the device.
        Address by node_id OR device_address. Honors Block Bus Writes; returns symbols written.
        Set include_defaults=true for the entire library, including protected original defaults.
        Requires the attached library for missing slots. Does not program firmware.
        """
        return _run_symbols(
            node_id, device_address, timeout, True, include_defaults=include_defaults
        )

    @tool("write", open_world=True)
    def project_synchronize_mdt_symbols(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 120,
    ) -> dict[int, bool]:
        """Refresh Smart device CRCs, then compare all custom and default symbols.
        Address by node_id OR device_address. Requires the complete attached library.
        Writes checksum control on Smart, only reads on Central. Honors Block Bus Writes.
        Does not write pixels, restart, or change the project. Returns zero-based slot -> equal.
        """
        return _run_symbols(
            node_id,
            device_address,
            timeout,
            False,
            include_defaults=True,
            synchronize=True,
        )

    def _run_symbols(
        node_id: int | None,
        device_address: str | None,
        timeout: float,
        writes: bool,
        *,
        include_defaults: bool,
        synchronize: bool = False,
    ) -> Any:
        from editor_gui.plugins.mcp.tools.connection import (
            await_operation,
            finished,
            not_started,
            wait_for_operation,
        )
        from editor_gui.plugins.project.program_queue import ORIGIN_MCP

        require_project(ctx)
        service = ctx.api.connection

        def start() -> tuple[Any, Any]:
            device = require_device(project, node_id, device_address)
            symbols = project.mdt_symbol_payload(
                device.node_id, include_defaults=include_defaults
            )
            if ctx.api.queue_action is not None:
                return ctx.api.queue_action(
                    device.node_id,
                    "mdt_symbols",
                    origin=ORIGIN_MCP,
                    symbols=symbols,
                    writes=writes,
                    include_defaults=include_defaults,
                    synchronize=synchronize,
                ), None
            return None, service.run_mdt_symbols(
                device, symbols, writes=writes, synchronize=synchronize
            )

        item, future = ctx.run_locked(start)
        if item is not None:
            return finished(wait_for_operation(ctx, item, timeout)).result
        if future is None:
            raise not_started(service, "the symbol operation")
        return await_operation(service, future, timeout, "the symbol operation")

    @tool("write")
    def project_load_mdt_default_symbols(
        new_library: bool = False,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Replace all custom symbols with defaults in one undo step, without bus access.
        Address by node_id OR device_address. Smart: new_library=false selects the first
        library, true the second, independently of the device generation. Central has one.
        Requires original pixel resources attached to the project. Program with
        project_program_mdt_symbols(include_defaults=true) to transfer the complete library.
        """
        require_project(ctx)

        def apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.load_mdt_default_symbols(device.node_id, new_library=new_library)
            return project.mdt_symbols(device.node_id)

        return ctx.run_locked(apply)

    @tool("write")
    def project_import_mdt_default_library(
        path: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Add missing MDT default symbols from a local .etsapp or symbol resource ZIP.

        Address by node_id OR device_address. Validates the app identity, supported
        application version and every symbol CRC. Stores only standard pixel resources
        once in the project for all matching devices, without executing app code.
        Retains custom symbols, library selections, links and download status. One undo
        step, no bus access. Returns the selected device's current symbol library.
        """
        require_project(ctx)
        from xknxeditor.vendor.mdt.symbol_resources import MAX_APP_SIZE

        with Path(path).open("rb") as stream:
            archive = stream.read(MAX_APP_SIZE + 1)

        def apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.import_mdt_default_library(device.node_id, archive)
            return project.mdt_symbols(device.node_id)

        return ctx.run_locked(apply)

    @tool("write")
    def project_import_mdt_symbol_library(
        path: str,
        invert: bool = False,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Import a complete symbol ZIP from a local editor path in one undo step, offline.
        Address by node_id OR device_address. Root-level Symbol_3.bmp..Symbol_36.bmp for
        Smart or Symbol_1.bmp..Symbol_20.bmp for Central, exactly 64x64 pixels, BMP format.
        Replaces every editable slot; protected slots and library selection stay unchanged.
        RGB average below 128 is black; invert reverses all colors before conversion.
        Returns the current library. Use project_program_mdt_symbols to transfer it.
        """
        require_project(ctx)
        archive = Path(path).read_bytes()

        def apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.import_mdt_symbol_library(device.node_id, archive, invert=invert)
            return project.mdt_symbols(device.node_id)

        return ctx.run_locked(apply)

    @tool("write")
    def project_export_mdt_symbol_library(
        path: str, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Export all editable effective symbols to a ZIP at a local editor path.
        Address by node_id OR device_address. Contains 64x64 black/white BMP files named
        Symbol_3.bmp..Symbol_36.bmp (Smart) or Symbol_1.bmp..Symbol_20.bmp (Central).
        Resolves defaults from the attached app resources; fails if pixels are missing.
        No bus or project changes. Returns path and bytes.
        """
        require_project(ctx)
        data = ctx.run_locked(
            lambda: project.export_mdt_symbol_library(
                require_device(project, node_id, device_address).node_id
            )
        )
        Path(path).write_bytes(data)
        return {"path": path, "bytes": len(data)}

    @tool("write")
    def project_import_mdt_visucontrol(
        path: str, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Import an .etsvcp into a VisuControl Plus device. Path is local to the editor.
        Address by node_id OR device_address. Validates the complete image before an atomic
        undoable edit. Empty scheduler/scene parts retain their previous values.
        Preserves compatible links and returns lost_links for removed objects.
        """
        require_project(ctx)
        data = Path(path).read_bytes()
        return ctx.run_locked(
            lambda: {
                "lost_links": project.import_mdt_visucontrol(
                    require_device(project, node_id, device_address).node_id, data
                )
            }
        )

    @tool("write")
    def project_export_mdt_visucontrol(
        path: str, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Export the current VisuControl Plus configuration to .etsvcp at a local editor path.
        Address by node_id OR device_address. Returns path and bytes; no project or bus changes.
        """
        require_project(ctx)
        data = ctx.run_locked(
            lambda: project.export_mdt_visucontrol(
                require_device(project, node_id, device_address).node_id
            )
        )
        Path(path).write_bytes(data)
        return {"path": path, "bytes": len(data)}

    @tool("read")
    def project_mdt_symbols(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Read an MDT Smart device's symbol library and custom slots. Address by node_id OR
        device_address. Returns app_id, slots, first_custom_slot, new_library
        (null for a complete read-back without a known default library), slot_info
        and default_libraries (available library selections, false=first, true=second),
        (zero-based slot, localized name, fixed role or null, group, editable), groups
        (functional/normal with localized labels), symbols and defaults from the attached
        library (slot, grayscale_hex: 4096 row-major pixels, data_hex: 514 packed bytes
        including CRC). Protected slots are included for viewing; names are fixed by the
        device family, not editable fields."""
        require_project(ctx)
        return ctx.run_locked(
            lambda: project.mdt_symbols(
                require_device(project, node_id, device_address).node_id
            )
        )

    @tool("read")
    def project_mdt_layout(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Read GTS page layouts and function choices. Address by node_id OR device_address.
        Returns orientation V/H, display width/height, pages (number, name, orientation,
        status, tiles), functions and status_functions (number, name). Page 0 is standby day,
        -1 standby night, positive numbers regular pages. Tile fields: positive x/y device
        coordinates, kind 0..6, function number, transparency from
        [255,32,29,26,23,20,16,13,10,6,3,0] (255 default), color 0 default, 1/2 individual colors."""
        require_project(ctx)
        return ctx.run_locked(
            lambda: project.mdt_layout(
                require_device(project, node_id, device_address).node_id
            )
        )

    @tool("write")
    def project_set_mdt_layout(
        page_number: int,
        name: str,
        orientation: str,
        tiles: list[dict[str, int]],
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Replace one GTS page layout, at most 12 tiles, in one undo step. Address by node_id
        OR device_address. Use page_number, orientation and tile fields from project_mdt_layout.
        Writes ordinary device parameters and keeps surviving object links. No bus access."""
        require_project(ctx)

        def apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.set_mdt_layout(
                device.node_id, page_number, name, orientation, tiles
            )
            return project.mdt_layout(device.node_id)

        return ctx.run_locked(apply)

    @tool("write")
    def project_set_mdt_symbol(
        slot: int,
        grayscale_hex: str | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a zero-based MDT custom symbol slot, or remove its override with null.
        Slots start at first_custom_slot from project_mdt_symbols (2 for Smart, 0 for Central).
        grayscale_hex holds 4096 row-major bytes, 64 by 64, black below 128. Address by
        node_id OR device_address. One undo step, no bus access. Returns the current library."""
        require_project(ctx)

        def apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.set_mdt_symbol(device.node_id, slot, grayscale_hex)
            return project.mdt_symbols(device.node_id)

        return ctx.run_locked(apply)

    @tool("write")
    def project_restore_mdt_symbol(
        slot: int, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Restore a zero-based symbol slot from the app library attached to this project.
        Address by node_id OR device_address. Creates a stored symbol in one undo step;
        use project_program_mdt_symbols to transfer it. No DLL is executed.
        """
        require_project(ctx)

        def restore() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.restore_mdt_symbol(device.node_id, slot)
            return project.mdt_symbols(device.node_id)

        return ctx.run_locked(restore)

    @tool("write")
    def project_set_mdt_symbol_library(
        new_library: bool, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Select the newer or older default symbol library for an MDT Smart device.
        Address by node_id OR device_address. Does not write to the bus or replace custom
        symbols; changes the library selection in one undo step. Returns the current library."""
        require_project(ctx)

        def apply() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            project.set_mdt_symbol_library(device.node_id, new_library)
            return project.mdt_symbols(device.node_id)

        return ctx.run_locked(apply)

    @tool("write")
    def project_import_mdt_symbol(
        path: str,
        slot: int,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Import a 64 by 64 PNG/BMP or 514-byte .data into a zero-based MDT symbol slot.
        Images must be exactly 64 by 64 pixels; no resizing. Black and white with a solid
        white background is recommended. RGB channels are averaged; below 128 becomes
        black on the device, otherwise white. Alpha/transparency is ignored. MDT .data
        must contain one packed symbol with a valid checksum. Replaces the selected
        symbol in the project; use project_program_mdt_symbols to transfer to the device.
        Path is local to the running editor. Address by node_id OR device_address.
        One undo step, no bus access; returns the current library."""
        from xknxeditor.vendor.mdt.symbols import import_symbol_image

        return project_set_mdt_symbol(
            slot,
            import_symbol_image(Path(path).read_bytes()).hex(),
            node_id,
            device_address,
        )

    @tool("write")
    def project_export_mdt_symbol(
        path: str,
        slot: int,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, str]:
        """Export a custom or attached default symbol as a 64 by 64 grayscale PNG to a local
        editor path. Preserves imported grayscale values; the device uses a black/white
        threshold of 128. Address by node_id OR device_address; slot is zero-based.
        Fails for a default symbol without pixel data.
        Returns path. Does not change the project or access the bus."""
        from xknxeditor.vendor.mdt.symbols import export_symbol_image

        view = project_mdt_symbols(node_id, device_address)
        symbol = next(
            (s for s in (*view["symbols"], *view["defaults"]) if s["slot"] == slot),
            None,
        )
        if symbol is None:
            raise ToolError("No symbol pixels available in this slot")
        Path(path).write_bytes(
            export_symbol_image(bytes.fromhex(symbol["grayscale_hex"]))
        )
        return {"path": path}

    @tool("write")
    def project_extended_copy(
        node_id: int | None = None,
        device_address: str | None = None,
        count: int = 1,
        find: str = "",
        replace: str = "",
        create_group_addresses: bool = False,
    ) -> dict[str, Any]:
        """The Tools window's extended copy: add ``count`` copies (1-100) of a device with its
        parameter values. Identify the device by ``node_id`` OR ``device_address`` (an individual
        address like "1.0.30"); pass exactly one. ``find``/``replace`` rewrite each copy's name;
        ``create_group_addresses`` creates and links a group address for every group object of
        each copy. Each step is undoable with project_undo. Returns {copied, node_ids, errors}."""
        require_project(ctx)
        if not 1 <= count <= _MAX_COPIES:
            raise ToolError(f"count must be 1 to {_MAX_COPIES}")
        plugin = _plugin()

        def _copy() -> dict[str, Any]:
            nid = require_device(project, node_id, device_address).node_id
            before = {d.node_id for d in project.devices}
            copied, errors = plugin.tools_extended_copy(
                nid, count, find, replace, create_group_addresses
            )
            new_ids = sorted(
                d.node_id for d in project.devices if d.node_id not in before
            )
            return {"copied": copied, "node_ids": new_ids, "errors": errors}

        return ctx.run_locked(_copy, timeout=300.0)

    @tool("destructive")
    def project_replace_device(
        node_id: int | None = None,
        device_address: str | None = None,
        template_node_id: int | None = None,
        template_device_address: str | None = None,
    ) -> dict[str, Any]:
        """The Tools window's replace device: replace a device by a copy of a template device.
        Identify the replaced device by ``node_id`` OR ``device_address`` (an individual address
        like "1.0.30"), the template by ``template_node_id`` OR ``template_device_address``; one
        of each pair. The copy gets the replaced device's name and individual address, and its
        group objects the group address links of the replaced device's objects with the same
        number and size. Undoable with project_undo (several steps). Returns {mapped, errors}:
        mapped counts the objects whose links moved."""
        require_project(ctx)
        plugin = _plugin()
        if (template_node_id is None) == (template_device_address is None):
            raise ToolError(
                "pass exactly one of template_node_id or template_device_address"
            )

        def _replace() -> dict[str, Any]:
            nid = require_device(project, node_id, device_address).node_id
            template = require_device(
                project, template_node_id, template_device_address
            ).node_id
            if nid == template:
                raise ToolError("a device cannot replace itself")
            return _result(*plugin.tools_replace_device(nid, template), key="mapped")

        return ctx.run_locked(_replace, timeout=300.0)

    @tool("write")
    def project_shift_individual_addresses(
        offset: int,
        node_ids: list[int] | None = None,
        device_addresses: list[str] | None = None,
    ) -> dict[str, Any]:
        """The Tools window's address shift: add ``offset`` to the device number of the
        individual address of each device, given by ``node_ids`` and/or ``device_addresses``
        (their individual addresses like "1.0.30"). All or nothing: when an address would leave
        its line or collide with another device, nothing changes and errors says why. Undoable
        with project_undo. Returns {changed, errors}."""
        require_project(ctx)
        plugin = _plugin()
        if not node_ids and not device_addresses:
            raise ToolError("pass node_ids or device_addresses")

        def _shift() -> dict[str, Any]:
            nids = [
                *(node_ids or ()),
                *(
                    require_device(project, device_address=a).node_id
                    for a in device_addresses or ()
                ),
            ]
            return _result(*plugin.tools_shift_addresses(nids, offset), key="changed")

        return ctx.run_locked(_shift)

    @tool("write")
    def project_shift_group_addresses(
        offset: int,
        group_address_ids: list[int] | None = None,
        group_addresses: list[str] | None = None,
    ) -> dict[str, Any]:
        """The Tools window's group address shift: add ``offset`` to each group address, given by
        ``group_address_ids`` (from project_list_group_addresses) and/or ``group_addresses``
        (printed addresses like "10/1/2" in the project's style); its links move with it. All or
        nothing: when an address would leave the valid range or collide with another group
        address, nothing changes and errors says why. Undoable with project_undo. Returns
        {changed, errors}."""
        require_project(ctx)
        plugin = _plugin()
        if not group_address_ids and not group_addresses:
            raise ToolError("pass group_address_ids or group_addresses")

        def _shift() -> dict[str, Any]:
            ids = [
                *(group_address_ids or ()),
                *(
                    require_group_address_id(project, group_address=a)
                    for a in group_addresses or ()
                ),
            ]
            return _result(
                *plugin.tools_shift_group_addresses(ids, offset), key="changed"
            )

        return ctx.run_locked(_shift)

    @tool("read")
    def project_compare_projects(
        path: str, password: str | None = None, timeout: float = 300.0
    ) -> dict[str, Any]:
        """The Tools window's project comparison: compare the open project with the project at
        ``path`` (an .xknx or .knxproj file on the editor's machine, opened read-only; the open
        project is not changed), ``password`` for a protected one. Returns {devices,
        group_addresses}, each a list of {key, kind, detail} with kind added, removed or changed
        (key: the individual or group address, "-" for a device without one); or {error,
        needs_password} when the other project cannot be opened (needs_password: it needs a
        (different) password).

        Devices are matched as the same device, not only by address: by the id of the project
        file they were imported from, then (two files of the same project) by their id, then by
        individual address; so a device with another address is one changed device ("address
        '1.1.5' -> '1.1.7'" in its detail). The detail of a changed device names its differing
        address, name, product, order number, application and location (building > ... > room).
        A changed device also has ``parameters`` and ``group_objects``: the differing ones as
        {label, kind, current, other} (their values in the open and in the other project, "" where
        absent; a group object's value sums up DPT, flags, priority and linked group addresses).

        It runs in the background like in the Tools window; ``timeout`` in seconds, after which
        it is stopped."""
        require_project(ctx)
        plugin = _plugin()
        job = ctx.run_locked(lambda: plugin.start_project_diff(path, password))
        try:
            outcome = job.future.result(timeout=timeout)
        except TimeoutError as exc:
            job.cancel()
            raise ToolError(
                f"the comparison did not finish within {timeout} s and was stopped"
            ) from exc
        if outcome.canceled:
            raise ToolError("the comparison was stopped")
        if outcome.result is None:
            return {"error": outcome.error, "needs_password": outcome.needs_password}

        def _rows(entry: Any, section: str) -> list[dict[str, str]]:
            return [
                {
                    "label": r.label,
                    "kind": r.kind,
                    "current": r.current,
                    "other": r.other,
                }
                for r in entry.rows
                if r.section == section
            ]

        def _entries(entries: list[Any]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for e in entries:
                item: dict[str, Any] = {
                    "key": e.key,
                    "kind": e.kind,
                    "detail": e.detail,
                }
                if e.rows:
                    item["parameters"] = _rows(e, "parameter")
                    item["group_objects"] = _rows(e, "group_object")
                out.append(item)
            return out

        return {
            "devices": _entries(outcome.result.devices),
            "group_addresses": _entries(outcome.result.group_addresses),
        }

    @tool("write")
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

    @tool("write")
    def project_link_com_objects(pairs: list[dict[str, Any]]) -> dict[str, Any]:
        """The Mass Linker's object-to-object links: for each pair create a group address and
        link both group objects to it, the source sending and the target receiving. Each pair is
        {source_node_id | source_device_address, source_com_object_ref_id, target_node_id |
        target_device_address, target_com_object_ref_id, address?, name?}: each device by its
        node_id OR its individual address (like "1.0.30"), address (in the project's style) or
        empty for the next free one, name or empty for the source object's name. Each step is
        undoable with project_undo. Returns {linked, errors}."""
        require_project(ctx)
        plugin = _plugin()

        def _link() -> dict[str, Any]:
            specs: list[tuple[Any, Any, str, str]] = []
            for pair in pairs:
                objects: list[Any] = []
                for side in ("source", "target"):
                    node = pair.get(f"{side}_node_id")
                    address = pair.get(f"{side}_device_address")
                    ref = pair.get(f"{side}_com_object_ref_id")
                    if (
                        not isinstance(node, int | None)
                        or not isinstance(address, str | None)
                        or (node is None) == (address is None)
                        or not isinstance(ref, str)
                    ):
                        raise ToolError(
                            f"every pair needs {side}_node_id or {side}_device_address, "
                            f"and {side}_com_object_ref_id"
                        )
                    device = require_device(project, node, address)
                    co = device.find_com_object(ref)
                    if co is None:
                        raise ToolError(
                            f"no group object {ref!r} on device {device.node_id}"
                        )
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
        mapping = project.plugin_mapping(device)
        if mapping is None:
            raise ToolError(
                f"device {device.node_id} has no plugin parameters the editor can show"
            )
        try:
            return mapping, project.plugin_sections(device, mapping)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

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
        # The item a row belongs to (e.g. "[2] Group 'Sauna'"): the label alone is just the field
        # name, so the item name lives here. Exposed and searched so a client can still find a row
        # by its item name.
        if parameter.group:
            data["group"] = parameter.group
        if parameter.raw_value is not None:
            data["raw_value"] = parameter.raw_value
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
        if section.collapsed:
            data["collapsed"] = True
        return data

    @tool("read")
    def project_plugin_parameters(
        node_id: int | None = None,
        device_address: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """The parameters a device's manufacturer plugin manages, as its Plugin Parameters tab
        shows them (devices with such a tab only, e.g. the Siemens KNX/DALI Gateway plus
        N 141/03). Identify the device by node_id OR device_address; ``search`` keeps the
        sections or parameters whose name or item name contains it. Returns {title, sections}:
        each section has {section, parameters, collapsed?} and, for a list the user can add items to,
        {collection: {label, item_types, needs_name}}; each parameter has {ref_id, label, value,
        default_value, writable, group?, choices?, item_id?, raw_value?} (group: the item the row belongs to,
        e.g. "[2] Group 'Sauna'"; item_id: for project_remove_plugin_item).
        Saved configurations are selected automatically when supported. Internal values remain
        available in initially collapsed sections. Reading never persists a conversion."""
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
                        value
                        for parameter, value in zip(
                            section.parameters, data["parameters"], strict=True
                        )
                        if any(
                            needle in text.lower()
                            for text in (
                                parameter.name,
                                parameter.label,
                                parameter.value,
                                parameter.group,
                            )
                        )
                    ]
                    if not data["parameters"] and "collection" not in data:
                        continue
                shown.append(data)
            return {"title": mapping.title, "sections": shown}

        return ctx.run_locked(_read)

    @tool("read")
    def project_generic_plugin_parameters(
        node_id: int | None = None,
        device_address: str | None = None,
        search: str | None = None,
        only_mismatches: bool = False,
        show_unfitted: bool = False,
    ) -> dict[str, Any]:
        """The settings the Siemens Generic Plug-in stored for a device, as Configuration
        Comparison in Plugin Config shows them (devices whose product data carries the plug-in's XML
        description, e.g. the N 512/11 switching actuator). Identify the device by node_id OR
        device_address; ``search`` keeps the pages or rows whose title, label or value contains
        it; ``only_mismatches`` keeps the rows whose value the stored memory image does not hold;
        ``show_unfitted`` adds the settings of extension modules that are not fitted (as the
        section's "Show settings for modules not fitted" tick box; never checked against the image).
        Returns {application, stored_application, application_matches, last_download,
        has_image, image_layout_known, counts: {match, mismatch, not_in_memory, unchecked} (of
        fitted modules), unfitted_count, pages}: each page has {title (empty for settings
        outside a block), channel (0 for channel-independent pages), fitted (false: a module
        not fitted), parameters}; each parameter has {ref_id, parameter, label, value
        (symbolic; the raw number for a "Percent" setting), value_text (the meaning; a
        "Percent" setting scaled to its full scale, e.g. "40 %"; a time whose name gives its
        format in that format with the raw time, e.g. "15:00 (900 s)"), scaled (true for the
        percentages), clock (true for those times),
        default_value, stored (false: the default), assigned (set by a rule of the
        description), fitted, memory ("match", "mismatch", "not_in_memory" or "unchecked"),
        address?, memory_value?, memory_value_text?}; a separator line has {separator: true,
        label}. After connection_read_plugin_memory, memory checks use that session's
        device read instead of the stored image; memory_source identifies "device" or
        "stored". Read only: nothing can be changed or downloaded."""
        require_project(ctx)
        plugin = _plugin()

        def _read() -> dict[str, Any]:
            from xknxeditor.vendor.siemens_generic import MemoryCheck

            device = require_device(project, node_id, device_address)
            view = plugin.generic_plugin_view(device, show_unfitted=show_unfitted)
            if view is None:
                raise ToolError(
                    f"device {device.node_id} has no Generic Plug-in settings the editor "
                    "can show"
                )
            needle = (search or "").lower().strip()
            pages: list[dict[str, Any]] = []
            for page in view.pages:
                title_hit = bool(needle) and needle in page.title.lower()
                rows: list[dict[str, Any]] = []
                for row in page.rows:
                    if only_mismatches and row.memory is not MemoryCheck.MISMATCH:
                        continue
                    if (
                        needle
                        and not title_hit
                        and needle not in row.label.lower()
                        and needle not in row.value_text.lower()
                        and needle not in row.value.lower()
                    ):
                        continue
                    if row.separator:
                        rows.append({"separator": True, "label": row.label})
                        continue
                    data: dict[str, Any] = {
                        "ref_id": row.ref_id,
                        "parameter": row.parameter,
                        "label": row.label,
                        "value": row.value,
                        "value_text": row.value_text,
                        "default_value": row.default_value,
                        "scaled": row.scaled,
                        "clock": row.clock,
                        "stored": row.stored,
                        "assigned": row.assigned,
                        "fitted": row.fitted,
                        "memory": row.memory.value,
                    }
                    if row.address is not None:
                        data["address"] = row.address
                    if row.memory_value:
                        data["memory_value"] = row.memory_value
                        data["memory_value_text"] = row.memory_value_text
                    rows.append(data)
                if any("ref_id" in r for r in rows):
                    pages.append(
                        {
                            "title": page.title,
                            "channel": page.channel,
                            "fitted": page.fitted,
                            "parameters": rows,
                        }
                    )
            return {
                "application": view.application_id,
                "stored_application": view.stored_application_id,
                "application_matches": view.application_matches,
                "last_download": view.last_download,
                "memory_source": "device"
                if plugin.generic_plugin_memory_result(device) is not None
                else "stored",
                "has_image": view.has_image,
                "image_layout_known": view.image_layout_known,
                "counts": {check.value: view.count(check) for check in MemoryCheck},
                "unfitted_count": view.unfitted_count,
                "pages": pages,
            }

        return ctx.run_locked(_read)

    @tool("read")
    def project_generic_plugin_compare(
        node_id: int | None = None,
        device_address: str | None = None,
        compare_node_id: int | None = None,
        compare_device_address: str | None = None,
        only_differences: bool = True,
        show_unfitted: bool = False,
    ) -> dict[str, Any]:
        """Two devices' Siemens Generic Plug-in settings side by side, as Configuration
        Comparison in Plugin Config shows them with "Compare With" (both devices must have the same
        application). Identify the first device by node_id OR device_address and the second by
        compare_node_id OR compare_device_address. ``only_differences`` (default true) keeps
        the settings whose values differ; ``show_unfitted`` includes the settings of extension
        modules that are not fitted. Returns {first, second (individual addresses),
        differences (count), compared (settings compared), pages}: each page has {title,
        channel, fitted, parameters}; each parameter has {ref_id, label, differs, first,
        second}, where first/second are {value, value_text, scaled, clock, stored, memory} or null when
        that device does not show the setting (another branch is active there). Read only."""
        require_project(ctx)
        plugin = _plugin()

        def _read() -> dict[str, Any]:
            from xknxeditor.vendor.siemens_generic import (
                GenericView,
                Row,
                compare_views,
            )

            first = require_device(project, node_id, device_address)
            if compare_node_id is None and compare_device_address is None:
                raise ToolError(
                    "pass the second device as compare_node_id or compare_device_address"
                )
            second = require_device(project, compare_node_id, compare_device_address)
            if first.node_id == second.node_id:
                raise ToolError("the two devices are the same device")
            views: list[GenericView] = []
            for device in (first, second):
                view = plugin.generic_plugin_view(device, show_unfitted=show_unfitted)
                if view is None:
                    raise ToolError(
                        f"device {device.node_id} has no Generic Plug-in settings the editor "
                        "can show"
                    )
                views.append(view)
            comparison = compare_views(views[0], views[1])
            if not comparison.same_application:
                raise ToolError("the two devices have different applications")

            def side(row: Row | None) -> dict[str, Any] | None:
                if row is None:
                    return None
                return {
                    "value": row.value,
                    "value_text": row.value_text,
                    "scaled": row.scaled,
                    "clock": row.clock,
                    "stored": row.stored,
                    "memory": row.memory.value,
                }

            pages: list[dict[str, Any]] = []
            compared = 0
            for page in comparison.pages:
                rows: list[dict[str, Any]] = []
                for entry in page.rows:
                    compared += 1
                    if only_differences and not entry.differs:
                        continue
                    rows.append(
                        {
                            "ref_id": entry.row.ref_id,
                            "label": entry.row.label,
                            "differs": entry.differs,
                            "first": side(entry.first),
                            "second": side(entry.second),
                        }
                    )
                if rows:
                    pages.append(
                        {
                            "title": page.title,
                            "channel": page.channel,
                            "fitted": page.fitted,
                            "parameters": rows,
                        }
                    )
            return {
                "first": first.individual_address,
                "second": second.individual_address,
                "differences": comparison.differences,
                "compared": compared,
                "pages": pages,
            }

        return ctx.run_locked(_read)

    def _find_section(device: Any, section_name: str) -> Any:
        _mapping, sections = _plugin_sections(device)
        found = next((s for s in sections if s.section == section_name), None)
        if found is None:
            raise ToolError(f"no plugin parameter section {section_name!r}")
        return found

    @tool("write")
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
            from editor_gui.plugin_providers import handles_parameter

            if handles_parameter(device, ref_id):
                try:
                    lost = project.edit_plugin_parameter(device, ref_id, value)
                except ValueError as exc:
                    raise ToolError(str(exc)) from exc
                _mapping, after = _plugin_sections(
                    require_device(project, device.node_id)
                )
                now = next(
                    (
                        p.value
                        for s in after
                        for p in s.parameters
                        if p.ref_id == ref_id
                    ),
                    parameter.value,
                )
                return {
                    "ref_id": ref_id,
                    "value": now,
                    "changed": now != parameter.value,
                    "lost_links": lost,
                }
            plugin = _plugin()
            if ref_id.startswith("triton_lcd:"):
                try:
                    plugin.handle_param_change(
                        device, ref_id, value, raise_on_error=True
                    )
                except ValueError as exc:
                    raise ToolError(str(exc)) from exc
            else:
                plugin.handle_param_change(device, ref_id, value)
            # A parameter edit can rebuild the device: read the value back from the new one.
            _mapping, after = _plugin_sections(require_device(project, device.node_id))
            now = next(
                (p.value for s in after for p in s.parameters if p.ref_id == ref_id),
                parameter.value,
            )
            return {"ref_id": ref_id, "value": now, "changed": now != parameter.value}

        return ctx.run_locked(_set)

    @tool("read")
    def project_plugin_capabilities(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Return the mapped native provider, setup state, supported-profile status,
        read-only preflight availability, allowed download_scopes and programming error
        (null when programming is available). readable identifies
        configurations the parameter view can inspect; stored_configuration means the original
        saved blob is selected without a persisted native conversion. Identification
        of a plugin family does not imply that its device profile is supported.
        """
        from dataclasses import asdict

        from editor_gui.plugin_providers import require_provider

        require_project(ctx)

        def run() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            try:
                provider = require_provider(device)
                capabilities = project.plugin_capabilities(device)
                assert capabilities is not None
                return {
                    "provider": provider.id,
                    **asdict(capabilities),
                    "download_scopes": [s.name for s in capabilities.download_scopes],
                }
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return ctx.run_locked(run)

    @tool("write")
    def project_setup_plugin(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Explicitly initialize the device's mapped native configuration as one undo step.
        Requires a supported product profile. Updates the stored plugin blob. Links
        without proven native roles are removed and returned as lost_links.
        """
        require_project(ctx)

        def run() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            try:
                return {"lost_links": project.setup_plugin(device)}
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return ctx.run_locked(run)

    @tool("write")
    def project_import_stored_plugin_config(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Convert the original plugin blob already stored on this device to native parameters.
        Requires importable_blob in project_plugin_capabilities. Preserves original bytes;
        applies configuration and object roles in one undo step. Unproven group links are
        removed and reported as lost_links. Never programs a device.
        """
        require_project(ctx)

        def run() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            try:
                return {"lost_links": project.import_stored_plugin_config(device)}
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return ctx.run_locked(run)

    @tool("write")
    def project_import_plugin_config(
        config_json: str, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Import configuration JSON using the device's mapped provider. Configuration,
        object bindings, compatible links and download status change in one undo step.
        Returns lost_links for incompatible or removed roles, with group addresses.
        """
        require_project(ctx)

        def run() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            try:
                return {
                    "lost_links": project.import_plugin_config(
                        device, config_json.encode()
                    )
                }
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return ctx.run_locked(run)

    @tool("read")
    def project_export_plugin_config(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Return config_json from the device's mapped native provider. Includes the
        application identity and its configuration values; object bindings are validated
        against the product. Does not modify the stored manufacturer blob.
        """
        require_project(ctx)

        def run() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            try:
                return {"config_json": project.export_plugin_config(device).decode()}
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return ctx.run_locked(run)

    @tool("read")
    def project_preview_plugin(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Return the provider's complete offline load plan: resource bytes/masks,
        properties, controls, allowed scopes and programming gate. No bus access.
        Each resource reports its payload size and separate allocation_size in bytes.
        The memory list contains absolute addresses, bytes and write masks.
        """
        require_project(ctx)

        def run() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            try:
                return project.preview_plugin(device, ctx.api.connection.master)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return ctx.run_locked(run)

    @tool("write")
    def project_setup_triton_display(
        node_id: int | None = None,
        device_address: str | None = None,
        device_type: str = "",
    ) -> dict[str, Any]:
        """Create a default Busch-Jaeger triton LCD display configuration on a display device.

        Needed once before the display lines can be read/edited via project_plugin_parameters /
        project_set_plugin_parameter (every line starts unused). Identify the device by node_id OR
        device_address. Requires a triton LCD plugin mapping. The display type is guessed from the
        application id, then the device's naming; pass ``device_type``
        (one of "6136UP", "triton-6323", "triton-6325") to set it explicitly or when the guess
        fails. No-op when a valid configuration already exists. Returns {ready, device_type, lines}
        from the stored configuration, including on a no-op."""
        require_project(ctx)

        def _setup() -> dict[str, Any]:
            from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME
            from xknxeditor.vendor.triton import DeviceType, from_json_bytes

            device = require_device(project, node_id, device_address)
            chosen: DeviceType | None = None
            if device_type:
                try:
                    chosen = DeviceType(device_type)
                except ValueError as exc:
                    allowed = ", ".join(d.value for d in DeviceType)
                    raise ToolError(
                        f"unknown device_type {device_type!r}; one of: {allowed}"
                    ) from exc
            mapping, _sections = _plugin_sections(device)
            if mapping.blob_reader != "triton_lcd":
                raise ToolError(
                    f"device {device.node_id} has no plugin parameters the editor can show"
                )
            try:
                blob = project.setup_triton_display(
                    device, BLOB_NAME, device_type=chosen
                )
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            if blob is None:
                raise ToolError(
                    "device is not a recognised triton LCD display; pass device_type to force"
                )
            config = from_json_bytes(blob)
            return {
                "ready": True,
                "device_type": config.device_type.value,
                "lines": len(config.lines),
            }

        return ctx.run_locked(_setup)

    @tool("write")
    def project_import_triton_prj(path: str) -> dict[str, Any]:
        """Import a Busch-Jaeger LVS 2 ".prj" project file onto the matching project devices.

        Reads the ".prj" file at ``path`` (on the editor's machine) and writes each display's
        configuration onto the project device whose individual address equals the display's
        physical address, as the Plugin Parameters tab would show and edit it afterwards. A display
        with no device at its address is skipped. Each imported display is one undo step (undoable
        with project_undo). Returns {imported, skipped}: ``imported`` is the list of individual
        addresses written, ``skipped`` a list of {address, reason} for missing or ambiguous devices.
        Duplicate source addresses use the last record. Byte-identical configurations are omitted;
        all configurations are validated before any write."""
        require_project(ctx)

        def _import() -> dict[str, Any]:
            from xknxeditor.vendor.triton import PrjError

            try:
                data = Path(path).read_bytes()
            except (OSError, ValueError) as exc:
                raise ToolError(f"cannot read {path!r}: {exc}") from exc
            try:
                return project.import_triton_prj(data)
            except PrjError as exc:
                raise ToolError(f"not a valid LVS 2 '.prj' file: {exc}") from exc

        return ctx.run_locked(_import)

    @tool("write")
    def project_export_triton_prj(
        path: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Export a device's triton LCD display configuration to a Busch-Jaeger LVS 2 ".prj" file.

        Writes a ".prj" to ``path`` (on the editor's machine) from the stored configuration of the
        device identified by node_id OR device_address. The file re-reads to the same configuration;
        the bytes the format analysis left unresolved are zero, so it is not a byte-copy of a file
        the LVS program itself saved. Returns {path, bytes}. Fails when the device has no triton LCD
        configuration (create one first with project_setup_triton_display or project_import_triton_prj)."""
        require_project(ctx)

        def _export() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            data = project.export_triton_prj(device)
            if data is None:
                raise ToolError(
                    f"device {device.node_id} has no triton LCD configuration to export"
                )
            try:
                Path(path).write_bytes(data)
            except OSError as exc:
                raise ToolError(f"cannot write {path!r}: {exc}") from exc
            return {"path": path, "bytes": len(data)}

        return ctx.run_locked(_export)

    @tool("write")
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

    @tool("destructive")
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
