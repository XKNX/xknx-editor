# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Project editing tools: lifecycle, reads, mutations, undo/redo, export.

Every tool body runs on the imgui main thread via ``ctx.run_on_ui`` because the project/catalog
services are not thread-safe (see :mod:`editor_gui.plugins.mcp.context`).

ID conventions: ``node_id`` (int, from project_list_devices) identifies a device; a com-object is
addressed by ``(node_id, com_object_ref_id)`` where ``com_object_ref_id`` is the ``ref_id`` from
project_list_com_objects. Group addresses use the integer ``group_address_id``; the ``address``
string (e.g. ``"1/2/3"``) follows the project's group_address_style (see project_info).

List tools return a ``{items, count}`` envelope; single-entity mutations return the affected entity.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.project.service import DeviceProduct

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import ComObject, Device
    from xknxeditor.proj import CompactResult

# Slow, file/parse-heavy operations get a generous main-thread budget (they briefly hold the frame).
_SLOW = 300.0

# The com-object flags a client may override (also the schema enum for project_set_com_object_flag).
Flag = Literal["communication", "read", "write", "transmit", "update", "read_on_init"]
_FLAGS = ("communication", "read", "write", "transmit", "update", "read_on_init")


def _items(rows: list[Any]) -> dict[str, Any]:
    """Uniform list envelope so every list tool returns a self-describing ``{items, count}``."""
    return {"items": rows, "count": len(rows)}


def _flags_dict(co: ComObject) -> dict[str, bool]:
    f = co.flags
    return {name: getattr(f, name) for name in _FLAGS}


def _com_object_dict(co: ComObject) -> dict[str, Any]:
    return {
        "ref_id": co.id,
        "db_id": co.db_id,
        # Only com-objects with a project row (db_id set) can be linked to a group address. Objects
        # that are visible in the application but not instantiated in this project have db_id=None.
        "linkable": co.db_id is not None,
        "name": co.name,
        "number": co.number,
        "dpt": co.dpt.code,
        "dpt_name": co.dpt.name,
        "object_size": co.object_size,
        "flags": _flags_dict(co),
    }


def _device_dict(device: Device) -> dict[str, Any]:
    return {
        "node_id": device.node_id,
        "name": device.name,
        "individual_address": device.individual_address or None,
        "description": device.description,
    }


def _ga_dict(g: Any) -> dict[str, Any]:
    return {
        "id": g.id,
        "address": g.address,
        "name": g.name,
        "datapoint_type": g.datapoint_type,
        "description": g.description,
        "data_secure": g.data_secure,
    }


def _widget_dict(widget: Any) -> dict[str, Any]:
    """Serialize a UiParameter widget into the value constraints an LLM needs to set it correctly."""
    if widget is None:
        return {"type": "none"}
    name = type(widget).__name__
    if name == "EnumWidget":
        return {
            "type": "enum",
            "choices": [
                {"value": c.value, "label": c.label, "id": c.id} for c in widget.choices
            ],
        }
    if name in ("NumberWidget", "NumberSliderWidget", "ProgressBarWidget"):
        return {"type": "number", "min": widget.min, "max": widget.max}
    if name in ("FloatWidget", "FloatSliderWidget"):
        return {"type": "float", "min": widget.min, "max": widget.max}
    if name == "CheckBoxWidget":
        return {"type": "checkbox", "values": [0, 1]}
    if name == "TextWidget":
        return {"type": "text", "max_length": widget.max_length}
    if name == "TimeWidget":
        return {"type": "time", "min": widget.min, "max": widget.max}
    return {"type": name}


def _parameter_dict(p: Any) -> dict[str, Any]:
    return {
        "ref_id": p.ref_id,
        "label": p.label,
        "value": p.value,
        "default_value": p.default_value,
        "access": getattr(p.access, "name", str(p.access)),
        "widget": _widget_dict(p.widget),
    }


def _collect_ui_parameters(nodes: Any) -> list[Any]:
    """Walk the device UI tree collecting UiParameter leaves (imgui-free)."""
    from xknxeditor.prod.parser_v2.ui import UiParameter, UiParameterBlock, UiTab

    result: list[Any] = []
    for node in nodes:
        if isinstance(node, UiParameter):
            result.append(node)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            result.extend(_collect_ui_parameters(node.children))
    return result


