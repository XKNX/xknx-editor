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

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.device import (
    collect_ui_parameters as _collect_ui_parameters,
)
from editor_gui.device import (
    com_object_display_name,
)
from editor_gui.device_plugins import parameters_in_plugin
from editor_gui.plugins.mcp.context import McpContext, make_tool, require_project
from editor_gui.plugins.project.element_status import usage_label, usage_options
from editor_gui.plugins.project.service import DeviceProduct, format_domain_address
from editor_gui.plugins.project.space_usages import SPACE_USAGES
from editor_gui.settings import config_dir
from xknxeditor.proj.core.medium import MEDIUM_NAMES, MediumError, medium_name

# A device whose parameters only the manufacturer's plugin dialog sets.
PLUGIN_PARAMETERS_ERROR = (
    "device {node_id} sets its parameters in the manufacturer's plugin dialog, which is "
    "not available here; its parameters cannot be changed"
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import ComObject, Device
    from editor_gui.plugins.project.service import ProjectService
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


def com_object_dict(co: ComObject) -> dict[str, Any]:
    return {
        "ref_id": co.id,
        "db_id": co.db_id,
        # Only com-objects with a project row (db_id set) can be linked to a group address. Objects
        # that are visible in the application but not instantiated in this project have db_id=None.
        "linkable": co.db_id is not None,
        "name": com_object_display_name(co) or co.name,
        "number": co.number,
        "dpt": co.dpt.code,
        "dpt_name": co.dpt.name,
        "object_size": co.object_size,
        "flags": _flags_dict(co),
        "description": co.description,
        "priority": co.priority,
    }


def _prune(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None, an empty string, or an empty list/tuple, to keep list
    payloads compact. A falsy-but-meaningful value (0, False) is kept. Used for the spaces tree,
    which has many empty optional fields; the device/group-address list dicts keep a fixed shape."""
    return {k: v for k, v in d.items() if v not in (None, "", [], ())}


# The media of lines and segments by the name a client passes.
Medium = Literal["TP", "PL", "RF", "IP", "IoT"]
_MEDIA: dict[str, str] = {name: medium for medium, name in MEDIUM_NAMES.items()}


def _segment_entry(segment: Any) -> dict[str, Any]:
    """A sub-segment as project_topology lists it."""
    entry: dict[str, Any] = {
        "id": segment.id,
        "number": segment.number,
        "name": segment.name,
        "medium": medium_name(segment.medium_type),
    }
    if segment.domain_address is not None:
        entry["domain_address"] = format_domain_address(
            segment.medium_type, segment.domain_address
        )
    return entry


def _line_entry(line: Any) -> dict[str, Any]:
    """A line as project_topology lists it: with its medium and its sub-segments."""
    entry: dict[str, Any] = {
        "id": line.id,
        "line_number": line.line_number,
        "name": line.name,
        "description": line.description,
        "medium": medium_name(line.medium_type),
        "main_segment_id": line.main_segment_id,
        "segments": [_segment_entry(s) for s in line.segments],
    }
    if line.domain_address is not None:
        entry["domain_address"] = format_domain_address(
            line.medium_type, line.domain_address
        )
    return entry


def _device_dict(device: Device) -> dict[str, Any]:
    return {
        "node_id": device.node_id,
        "name": device.name,
        "individual_address": device.individual_address or None,
        "description": device.description,
    }


def _additional_address_dict(entry: Any) -> dict[str, Any]:
    return {
        "index": entry.index,
        "address": entry.address,
        "name": entry.name,
        "description": entry.description,
    }


def _forced_dict(forced: Any) -> dict[str, Any] | None:
    """The group that forces its Security on what is below it, or None."""
    if forced is None:
        return None
    return {
        "group_range_id": forced.group_range_id,
        "name": forced.name,
        "mode": forced.mode,
    }


def _ga_dict(g: Any) -> dict[str, Any]:
    return {
        "id": g.id,
        "address": g.address,
        "name": g.name,
        "datapoint_type": g.datapoint_type,
        "description": g.description,
        "data_secure": g.data_secure,
        "security": g.security_mode or "Auto",
        "security_forced_by": _forced_dict(g.forced_by),
        "central": g.central,
    }


def _space_device_dict(d: Any) -> dict[str, Any]:
    return _prune(
        {
            "node_id": d.id,
            "name": d.name,
            "individual_address": d.individual_address or None,
            "description": d.description,
            "product_name": d.product_name,
            "manufacturer_name": d.manufacturer_name,
        }
    )


def _function_dict(f: Any) -> dict[str, Any]:
    return _prune(
        {
            "id": f.id,
            "name": f.name,
            "function_type": f.function_type,
            "usage_text": f.usage_text,
            "group_addresses": [
                {
                    "id": g.id,
                    "group_address_id": g.group_address_id,
                    "text": g.text,
                    "role": g.role,
                }
                for g in f.group_addresses
            ],
        }
    )


def _space_dict(s: Any) -> dict[str, Any]:
    """Recursively serialize a building/location tree node (space -> children/devices/functions).
    Empty optional fields (number, usage, usage_text, description, current_line_id) and empty
    device/function/children lists are omitted to keep the tree compact."""
    return _prune(
        {
            "id": s.id,
            "name": s.name,
            "space_type": s.space_type,
            "number": s.number,
            "usage": s.usage,
            "usage_text": usage_label(s.usage, s.usage_text)
            if s.usage
            else s.usage_text,
            "description": s.description,
            # The space's own Current Line (see project_get_current_line).
            "current_line_id": s.default_line_id,
            "devices": [_space_device_dict(d) for d in s.devices],
            "functions": [_function_dict(f) for f in s.functions],
            "children": [_space_dict(c) for c in s.children],
        }
    )


def _widget_dict(widget: Any, include_choices: bool = True) -> dict[str, Any]:
    """Serialize a UiParameter widget into the value constraints an LLM needs to set it correctly.

    ``include_choices=False`` (the bulk list/tree tools) drops an enum's full choice list to save
    tokens, keeping only ``choice_count``; the per-parameter project_get_parameter returns the
    complete ``choices``."""
    if widget is None:
        return {"type": "none"}
    name = type(widget).__name__
    if name == "EnumWidget":
        if not include_choices:
            return {"type": "enum", "choice_count": len(widget.choices)}
        return {
            "type": "enum",
            "choices": [{"value": c.value, "label": c.label} for c in widget.choices],
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


def _enum_value_label(widget: Any, value: Any) -> str | None:
    """Label of an enum widget's currently selected ``value``, or None for non-enums/no match."""
    if widget is None or type(widget).__name__ != "EnumWidget":
        return None
    for c in widget.choices:
        if str(c.value) == str(value):
            return c.label
    return None


def _parameter_dict(p: Any, include_choices: bool = True) -> dict[str, Any]:
    data = {
        "ref_id": p.ref_id,
        "label": p.label,
        "value": p.value,
        "default_value": p.default_value,
        "access": getattr(p.access, "name", str(p.access)),
        "widget": _widget_dict(p.widget, include_choices),
    }
    # With the choice list omitted, keep the current value readable by naming its label.
    if (
        not include_choices
        and (label := _enum_value_label(p.widget, p.value)) is not None
    ):
        data["value_label"] = label
    return data


def _resolve_enum_value(widget: Any, value_label: str, param_label: str) -> str:
    """The raw value of the enum choice whose label is ``value_label`` (exact, case-insensitive);
    raises ValueError when the parameter is not an enum or the label matches no/several choices."""
    if widget is None or type(widget).__name__ != "EnumWidget":
        raise ValueError(
            f"parameter {param_label!r} is not an enum; pass a raw value, not value_label"
        )
    want = value_label.strip().casefold()
    hits = [c for c in widget.choices if (c.label or "").strip().casefold() == want]
    if not hits:
        avail = ", ".join(repr(c.label) for c in widget.choices)
        raise ValueError(
            f"no choice labelled {value_label!r} for {param_label!r}; choices: {avail}"
        )
    if len(hits) > 1:
        raise ValueError(
            f"choice label {value_label!r} is ambiguous for {param_label!r}"
        )
    return str(hits[0].value)


def resolve_parameter_ref(
    nodes: list[Any], label: str, value_label: str | None = None
) -> dict[str, Any]:
    """Resolve a parameter by its exact (case-insensitive) label within ``nodes`` to ONE ref_id.

    Labels are not unique, so a label matching zero or several active parameters raises ValueError
    (listing the candidate ref_ids) rather than silently picking one — the caller narrows the scope.
    Matches only currently active parameters (the pruned UI). With ``value_label`` (an enum choice's
    text) the result also carries ``resolved_value``, the raw value for project_set_parameter.
    Returns {ref_id, label, value, access, resolved_value?}. Shared by project_resolve_parameter and
    (later) apply-time resolution in the batch tool."""
    want = label.strip().casefold()
    matches = [
        p
        for p in _collect_ui_parameters(nodes)
        if (p.label or "").strip().casefold() == want
    ]
    if not matches:
        raise ValueError(f"no active parameter labelled {label!r} in this scope")
    if len(matches) > 1:
        cands = ", ".join(sorted(p.ref_id for p in matches))
        raise ValueError(
            f"parameter label {label!r} is ambiguous ({len(matches)} matches: {cands}); "
            "narrow the scope with subtree_id"
        )
    p = matches[0]
    out: dict[str, Any] = {
        "ref_id": p.ref_id,
        "label": p.label,
        "value": p.value,
        "access": getattr(p.access, "name", str(p.access)),
    }
    if value_label is not None:
        out["resolved_value"] = _resolve_enum_value(p.widget, value_label, p.label)
    return out


def _tree_fingerprint(node_id: int, roots: list[Any]) -> str:
    """A short, structural fingerprint of a device's UI tree: node_id + the (path, container-raw-id,
    child parameter ref_ids) of every container, in a deterministic order. Captures the mapping that
    a scoped operation depends on; stays stable across ordinary value edits (ref_ids and container
    identity do not change on a value change). Hash prefix is enough — collisions would mean two
    distinct structures with the same path layout AND the same ref_ids per container."""
    import hashlib

    from xknxeditor.prod.parser_v2.ui import (
        UiParameter,
        UiParameterBlock,
        UiTab,
    )

    parts: list[str] = [f"n={node_id}"]

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, (UiTab, UiParameterBlock)):
            return
        refs: list[str] = []
        for i, ch in enumerate(node.children):
            if isinstance(ch, UiParameter):
                refs.append(ch.ref_id)
            walk(ch, f"{path}.{i}")
        raw = node.id or ""
        parts.append(f"{path}:{raw}:{','.join(refs)}")

    for i, root in enumerate(roots):
        walk(root, str(i))
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return digest


def resolve_in_scope(
    roots: list[Any],
    subtree_id: str | None,
    label: str,
    value_label: str | None = None,
    *,
    node_id: int | None = None,
    tree_token: str | None = None,
) -> dict[str, Any]:
    """:func:`resolve_parameter_ref` within an optional ``subtree_id`` scope of ``roots``. Raises
    ValueError for a stale scope token, an unknown scope, or an ambiguous/unknown label. Shared by
    project_resolve_parameter and the batch tool's apply-time resolution.

    When ``subtree_id`` is given, ``tree_token`` is checked against the CURRENT tree's fingerprint:
    a path can keep existing after a structural change but point to a different container — a bare
    path cannot detect that silently, so a scoped mutation requires the token obtained from the same
    tree read. The check runs HERE (not at batch entry) so earlier ops in the same batch that reshape
    the tree invalidate later scoped ops of the same batch rather than silently retargeting them."""
    if subtree_id is not None:
        if tree_token is None:
            raise ValueError(
                "a scoped (subtree_id) operation requires tree_token from the same "
                "project_parameter_tree response; re-read it to get a fresh token"
            )
        if node_id is None:
            raise ValueError("internal: tree_token validation needs node_id")
        current = _tree_fingerprint(node_id, roots)
        if current != tree_token:
            raise ValueError(
                f"stale tree_token: the device's tree has changed since it was obtained "
                f"(current {current!r} != {tree_token!r}); re-read project_parameter_tree and "
                "pass the fresh tree_token and subtree_id"
            )
        found = _find_subtree(roots, subtree_id)
        if found is None:
            raise ValueError(
                f"no tab/block at id {subtree_id!r}; call project_parameter_tree(outline=True) "
                "for the current ids"
            )
        return resolve_parameter_ref([found], label, value_label)
    return resolve_parameter_ref(roots, label, value_label)


def _ui_node(
    node: Any, include_choices: bool = True, path: str = "0"
) -> dict[str, Any]:
    """One node of the device UI tree as a dict, preserving group nesting (tabs/blocks -> children).

    Parameters carry the full _parameter_dict; groups carry their heading and children; separators are
    dropped. Lets a caller see which parameters belong to a channel / button pair / level.
    ``include_choices`` is threaded to the parameter serialization (the tree omits enum choice lists).
    ``path`` is the node's index path (root index, then child indices joined by '.'); it is emitted as
    each container's ``id`` because it is UNIQUE — the raw module/block id repeats across module
    instances, so it is kept only as ``ref``. ``subtree_id`` takes the ``id`` path."""
    from xknxeditor.prod.parser_v2.ui import (
        UiButton,
        UiParameter,
        UiParameterBlock,
        UiTab,
    )

    if isinstance(node, UiParameter):
        return {"kind": "parameter", **_parameter_dict(node, include_choices)}
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
            d
            for i, ch in enumerate(node.children)
            if (d := _ui_node(ch, include_choices, f"{path}.{i}")).get("kind")
            != "separator"
        ]
        out: dict[str, Any] = {
            "kind": "tab" if isinstance(node, UiTab) else "block",
            "id": path,
            "name": heading,
            "children": children,
        }
        if node.id:
            out["ref"] = node.id
        return out
    return {"kind": type(node).__name__.removeprefix("Ui").lower()}


