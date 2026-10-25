# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""MCP tools for the comments, installation hints, descriptions and numbers of project elements,
the properties of a group object, the project name and every group-address link at once.

Comments and installation hints are rich text (paragraphs of bold/italic/underlined runs), stored
as RTF. The tools read and write them as plain ``text`` (one paragraph per line) or as
``paragraphs`` of runs, and store them the way the editor's Comment tab does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.mcp.tools.project import (
    com_object_dict,
    require_device,
    require_group_address_id,
)
from editor_gui.plugins.project.service import interface_bus_access
from xknxeditor.proj.core.medium import MEDIUM_NAMES, medium_name
from xknxeditor.proj.core.rich_text import (
    RichText,
    Span,
    parse_rich_text,
    plain_text,
    rich_text_rtf_like,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.plugins.project.service import ProjectService

CommentKind = Literal[
    "project",
    "area",
    "line",
    "segment",
    "device",
    "space",
    "function",
    "trade",
    "group_range",
    "group_address",
]
CommentField = Literal["comment", "installation_hints"]
DescriptionKind = Literal[
    "area",
    "line",
    "segment",
    "group_range",
    "group_address",
    "space",
    "function",
    "trade",
]
NumberKind = Literal["area", "line", "group_range", "group_address", "space"]
ElementKind = Literal[
    "area", "line", "segment", "group_range", "group_address", "function", "trade"
]
StatusKind = Literal[
    "project", "area", "line", "segment", "device", "space", "function", "trade"
]
# The CompletionStatus values (models.COMPLETION_STATUSES), shown as Unknown, Editing, Finished
# design, Finished commissioning, Tested, Accepted and Locked.
Status = Literal[
    "Undefined",
    "Editing",
    "FinishedDesign",
    "FinishedCommissioning",
    "Tested",
    "Accepted",
    "Locked",
]
Priority = Literal["Low", "High", "Alert"]
BackboneMedium = Literal["IP", "TP"]
# The network latencies of the backbone's IP routing, in milliseconds.
Latency = Literal[400, 1000, 2000, 4000, 8000]
_MEDIA: dict[str, str] = {name: medium for medium, name in MEDIUM_NAMES.items()}

_FORMATS = ("bold", "italic", "underline")


def comment_dict(kind: str, element_id: int, field: str, stored: str) -> dict[str, Any]:
    """A stored comment as the tools return it: plain text and the formatted paragraphs."""
    text = parse_rich_text(stored)
    return {
        "kind": kind,
        "element_id": element_id,
        "field": field,
        "text": plain_text(text),
        "paragraphs": [
            [
                {"text": span.text} | {f: True for f in _FORMATS if getattr(span, f)}
                for span in paragraph
            ]
            for paragraph in text
        ],
    }


def comment_rich_text(
    text: str | None, paragraphs: list[list[dict[str, Any]]] | None
) -> RichText:
    """The rich text of exactly one of ``text`` (plain, one paragraph per line) and
    ``paragraphs`` (runs of ``{text, bold?, italic?, underline?}``)."""
    if (text is None) == (paragraphs is None):
        raise ToolError("pass exactly one of text or paragraphs")
    if text is not None:
        return parse_rich_text(text.replace("\r\n", "\n"))
    assert paragraphs is not None
    result: list[tuple[Span, ...]] = []
    for paragraph in paragraphs:
        runs: list[Span] = []
        for run in paragraph:
            value = run.get("text")
            if not isinstance(value, str):
                raise ToolError("every run of a paragraph needs a text string")
            unknown = set(run) - {"text", *_FORMATS}
            if unknown:
                raise ToolError(f"unknown run fields: {', '.join(sorted(unknown))}")
            runs.append(
                Span(
                    value,
                    bool(run.get("bold")),
                    bool(run.get("italic")),
                    bool(run.get("underline")),
                )
            )
        result.append(tuple(runs))
    return tuple(result)


def set_comment(
    project: ProjectService,
    kind: str,
    element_id: int,
    field: str,
    text: RichText,
) -> str:
    """Store ``text`` as an element's comment or a device's installation hints (one undo step;
    nothing when it does not change); returns what is stored then."""
    if field == "installation_hints" and kind != "device":
        raise ToolError("only a device has installation hints")
    stored = project.comment(kind, element_id, field)
    if stored is None:
        raise ToolError(f"no {kind} with id {element_id}")
    if text != parse_rich_text(stored):
        project.set_comment(kind, element_id, rich_text_rtf_like(text, stored), field)
    return project.comment(kind, element_id, field) or ""


def status_target(
    project: ProjectService,
    kind: str,
    element_id: int | None,
    device_address: str | None,
) -> int:
    """The id ``SetCompletionStatus`` takes for ``kind``: a device by its node_id OR its
    individual address, the project as 0, any other element by its id; checked to exist."""
    if kind == "device":
        return require_device(project, element_id, device_address).node_id
    if device_address is not None:
        raise ToolError("device_address is only for kind device")
    target = 0 if kind == "project" else element_id
    if target is None:
        raise ToolError(f"pass the element_id of the {kind}")
    if project.completion_status(kind, target) is None:
        raise ToolError(f"no {kind} with id {target}")
    return target


def set_description(
    project: ProjectService, kind: str, element_id: int, description: str
) -> None:
    """Set the description of an area, line, group range, group address, space or function (one
    undo step)."""
    if kind == "space":
        if project.find_space(element_id) is None:
            raise ToolError(f"no space with id {element_id}")
        project.set_space_description(element_id, description)
        return
    if project.element_details(kind, element_id) is None:
        raise ToolError(f"no {kind} with id {element_id}")
    project.set_element_description(kind, element_id, description)


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    def _element_dict(kind: str, element_id: int) -> dict[str, Any]:
        details = project.element_details(kind, element_id)
        if details is None:
            raise ToolError(f"no {kind} with id {element_id}")
        result: dict[str, Any] = {
            "kind": kind,
            "element_id": element_id,
            "type": details.title_kind,
            "name": details.name,
            "description": details.description,
        }
        if details.has_address:
            result["address"] = f"{details.prefix}{details.address}"
            result["number"] = details.address
            result["number_editable"] = details.editable
        if details.number is not None:
            result["number"] = details.number
        if details.status is not None:
            result["status"] = details.status
        if details.medium is not None:
            result["medium"] = medium_name(details.medium)
        if details.domain_address:
            result["domain_address"] = details.domain_address
        if details.security is not None:
            result["security"] = details.security
            forced = details.forced_by
            result["security_forced_by"] = (
                None
                if forced is None
                else {
                    "group_range_id": forced.group_range_id,
                    "name": forced.name,
                    "mode": forced.mode,
                }
            )
        if details.central is not None:
            result["central"] = details.central
        if details.datapoint_type is not None:
            result["datapoint_type"] = details.datapoint_type or None
        load = project.bus_load_texts(kind, element_id)
        if load is not None:
            result["device_count"], result["bus_current"] = load
        return result

    @tool("read")
    def project_get_comment(
        kind: CommentKind, element_id: int = 0, field: CommentField = "comment"
    ) -> dict[str, Any]:
        """Read the comment of a project element, or a device's installation hints
        (field="installation_hints"). ``kind`` with its id: project (element_id ignored),
        area/line/segment (id from project_topology), device (node_id), space/function (ids from
        project_spaces), trade (id from project_trades), group_range (id from the group range
        tree), group_address (id from
        project_list_group_addresses). Returns {kind, element_id, field, text, paragraphs}: text is
        the plain text (one line per paragraph), paragraphs the runs
        [{text, bold?, italic?, underline?}] of each paragraph."""
        require_project(ctx)

        def _read() -> dict[str, Any]:
            if field == "installation_hints" and kind != "device":
                raise ToolError("only a device has installation hints")
            stored = project.comment(kind, element_id, field)
            if stored is None:
                raise ToolError(f"no {kind} with id {element_id}")
            return comment_dict(kind, element_id, field, stored)

        return ctx.run_locked(_read)

    @tool("write")
    def project_set_comment(
        kind: CommentKind,
        element_id: int = 0,
        text: str | None = None,
        paragraphs: list[list[dict[str, Any]]] | None = None,
        field: CommentField = "comment",
    ) -> dict[str, Any]:
        """Set the comment of a project element, or a device's installation hints
        (field="installation_hints"); kind and element_id as for project_get_comment. Pass exactly
        one of ``text`` (plain, one paragraph per line; "" clears it) and ``paragraphs`` (a list of
        paragraphs, each a list of runs {text, bold?, italic?, underline?}) for formatted text.
        Undoable with project_undo. Returns what project_get_comment returns."""
        require_project(ctx)
        rich = comment_rich_text(text, paragraphs)

        def _set() -> dict[str, Any]:
            stored = set_comment(project, kind, element_id, field, rich)
            return comment_dict(kind, element_id, field, stored)

        return ctx.run_locked(_set)

    @tool("read")
    def project_get_element(kind: ElementKind, element_id: int) -> dict[str, Any]:
        """Read the details of an area, line, segment (ids from project_topology), group range,
        group address (id from project_list_group_addresses), building function (id from
        project_spaces) or trade (id from project_trades; its number is a free text, set with
        project_set_trade). Returns {kind, element_id, type, name, description, address?, number?,
        number_editable?, status?, medium?, domain_address?}: type is area, line, segment,
        main_group, middle_group, group_range (a group of the Free style), group_address or
        function; address is the full address ("1.2", "1/2", "1/2/3") and number its own part
        ("2"), which project_set_number changes unless number_editable is false (a segment and a
        function have none); status is the "Status" of an area, line, segment or function (see
        project_set_status); medium (TP, PL, RF, IP or IoT) is a line's or a segment's (see
        project_set_medium), domain_address an RF or PL one's. A group address and a group have
        security (their own mode: Auto, On or Off) and security_forced_by (the group above that
        overrides it, {group_range_id, name, mode} or null); a group address also central and
        datapoint_type (its "Data Type" as a DPST token, null for none; see
        project_set_group_address_dpt). An area (its main line), a line and a segment also have
        device_count (a line counts the devices of all its segments) and bus_current (as shown:
        "40 mA", "-" for none; a line's is its main segment's; "(estimated)" follows when a
        device without bus current data added an estimated 10 mA)."""
        require_project(ctx)
        return ctx.run_locked(lambda: _element_dict(kind, element_id))

    @tool("read")
    def project_get_status(
        kind: StatusKind,
        element_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Read the "Status" (CompletionStatus) of the project (kind project, no id), an area, a
        line or a segment (ids from project_topology), a device (element_id = its node_id, OR device_address,
        an individual address like "1.0.30"), a space or a building function (ids from
        project_spaces), or a trade (id from project_trades). Returns {kind, element_id, status};
        status is one of Undefined (shown as
        "Unknown"), Editing, FinishedDesign, FinishedCommissioning, Tested, Accepted, Locked."""
        require_project(ctx)

        def _read() -> dict[str, Any]:
            target = status_target(project, kind, element_id, device_address)
            return {
                "kind": kind,
                "element_id": target,
                "status": project.completion_status(kind, target),
            }

        return ctx.run_locked(_read)

    @tool("write")
    def project_set_status(
        status: Status,
        kind: StatusKind,
        element_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Set the "Status" (CompletionStatus) of the project, an area, a line, a segment, a
        device, a space or a building function, as its Status drop-down does; the element as for
        project_get_status. It documents how far the element is (planned, commissioned, tested,
        accepted by the customer, locked) and is written to the exported project. Undoable with
        project_undo. Returns {kind, element_id, status}."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            target = status_target(project, kind, element_id, device_address)
            project.set_completion_status(kind, target, status)
            return {
                "kind": kind,
                "element_id": target,
                "status": project.completion_status(kind, target),
            }

        return ctx.run_locked(_set)

    def _interfaces() -> list[dict[str, str]]:
        return [
            {
                "parameter": interface_bus_access(g.name, g.ip_addr, g.port),
                "text": f"{g.name} ({g.ip_addr}:{g.port} Udp - "
                f"{g.individual_address or ''})".replace(" - )", ")"),
            }
            for g in ctx.connection_plugin.gateways
            if g.supports_tunnelling
        ]

    def _backbone_dict() -> dict[str, Any]:
        area = next((a for a in project.get_areas() if a.area_number == 0), None)
        details = project.element_details("backbone", area.id) if area else None
        if area is None or details is None:
            raise ToolError("the project has no backbone (area 0)")
        return {
            "area_id": area.id,
            "name": details.name,
            "description": details.description,
            "status": details.status,
            "medium": medium_name(details.medium) if details.medium else None,
            "latency_ms": details.latency,
            "multicast_address": details.multicast_address,
            "security": details.backbone_security,
            "bus_connection": (
                {"parameter": details.bus_access, "text": details.bus_connection}
                if details.bus_access
                else None
            ),
            "found_interfaces": _interfaces(),
        }

    @tool("read")
    def project_get_backbone() -> dict[str, Any]:
        """Read the backbone (area 0 with its line 0.0) as its details show it. Returns
        {area_id, name, description, status, medium (IP or TP), latency_ms (the network latency:
        400 LAN, 1000 WLAN, 2000 WAN, 4000 Mobile, 8000 Satellite), multicast_address (of the IP
        routing), security (of the IP routing: Auto, On or Off), bus_connection ({parameter,
        text} of the connection kept with the backbone, null for Automatic), found_interfaces
        ([{parameter, text}] of the KNXnet/IP interfaces found on the network)}. Name,
        description and status change with project_rename_area, project_set_description and
        project_set_status (kind area)."""
        require_project(ctx)
        return ctx.run_locked(_backbone_dict)

    @tool("write")
    def project_set_backbone(
        medium: BackboneMedium | None = None,
        latency_ms: Latency | None = None,
        multicast_address: str | None = None,
        security: Literal["Auto", "On", "Off"] | None = None,
        bus_connection: str | None = None,
    ) -> dict[str, Any]:
        """Change the backbone as its details do; pass only what changes. ``medium`` IP or TP
        (the rules of project_set_medium apply); ``latency_ms`` the network latency (400 LAN,
        1000 WLAN, 2000 WAN, 4000 Mobile, 8000 Satellite); ``multicast_address`` of the IP
        routing (224.0.0.0 to 239.255.255.255); ``security`` of the IP routing (On needs a
        project password: it creates the backbone key); ``bus_connection`` "" for Automatic, or
        the parameter of one of project_get_backbone's found_interfaces or of its current
        bus_connection: an online operation (programming, a device test, a group value read or
        write …) that finds no open connection connects through it and closes it afterwards.
        Each change is an undo step (project_undo), except the security. Returns
        what project_get_backbone returns."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            backbone = _backbone_dict()
            if medium is not None:
                error = project.set_element_medium(
                    "backbone", backbone["area_id"], _MEDIA[medium]
                )
                if error:
                    raise ToolError(error)
            if latency_ms is not None or multicast_address is not None:
                error = project.set_ip_routing(
                    latency=latency_ms, multicast_address=multicast_address
                )
                if error:
                    raise ToolError(error)
            if security is not None:
                error = project.set_element_backbone_security(security)
                if error:
                    raise ToolError(error)
            if bus_connection is not None:
                known = {i["parameter"] for i in backbone["found_interfaces"]}
                current = backbone["bus_connection"]
                if current is not None:
                    known.add(current["parameter"])
                if bus_connection and bus_connection not in known:
                    raise ToolError(
                        'bus_connection must be "" (Automatic) or a parameter from '
                        "project_get_backbone"
                    )
                project.set_backbone_bus_connection(bus_connection)
            return _backbone_dict()

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_description(
        kind: DescriptionKind, element_id: int, description: str
    ) -> dict[str, Any]:
        """Set the description of an area, line, segment (ids from project_topology), group
        range, group address (id from project_list_group_addresses), space or building function
        (ids from project_spaces). Undoable with project_undo. Returns {kind, element_id,
        description}."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            set_description(project, kind, element_id, description)
            return {"kind": kind, "element_id": element_id, "description": description}

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_number(
        kind: NumberKind, element_id: int, number: str
    ) -> dict[str, Any]:
        """Give an element another number. area: its area number (0-15, its lines and devices
        move with it); line: its line number in the area (0-15); group_range: the number of a main
        (0-31) or middle group (0-7); group_address: the whole address in the project's style
        ("1/2/3"), its links move with it; space: the space's number, a free text. Undoable with
        project_undo. Returns project_get_element's fields, or {kind, element_id, number} for a
        space."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            if kind == "space":
                if project.find_space(element_id) is None:
                    raise ToolError(f"no space with id {element_id}")
                project.set_space_number(element_id, number)
                return {"kind": kind, "element_id": element_id, "number": number}
            details = project.element_details(kind, element_id)
            if details is None:
                raise ToolError(f"no {kind} with id {element_id}")
            if not details.editable:
                raise ToolError(f"the number of this {kind} cannot be changed")
            error = project.set_element_address(kind, element_id, number)
            if error is not None:
                raise ToolError(error)
            return _element_dict(kind, element_id)

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_com_object_properties(
        com_object_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
        description: str | None = None,
        priority: Priority | Literal[""] | None = None,
        datapoint_type: str | None = None,
    ) -> dict[str, Any]:
        """Set the description, priority and/or datapoint type of a project group object. Identify
        the device by ``node_id`` OR ``device_address``; pass ref_id from project_list_com_objects
        as com_object_ref_id (it must be linkable). An omitted field stays as it is; "" clears it
        (priority and datapoint type then follow the application again). datapoint_type is a dotted
        code ("5.001") or a DPST token ("DPST-5-1"). Each change is one undo step (project_undo).
        Returns {ref_id, db_id, linkable, name, number, dpt, dpt_name, object_size, flags,
        description, priority}."""
        require_project(ctx)
        if description is None and priority is None and datapoint_type is None:
            raise ToolError("pass description, priority, datapoint_type or several")

        def _set() -> dict[str, Any]:
            device = require_device(project, node_id, device_address)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {device.node_id}"
                )
            if co.db_id is None:
                raise ToolError(
                    f"com-object {com_object_ref_id!r} is not instantiated in the project "
                    "(not linkable/editable)"
                )
            if description is not None:
                project.set_com_object_description(
                    device, com_object_ref_id, description or None
                )
            if priority is not None:
                project.set_com_object_priority(
                    device, com_object_ref_id, priority or None
                )
            if datapoint_type is not None:
                project.set_com_object_datapoint_type(
                    device, com_object_ref_id, datapoint_type or None
                )
            updated = require_device(project, device.node_id).find_com_object(
                com_object_ref_id
            )
            return com_object_dict(updated if updated is not None else co)

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_name(name: str) -> dict[str, Any]:
        """Rename the open project (an empty name keeps the current one). Undoable with
        project_undo. Returns {name}."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            project.set_project_name(name)
            info = project.get_project_metadata()
            return {"name": info.name if info is not None else name.strip()}

        return ctx.run_locked(_set)

    @tool("read")
    def project_list_links(
        node_id: int | None = None,
        device_address: str | None = None,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """List the group-address links of the whole project in one call, optionally only those of
        one device (node_id, or device_address: an individual address like "1.0.30") and/or one
        group address (group_address_id, or group_address: a printed address like "10/1/2" in the
        project's style). Returns {items, count};
        each item has {node_id, group_address_id, is_sending, flags}, flags being the group
        object's effective communication, read, write, transmit and update flags. Use
        project_get_com_object_links for the group objects of a device's links."""
        require_project(ctx)

        def _read() -> list[dict[str, Any]]:
            ga_id = (
                None
                if group_address_id is None and group_address is None
                else require_group_address_id(project, group_address_id, group_address)
            )
            device_id = (
                None
                if node_id is None and device_address is None
                else require_device(project, node_id, device_address).node_id
            )
            return [
                {
                    "node_id": link.device_id,
                    "group_address_id": link.group_address_id,
                    "is_sending": link.is_sending,
                    "flags": {
                        "communication": link.communication,
                        "read": link.read,
                        "write": link.write,
                        "transmit": link.transmit,
                        "update": link.update,
                    },
                }
                for link in project.device_links()
                if (device_id is None or link.device_id == device_id)
                and (ga_id is None or link.group_address_id == ga_id)
            ]

        rows = ctx.run_locked(_read)
        return {"items": rows, "count": len(rows)}