def _ui_node(node: Any) -> dict[str, Any]:
    """One node of the device UI tree as a dict, preserving group nesting (tabs/blocks -> children).

    Parameters carry the full _parameter_dict; groups carry their heading and children; separators are
    dropped. Lets a caller see which parameters belong to a channel / button pair / level."""
    from xknxeditor.prod.parser_v2.ui import (
        UiButton,
        UiParameter,
        UiParameterBlock,
        UiTab,
    )

    if isinstance(node, UiParameter):
        return {"kind": "parameter", **_parameter_dict(node)}
    if isinstance(node, UiButton):
        return {
            "kind": "button",
            "id": node.id,
            "text": node.text,
            "online": node.online,
            "read_only": node.read_only,
        }
    if isinstance(node, (UiTab, UiParameterBlock)):
        heading = getattr(node, "text", None) or getattr(node, "name", None)
        children = [
            d for ch in node.children if (d := _ui_node(ch)).get("kind") != "separator"
        ]
        return {
            "kind": "tab" if isinstance(node, UiTab) else "block",
            "name": heading,
            "children": children,
        }
    return {"kind": type(node).__name__.removeprefix("Ui").lower()}


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    def _require_device(node_id: int) -> Device:
        device = project.find_device_by_node_id(node_id)
        if device is None:
            raise ToolError(
                f"no device with node_id {node_id}; call project_list_devices for valid ids"
            )
        return device

    # --- application update ("Update Application Program") ------------

    @tool
    def project_devices_with_update() -> dict[str, Any]:
        """List devices that project_update_application can update: the local catalog has a
        version of the same application program whose ReplacesVersions names the current one.

        Read-only. Each item: node_id, individual_address, name, application_id, current_version,
        available_version. Use project_update_application(node_id) to apply an update."""
        require_project(ctx)

        def _read() -> list[dict[str, Any]]:
            from xknxeditor.prod.app_id import parse_app_id

            rows: list[dict[str, Any]] = []
            for d in project.devices:
                newer = project.newer_application_version(d)
                if newer is None or d.app is None:
                    continue
                parsed = parse_app_id(d.app.id)
                rows.append(
                    {
                        "node_id": d.node_id,
                        "individual_address": d.individual_address or None,
                        "name": d.name,
                        "application_id": d.app.id,
                        "current_version": parsed.version if parsed else None,
                        "available_version": newer,
                    }
                )
            return rows

        return _items(ctx.run_locked(_read, timeout=_SLOW))

    @tool
    def project_update_application(node_id: int) -> dict[str, Any]:
        """Update a device to the local catalog version of the SAME application program that
        replaces the current one ("Update Application Program Version"). Parameter values carry over
        by ParameterRef number, group objects of the same size by ComObjectRef number with their
        flags, priority, datapoint type and group-address links.

        This changes the PROJECT only. To apply it on the bus, program the device afterwards
        (connection_program_device with scope "ap1"/application). Returns new_version, kept, dropped."""
        require_project(ctx)

        def _do() -> dict[str, Any]:
            device = _require_device(node_id)
            result = project.update_application(device)
            if result is None:
                raise ToolError(
                    f"no newer application version available for device {node_id}"
                )
            return {
                "node_id": node_id,
                "new_version": result.new_version,
                "kept": result.kept,
                "dropped": result.dropped,
                "note": "project updated; program the device (connection_program_device, "
                "scope application) to apply the change on the bus",
            }

        return ctx.run_locked(_do, timeout=_SLOW)

    @tool
    def project_list_application_programs(node_id: int) -> dict[str, Any]:
        """The application programs a device can be changed to with project_change_application:
        the other programs of its hardware in the local catalog, older and newer.

        Read-only. Each item: hardware2program_ref_id, application_id, name, version."""
        require_project(ctx)

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id)
            return [
                {
                    "hardware2program_ref_id": program.id,
                    "application_id": program.application_id,
                    "name": program.application_name,
                    "version": program.application_version,
                }
                for program in project.application_change_targets(device)
            ]

        return _items(ctx.run_locked(_read, timeout=_SLOW))

    @tool
    def project_change_application(
        node_id: int, hardware2program_ref_id: str
    ) -> dict[str, Any]:
        """Change a device to another application program of its hardware ("Change Application
        Program"), from project_list_application_programs. Parameters, group-object links and
        flags start from the new application's defaults; address, name and serial number stay.

        This changes the PROJECT only; program the device afterwards (full download)."""
        require_project(ctx)

        def _do() -> dict[str, Any]:
            device = _require_device(node_id)
            program = next(
                (
                    p
                    for p in project.application_change_targets(device)
                    if p.id == hardware2program_ref_id
                ),
                None,
            )
            if program is None:
                raise ToolError(
                    f"{hardware2program_ref_id!r} is not an application program device "
                    f"{node_id} can be changed to; see project_list_application_programs"
                )
            if not project.change_application(device, program):
                raise ToolError(
                    f"application {program.application_id!r} not resolvable"
                )
            return {
                "node_id": node_id,
                "application_id": program.application_id,
                "version": program.application_version,
                "note": "project changed; program the device to apply it on the bus",
            }

        return ctx.run_locked(_do, timeout=_SLOW)

    # --- lifecycle --------------------------------------------------------

    @tool
    def project_status() -> dict[str, Any]:
        """Whether a project is open and its path. Most project_* tools need an open project."""
        return ctx.run_locked(
            lambda: {
                "open": project.is_open,
                "path": str(project.path) if project.path else None,
            }
        )

    @tool
    def project_new(path: str) -> dict[str, Any]:
        """Create a new empty KNX project (.xknx) at ``path`` (on the editor host) and open it."""
        ctx.run_locked(lambda: project.new(Path(path)), timeout=_SLOW)
        return {"open": True, "path": str(project.path) if project.path else None}

    @tool
    def project_open(path: str, password: str | None = None) -> dict[str, Any]:
        """Open an existing KNX project (.xknx path on the editor host).

        ``password`` is required for a password-protected project."""
        ctx.run_locked(lambda: project.open(Path(path), password), timeout=_SLOW)
        return {"open": True, "path": str(project.path) if project.path else None}

    @tool
    def project_import_knxproj(
        source: str,
        dest: str,
        password: str | None = None,
        project_password: str | None = None,
    ) -> dict[str, Any]:
        """Import a .knxproj (``source``) into a new .xknx project at ``dest`` and open it.

        Both paths are on the editor host. ``password`` is required for encrypted projects; it
        then protects the new project too. An archive with KNX Secure data but without a password
        needs ``project_password`` to protect the new project."""
        ctx.run_locked(
            lambda: project.import_knxproj(
                Path(source),
                Path(dest),
                password=password,
                project_password=project_password,
            ),
            timeout=_SLOW,
        )
        return {"open": True, "path": str(project.path) if project.path else None}

    @tool
    def project_add_device_certificate(code: str) -> dict[str, Any]:
        """Add a KNX Data Secure device certificate: ``code`` is the FDSK from the device label
        (36 characters, groups separated by "-"). A device with the certificate's serial number is
        then commissioned securely with it. Needs a password-protected project."""
        from xknxeditor.proj import ProjectPasswordRequired
        from xknxeditor.proj.core.fdsk import InvalidFdskError

        require_project(ctx)
        try:
            fdsk = ctx.run_locked(lambda: project.add_device_certificate(code))
        except InvalidFdskError as exc:
            raise ToolError(f"not a valid FDSK: {exc}") from exc
        except ProjectPasswordRequired as exc:
            raise ToolError("set a project password first") from exc
        return {"serial_number": fdsk.serial_number.hex().upper()}

    @tool
    def project_set_secure_commissioning(
        node_id: int, activated: bool
    ) -> dict[str, Any]:
        """Activate or deactivate a KNX Data Secure device's secure commissioning (one undo step).
        Activated, the device gets a Tool Key (needs a password-protected project) and is
        commissioned with its device certificate. Deactivated, it is programmed in the clear: its
        "Auto" group addresses communicate in the clear and its links to group addresses with
        Security "On" are removed (the result lists both)."""
        from xknxeditor.proj import ProjectPasswordRequired

        require_project(ctx)

        def _set() -> dict[str, Any]:
            device = _require_device(node_id)
            if project.secure_commissioning(device) is None:
                raise ToolError(f"device {node_id} does not support KNX Data Secure")
            impact = None if activated else project.secure_commissioning_impact(device)
            try:
                project.set_secure_commissioning(device, activated)
            except ProjectPasswordRequired as exc:
                raise ToolError("set a project password first") from exc
            return {
                "node_id": node_id,
                "secure_commissioning": activated,
                "unsecured_group_address_ids": impact.unsecured if impact else [],
                "removed_link_ids": impact.removed_links if impact else [],
            }

        return ctx.run_locked(_set)

    @tool
    def project_get_ip_secure(
        node_id: int, include_passwords: bool = False
    ) -> dict[str, Any]:
        """The KNX IP Secure settings of a secure IP device commissioned securely: whether its
        tunnels are secured and its bus interfaces (kind, user id, tunnelling address). With
        ``include_passwords`` also its commissioning password, its authentication code and the
        bus interfaces' passwords - only when the user asks for them."""
        require_project(ctx)

        def _get() -> dict[str, Any]:
            settings = project.ip_secure_settings(_require_device(node_id))
            if settings is None:
                raise ToolError(
                    f"device {node_id} is no KNX IP Secure device commissioned securely"
                )
            result: dict[str, Any] = {
                "node_id": node_id,
                "secure_tunnelling": settings.secure_tunnelling,
                "interfaces": [
                    {
                        "ref_id": i.ref_id,
                        "kind": i.access,
                        "user_id": i.user_id,
                        "address": i.address,
                        **({"password": i.password} if include_passwords else {}),
                    }
                    for i in settings.interfaces
                ],
            }
            if include_passwords:
                result["commissioning_password"] = settings.commissioning_password
                result["authentication_code"] = settings.authentication_code
            return result

        return ctx.run_locked(_get)

    @tool
    def project_set_ip_secure(
        node_id: int,
        commissioning_password: str | None = None,
        authentication_code: str | None = None,
        interface_ref_id: str | None = None,
        interface_password: str | None = None,
        secure_tunnelling: bool | None = None,
    ) -> dict[str, str]:
        """Change a secure IP device's KNX IP Secure settings (the next download writes them):
        its commissioning password, its authentication code, the password of one bus interface
        (``interface_ref_id`` from project_get_ip_secure) or whether its tunnels are secured.
        Passwords have 1 to 20 Latin-1 characters."""
        from xknxeditor.proj import ProjectPasswordRequired

        require_project(ctx)
        if (interface_ref_id is None) != (interface_password is None):
            raise ToolError("give interface_ref_id and interface_password together")

        def _set() -> dict[str, str]:
            device = _require_device(node_id)
            if project.ip_secure_settings(device) is None:
                raise ToolError(
                    f"device {node_id} is no KNX IP Secure device commissioned securely"
                )
            try:
                project.set_ip_secure_settings(
                    device,
                    commissioning_password=commissioning_password,
                    authentication_code=authentication_code,
                    interface_password=(interface_ref_id, interface_password)
                    if interface_ref_id is not None and interface_password is not None
                    else None,
                    secure_tunnelling=secure_tunnelling,
                )
            except ProjectPasswordRequired as exc:
                raise ToolError("set a project password first") from exc
            except ValueError as exc:
                raise ToolError(f"not accepted: {exc}") from exc
            return {"status": "changed", "node_id": str(node_id)}

        return ctx.run_locked(_set)

    @tool
    def project_export_keyring(dest: str, password: str) -> dict[str, Any]:
        """Export the open project's key material as a keyring (``.knxkeys``) to ``dest``,
        encrypted with ``password``: the backbone key, the devices commissioned securely, the
        tunnels of the KNX IP Secure devices and the keys of the secured group addresses (e.g. for
        a secure tunnel of another tool)."""
        require_project(ctx)
        if not password:
            raise ToolError("a keyring password is needed")
        target = Path(dest)
        if target.suffix.lower() != ".knxkeys":
            target = target.with_suffix(".knxkeys")
        devices = ctx.run_locked(lambda: project.export_keyring(target, password))
        return {"path": str(target), "devices": devices}

    @tool
    def project_close() -> dict[str, str]:
        """Close the open project."""
        ctx.run_locked(project.close)
        return {"status": "closed"}

    @tool
    def project_export_knxproj(dest: str) -> dict[str, str]:
        """Export the open project to a .knxproj archive at ``dest`` (with manufacturer bundle)."""
        require_project(ctx)
        warnings: list[str] = []
        missing_refs: list[str] = []

        def _export() -> None:
            from editor_gui.plugins.project.knxproj_manufacturer import (
                collect_manufacturer_bundle,
            )
            from xknxeditor.proj import export_knxproj

            # Export READS the SQLite file, so use the working copy (a network project's home file
            # is only current after a write-back).
            source = project.working_path
            if source is None:
                raise ToolError("no project is open")
            bundle = collect_manufacturer_bundle(
                project.program_refs(), ctx.api.catalog
            )
            result = export_knxproj(
                source,
                Path(dest),
                password=project.project_password,
                extra_files=bundle.extra_files,
                master_xml=bundle.master_xml,
                # Substitute the canonical signed master when the bundled one is unusable.
                fetch_master=True,
            )
            if result.unverifiable_folders:
                warnings.append(
                    "folders without a verifiable signature: "
                    + ", ".join(result.unverifiable_folders)
                )
            if result.missing_references:
                missing_refs.extend(result.missing_references)
                warnings.append(
                    "references missing from the manufacturer bundle: "
                    + ", ".join(result.missing_references)
                )

        ctx.run_locked(_export, timeout=_SLOW)
        # Missing references mean ETS aborts the import ("key not present"), so the archive is not a
        # usable export: report it as incomplete rather than a success.
        out = {"status": "incomplete" if missing_refs else "exported", "dest": dest}
        if warnings:
            out["warning"] = "ETS may reject the import — " + "; ".join(warnings)
        return out

    @tool
    def project_compact(dry_run: bool = True) -> dict[str, Any]:
        """Remove product data no device uses from the open project and compact its file.

        With ``dry_run`` (the default) only reports what would be removed and the estimated file
        size. Otherwise removes it and compacts the file; this cannot be undone, and removed product
        data has to be imported again when such a product is added later."""
        require_project(ctx)

        def _compact() -> CompactResult | None:
            if dry_run:
                return project.analyse_compaction()
            return project.compact()

        result = ctx.run_locked(_compact, timeout=_SLOW)
        if result is None:
            raise ToolError("no project is open")
        report = result.report
        return {
            "status": "analysed" if dry_run else "compacted",
            "nothing_unused": report.nothing_removed,
            "file_size_before": result.file_size_before,
            "file_size_after": result.file_size_after,
            "removed": {
                "application_programs": report.application_programs,
                "products": report.products,
                "hardware": report.hardware,
                "catalog_items": report.catalog_items,
                "baggages": report.baggages,
                "manufacturers": report.manufacturers,
            },
            "product_data_size_before": report.size_before,
            "product_data_size_after": report.size_after,
        }

    @tool
    def project_info() -> dict[str, Any]:
        """Project metadata (name, author, group_address_style, tool/schema version).

        ``group_address_style`` (ThreeLevel/TwoLevel/Free) governs how group-address strings parse."""
        require_project(ctx)
        info = ctx.run_locked(project.get_project_metadata)
        if info is None:
            raise ToolError("no project metadata available")
        return {
            "id": info.id,
            "name": info.name,
            "group_address_style": info.group_address_style,
            "guid": info.guid,
            "created_by": info.created_by,
            "last_modified": info.last_modified,
            "schema_version": info.schema_version,
            "tool_version": info.tool_version,
        }

    # --- reads ------------------------------------------------------------

    @tool
    def project_list_devices(name_contains: str | None = None) -> dict[str, Any]:
        """Devices in the open project. Optional ``name_contains`` filters by name (case-insensitive)."""

        def _read() -> list[dict[str, Any]]:
            needle = (name_contains or "").lower()
            return [
                _device_dict(d)
                for d in project.devices
                if not needle or needle in d.name.lower()
            ]

        return _items(ctx.run_locked(_read))

    @tool
    def project_get_device(node_id: int) -> dict[str, Any]:
        """A device with its catalog metadata (manufacturer/order number/hardware)."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id)
            info = project.get_device_info(node_id)
            data = _device_dict(device)
            if info is not None:
                data |= {
                    "product_ref_id": info.product_ref_id,
                    "hardware2program_ref_id": info.hardware2program_ref_id,
                    "order_number": info.order_number,
                    "hardware_name": info.hardware_name,
                    "product_name": info.product_name,
                    "manufacturer_name": info.manufacturer_name,
                }
            return data

        return ctx.run_locked(_read)

    @tool
    def project_list_com_objects(node_id: int) -> dict[str, Any]:
        """A device's visible com-objects. Link only those with ``linkable`` true (db_id set)."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id)
            return [_com_object_dict(co) for co in device.get_visible_com_objects()]

        return _items(ctx.run_locked(_read))

    @tool
    def project_get_com_object(node_id: int, com_object_ref_id: str) -> dict[str, Any]:
        """A single com-object of a device by its ref_id (name, DPT, flags, linkable)."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {node_id}; "
                    "call project_list_com_objects for valid ref_ids"
                )
            return _com_object_dict(co)

        return ctx.run_locked(_read)

    @tool
    def project_get_com_object_links(
        node_id: int, com_object_ref_id: str
    ) -> dict[str, Any]:
        """The group addresses a device's com-object is linked to (reverse of
        project_get_ga_assignments). Use it to trace what a button/sensor object actually controls.

        Each item: ``assignment_id`` (to unlink), ``group_address_id``, ``address``, ``name``,
        ``is_sending``. Empty if the com-object is not instantiated (db_id None) or has no links."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {node_id}; "
                    "call project_list_com_objects for valid ref_ids"
                )
            if co.db_id is None:
                return []
            links: list[dict[str, Any]] = []
            for a in project.get_links_for_com_object(co.db_id):
                g = project.get_group_address(a.group_address_id)
                links.append(
                    {
                        "assignment_id": a.id,
                        "group_address_id": a.group_address_id,
                        "address": g.address if g else None,
                        "name": g.name if g else None,
                        "is_sending": a.is_sending,
                    }
                )
            return links

        return _items(ctx.run_locked(_read))

    @tool
    def project_list_parameters(node_id: int) -> dict[str, Any]:
        """A device's visible application parameters with current value, default, and allowed values.

        Use the returned ``ref_id`` and a ``value`` permitted by ``widget`` for project_set_parameter."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id)
            return [_parameter_dict(p) for p in _collect_ui_parameters(device.get_ui())]

        return _items(ctx.run_locked(_read))

    @tool
    def project_parameter_tree(node_id: int) -> dict[str, Any]:
        """A device's parameters as the NESTED UI tree (tabs -> blocks -> parameters), keeping the
        headings that project_list_parameters flattens away.

        Use this when the flat list is ambiguous — e.g. to see which parameters belong to a specific
        channel, button pair, or display level, because the same label ("Two-button function") repeats
        per slot and only its enclosing group tells them apart. Each parameter node carries the same
        fields as project_list_parameters (ref_id, label, value, widget)."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id)
            return [_ui_node(n) for n in device.get_ui()]

        return _items(ctx.run_locked(_read))

    @tool
    def project_get_parameter(node_id: int, parameter_ref_id: str) -> dict[str, Any]:
        """A single application parameter of a device by ref_id (value, default, allowed values)."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id)
            for p in _collect_ui_parameters(device.get_ui()):
                if p.ref_id == parameter_ref_id:
                    return _parameter_dict(p)
            raise ToolError(
                f"no parameter {parameter_ref_id!r} on device {node_id}; "
                "call project_list_parameters for valid ref_ids"
            )

        return ctx.run_locked(_read)

    @tool
    def project_list_group_addresses(
        name_contains: str | None = None,
    ) -> dict[str, Any]:
        """Group addresses in the project. Optional ``name_contains`` filters by name."""

        def _read() -> list[dict[str, Any]]:
            needle = (name_contains or "").lower()
            return [
                _ga_dict(g)
                for g in project.group_addresses
                if not needle or needle in g.name.lower()
            ]

        return _items(ctx.run_locked(_read))

    @tool
    def project_get_group_address(group_address_id: int) -> dict[str, Any]:
        """A single group address by id (address string, name, DPT, description)."""

        def _read() -> dict[str, Any]:
            g = project.get_group_address(group_address_id)
            if g is None:
                raise ToolError(f"no group address with id {group_address_id}")
            return _ga_dict(g)

        return ctx.run_locked(_read)

    @tool
    def project_next_free_group_address() -> dict[str, Any]:
        """The next unused group address (style-formatted), for planning before create."""
        require_project(ctx)
        address = ctx.run_locked(project.next_free_group_address)
        if address is None:
            raise ToolError("no free group address available")
        return {"address": address}

    @tool
    def project_get_ga_assignments(group_address_id: int) -> dict[str, Any]:
        """Com-object links of a group address, each resolved to its owning device.

        Lets you trace a group address back to the devices on it: every item carries the device
        (``node_id``, ``individual_address``) and the com-object (``com_object_ref_id``,
        ``com_object_name``, ``com_object_db_id``) plus ``is_sending``."""

        def _read() -> list[dict[str, Any]]:
            # map com-object db_id -> (device, com-object) so each assignment can name its owner
            owner: dict[int, tuple[Device, ComObject]] = {}
            for dev in project.devices:
                for co in dev.get_visible_com_objects():
                    if co.db_id is not None:
                        owner[co.db_id] = (dev, co)
            result: list[dict[str, Any]] = []
            for a in project.get_assignments_for_ga(group_address_id):
                dev_co = owner.get(a.com_object_id)
                dev, co = dev_co if dev_co else (None, None)
                result.append(
                    {
                        "id": a.id,
                        "com_object_db_id": a.com_object_id,
                        "group_address_id": a.group_address_id,
                        "is_sending": a.is_sending,
                        "node_id": dev.node_id if dev else None,
                        "individual_address": dev.individual_address if dev else None,
                        "com_object_ref_id": co.id if co else None,
                        "com_object_name": co.name if co else None,
                    }
                )
            return result

        return _items(ctx.run_locked(_read))

    @tool
    def project_topology() -> dict[str, Any]:
        """The area/line topology tree."""

        def _read() -> list[dict[str, Any]]:
            return [
                {
                    "id": area.id,
                    "area_number": area.area_number,
                    "name": area.name,
                    "lines": [
                        {"id": ln.id, "line_number": ln.line_number, "name": ln.name}
                        for ln in project.get_lines(area.id)
                    ],
                }
                for area in project.get_areas()
            ]

        return _items(ctx.run_locked(_read))

    # --- device mutations -------------------------------------------------

    @tool
    def project_add_device(
        product_ref_id: str,
        name: str | None = None,
        hardware2program_ref_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a device from the catalog by its product_ref_id (from catalog_list_products).

        ``hardware2program_ref_id`` selects one application version of the product; without it the
        newest version in the catalog is used. Returns the created device (node_id, name,
        individual_address)."""
        require_project(ctx)

        def _add() -> dict[str, Any]:
            product = max(
                (
                    p
                    for p in ctx.api.catalog.get_products()
                    if p.product_ref_id == product_ref_id
                    and hardware2program_ref_id in (None, p.hardware2program_ref_id)
                ),
                key=lambda p: p.application_version or 0,
                default=None,
            )
            if product is None:
                raise ToolError(
                    f"no catalog product {product_ref_id!r}; call catalog_list_products"
                )
            if product.application_id is None:
                raise ToolError(
                    f"product {product_ref_id!r} has no application and cannot be added"
                )
            app = ctx.api.catalog.get_application(product.application_id)
            if app is None:
                raise ToolError(
                    f"application {product.application_id!r} for product {product_ref_id!r} "
                    "is not in the catalog"
                )
            device_id = project.add_device(
                product_ref_id=product.product_ref_id,
                hardware2program_ref_id=product.hardware2program_ref_id,
                name=name or "",
                app=app,
                product=DeviceProduct.of(product),
            )
            if device_id is None:
                raise ToolError("add_device did not return a device id")
            device = project.find_device_by_node_id(device_id)
            return (
                _device_dict(device) if device is not None else {"node_id": device_id}
            )

        return ctx.run_locked(_add, timeout=_SLOW)

    @tool
    def project_rename_device(node_id: int, name: str) -> dict[str, Any]:
        """Rename a device. Returns the updated device."""

        def _rename() -> dict[str, Any]:
            device = _require_device(node_id)
            project.set_device_name(node_id, device.name, name)
            return _device_dict(_require_device(node_id))

        return ctx.run_locked(_rename)

    @tool
    def project_set_individual_address(node_id: int, address: str) -> dict[str, Any]:
        """Set a device's individual (physical) address, e.g. ``"1.1.5"``. Returns the updated device.

        This edits the project only; use connection_assign_individual_address to write it to hardware."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id)
            project.set_device_individual_address(
                node_id, device.individual_address, address
            )
            updated = _require_device(node_id)
            if updated.individual_address != address:
                raise ToolError(
                    f"could not set individual address {address!r} (invalid or already in use)"
                )
            return _device_dict(updated)

        return ctx.run_locked(_set)

    @tool
    def project_set_serial_number(node_id: int, serial_number: str) -> dict[str, Any]:
        """Set a device's KNX serial number (12 hex digits, e.g. ``"0083:7AA2039C"``; ``""`` clears
        it). Used to download its individual address by serial number."""
        from editor_gui.device_info import (
            parse_serial_text,
            serial_text,
            serial_to_project,
        )

        serial = parse_serial_text(serial_number) if serial_number.strip() else None
        if serial_number.strip() and serial is None:
            raise ToolError(
                f"invalid serial number {serial_number!r}; expected 12 hex digits"
            )

        def _set() -> dict[str, Any]:
            _require_device(node_id)
            project.set_device_commissioning(
                node_id, serial_number=serial_to_project(serial) if serial else ""
            )
            return {"node_id": node_id, "serial_number": serial_text(serial)}

        return ctx.run_locked(_set)

    @tool
    def project_set_com_object_flag(
        node_id: int, com_object_ref_id: str, flag: Flag, value: bool
    ) -> dict[str, Any]:
        """Override a com-object flag (communication/read/write/transmit/update/read_on_init).

        Returns the updated com-object."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {node_id}"
                )
            if co.db_id is None:
                raise ToolError(
                    f"com-object {com_object_ref_id!r} is not instantiated in the project "
                    "(not linkable/editable)"
                )
            project.set_flag(device, com_object_ref_id, flag, value)
            updated = _require_device(node_id).find_com_object(com_object_ref_id)
            return (
                _com_object_dict(updated)
                if updated is not None
                else {"ref_id": com_object_ref_id}
            )

        return ctx.run_locked(_set)

    @tool
    def project_set_parameter(
        node_id: int, parameter_ref_id: str, value: str
    ) -> dict[str, Any]:
        """Set an application parameter. Discover ref_id and allowed values via project_list_parameters.

        When you configure a device's push-button / display function (e.g. a glass push button, room
        controller), ALWAYS also set its display symbols and key labels/name — do not leave them at
        their defaults. Mirror an equivalent function on another button or device (read it with
        project_parameter_tree and copy the symbol/label parameter values), or choose fitting symbols
        yourself. Setting a function type usually reveals extra symbol/label parameters; re-read the
        block with project_parameter_tree and set them too, so the button shows correctly on the device.

        Returns the parameter's ref_id and resulting value."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id)
            if not any(
                p.ref_id == parameter_ref_id
                for p in _collect_ui_parameters(device.get_ui())
            ):
                raise ToolError(
                    f"no parameter {parameter_ref_id!r} on device {node_id}; "
                    "call project_list_parameters for valid ref_ids"
                )
            project.set_param(device, parameter_ref_id, value)
            for p in _collect_ui_parameters(device.get_ui()):
                if p.ref_id == parameter_ref_id:
                    return {"ref_id": p.ref_id, "value": p.value}
            return {"ref_id": parameter_ref_id, "value": value}

        return ctx.run_locked(_set)

    # --- topology mutations -----------------------------------------------

    @tool
    def project_create_area(area_number: int, name: str = "") -> dict[str, Any]:
        """Create a topology area. Returns its id."""
        require_project(ctx)
        area_id = ctx.run_locked(lambda: project.create_area(area_number, name))
        return {"area_id": area_id, "area_number": area_number, "name": name}

    @tool
    def project_rename_area(area_id: int, name: str) -> dict[str, Any]:
        """Rename a topology area."""
        ctx.run_locked(lambda: project.rename_area(area_id, "", name))
        return {"area_id": area_id, "name": name}

    @tool
    def project_remove_area(area_id: int) -> dict[str, str]:
        """Remove a topology area (and its lines/devices). Destructive; undo with project_undo."""
        ctx.run_locked(lambda: project.remove_area(area_id))
        return {"status": "removed", "area_id": str(area_id)}

    @tool
    def project_create_line(
        area_id: int, line_number: int, name: str = ""
    ) -> dict[str, Any]:
        """Create a line inside an area. Returns its id."""
        require_project(ctx)
        line_id = ctx.run_locked(
            lambda: project.create_line(area_id, line_number, name)
        )
        return {"line_id": line_id, "area_id": area_id, "line_number": line_number}

    @tool
    def project_rename_line(line_id: int, name: str) -> dict[str, Any]:
        """Rename a line."""
        ctx.run_locked(lambda: project.rename_line(line_id, "", name))
        return {"line_id": line_id, "name": name}

    @tool
    def project_remove_line(line_id: int) -> dict[str, str]:
        """Remove a line (and its devices). Destructive; undo with project_undo."""
        ctx.run_locked(lambda: project.remove_line(line_id))
        return {"status": "removed", "line_id": str(line_id)}

    # --- group address mutations ------------------------------------------

    @tool
    def project_create_group_address(
        address: str | None = None, name: str = ""
    ) -> dict[str, Any]:
        """Create a group address (``address`` like ``"1/2/3"`` per group_address_style; omit for the
        next free one). Returns the created group address (id, address, name)."""
        require_project(ctx)

        def _create() -> dict[str, Any]:
            ga_id = project.create_group_address(address, name)
            if ga_id is None:
                raise ToolError(
                    f"could not create group address {address!r} (invalid for the project's style)"
                )
            g = project.get_group_address(ga_id)
            if g is None:
                return {"group_address_id": ga_id}
            return {"group_address_id": g.id, "address": g.address, "name": g.name}

        return ctx.run_locked(_create)

    @tool
    def project_rename_group_address(
        group_address_id: int, name: str
    ) -> dict[str, Any]:
        """Rename a group address. Returns the updated group address."""

        def _rename() -> dict[str, Any]:
            project.rename_group_address(group_address_id, name)
            g = project.get_group_address(group_address_id)
            if g is None:
                raise ToolError(f"no group address with id {group_address_id}")
            return {"id": g.id, "address": g.address, "name": g.name}

        return ctx.run_locked(_rename)

    @tool
    def project_set_group_address_dpt(
        group_address_id: int, dpt: str | None
    ) -> dict[str, Any]:
        """Set (or clear, with null) a group address' datapoint type. Returns the updated GA."""

        def _set() -> dict[str, Any]:
            try:
                project.set_group_address_dpt(group_address_id, dpt)
            except ValueError as e:
                raise ToolError(str(e)) from e
            g = project.get_group_address(group_address_id)
            if g is None:
                raise ToolError(f"no group address with id {group_address_id}")
            return {
                "id": g.id,
                "address": g.address,
                "datapoint_type": g.datapoint_type,
            }

        return ctx.run_locked(_set)

    @tool
    def project_set_group_address_security(
        group_address_id: int, mode: str
    ) -> dict[str, Any]:
        """Set a group address' KNX Data Secure mode: "On" (secured, generates a key; every
        linked device must be commissioned securely), "Off" (not secured), or "Auto" (secured
        while every linked device is commissioned securely). A password-protected project is
        needed to store a generated key."""
        from xknxeditor.proj import GroupAddressSecurityError, ProjectPasswordRequired

        if mode not in ("Auto", "On", "Off"):
            raise ToolError('mode must be "Auto", "On" or "Off"')

        def _set() -> dict[str, Any]:
            try:
                project.set_group_address_security(group_address_id, mode)
            except ProjectPasswordRequired as e:
                raise ToolError("set a project password first") from e
            except GroupAddressSecurityError as e:
                raise ToolError(str(e)) from e
            g = project.get_group_address(group_address_id)
            if g is None:
                raise ToolError(f"no group address with id {group_address_id}")
            return {"id": g.id, "address": g.address, "secured": g.data_secure}

        return ctx.run_locked(_set)

    @tool
    def project_remove_group_address(group_address_id: int) -> dict[str, str]:
        """Remove a group address (and its links). Destructive; undo with project_undo."""
        ctx.run_locked(lambda: project.remove_group_address(group_address_id))
        return {"status": "removed", "group_address_id": str(group_address_id)}

    @tool
    def project_link_com_object(
        node_id: int,
        com_object_ref_id: str,
        group_address_id: int,
        is_sending: bool = False,
    ) -> dict[str, Any]:
        """Link a device com-object to a group address. Identify the com-object by ``(node_id,
        com_object_ref_id)`` from project_list_com_objects (it must be ``linkable``).

        ``is_sending`` marks the transmitting link and defaults to False. Set it True for a com-object
        that must SEND its telegram on the bus (a push-button/sensor output, i.e. one whose Transmit
        flag is set) — otherwise the object only receives and pressing/triggering it sends nothing.
        Each object should have exactly one sending group address. Returns the created assignment."""
        require_project(ctx)

        def _link() -> dict[str, Any]:
            device = _require_device(node_id)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {node_id}; "
                    "call project_list_com_objects for valid ref_ids"
                )
            if co.db_id is None:
                raise ToolError(
                    f"com-object {com_object_ref_id!r} is not instantiated in the project and "
                    "cannot be linked (only com-objects with linkable=true can be linked)"
                )
            link_id = project.link_com_object_to_ga(
                co.db_id, group_address_id, is_sending
            )
            if link_id is None:
                raise ToolError(
                    f"could not link com-object to group address {group_address_id} "
                    "(check the group address id exists; a group address with Security On "
                    "only takes devices commissioned securely)"
                )
            return {
                "link_id": link_id,
                "node_id": node_id,
                "com_object_ref_id": com_object_ref_id,
                "com_object_db_id": co.db_id,
                "group_address_id": group_address_id,
                "is_sending": is_sending,
            }

        return ctx.run_locked(_link)

    @tool
    def project_unlink_com_object(assignment_id: int) -> dict[str, str]:
        """Remove a com-object↔group-address link by its assignment id (from project_get_ga_assignments)."""
        ctx.run_locked(lambda: project.unlink_com_object_from_ga(assignment_id))
        return {"status": "unlinked", "assignment_id": str(assignment_id)}

    # --- undo / redo / history --------------------------------------------

    @tool
    def project_undo() -> dict[str, bool]:
        """Undo the last change. Returns whether anything was undone."""
        return {"undone": ctx.run_locked(project.undo)}

    @tool
    def project_redo() -> dict[str, bool]:
        """Redo the last undone change. Returns whether anything was redone."""
        return {"redone": ctx.run_locked(project.redo)}

    @tool
    def project_history() -> dict[str, Any]:
        """The edit history (id, label, whether reverted)."""
        return _items(
            ctx.run_locked(
                lambda: [
                    {"id": e.id, "label": e.display_text, "reverted": e.reverted}
                    for e in project.history()
                ]
            )
        )

    @tool
    def project_jump_to(event_id: int) -> dict[str, str]:
        """Jump the undo/redo cursor to a history entry (from project_history)."""
        ctx.run_locked(lambda: project.jump_to(event_id))
        return {"status": "jumped", "event_id": str(event_id)}