def _search_node(node: Any, needle: str, path: str = "0") -> dict[str, Any] | None:
    """``node`` as :func:`_ui_node` returns it, pruned to what the parameter view's search filter
    shows for ``needle`` (lower case): a parameter whose label contains it, a button whose text
    contains it, and the tabs/blocks leading to them. A tab or block whose heading contains it
    comes with all its content. ``None`` when nothing in ``node`` matches."""
    from xknxeditor.prod.parser_v2.ui import (
        UiButton,
        UiParameter,
        UiParameterBlock,
        UiTab,
    )

    if isinstance(node, UiParameter):
        return _ui_node(node, False, path) if needle in node.label.lower() else None
    if isinstance(node, UiButton):
        return _ui_node(node, False, path) if needle in node.text.lower() else None
    if not isinstance(node, (UiTab, UiParameterBlock)):
        return None
    heading = getattr(node, "text", None) or getattr(node, "name", None) or ""
    if needle in heading.lower():
        return _ui_node(node, False, path)
    children = [
        d
        for i, ch in enumerate(node.children)
        if (d := _search_node(ch, needle, f"{path}.{i}")) is not None
    ]
    if not children:
        return None
    out: dict[str, Any] = {
        "kind": "tab" if isinstance(node, UiTab) else "block",
        "id": path,
        "name": heading or None,
        "children": children,
    }
    if node.id:
        out["ref"] = node.id
    return out


# Above this many parameters, a default project_parameter_tree read returns the outline instead of
# the full tree, so a single call never floods the caller with a touch-panel-sized payload. Below it,
# the whole tree is cheap enough to send at once. Forced outline/subtree_id reads ignore this.
_TREE_AUTO_OUTLINE_PARAMS = 600


def _count_leaves(node: Any) -> tuple[int, int]:
    """``(parameters, com_objects)`` in ``node``'s whole subtree."""
    from xknxeditor.prod.parser_v2.ui import (
        UiComObject,
        UiParameter,
        UiParameterBlock,
        UiTab,
    )

    params = cos = 0
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, UiParameter):
            params += 1
        elif isinstance(current, UiComObject):
            cos += 1
        elif isinstance(current, (UiTab, UiParameterBlock)):
            stack.extend(current.children)
    return params, cos


def _outline_node(node: Any, path: str = "0") -> dict[str, Any] | None:
    """A container (tab/block) as an outline entry: id (the unique index path, see :func:`_ui_node`),
    heading, subtree leaf counts and nested containers only — no parameter/com-object leaves. ``None``
    for a non-container node."""
    from xknxeditor.prod.parser_v2.ui import UiParameterBlock, UiTab

    if not isinstance(node, (UiTab, UiParameterBlock)):
        return None
    params, cos = _count_leaves(node)
    children = [
        d
        for i, ch in enumerate(node.children)
        if (d := _outline_node(ch, f"{path}.{i}")) is not None
    ]
    out: dict[str, Any] = {
        "kind": "tab" if isinstance(node, UiTab) else "block",
        "id": path,
        "name": getattr(node, "text", None) or getattr(node, "name", None),
        "parameter_count": params,
        "com_object_count": cos,
        "children": children,
    }
    if node.id:
        out["ref"] = node.id
    return out


def _find_subtree(nodes: list[Any], subtree_id: str) -> Any | None:
    """The container at index path ``subtree_id`` (root index, then child indices joined by '.', as
    emitted in each container's ``id``), or None when the path does not resolve to a tab/block."""
    from xknxeditor.prod.parser_v2.ui import UiParameterBlock, UiTab

    try:
        parts = [int(p) for p in subtree_id.split(".")]
    except ValueError:
        return None
    if not parts:
        return None
    node: Any = nodes[parts[0]] if parts[0] < len(nodes) else None
    try:
        for p in parts[1:]:
            node = node.children[p]
    except (IndexError, AttributeError, TypeError):
        return None
    return node if isinstance(node, (UiTab, UiParameterBlock)) else None


def _dotted_dpt(value: str | None) -> str | None:
    """A DPT in the dotted form the rest of the API uses (e.g. ``1.001``), from an ETS token
    (``DPST-1-1``) or a value already dotted; the raw value if it cannot be parsed."""
    if not value:
        return value
    from editor_gui.dpt import dpt_from_token

    try:
        return dpt_from_token(value).code or value
    except Exception:
        return value


def _link_annotations(
    co_dpt_token: str | None,
    co_dpt_code: str | None,
    ga_dpt_token: str | None,
    is_sending: bool,
    existing: list[Any],
    group_address_id: int,
) -> dict[str, Any]:
    """Feedback a link should surface but the service only logs: a DPT mismatch (the GA keeps its
    own DPT, so a differing com-object DPT is a real wiring issue) and, for a sending link, the other
    group addresses the object already sends to (one sending GA per object is the norm). DPTs are
    compared as tokens (reliable) but reported dotted, as everywhere else."""
    out: dict[str, Any] = {}
    if co_dpt_token and ga_dpt_token and co_dpt_token != ga_dpt_token:
        out["dpt_mismatch"] = {
            "com_object": co_dpt_code or _dotted_dpt(co_dpt_token),
            "group_address": _dotted_dpt(ga_dpt_token),
        }
    if is_sending:
        others = sorted(
            {
                a.group_address_id
                for a in existing
                if a.is_sending and a.group_address_id != group_address_id
            }
        )
        if others:
            out["other_sending_group_address_ids"] = others
    return out


def perform_link(
    project: Any,
    node_id: int,
    com_object_ref_id: str,
    co: Any,
    group_address_id: int,
    is_sending: bool,
    *,
    hidden: bool = False,
) -> dict[str, Any] | None:
    """Create a com-object -> group-address link and build a feedback result: idempotent dedup
    (``already_linked``, no duplicate row), ``dpt_mismatch`` and ``other_sending_group_address_ids``
    (see :func:`_link_annotations`), and ``hidden: true`` when the device's parameters hide the
    com-object (see :func:`com_object_hidden`). ``co`` is the resolved com-object (``db_id`` set).
    Returns None when the service refuses the link (unknown GA / security), so the caller raises its
    own error. Shared by project_link_com_object and the batch link op so both give the same
    feedback."""
    existing = list(project.get_links_for_com_object(co.db_id))
    co_dpt = getattr(co, "dpt", None)
    ga_before = project.get_group_address(group_address_id)
    ga_dpt_before = ga_before.datapoint_type if ga_before else None
    annotations = _link_annotations(
        getattr(co_dpt, "token", None),
        getattr(co_dpt, "code", None),
        ga_dpt_before,
        is_sending,
        existing,
        group_address_id,
    )
    base: dict[str, Any] = {
        "node_id": node_id,
        "com_object_ref_id": com_object_ref_id,
        "com_object_db_id": co.db_id,
        "group_address_id": group_address_id,
    }
    if hidden:
        base["hidden"] = True
    # Idempotent: re-linking the same pair returns the existing link, never a duplicate.
    dup = next((a for a in existing if a.group_address_id == group_address_id), None)
    if dup is not None:
        return {
            **base,
            "link_id": dup.id,
            "is_sending": dup.is_sending,
            "already_linked": True,
            **annotations,
        }
    link_id = project.link_com_object_to_ga(co.db_id, group_address_id, is_sending)
    if link_id is None:
        return None
    return {
        **base,
        "link_id": link_id,
        "is_sending": is_sending,
        "already_linked": False,
        **annotations,
    }


def com_object_hidden(device: Device, com_object_ref_id: str) -> bool:
    """Whether the device's parameters currently hide its com-object ``com_object_ref_id``: the
    project keeps a link to it, but the device's application does not use the object until a
    parameter shows it again."""
    return all(co.id != com_object_ref_id for co in device.get_visible_com_objects())


def link_id_between(
    project: ProjectService,
    device: Device,
    com_object_ref_id: str,
    group_address_id: int,
) -> int:
    """The id of the link between ``device``'s com-object ``com_object_ref_id`` and the group
    address ``group_address_id``; raises ValueError when there is none."""
    co = device.find_com_object(com_object_ref_id)
    if co is None:
        raise ValueError(
            f"no com-object {com_object_ref_id!r} on device {device.node_id}; "
            "call project_list_com_objects for valid ref_ids"
        )
    links = project.get_links_for_com_object(co.db_id) if co.db_id is not None else []
    for link in links:
        if link.group_address_id == group_address_id:
            return link.id
    raise ValueError(
        f"com-object {com_object_ref_id!r} of device {device.node_id} is not linked to group "
        f"address {group_address_id}"
    )


def require_editable_param(device: Any, ref_id: str) -> Any:
    """The visible, user-editable UI parameter with ``ref_id`` on ``device``, else raises ToolError.

    MCP writes must target what the GUI shows as editable:
    - absent from the rendered UI (deactivated, Access.NONE, module instance not materialised) → reject;
    - present but Access.READ (plugin-wrapped or ParameterRef override) → reject.
    This is deliberately stricter than the core evaluator's active-set check (an Access.NONE
    parameter is still internally "active" because the mark fires before the Access gate), and it is
    checked AFTER semantic resolution so a batch op whose scope an earlier op changed sees the
    current access. Internal paths (write_parameter, calculations, raw restore) stay unaffected —
    they go through DynamicUI directly and do not use this helper."""
    for p in _collect_ui_parameters(device.get_ui()):
        if p.ref_id == ref_id:
            access_name = getattr(p.access, "name", str(p.access))
            if access_name != "READ_WRITE":
                raise ToolError(
                    f"parameter {ref_id!r} on device {device.node_id} is {access_name} "
                    "(not user-editable); its value is set internally"
                )
            return p
    raise ToolError(
        f"parameter {ref_id!r} is not visible on device {device.node_id} "
        "(not instantiated or deactivated by the current configuration); "
        "call project_list_parameters for the currently editable parameters"
    )


@dataclass(frozen=True, slots=True)
class VisibleState:
    """The parameters and com-objects a device shows, by ref id."""

    parameters: frozenset[str]
    com_objects: frozenset[str]


def visible_state(device: Any) -> VisibleState:
    """What ``device`` shows now, to tell what an edit shows or hides (see param_change_result)."""
    return VisibleState(
        frozenset(p.ref_id for p in _collect_ui_parameters(device.get_ui())),
        frozenset(co.id for co in device.get_visible_com_objects()),
    )


def apply_parameter(
    project: ProjectService, device: Device, parameter_ref_id: str, value: str
) -> dict[str, Any]:
    """Set a user-editable parameter as one undo step and return :func:`param_change_result`.

    edit_params (not set_param) applies and reconciles as one undo step and raises on reject
    WITHOUT the GUI's stash-and-retry, so a rejected MCP set never silently takes effect on a later
    edit. Shared by project_set_parameter and the batch set_parameter op."""
    # Reject NON-editable targets (absent / Access.NONE / Access.READ); matches what the GUI lets a
    # user change. The core engine's active-set guard alone would still accept hidden-but-
    # internally-active and read-only parameters.
    require_editable_param(device, parameter_ref_id)
    before = visible_state(device)
    changes = project.edit_params(device, [(parameter_ref_id, value)], mode="edit")
    # A change of the com-object set rebuilds the device; its new com-objects are on the new one,
    # the links the change dropped on the edited one.
    after = project.find_device_by_node_id(device.node_id) or device
    return param_change_result(
        after,
        parameter_ref_id,
        value,
        changes,
        before=before,
        lost_links=list(getattr(device, "last_lost_links", []) or []),
    )


def _compact_parameter_dict(p: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"ref_id": p.ref_id, "label": p.label, "value": p.value}
    if (label := _enum_value_label(p.widget, p.value)) is not None:
        data["value_label"] = label
    return data


def param_change_result(
    device: Any,
    parameter_ref_id: str,
    requested: str,
    changes: dict[str, tuple[str | None, str | None]],
    *,
    before: VisibleState | None = None,
    lost_links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A trustworthy set-parameter result: the EFFECTIVE value read back after the edit, whether the
    parameter is still active, and the calculated side-effect changes — so a caller never has to
    assume the requested value stuck (it may be normalized, or the parameter may have been
    deactivated by a structural change). ``changes`` is the edit's calculation change set.

    With ``before`` (the device's :func:`visible_state` before the edit) the result also names
    the parameters and com-objects the edit showed or hid. ``lost_links`` are the links the edit
    dropped (default: the device's ``last_lost_links``)."""
    effective: str | None = None
    active = False
    shown_parameters: list[dict[str, Any]] = []
    visible_parameters: set[str] = set()
    for p in _collect_ui_parameters(device.get_ui()):
        visible_parameters.add(p.ref_id)
        if p.ref_id == parameter_ref_id:
            effective, active = p.value, True
        if before is not None and p.ref_id not in before.parameters:
            shown_parameters.append(_compact_parameter_dict(p))
    if not active and parameter_ref_id in changes:
        # The edit deactivated the parameter itself (a structural change); report its stored value.
        effective = changes[parameter_ref_id][1]
    result: dict[str, Any] = {
        "ref_id": parameter_ref_id,
        "requested": requested,
        "value": effective,
        "active": active,
    }
    side_effects = [
        {"ref_id": ref, "old": old, "new": new}
        for ref, (old, new) in sorted(changes.items())
        if ref != parameter_ref_id and old != new
    ]
    if side_effects:
        result["changed"] = side_effects
    # Links the edit's com-object reconcile dropped (a function/mode change deactivating linked
    # objects), captured by the service before deletion.
    lost = (
        lost_links
        if lost_links is not None
        else list(getattr(device, "last_lost_links", []) or [])
    )
    if lost:
        result["lost_links"] = lost
    if before is not None:
        hidden_parameters = sorted(before.parameters - visible_parameters)
        objects = device.get_visible_com_objects()
        shown_objects = [
            {
                "ref_id": co.id,
                "number": co.number,
                "name": com_object_display_name(co) or co.name,
                "linkable": co.db_id is not None,
            }
            for co in objects
            if co.id not in before.com_objects
        ]
        hidden_objects = sorted(before.com_objects - {co.id for co in objects})
        for key, items in (
            ("shown_parameters", shown_parameters),
            ("hidden_parameters", hidden_parameters),
            ("shown_com_objects", shown_objects),
            ("hidden_com_objects", hidden_objects),
        ):
            if items:
                result[key] = items
    return result


def require_device(
    project: ProjectService,
    node_id: int | None = None,
    device_address: str | None = None,
) -> Device:
    """Resolve a device by its internal node_id OR its individual address (e.g. "1.0.30"). Exactly
    one must be given; the int form stays the primary id, the string form saves the extra
    project_list_devices lookup when the agent already knows the address."""
    if (node_id is None) == (device_address is None):
        raise ToolError(
            "pass exactly one of node_id (int) or device_address (str, e.g. '1.0.30')"
        )
    if device_address is not None:
        matches = [d for d in project.devices if d.individual_address == device_address]
        if not matches:
            raise ToolError(
                f"no device with individual_address {device_address!r}; "
                "call project_list_devices for the configured addresses"
            )
        if len(matches) > 1:
            nids = sorted(d.node_id for d in matches)
            raise ToolError(
                f"individual_address {device_address!r} is ambiguous "
                f"(node_ids: {nids}); pass node_id explicitly"
            )
        return matches[0]
    assert node_id is not None  # proven by the exactly-one check above
    device = project.find_device_by_node_id(node_id)
    if device is None:
        raise ToolError(
            f"no device with node_id {node_id}; call project_list_devices for valid ids"
        )
    return device


def require_group_address_id(
    project: ProjectService,
    group_address_id: int | None = None,
    group_address: str | None = None,
) -> int:
    """Resolve a group address id by its internal id OR its printed address (e.g. "10/1/2" in
    the project's style). Exactly one must be given, and the group address must exist. Keeps
    callers off the extra project_list_group_addresses round-trip in the common case."""
    if (group_address_id is None) == (group_address is None):
        raise ToolError(
            "pass exactly one of group_address_id (int) or group_address (str, e.g. '10/1/2')"
        )
    if group_address is not None:
        matches = [g for g in project.group_addresses if g.address == group_address]
        if not matches:
            raise ToolError(
                f"no group address {group_address!r}; call project_list_group_addresses "
                "or create it with project_create_group_address first"
            )
        if len(matches) > 1:
            ids = sorted(g.id for g in matches)
            raise ToolError(
                f"group_address {group_address!r} is ambiguous "
                f"(ids: {ids}); pass group_address_id explicitly"
            )
        return matches[0].id
    assert group_address_id is not None  # proven by the exactly-one check above
    if project.get_group_address(group_address_id) is None:
        raise ToolError(
            f"no group address with id {group_address_id}; call "
            "project_list_group_addresses for valid ids"
        )
    return group_address_id


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    project = ctx.api.project

    def _require_device(
        node_id: int | None = None, device_address: str | None = None
    ) -> Device:
        return require_device(project, node_id, device_address)

    def _require_group_address_id(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> int:
        return require_group_address_id(project, group_address_id, group_address)

    def _device_from(node_id: int | None, device_address: str | None) -> Device:
        """Device resolver call site shared by the tools that accept either addressing form."""
        return _require_device(node_id, device_address)

    # --- application update ("Update Application Program") ------------

    @tool("read")
    def project_devices_with_update() -> dict[str, Any]:
        """List devices that project_update_application can update: the local catalog has a
        version of the same application program whose ReplacesVersions names the current one.

        Returns {items, count}; each item has {node_id, individual_address, name, application_id,
        current_version, available_version}. Pass an item's node_id to project_update_application
        to update the project; this lookup does not change the project or bus."""
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

    @tool("destructive")
    def project_update_application(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Update a device to the local catalog version of the SAME application program that
        replaces the current one ("Update Application Program Version"). Identify the device by
        ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass exactly one.
        Parameter values carry over by ParameterRef number, group objects of the same size by
        ComObjectRef number with their flags, priority, datapoint type and group-address links.

        This is an undoable project edit (project_undo). To apply it on the bus, program the device
        afterwards with connection_program_device and scope="FULL". Returns {node_id, new_version,
        kept, dropped, note}; kept and dropped are counts of carried-over vs discarded parameters and
        group objects."""
        require_project(ctx)

        def _do() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            result = project.update_application(device)
            if result is None:
                raise ToolError(
                    f"no newer application version available for device {device.node_id}"
                )
            return {
                "node_id": device.node_id,
                "new_version": result.new_version,
                "kept": result.kept,
                "dropped": result.dropped,
                "note": "project updated; program the device (connection_program_device, "
                "scope FULL) to apply the change on the bus",
            }

        return ctx.run_locked(_do, timeout=_SLOW)

    @tool("read")
    def project_list_application_programs(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """The application programs a device can be changed to with project_change_application:
        the other programs of its hardware in the local catalog, older and newer.

        Identify the device by ``node_id`` OR ``device_address`` (an individual address like
        "1.0.30"); pass exactly one. Returns {items, count}; each item has
        {hardware2program_ref_id, application_id, name, version}. Pass hardware2program_ref_id to
        project_change_application for the same device. This lookup is read-only."""
        require_project(ctx)

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id, device_address)
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

    @tool("destructive")
    def project_change_application(
        hardware2program_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Change a device's project application to another program of its hardware. Identify the
        device by ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass
        exactly one. Use hardware2program_ref_id from project_list_application_programs for that
        device. Parameters, group-object links and flags reset to the new application's
        defaults; address, name and serial number stay.

        Undoable with project_undo. Apply it on the bus with connection_program_device(scope="FULL").
        Returns {node_id, application_id, version, note}."""
        require_project(ctx)

        def _do() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
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
                    f"{device.node_id} can be changed to; see project_list_application_programs"
                )
            if not project.change_application(device, program):
                raise ToolError(
                    f"application {program.application_id!r} not resolvable"
                )
            return {
                "node_id": device.node_id,
                "application_id": program.application_id,
                "version": program.application_version,
                "note": "project changed; program the device to apply it on the bus",
            }

        return ctx.run_locked(_do, timeout=_SLOW)

    # --- lifecycle --------------------------------------------------------

    @tool("read")
    def project_status() -> dict[str, Any]:
        """Return {open, path}, with the editor-host path or null when no project is open.
        Most project_* tools need an open project."""
        return ctx.run_locked(
            lambda: {
                "open": project.is_open,
                "path": str(project.path) if project.path else None,
            }
        )

    @tool("destructive")
    def project_new(path: str) -> dict[str, Any]:
        """Create a new empty KNX project (.xknx) at ``path`` (on the editor host) and open it. An
        existing file at ``path`` is replaced and the current project closed; file replacement is NOT
        undoable with project_undo. Returns {open, path}."""
        ctx.run_locked(lambda: project.new(Path(path)), timeout=_SLOW)
        return {"open": True, "path": str(project.path) if project.path else None}

    @tool("write")
    def project_open(path: str, password: str | None = None) -> dict[str, Any]:
        """Open an existing .xknx project at path on the editor host, closing the current project.
        Returns {open, path}. Switching projects is outside project_undo history.

        ``password`` is required for a password-protected project."""
        ctx.run_locked(lambda: project.open(Path(path), password), timeout=_SLOW)
        return {"open": True, "path": str(project.path) if project.path else None}

    @tool("destructive")
    def project_import_knxproj(
        source: str,
        dest: str,
        password: str | None = None,
        project_password: str | None = None,
    ) -> dict[str, Any]:
        """Import a .knxproj (``source``) into a new .xknx project at ``dest`` and open it.

        Both paths are on the editor host. ``password`` is required for encrypted projects; it
        then protects the new project too. An archive with KNX Secure data but without a password
        needs ``project_password`` to protect the new project. An existing file at ``dest`` is
        replaced and bundled product data is imported into the local catalog; neither the file
        replacement nor the catalog import is undoable with project_undo. Returns {open, path}."""
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

    @tool("write")
    def project_add_device_certificate(
        code: str, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Add a KNX Data Secure device certificate: ``code`` is the FDSK from the device label
        (36 characters, groups separated by "-"). A device with the certificate's serial number is
        then commissioned securely with it. With ``node_id`` OR ``device_address`` (an individual
        address like "1.0.30") the certificate is for that device: one without a serial number
        takes the certificate's, one with another is refused. Needs a password-protected
        project."""
        from editor_gui.device_info import serial_from_project, serial_to_project
        from xknxeditor.proj import ProjectPasswordRequired
        from xknxeditor.proj.core.fdsk import InvalidFdskError, parse_fdsk

        require_project(ctx)
        try:
            serial = parse_fdsk(code).serial_number
        except InvalidFdskError as exc:
            raise ToolError(f"not a valid FDSK: {exc}") from exc

        def _add() -> dict[str, Any]:
            device = (
                _require_device(node_id, device_address)
                if node_id is not None or device_address is not None
                else None
            )
            if device is not None:
                info = project.get_device_info(device.node_id)
                current = serial_from_project(info.serial_number) if info else None
                if current is not None and current != serial:
                    raise ToolError(
                        f"the certificate is for serial number {serial.hex().upper()}, "
                        f"device {device.node_id} has {current.hex().upper()}"
                    )
            try:
                project.add_device_certificate(code)
            except ProjectPasswordRequired as exc:
                raise ToolError("set a project password first") from exc
            if device is not None:
                project.set_device_commissioning(
                    device.node_id, serial_number=serial_to_project(serial)
                )
            return {"serial_number": serial.hex().upper()}

        return ctx.run_locked(_add)

    @tool("write")
    def project_set_secure_commissioning(
        activated: bool, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Activate or deactivate a KNX Data Secure device's secure commissioning (one undo step).
        Identify the device by ``node_id`` OR ``device_address`` (an individual address like
        "1.0.30"); pass exactly one. Activated, the device gets a Tool Key (needs a password-protected project) and is
        commissioned with its device certificate. Deactivated, it is programmed in the clear: its
        "Auto" group addresses communicate in the clear and its links to group addresses with
        Security "On" are removed (the result lists both)."""
        from xknxeditor.proj import ProjectPasswordRequired

        require_project(ctx)

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            if project.secure_commissioning(device) is None:
                raise ToolError(
                    f"device {device.node_id} does not support KNX Data Secure"
                )
            impact = None if activated else project.secure_commissioning_impact(device)
            try:
                project.set_secure_commissioning(device, activated)
            except ProjectPasswordRequired as exc:
                raise ToolError("set a project password first") from exc
            return {
                "node_id": device.node_id,
                "secure_commissioning": activated,
                "unsecured_group_address_ids": impact.unsecured if impact else [],
                "removed_link_ids": impact.removed_links if impact else [],
            }

        return ctx.run_locked(_set)

    @tool("read")
    def project_get_ip_secure(
        node_id: int | None = None,
        device_address: str | None = None,
        include_passwords: bool = False,
    ) -> dict[str, Any]:
        """The KNX IP Secure settings of a secure IP device commissioned securely: whether its
        tunnels are secured and its bus interfaces (kind, user id, tunnelling address). Identify
        the device by ``node_id`` OR ``device_address`` (an individual address like "1.0.30");
        pass exactly one. With ``include_passwords`` also its commissioning password, its
        authentication code and the bus interfaces' passwords - only when the user asks for
        them."""
        require_project(ctx)

        def _get() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            settings = project.ip_secure_settings(device)
            if settings is None:
                raise ToolError(
                    f"device {device.node_id} is no KNX IP Secure device commissioned securely"
                )
            result: dict[str, Any] = {
                "node_id": device.node_id,
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

    @tool("write")
    def project_set_ip_secure(
        node_id: int | None = None,
        device_address: str | None = None,
        commissioning_password: str | None = None,
        authentication_code: str | None = None,
        interface_ref_id: str | None = None,
        interface_password: str | None = None,
        secure_tunnelling: bool | None = None,
    ) -> dict[str, str]:
        """Change a secure IP device's KNX IP Secure settings (the next download writes them).
        Identify the device by ``node_id`` OR ``device_address`` (an individual address like
        "1.0.30"); pass exactly one. Settable: its commissioning password, its authentication code, the password of one bus interface
        (``interface_ref_id`` from project_get_ip_secure) or whether its tunnels are secured.
        Passwords have 1 to 20 Latin-1 characters."""
        from xknxeditor.proj import ProjectPasswordRequired

        require_project(ctx)
        if (interface_ref_id is None) != (interface_password is None):
            raise ToolError("give interface_ref_id and interface_password together")

        def _set() -> dict[str, str]:
            device = _require_device(node_id, device_address)
            if project.ip_secure_settings(device) is None:
                raise ToolError(
                    f"device {device.node_id} is no KNX IP Secure device commissioned securely"
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
            return {"status": "changed", "node_id": str(device.node_id)}

        return ctx.run_locked(_set)

    @tool("destructive")
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

    @tool("write")
    def project_close() -> dict[str, str]:
        """Close the open project. Returns {status: "closed"}. Reopen it with project_open;
        closing is outside project_undo history."""
        ctx.run_locked(project.close)
        return {"status": "closed"}

    @tool("destructive")
    def project_export_knxproj(dest: str) -> dict[str, str]:
        """Export the open project to a .knxproj archive at ``dest`` (with manufacturer bundle).
        Returns {status, dest} plus an optional ``warning``. ``status="incomplete"`` means
        manufacturer references are missing — ETS may reject the archive, so inspect the warning."""
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
                # The current signed master, cached with the app's master data.
                fetch_master=True,
                master_cache_dir=config_dir(),
                module_arguments=project.module_arguments(),
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

    @tool("destructive")
    def project_compact(dry_run: bool = True) -> dict[str, Any]:
        """Remove product data no device uses from the open project and compact its file.

        dry_run=true (default) previews removal and estimates file_size_after. Applying compaction
        is irreversible with project_undo; removed product data must be imported again if needed.
        Returns {status, nothing_unused, file_size_before, file_size_after, removed,
        product_data_size_before, product_data_size_after}; sizes are bytes. status is "analysed" or
        "compacted". removed contains lists named application_programs, products, hardware,
        catalog_items, baggages and manufacturers: IDs, except baggages contains archive paths."""
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

    @tool("read")
    def project_info() -> dict[str, Any]:
        """Project metadata. Returns {id, name, group_address_style, guid, created_by, last_modified,
        schema_version, tool_version}. ``group_address_style`` (ThreeLevel/TwoLevel/Free) governs how
        group-address strings parse."""
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

    @tool("read")
    def project_list_devices(name_contains: str | None = None) -> dict[str, Any]:
        """List project devices, optionally filtering names by a case-insensitive substring.
        Returns {items, count}; each item has {node_id, name, individual_address, description}.
        Pass node_id to device tools; individual_address is null when unset. This reads the project."""

        def _read() -> list[dict[str, Any]]:
            needle = (name_contains or "").lower()
            return [
                _device_dict(d)
                for d in project.devices
                if not needle or needle in d.name.lower()
            ]

        return _items(ctx.run_locked(_read))

    @tool("read")
    def project_get_device(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Read a project device. Identify it by ``node_id`` OR ``device_address`` (an individual
        address like "1.0.30"); pass exactly one. Returns {node_id, name, individual_address,
        description}, plus {product_ref_id, hardware2program_ref_id, order_number, hardware_name,
        product_name, manufacturer_name, additional_addresses} when catalog metadata is available.
        individual_address is null when unset. additional_addresses lists the device's additional
        individual addresses (the tunnelling addresses of an IP interface) as {index, address, name,
        description}; index is 1-based (the AddressIndex of a tunnelling interface), address an
        "a.l.d" string or null for an entry without one."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            info = project.get_device_info(device.node_id)
            data = _device_dict(device)
            if info is not None:
                data |= {
                    "product_ref_id": info.product_ref_id,
                    "hardware2program_ref_id": info.hardware2program_ref_id,
                    "order_number": info.order_number,
                    "hardware_name": info.hardware_name,
                    "product_name": info.product_name,
                    "manufacturer_name": info.manufacturer_name,
                    "additional_addresses": [
                        _additional_address_dict(entry)
                        for entry in info.additional_addresses
                    ],
                }
            return data

        return ctx.run_locked(_read)

    @tool("read")
    def project_list_com_objects(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """List a project device's visible com-objects. Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one.
        Returns {items, count}; each item has {ref_id, db_id, linkable, name, number, dpt, dpt_name,
        object_size, flags, description, priority}. flags contains communication, read, write,
        transmit, update, read_on_init.
        Pass ref_id as com_object_ref_id with the same device. Linking and flag edits require
        linkable=true (db_id set)."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id, device_address)
            return [com_object_dict(co) for co in device.get_visible_com_objects()]

        return _items(ctx.run_locked(_read))

    @tool("read")
    def find_potential_com_objects(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """List EVERY com-object the device's application DECLARES — a superset of the ones currently
        active (project_list_com_objects) — so you can see which objects a function could add before
        switching its parameters (for group-address planning). Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one.

        Returns {items, count}; each item has {ref_id, number, name, function_text, dpt, object_size}
        from the STATIC declaration: names/function texts are templates (no parameter or module
        substitution) and ``number`` is the base (a module instance offsets it). This is NOT a
        reachability proof — a declared object may never be activatable in a given configuration, and
        the parameter values that activate each one are NOT included. For the active, linkable objects
        (with db_id/flags) and their links, use project_list_com_objects."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id, device_address)
            dyn = device.dynamic_ui
            if dyn is None:
                raise ToolError(f"device {device.node_id} has no application loaded")
            return dyn.declared_com_objects()

        return _items(ctx.run_locked(_read))

    @tool("read")
    def project_get_com_object(
        com_object_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Read a project com-object. Identify the device by ``node_id`` OR ``device_address`` (an
        individual address like "1.0.30"); pass exactly one. Pass the object's ref_id from
        project_list_com_objects as com_object_ref_id. Returns {ref_id, db_id, linkable, name, number,
        dpt, dpt_name, object_size, flags, description, priority}; flags contains communication,
        read, write, transmit, update, read_on_init. linkable=true is required for linking and flag edits."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {device.node_id}; "
                    "call project_list_com_objects for valid ref_ids"
                )
            return com_object_dict(co)

        return ctx.run_locked(_read)

    @tool("read")
    def project_get_com_object_links(
        com_object_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Read a com-object's group-address links in the project. Identify the device by ``node_id``
        OR ``device_address`` (an individual address like "1.0.30"); pass exactly one. Pass ref_id
        from project_list_com_objects as com_object_ref_id.
        Returns {items, count}; each item has {assignment_id, group_address_id, address, name,
        is_sending}. Pass assignment_id to project_unlink_com_object. items is empty when the object
        is not instantiated or has no links; address and name are null if the group address is absent.
        These project links do not verify what is currently programmed on the bus."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id, device_address)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {device.node_id}; "
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

    @tool("read")
    def project_list_parameters(
        node_id: int | None = None,
        device_address: str | None = None,
        search: str | None = None,
        modified_only: bool = False,
        compact: bool = False,
    ) -> dict[str, Any]:
        """A device's visible application parameters with current value and default. Identify the
        device by ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass
        exactly one.
        Returns {items, count}; each item has ref_id, label, value, default_value, access, widget.
        To keep the list compact, an enum ``widget`` carries only ``choice_count`` (plus
        ``value_label`` on the item for the current value), NOT the full choice list — call
        project_get_parameter for a single parameter's complete ``choices``. Optional ``search``
        keeps only items whose label contains it (case-insensitive) — use it on a large device
        instead of pulling every parameter; project_parameter_tree(search=...) gives each match
        with its tab/block, which tells repeated labels (one per channel) apart. ``modified_only`` keeps only the parameters whose value
        differs from their default (those the parameter view marks as changed). ``compact`` gives
        each item only ref_id, label and value (plus value_label for an enum).

        Pass an item's ``ref_id`` to project_set_parameter as its ``parameter_ref_id`` argument."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id, device_address)
            rows = [
                _compact_parameter_dict(p)
                if compact
                else _parameter_dict(p, include_choices=False)
                for p in _collect_ui_parameters(device.get_ui())
                if not modified_only or p.value != p.default_value
            ]
            if search:
                needle = search.casefold()
                rows = [r for r in rows if needle in str(r.get("label", "")).casefold()]
            return rows

        return _items(ctx.run_locked(_read))

    @tool("read")
    def project_parameter_tree(
        node_id: int | None = None,
        device_address: str | None = None,
        outline: bool = False,
        subtree_id: str | None = None,
        tree_token: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Read a device's nested parameter UI. Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one. Group
        headings distinguish repeated labels for different channels, buttons or display levels.

        ``search`` filters the tree like the search field of the parameter view (case-insensitive
        substring): it keeps the parameters whose label and the buttons whose text contain it,
        with the tabs/blocks leading to them, and a tab or block whose heading contains it with all
        its content. Every match so comes with its section (e.g. which channel's "Function" it
        is) and the section ids for a scoped read. Combine it with ``subtree_id`` to search one
        section; not with ``outline``. A search that matches nothing returns no items.
        Returns {items, count, outline, tree_token}; count is the number of top-level nodes. Node
        fields depend on kind:
        - "tab"/"block": {kind, id, name, children, ref?}, with children using the same node shapes.
          ``id`` is a UNIQUE index path (pass it verbatim as ``subtree_id``); ``ref`` is the raw
          module/block id, which REPEATS across module instances and must not be used to address one.
        - "parameter": {kind, ref_id, label, value, default_value, access, widget}.
        - "button": {kind, id, text, online, read_only}; other nodes contain only kind.
        To stay compact, an enum ``widget`` here carries only ``choice_count`` (plus ``value_label``
        for the current value), NOT the full choice list — call project_get_parameter for one
        parameter's complete ``choices``.

        The response also carries ``outline`` (bool). On a LARGE device a plain call
        (no ``outline``/``subtree_id``) returns the outline automatically instead of a huge tree —
        then ``outline`` is true and ``parameter_count`` gives the full size. To avoid pulling a big
        tree whole:
        - ``outline=True`` returns ONLY the container skeleton — each tab/block as
          {kind, id, name, parameter_count, com_object_count, children} with no parameter/com-object
          leaves. Read this to see the structure and pick a section.
        - ``subtree_id="<id>"`` returns only that one tab/block's subtree (optionally also with
          ``outline=True``). Scoped reads REQUIRE ``tree_token`` from the SAME read the id came
          from — a path can keep existing after a structural change but point to a different
          container, and the token detects that. A stale token raises; re-read without the subtree
          to get a fresh one. ``search`` (above) finds parameters with their sections.
        - ``tree_token`` is a short fingerprint of the device's current structure. Pass it back to
          any scoped read/mutation (``subtree_id``, project_resolve_parameter, batch set_parameter)
          to prove the structure has not changed since the id was obtained.

        Pass a parameter's ref_id as parameter_ref_id to project_get_parameter/project_set_parameter.
        This reads project configuration."""

        needle = (search or "").lower().strip()
        if needle and outline:
            raise ToolError(
                "search returns the matching nodes; pass it without outline"
            )

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            resolved_node_id = device.node_id
            roots: list[Any] = list(device.get_ui())
            current_token = _tree_fingerprint(resolved_node_id, roots)
            if subtree_id is not None:
                if tree_token is None:
                    raise ToolError(
                        "a scoped (subtree_id) read requires tree_token from the same "
                        "project_parameter_tree response; re-read without subtree_id first"
                    )
                if tree_token != current_token:
                    raise ToolError(
                        f"stale tree_token for device {resolved_node_id} (current "
                        f"{current_token!r} != {tree_token!r}); re-read project_parameter_tree "
                        "to refresh"
                    )
                found = _find_subtree(roots, subtree_id)
                if found is None:
                    raise ToolError(
                        f"no tab/block at id {subtree_id!r} on device {resolved_node_id}; call "
                        "project_parameter_tree(outline=True) for the current ids"
                    )
                scoped: list[tuple[str, Any]] = [(subtree_id, found)]
            else:
                scoped = [(str(i), n) for i, n in enumerate(roots)]
            if needle:
                rows = [
                    d
                    for p, n in scoped
                    if (d := _search_node(n, needle, p)) is not None
                ]
                return {
                    "items": rows,
                    "count": len(rows),
                    "outline": False,
                    "tree_token": current_token,
                }
            use_outline = outline
            total: int | None = None
            # A plain call on a large device falls back to the outline so one read is never huge.
            if not outline and subtree_id is None:
                total = sum(_count_leaves(n)[0] for _, n in scoped)
                if total > _TREE_AUTO_OUTLINE_PARAMS:
                    use_outline = True
            if use_outline:
                rows = [d for p, n in scoped if (d := _outline_node(n, p)) is not None]
            else:
                rows = [_ui_node(n, False, p) for p, n in scoped]
            env: dict[str, Any] = {
                "items": rows,
                "count": len(rows),
                "outline": use_outline,
                "tree_token": current_token,
            }
            if use_outline and not outline and total is not None:
                env["parameter_count"] = total
            return env

        return ctx.run_locked(_read)

    @tool("read")
    def project_get_parameter(
        parameter_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Read a project application parameter. Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one. Pass ref_id from
        project_list_parameters or project_parameter_tree as parameter_ref_id.
        Returns {ref_id, label, value, default_value, access, widget}; for an enum, widget includes
        the full ``choices`` (value/label/id) — the allowed values for project_set_parameter."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            for p in _collect_ui_parameters(device.get_ui()):
                if p.ref_id == parameter_ref_id:
                    return _parameter_dict(p)
            raise ToolError(
                f"no parameter {parameter_ref_id!r} on device {device.node_id}; "
                "call project_list_parameters for valid ref_ids"
            )

        return ctx.run_locked(_read)

    @tool("read")
    def project_resolve_parameter(
        label: str,
        node_id: int | None = None,
        device_address: str | None = None,
        subtree_id: str | None = None,
        value_label: str | None = None,
        tree_token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a parameter by its human ``label`` to the ``ref_id`` that project_set_parameter
        needs, so a caller can avoid the long XML ref ids. Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one.

        ``label`` is matched EXACTLY (case-insensitive) among the device's currently ACTIVE
        parameters, within a scope. Labels are NOT unique (a device repeats a label per channel), so:
        - pass ``subtree_id`` (a container ``id`` from project_parameter_tree(outline=True)) to scope
          the match to one channel/block — strongly recommended on a large device;
        - a label that matches several parameters raises with the candidate ref_ids (narrow the
          scope); a label that matches none raises too. It never guesses.
        With ``value_label`` (an enum choice's text) the result also carries ``resolved_value``, the
        raw value to pass to project_set_parameter.

        A scoped call (with ``subtree_id``) REQUIRES ``tree_token`` from the SAME
        project_parameter_tree response the id came from. A path can keep existing after a
        structural change but point to a different container, which would silently retarget the
        write; the token guards against that. A stale token raises; re-read the tree to refresh.

        Returns {ref_id, label, value, access, resolved_value?}. This resolves only — pass ``ref_id``
        (and ``resolved_value``) to project_set_parameter to change it."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            return resolve_in_scope(
                list(device.get_ui()),
                subtree_id,
                label,
                value_label,
                node_id=device.node_id,
                tree_token=tree_token,
            )

        return ctx.run_locked(_read)

    @tool("read")
    def project_list_group_addresses(
        name_contains: str | None = None,
    ) -> dict[str, Any]:
        """List project group addresses, optionally filtering names by a case-insensitive substring.
        Returns {items, count}; each item has {id, address, name, datapoint_type, description,
        data_secure, security, security_forced_by, central}: data_secure whether it is secured
        now, security its own mode (Auto, On or Off), security_forced_by the main or middle group
        that overrides it ({group_range_id, name, mode}, null for none), central whether it is
        marked central. Pass ``id`` as group_address_id to project tools. ``address`` is formatted in the
        project's style; monitor tools accept a group-address string and normalize it (to ThreeLevel)
        internally."""

        def _read() -> list[dict[str, Any]]:
            needle = (name_contains or "").lower()
            return [
                _ga_dict(g)
                for g in project.group_addresses
                if not needle or needle in g.name.lower()
            ]

        return _items(ctx.run_locked(_read))

    @tool("read")
    def project_get_group_address(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, Any]:
        """Read a project group address. Identify it by ``group_address_id`` (id from
        project_list_group_addresses) OR ``group_address`` (a printed address like "10/1/2" in the
        project's style); pass exactly one. Returns the fields project_list_group_addresses
        lists."""

        def _read() -> dict[str, Any]:
            ga_id = _require_group_address_id(group_address_id, group_address)
            g = project.get_group_address(ga_id)
            if g is None:
                raise ToolError(f"no group address with id {ga_id}")
            return _ga_dict(g)

        return ctx.run_locked(_read)

    @tool("read")
    def project_next_free_group_address() -> dict[str, Any]:
        """Return {address} for the next unused project group address, formatted per project_info's
        group_address_style. This does not reserve it or check the bus. Pass address to
        project_create_group_address to create it."""
        require_project(ctx)
        address = ctx.run_locked(project.next_free_group_address)
        if address is None:
            raise ToolError("no free group address available")
        return {"address": address}

    @tool("read")
    def project_get_ga_assignments(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, Any]:
        """Read a group address's project com-object links. Identify it by ``group_address_id`` (id
        from project_list_group_addresses) OR ``group_address`` (a printed address like "10/1/2" in
        the project's style); pass exactly one. Returns {items, count}; each item has
        {id, com_object_db_id, group_address_id, is_sending, node_id, individual_address,
        com_object_ref_id, com_object_name}. Device/object details are null when unresolved.
        Pass id as assignment_id to project_unlink_com_object, or use node_id with com_object_ref_id
        for object tools. This reads project links without checking the bus."""

        def _read() -> list[dict[str, Any]]:
            ga_id = _require_group_address_id(group_address_id, group_address)
            # map com-object db_id -> (device, com-object) so each assignment can name its owner
            owner: dict[int, tuple[Device, ComObject]] = {}
            for dev in project.devices:
                for co in dev.get_visible_com_objects():
                    if co.db_id is not None:
                        owner[co.db_id] = (dev, co)
            result: list[dict[str, Any]] = []
            for a in project.get_assignments_for_ga(ga_id):
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
                        "com_object_name": com_object_display_name(co) if co else None,
                    }
                )
            return result

        return _items(ctx.run_locked(_read))

    @tool("read")
    def project_topology() -> dict[str, Any]:
        """Read the project topology. Returns {items, count}; each area item has
        {id, area_number, name, description, lines}, and each line has {id, line_number, name,
        description, medium, domain_address?, main_segment_id, segments}: medium is TP, PL, RF,
        IP or IoT (its main segment's), segments its sub-segments [{id, number, name, medium,
        domain_address?}]. count counts areas.
        Pass an area's id as area_id and a line's id as line_id to topology tools; these IDs differ
        from the area/line numbers used in individual addresses."""

        def _read() -> list[dict[str, Any]]:
            return [
                {
                    "id": area.id,
                    "area_number": area.area_number,
                    "name": area.name,
                    "description": area.description,
                    "lines": [_line_entry(ln) for ln in project.get_lines(area.id)],
                }
                for area in project.get_areas()
            ]

        return _items(ctx.run_locked(_read))

    @tool("read")
    def project_spaces() -> dict[str, Any]:
        """Read the building/location tree (building -> floor -> room), each with its placed devices
        and functions. Use this to find a room's devices when device names are empty — the space names
        carry the location. Returns {items, count, unassigned_devices}; count counts top-level nodes.
        Each space has {id, name, space_type, number, usage_text, description, devices, functions,
        children}; a device has {node_id, name, individual_address, description, product_name,
        manufacturer_name} (pass node_id to device tools). unassigned_devices lists devices placed in
        no space. Pass a space's id as project_set_device_space's space_id."""

        def _read() -> dict[str, Any]:
            spaces = [_space_dict(s) for s in project.get_space_tree()]
            unassigned = [
                _space_device_dict(d) for d in project.get_unassigned_devices()
            ]
            return {
                "items": spaces,
                "count": len(spaces),
                "unassigned_devices": unassigned,
            }

        return ctx.run_locked(_read)

    @tool("write")
    def project_set_device_space(
        node_id: int | None = None,
        device_address: str | None = None,
        space_id: int | None = None,
    ) -> dict[str, Any]:
        """Place a device in a room/space, or remove it from any space with space_id=null. Use a
        space ``id`` from project_spaces; identify the device by ``node_id`` OR ``device_address``
        (an individual address like "1.0.30"), pass exactly one. Undoable project edit. Returns
        {node_id, space_id}."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            project.set_device_space(device.node_id, space_id)
            return {"node_id": device.node_id, "space_id": space_id}

        return ctx.run_locked(_set)

    @tool("read")
    def project_compare_devices(
        node_id_a: int | None = None,
        node_id_b: int | None = None,
        device_address_a: str | None = None,
        device_address_b: str | None = None,
        only_differences: bool = True,
    ) -> dict[str, Any]:
        """Compare two devices across header, parameters and com-objects. Identify device a by
        ``node_id_a`` OR ``device_address_a`` (an individual address like "1.0.30"), device b by
        ``node_id_b`` OR ``device_address_b``; one of each pair. Items are keyed so two instances of the SAME application line up (parameters by
        application-relative ref, com-objects by object number). Returns {node_id_a, node_id_b,
        difference_count, header, parameters, com_objects}; each section is a list of rows
        {key, label, kind, a, b} where kind is added|removed|changed|same and a/b are the values on
        each device (\"\" when the item is absent there). Com-object rows additionally carry
        a_links/b_links: the group addresses each device links as [{address, sending}] (sending
        first). This lets a caller tell a structural difference (object linked on one side only, or
        different flags/DPT) from a mere room-mapped GA difference (same structure, both linked
        sending, only the address differs). With only_differences=true (default) the 'same' rows are
        omitted to keep the result small."""
        from editor_gui.plugins.project.ui.device_diff import (
            diff_devices,
            snapshot_device,
        )

        require_project(ctx)
        for side, nid, address in (
            ("a", node_id_a, device_address_a),
            ("b", node_id_b, device_address_b),
        ):
            if (nid is None) == (address is None):
                raise ToolError(
                    f"pass exactly one of node_id_{side} or device_address_{side}"
                )

        def _rows(rows: list[Any]) -> list[dict[str, Any]]:
            return [
                {"key": r.key, "label": r.label, "kind": r.kind, "a": r.a, "b": r.b}
                for r in rows
                if not only_differences or r.kind != "same"
            ]

        def _links(cell: Any) -> list[dict[str, Any]]:
            links = cell.links if cell is not None else ()
            return [{"address": addr, "sending": sending} for addr, sending in links]

        def _co_rows(rows: list[Any], snap_a: Any, snap_b: Any) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for r in rows:
                if only_differences and r.kind == "same":
                    continue
                out.append(
                    {
                        "key": r.key,
                        "label": r.label,
                        "kind": r.kind,
                        "a": r.a,
                        "b": r.b,
                        "a_links": _links(snap_a.com_objects.get(r.key)),
                        "b_links": _links(snap_b.com_objects.get(r.key)),
                    }
                )
            return out

        def _compare() -> dict[str, Any]:
            dev_a = _require_device(node_id_a, device_address_a)
            dev_b = _require_device(node_id_b, device_address_b)

            def get_links(db_id: int) -> list[tuple[int, bool]]:
                return [
                    (a.group_address_id, a.is_sending)
                    for a in project.get_links_for_com_object(db_id)
                ]

            def get_address(ga_id: int) -> str:
                ga = project.get_group_address(ga_id)
                return ga.address if ga is not None else ""

            snap_a = snapshot_device(dev_a, get_links, get_address)
            snap_b = snapshot_device(dev_b, get_links, get_address)
            result = diff_devices(snap_a, snap_b)
            return {
                "node_id_a": dev_a.node_id,
                "node_id_b": dev_b.node_id,
                "difference_count": result.difference_count,
                "header": _rows(result.header),
                "parameters": _rows(result.parameters),
                "com_objects": _co_rows(result.com_objects, snap_a, snap_b),
            }

        return ctx.run_locked(_compare, timeout=_SLOW)

    @tool("write")
    def project_create_space(
        space_type: str, name: str, parent_id: int | None = None, usage: str = ""
    ) -> dict[str, Any]:
        """Create a building/location space. ``space_type`` is one of Building, BuildingPart, Floor,
        Room, Corridor, Stairway, DistributionBoard. ``parent_id`` is a parent space ``id`` from
        project_spaces (null = top level). A Room can get its ``usage`` (its "Use", an id from
        project_list_space_usages; "" for none). Undoable project edit. Returns {space_id,
        parent_id, space_type, name}."""
        require_project(ctx)
        if usage and (space_type != "Room" or usage not in SPACE_USAGES):
            raise ToolError(
                "usage is for a Room only and must be an id from project_list_space_usages"
            )

        def _create() -> dict[str, Any]:
            sid = project.create_space(parent_id, space_type, name, usage)
            if sid is None:
                raise ToolError("could not create space (no project open)")
            return {
                "space_id": sid,
                "parent_id": parent_id,
                "space_type": space_type,
                "name": name,
            }

        return ctx.run_locked(_create)

    @tool("read")
    def project_list_space_usages() -> dict[str, Any]:
        """The uses a Room can have (its "Use" drop-down): the space usages of the KNX master data.
        Returns {items, count}; each item has {usage, text}: the id for project_set_space_usage
        and project_create_space, and its text in the editor's language, sorted by text."""
        return _items(
            [{"usage": usage, "text": text} for usage, text in usage_options() if usage]
        )

    @tool("write")
    def project_set_space_usage(space_id: int, usage: str) -> dict[str, Any]:
        """Set the "Use" of a Room (``id`` from project_spaces): an id from
        project_list_space_usages, or "" for none. Undoable project edit. Returns {space_id,
        usage, usage_text}."""
        require_project(ctx)
        if usage and usage not in SPACE_USAGES:
            raise ToolError(f"unknown usage {usage!r}; see project_list_space_usages")

        def _set() -> dict[str, Any]:
            space = project.find_space(space_id)
            if space is None:
                raise ToolError(f"no space with id {space_id}")
            if space.space_type != "Room":
                raise ToolError("only a Room has a use")
            project.set_space_usage(space_id, usage)
            return {
                "space_id": space_id,
                "usage": usage,
                "usage_text": usage_label(usage) if usage else "",
            }

        return ctx.run_locked(_set)

    def _current_line(space_id: int) -> dict[str, Any]:
        if project.find_space(space_id) is None:
            raise ToolError(f"no space with id {space_id}")
        current = project.current_line(space_id)
        if current is None:
            return {
                "space_id": space_id,
                "line_id": None,
                "line": None,
                "set_by_space_id": None,
            }
        line_id, setter = current
        return {
            "space_id": space_id,
            "line_id": line_id,
            "line": dict(project.line_choices()).get(line_id),
            "set_by_space_id": setter.id,
        }

    @tool("read")
    def project_get_current_line(space_id: int) -> dict[str, Any]:
        """The "Current Line" of a space (``id`` from project_spaces): the line a device added to
        the space (project_add_device with space_id) is placed on. It is the space's own, else that
        of the nearest space above it that has one. Returns {space_id, line_id, line,
        set_by_space_id}: ``line`` is the line's address and name ("1.1 Name"),
        ``set_by_space_id`` the space that sets it (``space_id`` itself for its own); all null
        when no space sets one."""
        require_project(ctx)
        return ctx.run_locked(lambda: _current_line(space_id))

    @tool("write")
    def project_set_current_line(
        space_id: int, line_id: int | None = None
    ) -> dict[str, Any]:
        """Set the "Current Line" of a space (``id`` from project_spaces) to the line ``line_id``
        (a line's id from project_topology), or clear the space's own with null, so it takes the
        one of the spaces above it again. Undoable project edit. Returns the same as
        project_get_current_line."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            if project.find_space(space_id) is None:
                raise ToolError(f"no space with id {space_id}")
            if line_id is not None and line_id not in dict(project.line_choices()):
                raise ToolError(f"no line with id {line_id}; see project_topology")
            project.set_space_default_line(space_id, line_id)
            return _current_line(space_id)

        return ctx.run_locked(_set)

    @tool("write")
    def project_rename_space(space_id: int, name: str) -> dict[str, Any]:
        """Rename a space (``id`` from project_spaces). Undoable project edit. Returns
        {space_id, name}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.rename_space(space_id, name))
        return {"space_id": space_id, "name": name}

    @tool("write")
    def project_set_space_type(space_id: int, space_type: str) -> dict[str, Any]:
        """Set a space's type (Building, BuildingPart, Floor, Room, Corridor, Stairway,
        DistributionBoard). ``id`` from project_spaces. Undoable project edit. Returns
        {space_id, space_type}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.set_space_type(space_id, space_type))
        return {"space_id": space_id, "space_type": space_type}

    @tool("write")
    def project_move_space(
        space_id: int, new_parent_id: int | None = None
    ) -> dict[str, Any]:
        """Move a space under a different parent space, or to the top level with new_parent_id=null.
        Both ``id`` values come from project_spaces. Undoable project edit. Returns
        {space_id, new_parent_id}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.move_space(space_id, new_parent_id))
        return {"space_id": space_id, "new_parent_id": new_parent_id}

    @tool("destructive")
    def project_remove_space(space_id: int) -> dict[str, Any]:
        """Remove a space and its sub-spaces (``id`` from project_spaces); devices placed in them
        become unassigned. Undoable project edit. Returns {status, space_id}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.remove_space(space_id))
        return {"status": "removed", "space_id": space_id}

    # --- device mutations -------------------------------------------------

    @tool("write")
    def project_add_device(
        product_ref_id: str,
        name: str | None = None,
        hardware2program_ref_id: str | None = None,
        space_id: int | None = None,
    ) -> dict[str, Any]:
        """Add a device to the project using product_ref_id from catalog_list_products.
        Optional hardware2program_ref_id from the same catalog listing selects its application;
        omitting it selects the newest catalog version. With ``space_id`` (an ``id`` from
        project_spaces) the device is added to that space and placed on its Current Line (see
        project_get_current_line) with the next free address; otherwise, and when the space has
        no Current Line, it goes on the first TP line. Undoable with project_undo; commissioning
        hardware requires separate connection tools.

        Returns {node_id, name, individual_address, description}, or only {node_id} if the created
        device cannot be loaded. Pass node_id to device tools."""
        require_project(ctx)

        def _add() -> dict[str, Any]:
            if space_id is not None and project.find_space(space_id) is None:
                raise ToolError(f"no space with id {space_id}")
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
            try:
                device_id = project.add_device(
                    product_ref_id=product.product_ref_id,
                    hardware2program_ref_id=product.hardware2program_ref_id,
                    name=name or "",
                    app=app,
                    product=DeviceProduct.of(product),
                    space_id=space_id,
                )
            except MediumError as error:
                raise ToolError(str(error)) from error
            if device_id is None:
                raise ToolError("add_device did not return a device id")
            device = project.find_device_by_node_id(device_id)
            return (
                _device_dict(device) if device is not None else {"node_id": device_id}
            )

        return ctx.run_locked(_add, timeout=_SLOW)

    @tool("write")
    def project_rename_device(
        node_id: int | None = None,
        device_address: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename a project device and/or set its description. Identify the device by ``node_id``
        OR ``device_address`` (an individual address like "1.0.30"); pass exactly one. Pass
        ``name``, ``description`` or both; an omitted field is left unchanged. Undoable with
        project_undo. Returns {node_id, name, individual_address, description}."""
        if name is None and description is None:
            raise ToolError("pass name, description or both")

        def _rename() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            nid = device.node_id
            if name is not None:
                project.set_device_name(nid, device.name, name)
            if description is not None:
                project.set_device_description(
                    nid, device.description or "", description
                )
            return _device_dict(_require_device(nid))

        return ctx.run_locked(_rename)

    @tool("write")
    def project_set_individual_address(
        address: str, node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Set a project device's individual address to ``address``, e.g. "1.1.5". Identify the
        device by ``node_id`` OR ``device_address`` (its CURRENT individual address); pass exactly
        one. The target area/line must exist in project_topology and the address must be unused in
        the project; moved into another line, the device goes on its main segment, whose medium
        its hardware must support. Returns {node_id, name, individual_address, description}.
        Undoable with project_undo. This does not check or write the bus; use
        connection_assign_individual_address to assign it to hardware."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            try:
                project.set_device_individual_address(
                    device.node_id, device.individual_address, address
                )
            except MediumError as error:
                raise ToolError(str(error)) from error
            updated = _require_device(device.node_id)
            if updated.individual_address != address:
                raise ToolError(
                    f"could not set individual address {address!r} (invalid or already in use)"
                )
            return _device_dict(updated)

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_additional_address(
        index: int,
        address: str | None,
        node_id: int | None = None,
        device_address: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Set an additional individual address of a project device (a tunnelling address of an
        IP interface). Identify the device by ``node_id`` OR ``device_address`` (its individual
        address like "1.0.30"); pass exactly one. index is 1-based (see
        additional_addresses in project_get_device; entries up to it are created). address is an
        "a.l.d" string in the device's own line, or a device number 1-255; null or "" clears it.
        It must not be used by a device or another additional address of the line. name and
        description, when given, are set too. Each change is one undo step (project_undo).
        Returns {node_id, additional_addresses}. This does not write the bus; a full download of
        the device writes the addresses."""
        from xknxeditor.proj import AdditionalAddressError

        require_project(ctx)

        def _set() -> dict[str, Any]:
            nid = _require_device(node_id, device_address).node_id
            try:
                project.set_additional_address(nid, index, address)
                if name is not None:
                    project.set_additional_address_name(nid, index, name)
                if description is not None:
                    project.set_additional_address_description(nid, index, description)
            except IndexError as exc:
                raise ToolError(f"invalid index {index}; use 1 to 255") from exc
            except AdditionalAddressError as exc:
                raise ToolError(str(exc)) from exc
            return {
                "node_id": nid,
                "additional_addresses": [
                    _additional_address_dict(entry)
                    for entry in project.additional_addresses(nid)
                ],
            }

        return ctx.run_locked(_set)

    @tool("write")
    def project_assign_additional_addresses(
        node_id: int | None = None,
        device_address: str | None = None,
        count: int | None = None,
    ) -> dict[str, Any]:
        """Assign the missing additional individual addresses of a project device (the
        tunnelling addresses of an IP interface). Identify the device by ``node_id`` OR
        ``device_address`` (its individual address like "1.0.30"); pass exactly one. Entries
        are created up to count (default: as many as the device's application has) and every
        entry without an address gets one, the highest free device numbers of the device's line
        counting down from 255. One undo step (project_undo). Fails when the line has too few free
        addresses. Returns {node_id, additional_addresses}, each {index, address, name,
        description}."""
        from xknxeditor.proj import AdditionalAddressError

        require_project(ctx)

        def _assign() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            try:
                entries = project.assign_additional_addresses(device, count)
            except AdditionalAddressError as exc:
                raise ToolError(str(exc)) from exc
            return {
                "node_id": device.node_id,
                "additional_addresses": [
                    _additional_address_dict(entry) for entry in entries
                ],
            }

        return ctx.run_locked(_assign)

    @tool("write")
    def project_set_serial_number(
        serial_number: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Store a device's KNX serial number in the project. Identify the device by ``node_id``
        OR ``device_address`` (an individual address like "1.0.30"); pass exactly one.
        serial_number takes 12 hex digits (e.g. "0083:7AA2039C"); an empty string clears it.
        Returns {node_id, serial_number}. Undoable with project_undo; this records metadata without
        writing hardware. connection_program_device can use it with use_serial_number=true."""
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
            nid = _require_device(node_id, device_address).node_id
            project.set_device_commissioning(
                nid, serial_number=serial_to_project(serial) if serial else ""
            )
            return {"node_id": nid, "serial_number": serial_text(serial)}

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_com_object_flag(
        com_object_ref_id: str,
        flag: Flag,
        value: bool,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Override a project com-object flag: communication/read/write/transmit/update/read_on_init.
        Identify the device by ``node_id`` OR ``device_address`` (an individual address like
        "1.0.30"); pass exactly one. Pass ref_id from project_list_com_objects for that device as
        com_object_ref_id; the object must have linkable=true. Undoable with project_undo; program the device to apply
        the change on the bus.

        Returns {ref_id, db_id, linkable, name, number, dpt, dpt_name, object_size, flags,
        description, priority}, or only {ref_id} if the updated object cannot be found. flags
        contains the six named flags."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
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
            project.set_flag(device, com_object_ref_id, flag, value)
            updated = _require_device(device.node_id).find_com_object(com_object_ref_id)
            return (
                com_object_dict(updated)
                if updated is not None
                else {"ref_id": com_object_ref_id}
            )

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_parameter(
        parameter_ref_id: str,
        value: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Set an application parameter. Identify the device by ``node_id`` OR ``device_address`` (an
        individual address like "1.0.30"); pass exactly one. Pass the parameter's `ref_id` (from
        project_list_parameters, or project_parameter_tree) as the `parameter_ref_id` argument; use a
        `value` permitted by that parameter's `widget`.

        When you configure a device's push-button / display function (e.g. a glass push button, room
        controller), ALWAYS also set its display symbols and key labels/name — do not leave them at
        their defaults. Mirror an equivalent function on another button or device (read it with
        project_parameter_tree and copy the symbol/label parameter values), or choose fitting symbols
        yourself. Setting a function type usually reveals extra symbol/label parameters; re-read the
        block with project_parameter_tree and set them too, so the button shows correctly on the device.

        Returns {ref_id, requested, value, active, changed?, lost_links?, shown_parameters?,
        hidden_parameters?, shown_com_objects?, hidden_com_objects?}: ``value`` is read back
        AFTER the edit (may be a normalized form of ``requested``, or null with ``active=false`` if
        the edit deactivated the parameter), ``changed`` lists other parameters a calculation changed
        as a side effect, and ``lost_links`` lists group-address links this change dropped by
        deactivating their com-objects ({com_object_ref_id, group_address_id, address}).
        ``shown_parameters`` ({ref_id, label, value, value_label?}) and ``shown_com_objects``
        ({ref_id, number, name, linkable}) are what the edit made visible, ``hidden_parameters`` and
        ``hidden_com_objects`` the ref_ids it hid — after a function/mode change, set and link those
        instead of re-reading the whole device. A rejected value raises with the reason and does
        NOT take effect on a later edit."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            if parameters_in_plugin(device):
                raise ToolError(PLUGIN_PARAMETERS_ERROR.format(node_id=device.node_id))
            return apply_parameter(project, device, parameter_ref_id, value)

        return ctx.run_locked(_set)

    # --- topology mutations -----------------------------------------------

    @tool("write")
    def project_create_area(area_number: int, name: str = "") -> dict[str, Any]:
        """Create a project topology area. Undoable with project_undo.
        Returns {area_id, area_number, name}; use area_id for project_create_line and area edits."""
        require_project(ctx)
        area_id = ctx.run_locked(lambda: project.create_area(area_number, name))
        return {"area_id": area_id, "area_number": area_number, "name": name}

    @tool("write")
    def project_rename_area(area_id: int, name: str) -> dict[str, Any]:
        """Rename a project topology area, using an area's id from project_topology as area_id.
        Undoable with project_undo. Returns {area_id, name}."""
        ctx.run_locked(lambda: project.rename_area(area_id, "", name))
        return {"area_id": area_id, "name": name}

    @tool("destructive")
    def project_remove_area(area_id: int) -> dict[str, str]:
        """Remove a project topology area and its lines/devices. Use an area's id from
        project_topology as area_id. Undoable with project_undo; hardware is unchanged.
        Returns {status: "removed", area_id}, with area_id as a string."""
        ctx.run_locked(lambda: project.remove_area(area_id))
        return {"status": "removed", "area_id": str(area_id)}

    @tool("write")
    def project_create_line(
        area_id: int, line_number: int, name: str = ""
    ) -> dict[str, Any]:
        """Create a project topology line. Use an area's id from project_topology as area_id.
        Undoable with project_undo. Returns {line_id, area_id, line_number}; pass line_id to line edits."""
        require_project(ctx)
        line_id = ctx.run_locked(
            lambda: project.create_line(area_id, line_number, name)
        )
        return {"line_id": line_id, "area_id": area_id, "line_number": line_number}

    @tool("write")
    def project_rename_line(line_id: int, name: str) -> dict[str, Any]:
        """Rename a project topology line, using a line's id from project_topology as line_id.
        Undoable with project_undo. Returns {line_id, name}."""
        ctx.run_locked(lambda: project.rename_line(line_id, "", name))
        return {"line_id": line_id, "name": name}

    @tool("destructive")
    def project_remove_line(line_id: int) -> dict[str, str]:
        """Remove a project topology line and its devices. Use a line's id from project_topology as
        line_id. Undoable with project_undo; hardware is unchanged. Returns {status: "removed", line_id},
        with line_id as a string."""
        ctx.run_locked(lambda: project.remove_line(line_id))
        return {"status": "removed", "line_id": str(line_id)}

    def _segment_dict(segment_id: int) -> dict[str, Any]:
        segment = project.find_segment(segment_id)
        if segment is None:
            raise ToolError(f"no segment with id {segment_id}")
        return _segment_entry(segment)

    @tool("write")
    def project_add_segment(
        line_id: int, name: str | None = None, medium: Medium | None = None
    ) -> dict[str, Any]:
        """Add a sub-segment to a line (``id`` from project_topology). A line's devices are on its
        main segment (the line itself) unless they are on a sub-segment; a TP line can have TP or
        RF sub-segments, an IP line TP, RF or IoT ones, an RF, PL or IoT line none, and a line at
        most 128 segments. ``medium`` defaults to the line's own when a sub-segment can have it,
        else TP; ``name`` to the editor's default. Undoable with project_undo. Returns the
        segment as project_topology lists it: {id, number, name, medium, domain_address?}."""
        require_project(ctx)

        def _add() -> dict[str, Any]:
            if project.find_line(line_id) is None:
                raise ToolError(f"no line with id {line_id}")
            try:
                segment_id = project.add_segment(line_id, name)
                if segment_id is None:
                    raise ToolError("could not add a segment (no project open)")
                if medium is not None:
                    project.set_segment_medium(segment_id, _MEDIA[medium])
            except MediumError as error:
                raise ToolError(str(error)) from error
            return _segment_dict(segment_id)

        return ctx.run_locked(_add)

    @tool("write")
    def project_rename_segment(segment_id: int, name: str) -> dict[str, Any]:
        """Rename a sub-segment (``id`` from project_topology). Undoable with project_undo.
        Returns the segment as project_topology lists it."""
        require_project(ctx)

        def _rename() -> dict[str, Any]:
            _segment_dict(segment_id)
            project.rename_segment(segment_id, name)
            return _segment_dict(segment_id)

        return ctx.run_locked(_rename)

    @tool("destructive")
    def project_remove_segment(segment_id: int) -> dict[str, Any]:
        """Remove a sub-segment and its devices (``id`` from project_topology); a line's main
        segment cannot be removed. Undoable with project_undo; hardware is unchanged. Returns
        {status: "removed", segment_id}."""
        require_project(ctx)

        def _remove() -> dict[str, Any]:
            if project.segment_medium(segment_id) is None:
                raise ToolError(f"no segment with id {segment_id}")
            try:
                project.remove_segment(segment_id)
            except MediumError as error:
                raise ToolError(str(error)) from error
            return {"status": "removed", "segment_id": segment_id}

        return ctx.run_locked(_remove)

    @tool("write")
    def project_set_medium(
        medium: Medium,
        line_id: int | None = None,
        segment_id: int | None = None,
    ) -> dict[str, Any]:
        """Set the medium of a line (its main segment's) or of a sub-segment; pass exactly one of
        ``line_id`` and ``segment_id`` (ids from project_topology). The medium can only change
        while the segment holds no device, and only to one its line allows (see
        project_add_segment); an RF or PL segment gets a domain address of its own. Undoable with
        project_undo. Returns {kind, element_id, medium, domain_address?}."""
        require_project(ctx)
        if (line_id is None) == (segment_id is None):
            raise ToolError("pass exactly one of line_id and segment_id")

        def _set() -> dict[str, Any]:
            kind = "line" if line_id is not None else "segment"
            element_id = line_id if line_id is not None else segment_id
            assert element_id is not None
            if project.element_details(kind, element_id) is None:
                raise ToolError(f"no {kind} with id {element_id}")
            try:
                if kind == "line":
                    project.set_line_medium(element_id, _MEDIA[medium])
                else:
                    project.set_segment_medium(element_id, _MEDIA[medium])
            except MediumError as error:
                raise ToolError(str(error)) from error
            details = project.element_details(kind, element_id)
            assert details is not None and details.medium is not None
            result: dict[str, Any] = {
                "kind": kind,
                "element_id": element_id,
                "medium": medium_name(details.medium),
            }
            if details.domain_address:
                result["domain_address"] = details.domain_address
            return result

        return ctx.run_locked(_set)

    @tool("write")
    def project_move_device_to_segment(
        segment_id: int,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Move a device to a segment (``id`` from project_topology; a line's main segment is its
        ``main_segment_id``). Identify the device by ``node_id`` OR ``device_address`` (an
        individual address like "1.0.30"). Within its line it keeps its address, into another
        line it gets that line's lowest free one. Its hardware must support the segment's
        medium. Undoable with project_undo. Returns {node_id, name, individual_address,
        description}."""
        require_project(ctx)

        def _move() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            try:
                project.move_device_to_segment(device.node_id, segment_id)
            except MediumError as error:
                raise ToolError(str(error)) from error
            except ValueError as error:
                raise ToolError(str(error)) from error
            return _device_dict(_require_device(device.node_id))

        return ctx.run_locked(_move)

    # --- group address mutations ------------------------------------------

    @tool("write")
    def project_create_group_address(
        address: str | None = None, name: str = ""
    ) -> dict[str, Any]:
        """Create a project group address. address follows project_info's group_address_style
        (e.g. "1/2/3"); omit it for the next free address. Returns {group_address_id, address, name},
        or only {group_address_id} if the created row cannot be read. Pass group_address_id to group
        address and linking tools. Undoable with project_undo; hardware changes require linking
        com-objects and programming their devices."""
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

    @tool("write")
    def project_rename_group_address(
        name: str,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Rename a project group address. Identify it by ``group_address_id`` (id from
        project_list_group_addresses) OR ``group_address`` (a printed address like "10/1/2" in the
        project's style); pass exactly one. Undoable with project_undo. Returns {id, address,
        name}."""

        def _rename() -> dict[str, Any]:
            ga_id = _require_group_address_id(group_address_id, group_address)
            project.rename_group_address(ga_id, name)
            g = project.get_group_address(ga_id)
            if g is None:
                raise ToolError(f"no group address with id {ga_id}")
            return {"id": g.id, "address": g.address, "name": g.name}

        return ctx.run_locked(_rename)

    def _group_ranges() -> list[dict[str, Any]]:
        """Flatten the group-range tree (main/middle groups) with parent ids."""
        out: list[dict[str, Any]] = []

        def _walk(nodes: list[Any], parent_id: int | None, depth: int) -> None:
            for r in nodes:
                out.append(
                    {
                        "id": r.id,
                        "address": r.address or "",
                        "name": r.name or "",
                        "description": r.description or "",
                        "parent_id": parent_id,
                        "depth": depth,
                        "security": r.security_mode or "Auto",
                        "security_forced_by": _forced_dict(r.forced_by),
                    }
                )
                _walk(r.children, r.id, depth + 1)

        _walk(project.get_group_range_tree(), None, 0)
        return out

    @tool("read")
    def project_list_group_ranges(name_contains: str | None = None) -> dict[str, Any]:
        """List the group-address folders (main and middle groups), optionally filtering names by
        a case-insensitive substring. Returns {items, count}; each item has {id, address, name,
        description, parent_id, depth, security, security_forced_by} (depth 0 = main group; name
        and description are "" when unset, parent_id is null for main groups; security is its
        own mode, Auto, On or Off, and security_forced_by the group above it that overrides it,
        see project_set_group_range_security). Pass ``id`` as group_range_id to
        project_rename_group_range."""

        def _read() -> list[dict[str, Any]]:
            needle = (name_contains or "").lower()
            return [
                r for r in _group_ranges() if not needle or needle in r["name"].lower()
            ]

        return _items(ctx.run_locked(_read))

    @tool("write")
    def project_rename_group_range(group_range_id: int, name: str) -> dict[str, Any]:
        """Rename a group-address folder (main or middle group). Pass id from
        project_list_group_ranges as group_range_id. Undoable with project_undo. Returns
        {id, address, name}."""
        require_project(ctx)

        def _rename() -> dict[str, Any]:
            if not any(r["id"] == group_range_id for r in _group_ranges()):
                raise ToolError(f"no group range with id {group_range_id}")
            project.rename_group_range(group_range_id, name.strip())
            found = next(r for r in _group_ranges() if r["id"] == group_range_id)
            return {
                "id": found["id"],
                "address": found["address"],
                "name": found["name"],
            }

        return ctx.run_locked(_rename)

    def _function_ids() -> set[int]:
        ids: set[int] = set()

        def _walk(nodes: list[Any]) -> None:
            for s in nodes:
                ids.update(f.id for f in s.functions)
                _walk(s.children)

        _walk(project.get_space_tree())
        return ids

    @tool("write")
    def project_rename_function(function_id: int, name: str) -> dict[str, Any]:
        """Rename a building function (a function placed in a room). Pass a function ``id`` from
        project_spaces as function_id. Undoable with project_undo. Returns {function_id, name}."""
        require_project(ctx)

        def _rename() -> dict[str, Any]:
            if function_id not in _function_ids():
                raise ToolError(f"no function with id {function_id}")
            project.rename_function(function_id, name.strip())
            return {"function_id": function_id, "name": name.strip()}

        return ctx.run_locked(_rename)

    @tool("write")
    def project_set_group_address_dpt(
        dpt: str | None,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a project group address's datapoint type, or clear it with dpt=null. Identify it by
        ``group_address_id`` (id from project_list_group_addresses) OR ``group_address`` (a printed
        address like "10/1/2" in the project's style); pass exactly one. dpt accepts dotted codes
        such as "1.001" or DPST tokens such as "DPST-1-1" and is stored as a DPST token.
        Returns {id, address, datapoint_type}. Undoable with project_undo; this updates project
        metadata used for value encoding/decoding without programming hardware."""

        def _set() -> dict[str, Any]:
            ga_id = _require_group_address_id(group_address_id, group_address)
            try:
                project.set_group_address_dpt(ga_id, dpt)
            except ValueError as e:
                raise ToolError(str(e)) from e
            g = project.get_group_address(ga_id)
            if g is None:
                raise ToolError(f"no group address with id {ga_id}")
            return {
                "id": g.id,
                "address": g.address,
                "datapoint_type": g.datapoint_type,
            }

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_group_address_security(
        mode: str,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a group address' KNX Data Secure mode (its "Security"): "On" (Forced on: secured,
        generates a key; every linked device must be commissioned securely), "Off" (Forced off:
        not secured), or "Auto" (Automatic: secured while every linked device is commissioned
        securely). A main or middle group forcing On or Off overrides it (see
        project_set_group_range_security); the mode set here then counts once no group does. A
        password-protected project is needed to store a generated key. Identify the group address
        by ``group_address_id`` (id from project_list_group_addresses) OR ``group_address`` (a
        printed address like "10/1/2" in the project's style); pass exactly one. Returns {id,
        address, secured}."""
        from xknxeditor.proj import GroupAddressSecurityError, ProjectPasswordRequired

        if mode not in ("Auto", "On", "Off"):
            raise ToolError('mode must be "Auto", "On" or "Off"')

        def _set() -> dict[str, Any]:
            ga_id = _require_group_address_id(group_address_id, group_address)
            try:
                project.set_group_address_security(ga_id, mode)
            except ProjectPasswordRequired as e:
                raise ToolError("set a project password first") from e
            except GroupAddressSecurityError as e:
                raise ToolError(str(e)) from e
            g = project.get_group_address(ga_id)
            if g is None:
                raise ToolError(f"no group address with id {ga_id}")
            return {"id": g.id, "address": g.address, "secured": g.data_secure}

        return ctx.run_locked(_set)

    @tool("write")
    def project_set_group_range_security(
        group_range_id: int, mode: Literal["Auto", "On", "Off"]
    ) -> dict[str, Any]:
        """Set the "Security" of a main or middle group (id from project_list_group_ranges):
        "On" (Forced on) secures and "Off" (Forced off) unsecures every group address and group
        below it, whatever their own mode; the nearest forcing group above an address decides.
        "Auto" (Automatic) gives them back their own modes, which stay stored meanwhile. "On"
        needs every device linked below it commissioned securely, and a password-protected
        project for the keys it generates. Undoable with project_undo. Returns the group as
        project_list_group_ranges lists it, with ``secured``: the addresses below it secured
        now."""
        from xknxeditor.proj import GroupAddressSecurityError, ProjectPasswordRequired

        require_project(ctx)

        def _set() -> dict[str, Any]:
            if not any(r["id"] == group_range_id for r in _group_ranges()):
                raise ToolError(f"no group range with id {group_range_id}")
            try:
                project.set_group_range_security(group_range_id, mode)
            except ProjectPasswordRequired as e:
                raise ToolError("set a project password first") from e
            except GroupAddressSecurityError as e:
                raise ToolError(str(e)) from e
            found = next(r for r in _group_ranges() if r["id"] == group_range_id)
            node = _range_node(group_range_id)
            secured = sorted(ga.text for ga in _addresses_below(node) if ga.data_secure)
            return {**found, "secured": secured}

        return ctx.run_locked(_set)

    def _range_node(group_range_id: int) -> Any:
        def _find(nodes: list[Any]) -> Any:
            for r in nodes:
                if r.id == group_range_id:
                    return r
                found = _find(r.children)
                if found is not None:
                    return found
            return None

        return _find(project.get_group_range_tree())

    def _addresses_below(node: Any) -> list[Any]:
        if node is None:
            return []
        found = list(node.group_addresses)
        for child in node.children:
            found.extend(_addresses_below(child))
        return found

    @tool("write")
    def project_set_group_address_central(
        central: bool,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Mark a group address as central or not (its "Central" checkbox): a central group
        address is reused, not copied, when devices or groups are copied within the project. It
        changes nothing on the bus. Identify the group address by ``group_address_id`` OR
        ``group_address`` (a printed address like "10/1/2"); pass exactly one. Undoable with
        project_undo. Returns the fields project_list_group_addresses lists."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            ga_id = _require_group_address_id(group_address_id, group_address)
            project.set_group_address_central(ga_id, central)
            g = project.get_group_address(ga_id)
            if g is None:
                raise ToolError(f"no group address with id {ga_id}")
            return _ga_dict(g)

        return ctx.run_locked(_set)

    @tool("destructive")
    def project_remove_group_address(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, str]:
        """Remove a project group address and its links. Identify it by ``group_address_id`` (id
        from project_list_group_addresses) OR ``group_address`` (a printed address like "10/1/2" in
        the project's style); pass exactly one. Undoable with project_undo; program affected devices
        to remove links on the bus. Returns {status: "removed", group_address_id}, with
        group_address_id as a string."""

        def _remove() -> int:
            ga_id = _require_group_address_id(group_address_id, group_address)
            project.remove_group_address(ga_id)
            return ga_id

        ga_id = ctx.run_locked(_remove)
        return {"status": "removed", "group_address_id": str(ga_id)}

    @tool("write")
    def project_link_com_object(
        com_object_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
        group_address_id: int | None = None,
        group_address: str | None = None,
        is_sending: bool = False,
    ) -> dict[str, Any]:
        """Create an undoable project link between a device com-object and a group address. The
        device is identified by ``node_id`` (from project_list_devices) OR ``device_address`` (an
        individual address like "1.0.30"); the group address by ``group_address_id`` (from
        project_list_group_addresses) OR ``group_address`` (a printed address like "10/1/2" in the
        project's style). Pass exactly one of each pair. ``com_object_ref_id`` is the object's
        ``ref_id`` from project_list_com_objects (linkable=true).

        ``is_sending`` (default False) marks which group address an object with SEVERAL links sends to
        (it orders the associations, sending GA first); it does NOT change the object's flags, and for
        a single link it has no byte effect — the send/receive direction comes from the object's own
        flags. For a transmitting object (push-button/sensor output) linked to several GAs, mark its
        intended sending GA; keep one sending GA per object. Returns {link_id, node_id,
        com_object_ref_id, com_object_db_id, group_address_id, is_sending, already_linked} plus, when
        relevant, ``dpt_mismatch`` ({com_object, group_address} — the GA keeps its own DPT, so the
        DPT metadata differs; verify compatibility) and ``other_sending_group_address_ids`` (the
        object already sends to these, so it now has several sending GAs), and ``hidden: true`` when
        the device's parameters currently hide the object (the link is kept, but the application
        does not use the object until a parameter shows it — check the activating parameter).
        Linking the same pair again is idempotent (``already_linked: true``, no duplicate). Pass
        link_id as project_unlink_com_object's assignment_id. Program the device to apply the link on
        the bus."""
        require_project(ctx)

        def _link() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            ga_id = _require_group_address_id(group_address_id, group_address)
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {device.node_id}; "
                    "call project_list_com_objects for valid ref_ids"
                )
            if co.db_id is None:
                raise ToolError(
                    f"com-object {com_object_ref_id!r} is not instantiated in the project and "
                    "cannot be linked (only com-objects with linkable=true can be linked)"
                )
            result = perform_link(
                project,
                device.node_id,
                com_object_ref_id,
                co,
                ga_id,
                is_sending,
                hidden=com_object_hidden(device, com_object_ref_id),
            )
            if result is None:
                raise ToolError(
                    f"could not link com-object to group address {ga_id} "
                    "(check the group address id exists; a group address with Security On "
                    "only takes devices commissioned securely)"
                )
            return result

        return ctx.run_locked(_link)

    @tool("destructive")
    def project_unlink_com_object(
        assignment_id: int | None = None,
        com_object_ref_id: str | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, str]:
        """Remove an undoable project link, either by ``assignment_id`` — an ``id`` from
        project_get_ga_assignments, an ``assignment_id`` from project_get_com_object_links, or a
        ``link_id`` from project_link_com_object — OR by the linked pair: ``com_object_ref_id``
        (ref_id from project_list_com_objects) of the device ``node_id`` OR ``device_address``, and
        the group address ``group_address_id`` OR ``group_address`` (a printed address like
        "10/1/2"), one of each pair. Program the device to apply the removal on the bus.
        Returns {status, assignment_id}."""
        by_pair = (
            com_object_ref_id,
            node_id,
            device_address,
            group_address_id,
            group_address,
        )
        if (assignment_id is None) == all(v is None for v in by_pair):
            raise ToolError(
                "pass assignment_id, or com_object_ref_id with its device and group address"
            )

        def _unlink() -> int:
            link_id = assignment_id
            if link_id is None:
                if com_object_ref_id is None:
                    raise ToolError(
                        "com_object_ref_id is needed to unlink by the linked pair"
                    )
                device = _require_device(node_id, device_address)
                ga_id = _require_group_address_id(group_address_id, group_address)
                link_id = link_id_between(project, device, com_object_ref_id, ga_id)
            project.unlink_com_object_from_ga(link_id)
            return link_id

        link_id = ctx.run_locked(_unlink)
        return {"status": "unlinked", "assignment_id": str(link_id)}

    # --- undo / redo / history --------------------------------------------

    @tool("write")
    def project_undo() -> dict[str, bool]:
        """Undo the last undoable project edit. Returns {undone: bool}. Bus programming, address
        assignment, and file replacements are outside project history and are not reverted."""
        return {"undone": ctx.run_locked(project.undo)}

    @tool("write")
    def project_redo() -> dict[str, bool]:
        """Redo the last undone project edit. Returns {redone: bool}."""
        return {"redone": ctx.run_locked(project.redo)}

    @tool("read")
    def project_history() -> dict[str, Any]:
        """Project edit history. Returns {items, count}; each item has {id, label, reverted}. Pass an
        item's ``id`` as project_jump_to's ``event_id``."""
        return _items(
            ctx.run_locked(
                lambda: [
                    {"id": e.id, "label": e.display_text, "reverted": e.reverted}
                    for e in project.history()
                ]
            )
        )

    @tool("read")
    def project_log(
        contains: str | None = None, offset: int = 0, limit: int = 200
    ) -> dict[str, Any]:
        """The Project Log of a project imported from a .knxproj: who changed or downloaded what
        and when (the editor writes no entries; a project created here has none). Optionally only
        the entries whose date, user or comment contains ``contains`` (case-insensitive), from the
        ``offset``-th one on, at most ``limit``. Returns {items, count, total}; each item has
        {date, user_name, comment, decrypted}: comment is the text when it could be decrypted
        (decrypted=true), else as stored. total counts the matching entries."""
        require_project(ctx)
        if offset < 0 or limit < 1:
            raise ToolError("offset must be 0 or more and limit 1 or more")

        def _read() -> dict[str, Any]:
            needle = (contains or "").lower()
            rows = [
                {
                    "date": t.date,
                    "user_name": t.user_name,
                    "comment": t.comment_plain or t.comment,
                    "decrypted": t.comment_plain is not None,
                }
                for t in project.get_project_traces()
            ]
            if needle:
                rows = [
                    r
                    for r in rows
                    if any(
                        needle in r[key].lower()
                        for key in ("date", "user_name", "comment")
                    )
                ]
            page = rows[offset : offset + limit]
            return {"items": page, "count": len(page), "total": len(rows)}

        return ctx.run_locked(_read)

    @tool("write")
    def project_jump_to(event_id: int) -> dict[str, str]:
        """Move the project undo/redo cursor to a history entry, passing id from project_history as
        event_id. This reverts or reapplies project edits; bus changes and file replacements are
        outside this history. Returns {status: "jumped", event_id}, with event_id as a string."""
        ctx.run_locked(lambda: project.jump_to(event_id))
        return {"status": "jumped", "event_id": str(event_id)}
