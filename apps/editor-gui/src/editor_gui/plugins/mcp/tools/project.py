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

import re
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
from editor_gui.plugins.project.service import DeviceProduct
from editor_gui.settings import config_dir

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


def _ga_dict(g: Any) -> dict[str, Any]:
    return {
        "id": g.id,
        "address": g.address,
        "name": g.name,
        "datapoint_type": g.datapoint_type,
        "description": g.description,
        "data_secure": g.data_secure,
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
    Empty optional fields (number, usage_text, description) and empty device/function/children
    lists are omitted to keep the tree compact."""
    return _prune(
        {
            "id": s.id,
            "name": s.name,
            "space_type": s.space_type,
            "number": s.number,
            "usage_text": s.usage_text,
            "description": s.description,
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


def _matches_channel(channel_query: str, heading: str) -> bool:
    import re

    cq = channel_query.strip().casefold()
    h = heading.strip().casefold()
    if not cq or not h:
        return False
    if cq == h:
        return True
    tokens = [t.casefold() for t in re.split(r"[\s\-_:/]+", h) if t]
    if cq in tokens:
        return True
    if len(cq) <= 2:
        for t in tokens:
            if t.endswith(cq) and len(t) > len(cq):
                prefix = t[: -len(cq)]
                if prefix in (
                    "kanal",
                    "channel",
                    "ausgang",
                    "eingang",
                    "output",
                    "input",
                    "ch",
                ):
                    return True
    return cq in h


def _matches_path(path_query: str, ancestry: tuple[str, ...]) -> bool:
    import re

    if not path_query or not ancestry:
        return False
    segments = [
        s.strip().casefold()
        for s in re.split(r"\s*[/\\>:]\s*", path_query)
        if s.strip()
    ]
    if not segments:
        return False
    anc_headings = [h.strip().casefold() for h in ancestry]

    # Subsequence matching: all query segments must match headings in order
    anc_idx = 0
    for seg in segments:
        matched = False
        while anc_idx < len(anc_headings):
            h = anc_headings[anc_idx]
            anc_idx += 1
            if seg == h or seg in h or _matches_channel(seg, h):
                matched = True
                break
        if not matched:
            return False
    return True


def _collect_ui_parameters_with_ancestry(
    nodes: Any,
    current_ancestry: tuple[str, ...] = (),
) -> list[tuple[Any, tuple[str, ...]]]:
    from xknxeditor.prod.parser_v2.ui import UiParameter, UiParameterBlock, UiTab

    result: list[tuple[Any, tuple[str, ...]]] = []
    for node in nodes:
        if isinstance(node, UiParameter):
            result.append((node, current_ancestry))
        elif isinstance(node, (UiTab, UiParameterBlock)):
            heading = getattr(node, "text", None) or getattr(node, "name", None) or ""
            next_ancestry = (
                (*current_ancestry, heading) if heading else current_ancestry
            )
            result.extend(
                _collect_ui_parameters_with_ancestry(node.children, next_ancestry)
            )
    return result


def _parameter_dict(
    p: Any,
    include_choices: bool = True,
    ancestry: tuple[str, ...] = (),
    compact: bool = False,
) -> dict[str, Any]:
    if compact:
        data: dict[str, Any] = {
            "ref_id": p.ref_id,
            "label": p.label,
            "value": p.value,
        }
        if (label := _enum_value_label(p.widget, p.value)) is not None:
            data["value_label"] = label
        if ancestry:
            data["channel"] = ancestry[0]
            data["path"] = " / ".join(ancestry)
        return data

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
    if ancestry:
        data["channel"] = ancestry[0]
        data["path"] = " / ".join(ancestry)
    return data


def resolve_enum_value(widget: Any, value_label: str, param_label: str) -> str:
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


_resolve_enum_value = resolve_enum_value


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
    label: str | None = None,
    value_label: str | None = None,
    *,
    channel: str | None = None,
    path: str | None = None,
    node_id: int | None = None,
    tree_token: str | None = None,
) -> dict[str, Any]:
    """:func:`resolve_parameter_ref` within an optional ``subtree_id``, ``channel`` or ``path`` scope of ``roots``.
    Raises ValueError for a stale scope token, an unknown scope, or an ambiguous/unknown label. Shared by
    project_resolve_parameter and the batch tool's apply-time resolution."""
    import re

    effective_label = (label or "").strip()
    effective_path = (path or "").strip() if path else None

    if not effective_label and effective_path:
        # If no label was given, split the path: the last segment is the label, preceding segments are the ancestry scope.
        segments = [
            s.strip() for s in re.split(r"\s*[/\\>:]\s*", effective_path) if s.strip()
        ]
        if len(segments) >= 2:
            effective_label = segments[-1]
            effective_path = " / ".join(segments[:-1])
        elif len(segments) == 1:
            effective_label = segments[0]
            effective_path = None

    if not effective_label:
        raise ValueError("label or path is required to resolve parameter")

    search_roots = roots
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
        search_roots = [found]

    param_pairs = _collect_ui_parameters_with_ancestry(search_roots)
    if channel:
        param_pairs = [
            (p, anc)
            for p, anc in param_pairs
            if any(_matches_channel(channel, h) for h in anc)
        ]
        if not param_pairs:
            raise ValueError(f"no active parameters found in channel {channel!r}")

    if effective_path:
        param_pairs = [
            (p, anc) for p, anc in param_pairs if _matches_path(effective_path, anc)
        ]
        if not param_pairs:
            raise ValueError(
                f"no active parameters found matching path {effective_path!r}"
            )

    want = effective_label.casefold()
    matches = [
        (p, anc) for p, anc in param_pairs if (p.label or "").strip().casefold() == want
    ]
    if not matches:
        scope_str = (
            f"in path {effective_path!r}"
            if effective_path
            else (f"in channel {channel!r}" if channel else "in this scope")
        )
        raise ValueError(
            f"no active parameter labelled {effective_label!r} {scope_str}"
        )
    if len(matches) > 1:
        cands = ", ".join(sorted(p.ref_id for p, _ in matches))
        scope_hint = "narrow the scope with path, channel, or subtree_id"
        raise ValueError(
            f"parameter label {effective_label!r} is ambiguous ({len(matches)} matches: {cands}); "
            f"{scope_hint}"
        )
    p, anc = matches[0]
    out: dict[str, Any] = {
        "ref_id": p.ref_id,
        "label": p.label,
        "value": p.value,
        "access": getattr(p.access, "name", str(p.access)),
    }
    if anc:
        out["channel"] = anc[0]
        out["path"] = " / ".join(anc)
    if value_label is not None:
        out["resolved_value"] = _resolve_enum_value(p.widget, value_label, p.label)
    return out


def _satisfying_value_for_condition(cond: str, choices: list[str] | None = None) -> str:
    cleaned = cond.strip()
    # Normalize relational operators with whitespace (e.g. "!= 0" -> "!=0", ">= 2" -> ">=2")
    cleaned = re.sub(r"^([><!=]=?)\s+", r"\1", cleaned)

    if cleaned.startswith("=="):
        return cleaned[2:].strip()
    if cleaned.startswith("="):
        return cleaned[1:].strip()

    # Space-separated alternative condition tokens (e.g. "1 2 3" or "!=0 !=1")
    parts = cleaned.split()
    if len(parts) > 1:
        return _satisfying_value_for_condition(parts[0], choices)

    if choices:
        from xknxeditor.prod.parser_v2.nodes.choose import satisfies

        for c in choices:
            if satisfies(cleaned, c):
                return c

    if cleaned.startswith("!="):
        try:
            val = int(cleaned[2:])
            return "1" if val == 0 else "0"
        except ValueError:
            pass
    if cleaned.startswith(">="):
        try:
            int(cleaned[2:])
            return cleaned[2:]
        except ValueError:
            pass
    if cleaned.startswith("<="):
        try:
            int(cleaned[2:])
            return cleaned[2:]
        except ValueError:
            pass
    if cleaned.startswith(">"):
        try:
            return str(int(cleaned[1:]) + 1)
        except ValueError:
            pass
    if cleaned.startswith("<"):
        try:
            return str(max(0, int(cleaned[1:]) - 1))
        except ValueError:
            pass
    return cleaned


def _walk_static_dynamic_tree(
    node: Any,
    gates: tuple[tuple[str, str], ...] = (),
    ancestry: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    from xknxeditor.prod.parser_v2.nodes.channel import ChannelNode
    from xknxeditor.prod.parser_v2.nodes.choose import ChooseWhenNode
    from xknxeditor.prod.parser_v2.nodes.com_object_parameter_block import (
        ComObjectParameterBlockNode,
    )
    from xknxeditor.prod.parser_v2.nodes.parameter_ref_ref import ParameterRefRefNode

    results: list[dict[str, Any]] = []

    def _walk(curr: Any, g: tuple[tuple[str, str], ...], anc: tuple[str, ...]) -> None:
        if curr is None:
            return
        if isinstance(curr, ParameterRefRefNode):
            pr = getattr(curr, "_param_ref", None)
            p = getattr(curr, "_param", None)
            elem = getattr(curr, "_elem", None)
            ref_id = getattr(elem, "ref_id", None) or getattr(pr, "id", "")
            lbl = (
                getattr(pr, "text", None)
                or getattr(p, "text", None)
                or getattr(elem, "name", None)
                or ""
            )
            results.append(
                {
                    "ref_id": ref_id,
                    "label": lbl,
                    "ancestry": anc,
                    "path": " / ".join(anc),
                    "gates": g,
                    "node": curr,
                }
            )
        elif isinstance(curr, ChooseWhenNode):
            cond_map = getattr(curr, "_condition_to_nodes", {})
            pref_id = getattr(curr, "_param_ref_id", "")
            for cond, ch_list in cond_map.items():
                next_g = (*g, (pref_id, cond))
                for c in ch_list:
                    _walk(c, next_g, anc)
            defaults = getattr(curr, "_default_nodes", None)
            if defaults:
                for c in defaults:
                    _walk(c, g, anc)
        elif isinstance(curr, ChannelNode):
            heading = getattr(curr, "_text", None) or getattr(curr, "_name", None) or ""
            next_anc = (*anc, heading) if heading else anc
            for c in getattr(curr, "_children", []):
                _walk(c, g, next_anc)
        elif isinstance(curr, ComObjectParameterBlockNode):
            elem = getattr(curr, "_elem", None)
            heading = (
                getattr(curr, "_heading_text", None)
                or getattr(elem, "text", None)
                or getattr(elem, "name", None)
                or ""
            )
            next_anc = (*anc, heading) if heading else anc
            for c in getattr(curr, "_children", []):
                _walk(c, g, next_anc)
        elif hasattr(curr, "_children"):
            elem = getattr(curr, "_elem", None)
            heading = (
                getattr(curr, "text", None)
                or getattr(curr, "_text", None)
                or getattr(curr, "_heading_text", None)
                or getattr(curr, "_name", None)
                or getattr(curr, "name", None)
                or (getattr(elem, "text", None) if elem is not None else None)
                or (getattr(elem, "name", None) if elem is not None else None)
                or ""
            )
            next_anc = (*anc, str(heading)) if heading else anc
            for c in getattr(curr, "_children", []):
                _walk(c, g, next_anc)
        elif hasattr(curr, "_subtree"):
            elem = getattr(curr, "_elem", None)
            heading = (
                getattr(curr, "text", None)
                or getattr(curr, "_text", None)
                or getattr(curr, "_heading_text", None)
                or getattr(curr, "_name", None)
                or getattr(curr, "name", None)
                or (getattr(elem, "text", None) if elem is not None else None)
                or (getattr(elem, "name", None) if elem is not None else None)
                or ""
            )
            next_anc = (*anc, str(heading)) if heading else anc
            _walk(curr._subtree, g, next_anc)

    _walk(node, gates, ancestry)
    return results


def find_activation_path(
    device: Any,
    target_ref_id: str | None = None,
    label: str | None = None,
    path: str | None = None,
    value: str | None = None,
    value_label: str | None = None,
) -> list[tuple[str, str, str]]:
    """Determine the sequence of parameter edits (parent gates + target) to activate and set a parameter."""
    import re

    from xknxeditor.prod.parser_v2.nodes.choose import satisfies

    active_map = {p.ref_id: p for p in _collect_ui_parameters(device.get_ui())}

    # 1. Check if already active
    if target_ref_id and target_ref_id in active_map:
        target_p = active_map[target_ref_id]
        target_val = value
        if target_val is None and value_label is not None:
            target_val = _resolve_enum_value(
                target_p.widget, value_label, target_p.label
            )
        if target_val is not None:
            return [(target_ref_id, str(target_val), "target parameter")]
        return []

    if not target_ref_id and (label or path):
        try:
            resolved = resolve_in_scope(
                list(device.get_ui()),
                None,
                label=label,
                path=path,
                node_id=device.node_id,
                value_label=value_label,
            )
            target_ref_id = str(resolved["ref_id"])
            target_val = resolved.get("resolved_value", value)
            if target_val is not None:
                return [(target_ref_id, str(target_val), "target parameter")]
            return []
        except ValueError:
            pass  # not currently active; resolve through static activation graph

    # 2. Target is not active yet; traverse the static dynamic tree
    app = getattr(device, "app", None)
    if app is None:
        raise ToolError(f"device {device.node_id} has no application program loaded")
    _, tree_builder = app.resolved()
    static_tree = tree_builder.tree
    entries = _walk_static_dynamic_tree(static_tree)

    if target_ref_id:
        cands = [e for e in entries if e["ref_id"] == target_ref_id]
    else:
        effective_label = (label or "").strip()
        effective_path = (path or "").strip() if path else None
        if not effective_label and effective_path:
            segs = [
                s.strip()
                for s in re.split(r"\s*[/\\>:]\s*", effective_path)
                if s.strip()
            ]
            if len(segs) >= 2:
                effective_label = segs[-1]
                effective_path = " / ".join(segs[:-1])
            elif len(segs) == 1:
                effective_label = segs[0]
                effective_path = None
        cands = [
            e
            for e in entries
            if (
                not effective_label
                or str(e["label"]).lower() == effective_label.lower()
            )
            and (
                not effective_path
                or _matches_path(effective_path, tuple(e.get("ancestry", ())))
            )
        ]

    if not cands:
        ident = target_ref_id or path or label
        raise ToolError(
            f"no parameter found matching {ident!r} in device {device.node_id} application"
        )

    by_ref = {c["ref_id"]: c for c in cands}
    if len(by_ref) > 1:
        ref_list = ", ".join(sorted(by_ref.keys()))
        raise ToolError(
            f"ambiguous parameter target ({len(by_ref)} candidates: {ref_list}); "
            "specify a more specific hierarchy path"
        )
    matched = next(iter(by_ref.values()))
    target_ref_id = matched["ref_id"]

    edits: list[tuple[str, str, str]] = []

    # Satisfy each gate from root to leaf
    for gate_ref, cond in matched["gates"]:
        curr = active_map.get(gate_ref)
        if curr is not None and satisfies(cond, curr.value):
            continue
        gate_choices: list[str] | None = None
        if curr is not None:
            w_choices: Any = getattr(curr.widget, "choices", None)
            if w_choices:
                gate_choices = [str(getattr(c, "value", "")) for c in w_choices]
        s_val = _satisfying_value_for_condition(cond, gate_choices)
        edits.append(
            (
                gate_ref,
                s_val,
                f"activation gate for {matched['label'] or target_ref_id}",
            )
        )

    # Determine target value
    target_val = value
    if target_val is None and value_label is not None:
        target_node: Any = matched.get("node")
        pr: Any = getattr(target_node, "_param_ref", None)
        pt: Any = getattr(pr, "parameter_type", None) or getattr(
            getattr(target_node, "_param", None), "parameter_type", None
        )
        choices_list: list[Any] = list(getattr(pt, "choices", None) or [])
        for ch in choices_list:
            ch_text = str(getattr(ch, "text", "") or getattr(ch, "name", ""))
            if ch_text.lower() == value_label.strip().lower():
                target_val = str(getattr(ch, "value", ""))
                break
        if target_val is None:
            raise ToolError(
                f"unknown value_label {value_label!r} for parameter {target_ref_id!r}"
            )

    if target_val is not None and target_ref_id is not None:
        edits.append((target_ref_id, str(target_val), "target value"))

    return edits


def _scan_device_slots(
    device: Any,
    slot_type: str | None = None,
    function_filter: str | None = None,
) -> dict[str, Any]:
    """Scan a device for functional slots/channels and their primary configuration state."""
    from xknxeditor.prod.parser_v2.ui import UiParameterBlock, UiTab

    slots: list[dict[str, Any]] = []
    seen_selectors: set[str] = set()

    def _inspect_container(node: Any, ancestry: list[str]) -> None:
        title = str(getattr(node, "text", None) or getattr(node, "name", None) or "")
        curr_anc = [*ancestry, title] if title else ancestry
        children: list[Any] = list(getattr(node, "children", []) or [])
        params = [
            ch for ch in children if hasattr(ch, "ref_id") and hasattr(ch, "widget")
        ]

        candidates: list[tuple[int, Any]] = []
        for p in params:
            widget: Any = getattr(p, "widget", None)
            choices_cand: list[Any] = list(getattr(widget, "choices", None) or [])
            if not choices_cand:
                continue
            lbl = str(getattr(p, "label", None) or "").lower()
            # Known limitation: Heuristic exclusion and selector patterns are language-dependent (de/en).
            # Word boundaries prevent false exclusions (e.g. 'zeit' in 'Zeitschaltuhr') while
            # filtering secondary parameters like 'Entprellzeit', 'Dimmmode time', 'Timeout model', or 'Verzögerung'.
            if re.search(
                r"\b(zentral\w*|central\w*|sperr\w*|lock\w*|block\w*|meldung\w*|status\w*|"
                r"zyklisch\w*|cyclic\w*|verzögerung\w*|delay\w*|timeout\w*|\w*time|\w*zeit)\b",
                lbl,
                re.IGNORECASE,
            ):
                continue

            score = 0
            if len(choices_cand) > 2:
                score += 10
            elif len(choices_cand) == 2:
                score += 1

            if re.search(r"\b(kanalfunktion|channel\s*function)\b", lbl, re.IGNORECASE):
                score += 8
            elif re.search(r"\b(betriebsart|mode)\b", lbl, re.IGNORECASE):
                score += 6
            elif re.search(r"\b(funktion|function)\b", lbl, re.IGNORECASE):
                score += 4

            if score > 0:
                candidates.append((score, p))

        selector: Any = None
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            selector = candidates[0][1]

        if selector is not None and selector.ref_id not in seen_selectors:
            seen_selectors.add(selector.ref_id)
            val = str(getattr(selector, "value", "") or "")
            widget: Any = getattr(selector, "widget", None)
            choices: list[Any] = list(getattr(widget, "choices", None) or [])
            val_label: Any = getattr(selector, "value_label", None)
            if not val_label and choices:
                for c in choices:
                    if str(getattr(c, "value", "")) == val:
                        val_label = getattr(c, "label", None)
                        break
            val_label_str: str = str(val_label) if val_label is not None else val

            is_free = val in ("0", "") or any(
                term in val_label_str.lower()
                for term in (
                    "deaktiviert",
                    "nicht aktiv",
                    "keine funktion",
                    "disabled",
                    "off",
                    "none",
                    "unused",
                    "unbenutzt",
                )
            )
            status = "free" if is_free else "used"
            supported: list[dict[str, str]] = [
                {
                    "value": str(getattr(c, "value", "")),
                    "label": str(getattr(c, "label", "")),
                }
                for c in choices
            ]
            slot_name = title or (curr_anc[-1] if curr_anc else str(selector.label))
            slot_path = " / ".join(curr_anc)

            include = True
            if slot_type:
                st = slot_type.lower()
                if st not in slot_name.lower() and st not in slot_path.lower():
                    include = False

            if include and function_filter:
                ff = function_filter.lower()
                if is_free:
                    include = any(ff in c["label"].lower() for c in supported)
                else:
                    include = ff in val_label_str.lower()

            if include:
                slots.append(
                    {
                        "slot": slot_name,
                        "path": slot_path,
                        "status": status,
                        "function": val_label_str,
                        "selector_ref_id": selector.ref_id,
                        "selector_label": selector.label,
                        "supported_functions": supported,
                    }
                )

        for ch in children:
            if isinstance(ch, (UiTab, UiParameterBlock)):
                _inspect_container(ch, curr_anc)

    for root in list(device.get_ui()):
        if isinstance(root, (UiTab, UiParameterBlock)):
            _inspect_container(root, [])

    free_count = sum(1 for s in slots if s["status"] == "free")
    used_count = sum(1 for s in slots if s["status"] == "used")
    return {
        "node_id": device.node_id,
        "device_address": device.individual_address,
        "total_slots": len(slots),
        "free_count": free_count,
        "used_count": used_count,
        "slots": slots,
    }


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
) -> dict[str, Any] | None:
    """Create a com-object -> group-address link and build a feedback result: idempotent dedup
    (``already_linked``, no duplicate row), ``dpt_mismatch`` and ``other_sending_group_address_ids``
    (see :func:`_link_annotations`). ``co`` is the resolved com-object (``db_id`` set). Returns None
    when the service refuses the link (unknown GA / security), so the caller raises its own error.
    Shared by project_link_com_object and the batch link op so both give the same feedback."""
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
    device = project.find_device_by_node_id(node_id)
    is_effective = True
    if device is not None:
        visible_ids = {v.id for v in device.get_visible_com_objects()}
        is_effective = getattr(co, "id", None) in visible_ids
    annotations["is_effective"] = is_effective
    if not is_effective:
        annotations["ineffective_warning"] = (
            f"com-object {com_object_ref_id!r} is instantiated but currently inactive/hidden "
            "by the device's parameter settings (ineffective link); telegrams will not be "
            "processed on the bus until its activating parameter is enabled"
        )
    base: dict[str, Any] = {
        "node_id": node_id,
        "com_object_ref_id": com_object_ref_id,
        "com_object_db_id": co.db_id,
        "group_address_id": group_address_id,
    }
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


def param_change_result(
    device: Any,
    parameter_ref_id: str,
    requested: str,
    changes: dict[str, tuple[str | None, str | None]],
    *,
    prior_visible_co_ids: set[str] | None = None,
    prior_active_param_ids: set[str] | None = None,
) -> dict[str, Any]:
    """A trustworthy set-parameter result: the EFFECTIVE value read back after the edit, whether the
    parameter is still active, and the calculated side-effect changes — so a caller never has to
    assume the requested value stuck (it may be normalized, or the parameter may have been
    deactivated by a structural change). ``changes`` is the edit's calculation change set."""
    effective: str | None = None
    active = False
    for p in _collect_ui_parameters(device.get_ui()):
        if p.ref_id == parameter_ref_id:
            effective, active = p.value, True
            break
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
    lost = list(getattr(device, "last_lost_links", []) or [])
    if lost:
        result["lost_links"] = lost

    if prior_visible_co_ids is not None:
        activated_cos = [
            com_object_dict(co)
            for co in device.get_visible_com_objects()
            if co.id not in prior_visible_co_ids
        ]
        result["activated_com_objects"] = activated_cos
    else:
        result["activated_com_objects"] = []

    if prior_active_param_ids is not None:
        param_pairs = _collect_ui_parameters_with_ancestry(list(device.get_ui()))
        unlocked = [
            _parameter_dict(p, include_choices=True, ancestry=anc)
            for p, anc in param_pairs
            if p.ref_id not in prior_active_param_ids
        ]
        result["unlocked_parameters"] = unlocked
    else:
        result["unlocked_parameters"] = []

    return result


def _session_diff(project: Any, only_differences: bool = True) -> dict[str, Any]:
    from xknxeditor.proj.core.addressing import format_ga

    is_dirty = project.edited_since_open()
    diff_params: list[dict[str, Any]] = []
    gas_added: list[dict[str, Any]] = []
    gas_removed: list[dict[str, Any]] = []
    gas_modified: list[dict[str, Any]] = []
    links_added: list[dict[str, Any]] = []
    links_removed: list[dict[str, Any]] = []
    devices_added: list[dict[str, Any]] = []
    devices_removed: list[dict[str, Any]] = []
    devices_modified: list[dict[str, Any]] = []

    if is_dirty and project._pid is not None:
        history = project._svc.history(project._pid)
        baseline_keys = project._history_baseline
        effective_events = [
            e
            for e in history
            if not e.reverted
            and (e.id, e.event_type, repr(e.data)) not in baseline_keys
            and e.event_type != "SetDeviceCommissioning"
        ]

        # Flatten composite events
        flat_events: list[tuple[str, dict[str, Any]]] = []
        for e in effective_events:
            if e.event_type == "Composite":
                for sub in e.data.get("events", []):
                    flat_events.append((sub.get("type", ""), sub.get("data", {})))
            else:
                flat_events.append((e.event_type, e.data))

        # 1. Parameter changes: track by (device_id, ref_id)
        param_history: dict[tuple[int, str], list[tuple[str | None, str]]] = {}
        for ev_type, data in flat_events:
            if ev_type == "SetParameter":
                dev_id = int(data.get("device_id", 0))
                ref_id = str(data.get("ref_id", ""))
                val = str(data.get("value", ""))
                old_val = data.get("old_value")
                old_val_str = str(old_val) if old_val is not None else None
                key = (dev_id, ref_id)
                if key not in param_history:
                    param_history[key] = []
                param_history[key].append((old_val_str, val))

        # Check reverted baseline events (undo on pre-session state)
        reverted_baseline = [
            e
            for e in history
            if e.reverted
            and (e.id, e.event_type, repr(e.data)) in baseline_keys
            and e.event_type != "SetDeviceCommissioning"
        ]
        for e in reverted_baseline:
            ev_list = (
                e.data.get("events", [])
                if e.event_type == "Composite"
                else [{"type": e.event_type, "data": e.data}]
            )
            for sub in ev_list:
                if sub.get("type") == "SetParameter":
                    sdata = sub.get("data", {})
                    dev_id = int(sdata.get("device_id", 0))
                    ref_id = str(sdata.get("ref_id", ""))
                    curr_val = sdata.get("old_value")
                    base_val = sdata.get("value")
                    key = (dev_id, ref_id)
                    if key not in param_history:
                        param_history[key] = []
                    param_history[key].append(
                        (
                            str(base_val) if base_val is not None else None,
                            str(curr_val) if curr_val is not None else "",
                        )
                    )

        for (dev_id, ref_id), changes in param_history.items():
            first_old = changes[0][0]
            last_new = changes[-1][1]
            if first_old != last_new:
                device = project.find_device_by_node_id(dev_id)
                dev_addr = device.individual_address if device else None
                dev_name = device.name if device else f"Device {dev_id}"
                param_label = ref_id
                channel = None
                value_label = None
                if device is not None:
                    try:
                        for p, anc in _collect_ui_parameters_with_ancestry(
                            list(device.get_ui())
                        ):
                            if p.ref_id == ref_id:
                                param_label = p.name or ref_id
                                channel = anc[0] if anc else None
                                value_label = _enum_value_label(p.widget, last_new)
                                break
                    except Exception:
                        pass
                item: dict[str, Any] = {
                    "device_address": dev_addr,
                    "device_name": dev_name,
                    "node_id": dev_id,
                    "ref_id": ref_id,
                    "label": param_label,
                    "old_value": first_old,
                    "new_value": last_new,
                }
                if channel:
                    item["channel"] = channel
                if value_label:
                    item["new_value_label"] = value_label
                diff_params.append(item)

        # 2. Group addresses
        seen_created_gas: set[int] = set()
        for ev_type, data in flat_events:
            if ev_type == "CreateGroupAddress":
                ga_id = data.get("ga_id")
                if ga_id is not None and ga_id not in seen_created_gas:
                    seen_created_gas.add(ga_id)
                    ga = project.get_group_address(ga_id)
                    if ga is not None:
                        gas_added.append(
                            {
                                "address": ga.address,
                                "name": ga.name,
                                "datapoint_type": ga.datapoint_type,
                            }
                        )
            elif ev_type == "SetGroupAddressDatapointType":
                ga_id = data.get("ga_id")
                if ga_id is not None and ga_id not in seen_created_gas:
                    ga = project.get_group_address(ga_id)
                    gas_modified.append(
                        {
                            "address": ga.address if ga else str(ga_id),
                            "old_datapoint_type": data.get("old_datapoint_type"),
                            "new_datapoint_type": data.get("datapoint_type"),
                        }
                    )
            elif ev_type == "RenameGroupAddress":
                ga_id = data.get("ga_id")
                if ga_id is not None and ga_id not in seen_created_gas:
                    ga = project.get_group_address(ga_id)
                    gas_modified.append(
                        {
                            "address": ga.address if ga else str(ga_id),
                            "old_name": data.get("old_name"),
                            "new_name": data.get("name"),
                        }
                    )
            elif ev_type == "RemoveGroupAddress":
                raw_addr = data.get("address")
                addr_str = (
                    format_ga(raw_addr, project.group_address_style)
                    if raw_addr is not None
                    else str(data.get("ga_id", ""))
                )
                gas_removed.append(
                    {
                        "address": addr_str,
                        "name": data.get("name"),
                    }
                )

        # 3. Links
        for ev_type, data in flat_events:
            if ev_type == "LinkComObject":
                co_id = data.get("com_object_id")
                ga_id = data.get("group_address_id")
                ga = project.get_group_address(ga_id) if ga_id is not None else None
                co_obj = None
                dev_obj = None
                if co_id is not None:
                    for d in project.devices:
                        for co in d.com_objects:
                            if co.db_id == co_id:
                                co_obj = co
                                dev_obj = d
                                break
                        if co_obj is not None:
                            break
                if co_obj is not None and ga is not None and dev_obj is not None:
                    links = list(project.get_links_for_com_object(co_id))
                    if any(lnk.group_address_id == ga_id for lnk in links):
                        links_added.append(
                            {
                                "device_address": dev_obj.individual_address,
                                "device_name": dev_obj.name,
                                "com_object_ref_id": co_obj.id,
                                "com_object_name": com_object_display_name(co_obj)
                                or co_obj.name,
                                "com_object_number": co_obj.number,
                                "group_address": ga.address,
                                "is_sending": data.get("is_sending", False),
                            }
                        )
            elif ev_type == "UnlinkComObject":
                links_removed.append(
                    {
                        "link_id": data.get("link_id"),
                        "com_object_id": data.get("com_object_id"),
                        "group_address_id": data.get("group_address_id"),
                    }
                )

        # 4. Devices
        for ev_type, data in flat_events:
            if ev_type == "AddDevice":
                dev_id = data.get("device_id")
                dev = (
                    project.find_device_by_node_id(dev_id)
                    if dev_id is not None
                    else None
                )
                if dev is not None:
                    devices_added.append(
                        {
                            "node_id": dev.node_id,
                            "individual_address": dev.individual_address,
                            "name": dev.name,
                        }
                    )
            elif ev_type == "RemoveDevice":
                devices_removed.append(
                    {
                        "name": data.get("name"),
                        "individual_address": data.get("address"),
                    }
                )
            elif ev_type in ("SetDeviceName", "MoveDevice"):
                dev_id = data.get("device_id")
                dev = (
                    project.find_device_by_node_id(dev_id)
                    if dev_id is not None
                    else None
                )
                if dev is not None:
                    devices_modified.append(
                        {
                            "node_id": dev.node_id,
                            "individual_address": dev.individual_address,
                            "name": dev.name,
                            "change": ev_type,
                        }
                    )

    summary: dict[str, Any] = {
        "parameters_changed": len(diff_params),
        "group_addresses_added": len(gas_added),
        "group_addresses_removed": len(gas_removed),
        "group_addresses_modified": len(gas_modified),
        "links_added": len(links_added),
        "links_removed": len(links_removed),
        "devices_added": len(devices_added),
        "devices_removed": len(devices_removed),
        "devices_modified": len(devices_modified),
    }

    if not only_differences:
        summary["total_elements"] = {
            "devices": len(project.devices),
            "group_addresses": len(project.group_addresses),
        }

    has_differences = any(isinstance(v, int) and v > 0 for v in summary.values())

    return {
        "mode": "session",
        "compare_against": None,
        "is_dirty": is_dirty or has_differences,
        "summary": summary,
        "differences": {
            "parameters": diff_params,
            "group_addresses": {
                "added": gas_added,
                "removed": gas_removed,
                "modified": gas_modified,
            },
            "links": {
                "added": links_added,
                "removed": links_removed,
            },
            "devices": {
                "added": devices_added,
                "removed": devices_removed,
                "modified": devices_modified,
            },
        },
    }


def _file_diff(
    project: Any, compare_path_str: str, only_differences: bool = True
) -> dict[str, Any]:
    import tempfile
    from pathlib import Path

    from sqlalchemy.orm import Session

    from xknxeditor.proj import import_knxproj
    from xknxeditor.proj.core.addressing import format_ga
    from xknxeditor.proj.db import make_engine, url_for
    from xknxeditor.proj.models import (
        ComObject as DbCO,
    )
    from xknxeditor.proj.models import (
        ComObjectLink as DbLink,
    )
    from xknxeditor.proj.models import (
        Device as DbDevice,
    )
    from xknxeditor.proj.models import (
        GroupAddress as DbGA,
    )
    from xknxeditor.proj.models import (
        Parameter as DbParam,
    )

    path = Path(compare_path_str)
    if not path.is_file():
        raise ToolError(f"file not found: {compare_path_str}")

    suffix = path.suffix.lower()

    def _extract_data(session: Session) -> dict[str, Any]:
        devices = session.query(DbDevice).all()
        dev_by_id: dict[int, tuple[str, str]] = {}
        dev_map: dict[str, str] = {}
        for d in devices:
            seg = d.segment
            line = seg.line if seg else None
            area = line.area if line else None
            if area and line and d.address is not None:
                ia = f"{area.address}.{line.address}.{d.address}"
            else:
                ia = None
            key = ia if ia else f"dev_{d.id}"
            dev_by_id[d.id] = (key, d.name)
            dev_map[key] = d.name

        gas = session.query(DbGA).all()
        ga_by_id: dict[int, tuple[int, str, str | None]] = {}
        ga_map: dict[int, tuple[str, str | None]] = {}
        for g in gas:
            ga_by_id[g.id] = (g.address, g.name, g.datapoint_type)
            ga_map[g.address] = (g.name, g.datapoint_type)

        params = session.query(DbParam).all()
        param_map: dict[tuple[str, str], str] = {}
        for p in params:
            d_info = dev_by_id.get(p.device_id)
            if d_info:
                param_map[(d_info[0], p.ref_id)] = p.value

        links = session.query(DbLink).all()
        cos_by_id = {
            co.id: (co.ref_id, co.device_id) for co in session.query(DbCO).all()
        }
        link_set: set[tuple[str, str, int, bool]] = set()
        for lnk in links:
            co_info = cos_by_id.get(lnk.com_object_id)
            ga_info = ga_by_id.get(lnk.group_address_id)
            if co_info and ga_info:
                d_info = dev_by_id.get(co_info[1])
                if d_info:
                    link_set.add((d_info[0], co_info[0], ga_info[0], lnk.is_sending))

        return {
            "devices": dev_map,
            "gas": ga_map,
            "params": param_map,
            "links": link_set,
        }

    def _compare(target_db: Path) -> dict[str, Any]:
        curr_session: Session = project._svc._state(project._pid).session
        curr_data = _extract_data(curr_session)

        engine = make_engine(url_for(target_db))
        with Session(engine) as comp_session:
            comp_data = _extract_data(comp_session)
        engine.dispose()

        # Compare Devices
        devices_added: list[dict[str, Any]] = []
        devices_removed: list[dict[str, Any]] = []
        devices_modified: list[dict[str, Any]] = []
        for dev_key, dev_name in curr_data["devices"].items():
            if dev_key not in comp_data["devices"]:
                devices_added.append({"address": dev_key, "name": dev_name})
            elif comp_data["devices"][dev_key] != dev_name:
                devices_modified.append(
                    {
                        "address": dev_key,
                        "old_name": comp_data["devices"][dev_key],
                        "new_name": dev_name,
                    }
                )
        for dev_key, dev_name in comp_data["devices"].items():
            if dev_key not in curr_data["devices"]:
                devices_removed.append({"address": dev_key, "name": dev_name})

        # Compare Group Addresses
        gas_added: list[dict[str, Any]] = []
        gas_removed: list[dict[str, Any]] = []
        gas_modified: list[dict[str, Any]] = []
        for ga_addr, (ga_name, ga_dpt) in curr_data["gas"].items():
            addr_str = format_ga(ga_addr, project.group_address_style)
            if ga_addr not in comp_data["gas"]:
                gas_added.append(
                    {"address": addr_str, "name": ga_name, "datapoint_type": ga_dpt}
                )
            else:
                old_name, old_dpt = comp_data["gas"][ga_addr]
                if old_name != ga_name or old_dpt != ga_dpt:
                    gas_modified.append(
                        {
                            "address": addr_str,
                            "old_name": old_name,
                            "new_name": ga_name,
                            "old_datapoint_type": old_dpt,
                            "new_datapoint_type": ga_dpt,
                        }
                    )
        for ga_addr, (ga_name, ga_dpt) in comp_data["gas"].items():
            if ga_addr not in curr_data["gas"]:
                gas_removed.append(
                    {
                        "address": format_ga(ga_addr, project.group_address_style),
                        "name": ga_name,
                        "datapoint_type": ga_dpt,
                    }
                )

        # Compare Parameters
        diff_params: list[dict[str, Any]] = []
        all_param_keys = set(curr_data["params"]).union(comp_data["params"])
        for dev_key, ref_id in sorted(all_param_keys):
            curr_val = curr_data["params"].get((dev_key, ref_id))
            comp_val = comp_data["params"].get((dev_key, ref_id))
            if curr_val != comp_val:
                dev_name = curr_data["devices"].get(dev_key) or comp_data[
                    "devices"
                ].get(dev_key)
                diff_params.append(
                    {
                        "device_address": dev_key,
                        "device_name": dev_name,
                        "ref_id": ref_id,
                        "current_value": curr_val,
                        "baseline_value": comp_val,
                    }
                )

        # Compare Links
        links_added: list[dict[str, Any]] = []
        links_removed: list[dict[str, Any]] = []
        for dev_key, co_ref, ga_addr, is_sending in (
            curr_data["links"] - comp_data["links"]
        ):
            links_added.append(
                {
                    "device_address": dev_key,
                    "com_object_ref_id": co_ref,
                    "group_address": format_ga(ga_addr, project.group_address_style),
                    "is_sending": is_sending,
                }
            )
        for dev_key, co_ref, ga_addr, is_sending in (
            comp_data["links"] - curr_data["links"]
        ):
            links_removed.append(
                {
                    "device_address": dev_key,
                    "com_object_ref_id": co_ref,
                    "group_address": format_ga(ga_addr, project.group_address_style),
                    "is_sending": is_sending,
                }
            )

        summary: dict[str, Any] = {
            "parameters_changed": len(diff_params),
            "group_addresses_added": len(gas_added),
            "group_addresses_removed": len(gas_removed),
            "group_addresses_modified": len(gas_modified),
            "links_added": len(links_added),
            "links_removed": len(links_removed),
            "devices_added": len(devices_added),
            "devices_removed": len(devices_removed),
            "devices_modified": len(devices_modified),
        }

        if not only_differences:
            summary["total_elements"] = {
                "devices": len(curr_data["devices"]),
                "group_addresses": len(curr_data["gas"]),
            }

        has_differences = any(isinstance(v, int) and v > 0 for v in summary.values())

        return {
            "mode": "file",
            "compare_against": str(path),
            "is_dirty": has_differences,
            "summary": summary,
            "differences": {
                "parameters": diff_params,
                "group_addresses": {
                    "added": gas_added,
                    "removed": gas_removed,
                    "modified": gas_modified,
                },
                "links": {
                    "added": links_added,
                    "removed": links_removed,
                },
                "devices": {
                    "added": devices_added,
                    "removed": devices_removed,
                    "modified": devices_modified,
                },
            },
        }

    if suffix == ".knxproj":
        with tempfile.TemporaryDirectory() as tmp_str:
            target_db = Path(tmp_str) / "compare.xknx"
            import_knxproj(path, target_db)
            return _compare(target_db)
    elif suffix == ".xknx":
        return _compare(path)
    else:
        raise ToolError(
            f"unsupported file format {suffix!r}; compare_against must be a .knxproj or .xknx file"
        )


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
        """Resolve a group address id by its internal id OR its printed address (e.g. "10/1/2" in
        the project's style). Exactly one must be given. Keeps callers off the extra
        round-trip in the common case."""
        if (group_address_id is None) == (group_address is None):
            raise ToolError(
                "pass exactly one of group_address_id (int) or group_address (str, e.g. '10/1/2')"
            )
        if group_address is not None:
            matches = [g for g in project.group_addresses if g.address == group_address]
            if not matches:
                raise ToolError(
                    f"no group address {group_address!r}; create it with project_create_group_address "
                    "or project_batch first"
                )
            if len(matches) > 1:
                ids = sorted(g.id for g in matches)
                raise ToolError(
                    f"group_address {group_address!r} is ambiguous "
                    f"(ids: {ids}); pass group_address_id explicitly"
                )
            return matches[0].id
        return int(group_address_id)  # type: ignore[arg-type]

    def _require_ga(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> Any:
        ga_id = _require_group_address_id(group_address_id, group_address)
        g = project.get_group_address(ga_id)
        if g is None:
            raise ToolError(f"no group address with id {ga_id}")
        return g

    def _device_from(node_id: int | None, device_address: str | None) -> Device:
        """Device resolver call site shared by the tools that accept either addressing form."""
        return _require_device(node_id, device_address)

    # --- application update ("Update Application Program") ------------

    @tool(read_only=True)
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

    @tool(destructive=True)
    def project_update_application(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """Update a device to the local catalog version of the SAME application program that
        replaces the current one ("Update Application Program Version"). Parameter values carry over
        by ParameterRef number, group objects of the same size by ComObjectRef number with their
        flags, priority, datapoint type and group-address links.

        This is an undoable project edit (project_undo). To apply it on the bus, program the device
        afterwards with connection_program_device and scope="FULL". Returns {node_id, new_version,
        kept, dropped, note}; kept and dropped are counts of carried-over vs discarded parameters and
        group objects."""
        require_project(ctx)

        def _do() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            result = project.update_application(device)
            if result is None:
                raise ToolError(
                    f"no newer application version available for device {dev_id}"
                )
            return {
                "node_id": dev_id,
                "device_address": device.individual_address,
                "new_version": result.new_version,
                "kept": result.kept,
                "dropped": result.dropped,
                "note": "project updated; program the device (connection_program_device, "
                "scope FULL) to apply the change on the bus",
            }

        return ctx.run_locked(_do, timeout=_SLOW)

    @tool(read_only=True)
    def project_list_application_programs(
        node_id: int | None = None, device_address: str | None = None
    ) -> dict[str, Any]:
        """The application programs a device can be changed to with project_change_application:
        the other programs of its hardware in the local catalog, older and newer.

        Use node_id from project_list_devices or device_address. Returns {items, count}; each item has
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

    @tool(destructive=True)
    def project_change_application(
        hardware2program_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Change a device's project application to another program of its hardware. Use node_id
        or device_address and hardware2program_ref_id from project_list_application_programs
        for that device. Parameters, group-object links and flags reset to the new application's
        defaults; address, name and serial number stay.

        Undoable with project_undo. Apply it on the bus with connection_program_device(scope="FULL").
        Returns {node_id, application_id, version, note}."""
        require_project(ctx)

        def _do() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
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
                    f"{dev_id} can be changed to; see project_list_application_programs"
                )
            if not project.change_application(device, program):
                raise ToolError(
                    f"application {program.application_id!r} not resolvable"
                )
            return {
                "node_id": dev_id,
                "device_address": device.individual_address,
                "application_id": program.application_id,
                "version": program.application_version,
                "note": "project updated; program the device (connection_program_device, "
                "scope FULL) to apply the change on the bus",
            }

        return ctx.run_locked(_do, timeout=_SLOW)

    # --- lifecycle --------------------------------------------------------

    @tool(read_only=True)
    def project_status() -> dict[str, Any]:
        """Return {open, path}, with the editor-host path or null when no project is open.
        Most project_* tools need an open project."""
        return ctx.run_locked(
            lambda: {
                "open": project.is_open,
                "path": str(project.path) if project.path else None,
            }
        )

    @tool(destructive=True)
    def project_new(path: str) -> dict[str, Any]:
        """Create a new empty KNX project (.xknx) at ``path`` (on the editor host) and open it. An
        existing file at ``path`` is replaced and the current project closed; file replacement is NOT
        undoable with project_undo. Returns {open, path}."""
        ctx.run_locked(lambda: project.new(Path(path)), timeout=_SLOW)
        return {"open": True, "path": str(project.path) if project.path else None}

    @tool
    def project_open(path: str, password: str | None = None) -> dict[str, Any]:
        """Open an existing .xknx project at path on the editor host, closing the current project.
        Returns {open, path}. Switching projects is outside project_undo history.

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

    @tool
    def project_add_device_certificate(
        code: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Add a KNX Data Secure device certificate: ``code`` is the FDSK from the device label
        (36 characters, groups separated by "-"). A device with the certificate's serial number is
        then commissioned securely with it. With ``node_id`` or ``device_address`` the certificate is for that device:
        one without a serial number takes the certificate's, one with another is refused. Needs a
        password-protected project."""
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
                if (node_id is not None or device_address is not None)
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

    @tool
    def project_set_secure_commissioning(
        activated: bool,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Activate or deactivate a KNX Data Secure device's secure commissioning (one undo step).
        Accepts node_id or device_address. Activated, the device gets a Tool Key (needs a password-protected
        project) and is commissioned with its device certificate. Deactivated, it is programmed in the clear: its
        "Auto" group addresses communicate in the clear and its links to group addresses with
        Security "On" are removed (the result lists both)."""
        from xknxeditor.proj import ProjectPasswordRequired

        require_project(ctx)

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            if project.secure_commissioning(device) is None:
                raise ToolError(f"device {dev_id} does not support KNX Data Secure")
            impact = None if activated else project.secure_commissioning_impact(device)
            try:
                project.set_secure_commissioning(device, activated)
            except ProjectPasswordRequired as exc:
                raise ToolError("set a project password first") from exc
            return {
                "node_id": dev_id,
                "device_address": device.individual_address,
                "secure_commissioning": activated,
                "unsecured_group_address_ids": impact.unsecured if impact else [],
                "removed_link_ids": impact.removed_links if impact else [],
            }

        return ctx.run_locked(_set)

    @tool(read_only=True)
    def project_get_ip_secure(
        node_id: int | None = None,
        device_address: str | None = None,
        include_passwords: bool = False,
    ) -> dict[str, Any]:
        """The KNX IP Secure settings of a secure IP device commissioned securely: whether its
        tunnels are secured and its bus interfaces (kind, user id, tunnelling address). With
        ``include_passwords`` also its commissioning password, its authentication code and the
        bus interfaces' passwords - only when the user asks for them."""
        require_project(ctx)

        def _get() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            settings = project.ip_secure_settings(device)
            if settings is None:
                raise ToolError(
                    f"device {dev_id} is no KNX IP Secure device commissioned securely"
                )
            result: dict[str, Any] = {
                "node_id": dev_id,
                "device_address": device.individual_address,
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
        node_id: int | None = None,
        device_address: str | None = None,
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
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            if project.ip_secure_settings(device) is None:
                raise ToolError(
                    f"device {dev_id} is no KNX IP Secure device commissioned securely"
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
            return {"status": "changed", "node_id": str(dev_id)}

        return ctx.run_locked(_set)

    @tool(destructive=True)
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
        """Close the open project. Returns {status: "closed"}. Reopen it with project_open;
        closing is outside project_undo history."""
        ctx.run_locked(project.close)
        return {"status": "closed"}

    @tool(destructive=True)
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

    @tool(destructive=True)
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

    @tool(read_only=True)
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

    @tool(read_only=True)
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

    @tool(read_only=True)
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

    @tool(read_only=True)
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

    @tool(read_only=True)
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

    @tool(read_only=True)
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
            data = com_object_dict(co)
            visible_ids = {v.id for v in device.get_visible_com_objects()}
            data["is_effective"] = getattr(co, "id", None) in visible_ids
            return data

        return ctx.run_locked(_read)

    @tool(read_only=True)
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

    @tool(read_only=True)
    def project_list_parameters(
        node_id: int | None = None,
        device_address: str | None = None,
        search: str | None = None,
        query: str | None = None,
        channel: str | None = None,
        modified_only: bool = False,
        compact: bool = False,
    ) -> dict[str, Any]:
        """List visible parameters (to discover free channels/functions on multi-channel devices, prefer project_find_free_slots). Identify the
        device by ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass
        exactly one.
        Returns {items, count}; each item has ref_id, label, value, default_value, access, widget.
        To keep the list compact, an enum ``widget`` carries only ``choice_count`` (plus
        ``value_label`` on the item for the current value), NOT the full choice list — call
        project_get_parameter for a single parameter's complete ``choices``.

        Filters:
        - ``search`` / ``query``: filters by substring match on label (case-insensitive).
        - ``channel``: filters parameters by channel or section (e.g. 'A', 'Kanal A', '1').
        - ``modified_only``: if True, returns only parameters whose current value differs from default.
        - ``compact``: if True, returns a lightweight dictionary omitting schema details and choice lists.

        Pass an item's ``ref_id`` to project_set_parameter as its ``parameter_ref_id`` argument."""

        def _read() -> list[dict[str, Any]]:
            device = _require_device(node_id, device_address)
            param_pairs = _collect_ui_parameters_with_ancestry(device.get_ui())

            if channel:
                param_pairs = [
                    (p, anc)
                    for p, anc in param_pairs
                    if any(_matches_channel(channel, h) for h in anc)
                ]

            if modified_only:
                param_pairs = [
                    (p, anc)
                    for p, anc in param_pairs
                    if str(p.value) != str(p.default_value)
                ]

            rows = [
                _parameter_dict(p, include_choices=False, ancestry=anc, compact=compact)
                for p, anc in param_pairs
            ]

            needle = (query or search or "").casefold()
            if needle:
                rows = [r for r in rows if needle in str(r.get("label", "")).casefold()]
            return rows

        return _items(ctx.run_locked(_read))

    @tool(read_only=True)
    def project_parameter_tree(
        node_id: int | None = None,
        device_address: str | None = None,
        outline: bool = False,
        subtree_id: str | None = None,
        tree_token: str | None = None,
    ) -> dict[str, Any]:
        """Read a device's nested parameter UI. Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one. Group
        headings distinguish repeated labels for different channels, buttons or display levels.
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
          to get a fresh one. project_list_parameters(search=...) finds a parameter by label.
        - ``tree_token`` is a short fingerprint of the device's current structure. Pass it back to
          any scoped read/mutation (``subtree_id``, project_resolve_parameter, batch set_parameter)
          to prove the structure has not changed since the id was obtained.

        Pass a parameter's ref_id as parameter_ref_id to project_get_parameter/project_set_parameter.
        This reads project configuration."""

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
                "node_id": resolved_node_id,
                "device_address": device.individual_address,
            }
            if use_outline and not outline and total is not None:
                env["parameter_count"] = total
            return env

        return ctx.run_locked(_read)

    @tool(read_only=True)
    def project_get_parameter(
        parameter_ref_id: str | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        label: str | None = None,
        path: str | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Read a project application parameter. Identify the device by ``node_id`` OR
        ``device_address`` (an individual address like "1.0.30"); pass exactly one. Identify the parameter
        by ``parameter_ref_id`` OR semantically by ``path`` (e.g. "Steuerung 1 / Funktion") or ``label``.
        Returns {ref_id, label, value, default_value, access, widget}; for an enum, widget includes
        the full ``choices`` (value/label/id) — the allowed values for project_set_parameter."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            target_ref = parameter_ref_id
            if not target_ref:
                if not label and not path:
                    raise ToolError(
                        "project_get_parameter needs parameter_ref_id, path, or label"
                    )
                resolved = resolve_in_scope(
                    list(device.get_ui()),
                    None,
                    label=label,
                    channel=channel,
                    path=path,
                    node_id=device.node_id,
                )
                target_ref = resolved["ref_id"]

            for p in _collect_ui_parameters(device.get_ui()):
                if p.ref_id == target_ref:
                    return _parameter_dict(p)
            raise ToolError(
                f"no parameter {target_ref!r} on device {device.node_id}; "
                "call project_list_parameters for valid ref_ids"
            )

        return ctx.run_locked(_read)

    @tool(read_only=True)
    def project_resolve_parameter(
        label: str | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        channel: str | None = None,
        path: str | None = None,
        subtree_id: str | None = None,
        value_label: str | None = None,
        tree_token: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a parameter by its human ``label`` or hierarchy ``path`` (e.g. "Steuerung 1 / Funktion")
        to the ``ref_id`` that project_set_parameter needs, so a caller can avoid the long XML ref ids.
        Identify the device by ``node_id`` OR ``device_address`` (an individual address like "1.0.30"); pass exactly one.

        ``label`` or ``path`` is matched among the device's currently ACTIVE parameters, within an optional scope.
        - pass ``path`` (e.g. 'Steuerung 1 / Funktion' or 'Seite 1 / Steuerung 1') to uniquely resolve across identical channel labels;
        - pass ``channel`` (e.g. 'A', 'Kanal A', '1') to scope to a channel by name without needing
          index paths or tokens;
        - pass ``subtree_id`` (a container ``id`` from project_parameter_tree(outline=True)) to scope
          the match to one channel/block;
        - a label that matches several parameters raises with the candidate ref_ids (narrow the
          scope); a label that matches none raises too. It never guesses.
        With ``value_label`` (an enum choice's text) the result also carries ``resolved_value``, the
        raw value to pass to project_set_parameter.

        Returns {ref_id, label, value, access, resolved_value?, channel?, path?}."""

        def _read() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            return resolve_in_scope(
                list(device.get_ui()),
                subtree_id,
                label=label,
                value_label=value_label,
                channel=channel,
                path=path,
                node_id=device.node_id,
                tree_token=tree_token,
            )

        return ctx.run_locked(_read)

    @tool(read_only=True)
    def project_list_group_addresses(
        name_contains: str | None = None,
    ) -> dict[str, Any]:
        """List project group addresses, optionally filtering names by a case-insensitive substring.
        Returns {items, count}; each item has {id, address, name, datapoint_type, description,
        data_secure}. Pass ``id`` as group_address_id to project tools. ``address`` is formatted in the
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

    @tool(read_only=True)
    def project_get_group_address(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, Any]:
        """Read a project group address using group_address_id or group_address string (e.g. '10/1/2').
        Returns {id, address, name, datapoint_type, description, data_secure}."""

        def _read() -> dict[str, Any]:
            g = _require_ga(group_address_id, group_address)
            return _ga_dict(g)

        return ctx.run_locked(_read)

    @tool(read_only=True)
    def project_next_free_group_address() -> dict[str, Any]:
        """Return {address} for the next unused project group address, formatted per project_info's
        group_address_style. This does not reserve it or check the bus. Pass address to
        project_create_group_address to create it."""
        require_project(ctx)
        address = ctx.run_locked(project.next_free_group_address)
        if address is None:
            raise ToolError("no free group address available")
        return {"address": address}

    @tool(read_only=True)
    def project_get_ga_assignments(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, Any]:
        """Read a group address's project com-object links using group_address_id or group_address string
        (e.g. '10/1/2'). Returns {items, count}; each item has {id, com_object_db_id, group_address_id,
        is_sending, node_id, individual_address, com_object_ref_id, com_object_name}."""

        def _read() -> list[dict[str, Any]]:
            g = _require_ga(group_address_id, group_address)
            target_ga_id = g.id
            owner: dict[int, tuple[Device, ComObject]] = {}
            for dev in project.devices:
                for co in dev.get_visible_com_objects():
                    if co.db_id is not None:
                        owner[co.db_id] = (dev, co)
            result: list[dict[str, Any]] = []
            for a in project.get_assignments_for_ga(target_ga_id):
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
                        "com_object_ref_id": getattr(co, "id", None),
                        "com_object_name": (
                            com_object_display_name(co) if co else None
                        ),
                    }
                )
            return result

        return _items(ctx.run_locked(_read))

    @tool(read_only=True)
    def project_topology() -> dict[str, Any]:
        """Read the project topology. Returns {items, count}; each area item has
        {id, area_number, name, description, lines}, and each line has {id, line_number, name,
        description}. count counts areas.
        Pass an area's id as area_id and a line's id as line_id to topology tools; these IDs differ
        from the area/line numbers used in individual addresses."""

        def _read() -> list[dict[str, Any]]:
            return [
                {
                    "id": area.id,
                    "area_number": area.area_number,
                    "name": area.name,
                    "description": area.description,
                    "lines": [
                        {
                            "id": ln.id,
                            "line_number": ln.line_number,
                            "name": ln.name,
                            "description": ln.description,
                        }
                        for ln in project.get_lines(area.id)
                    ],
                }
                for area in project.get_areas()
            ]

        return _items(ctx.run_locked(_read))

    @tool(read_only=True)
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

    @tool
    def project_set_device_space(
        node_id: int | None = None,
        device_address: str | None = None,
        space_id: int | None = None,
    ) -> dict[str, Any]:
        """Place a device in a room/space, or remove it from any space with space_id=null. Accepts
        node_id or device_address, and a space id from project_spaces. Undoable project
        edit. Returns {node_id, device_address, space_id}."""
        require_project(ctx)

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            project.set_device_space(dev_id, space_id)
            return {
                "node_id": dev_id,
                "device_address": device.individual_address,
                "space_id": space_id,
            }

        return ctx.run_locked(_set)

    @tool(read_only=True)
    def project_compare_devices(
        node_id_a: int | None = None,
        node_id_b: int | None = None,
        device_address_a: str | None = None,
        device_address_b: str | None = None,
        only_differences: bool = True,
    ) -> dict[str, Any]:
        """Compare two devices (by node_id or device_address) across header, parameters and
        com-objects. Items are keyed so two instances of the SAME application line up (parameters by
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
                "device_address_a": dev_a.individual_address,
                "device_address_b": dev_b.individual_address,
                "difference_count": result.difference_count,
                "header": _rows(result.header),
                "parameters": _rows(result.parameters),
                "com_objects": _co_rows(result.com_objects, snap_a, snap_b),
            }

        return ctx.run_locked(_compare, timeout=_SLOW)

    @tool
    def project_create_space(
        space_type: str, name: str, parent_id: int | None = None
    ) -> dict[str, Any]:
        """Create a building/location space. ``space_type`` is one of Building, BuildingPart, Floor,
        Room, Corridor, Stairway, DistributionBoard. ``parent_id`` is a parent space ``id`` from
        project_spaces (null = top level). Undoable project edit. Returns {space_id, parent_id,
        space_type, name}."""
        require_project(ctx)

        def _create() -> dict[str, Any]:
            sid = project.create_space(parent_id, space_type, name)
            if sid is None:
                raise ToolError("could not create space (no project open)")
            return {
                "space_id": sid,
                "parent_id": parent_id,
                "space_type": space_type,
                "name": name,
            }

        return ctx.run_locked(_create)

    @tool
    def project_rename_space(space_id: int, name: str) -> dict[str, Any]:
        """Rename a space (``id`` from project_spaces). Undoable project edit. Returns
        {space_id, name}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.rename_space(space_id, name))
        return {"space_id": space_id, "name": name}

    @tool
    def project_set_space_type(space_id: int, space_type: str) -> dict[str, Any]:
        """Set a space's type (Building, BuildingPart, Floor, Room, Corridor, Stairway,
        DistributionBoard). ``id`` from project_spaces. Undoable project edit. Returns
        {space_id, space_type}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.set_space_type(space_id, space_type))
        return {"space_id": space_id, "space_type": space_type}

    @tool
    def project_move_space(
        space_id: int, new_parent_id: int | None = None
    ) -> dict[str, Any]:
        """Move a space under a different parent space, or to the top level with new_parent_id=null.
        Both ``id`` values come from project_spaces. Undoable project edit. Returns
        {space_id, new_parent_id}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.move_space(space_id, new_parent_id))
        return {"space_id": space_id, "new_parent_id": new_parent_id}

    @tool(destructive=True)
    def project_remove_space(space_id: int) -> dict[str, Any]:
        """Remove a space and its sub-spaces (``id`` from project_spaces); devices placed in them
        become unassigned. Undoable project edit. Returns {status, space_id}."""
        require_project(ctx)
        ctx.run_locked(lambda: project.remove_space(space_id))
        return {"status": "removed", "space_id": space_id}

    # --- device mutations -------------------------------------------------

    @tool
    def project_add_device(
        product_ref_id: str,
        name: str | None = None,
        hardware2program_ref_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a device to the project using product_ref_id from catalog_list_products.
        Optional hardware2program_ref_id from the same catalog listing selects its application;
        omitting it selects the newest catalog version. Undoable with project_undo; commissioning
        hardware requires separate connection tools.

        Returns {node_id, name, individual_address, description}, or only {node_id} if the created
        device cannot be loaded. Pass node_id to device tools."""
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
    def project_rename_device(
        node_id: int | None = None,
        device_address: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename a project device and/or set its description, using node_id from
        project_list_devices or device_address. Pass ``name``, ``description`` or both; an omitted field is left
        unchanged. Undoable with project_undo. Returns {node_id, name, individual_address,
        description}."""
        if name is None and description is None:
            raise ToolError("pass name, description or both")

        def _rename() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            if name is not None:
                project.set_device_name(dev_id, device.name, name)
            if description is not None:
                project.set_device_description(
                    dev_id, device.description or "", description
                )
            return _device_dict(_require_device(dev_id))

        return ctx.run_locked(_rename)

    @tool
    def project_set_individual_address(
        address: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a project device's individual address, e.g. "1.1.5", using node_id or device_address.
        The target area/line must exist in project_topology and the address
        must be unused in the project. Returns {node_id, name, individual_address, description}.
        Undoable with project_undo. This does not check or write the bus; use
        connection_assign_individual_address to assign it to hardware."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            project.set_device_individual_address(
                dev_id, device.individual_address, address
            )
            updated = _require_device(dev_id)
            if updated.individual_address != address:
                raise ToolError(
                    f"could not set individual address {address!r} (invalid or already in use)"
                )
            return _device_dict(updated)

        return ctx.run_locked(_set)

    @tool
    def project_set_additional_address(
        index: int,
        address: str | None,
        node_id: int | None = None,
        device_address: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Set an additional individual address of a project device (a tunnelling address of an
        IP interface) using node_id or device_address. index is 1-based (see
        additional_addresses in project_get_device; entries up to it are created). address is an
        "a.l.d" string in the device's own line, or a device number 1-255; null or "" clears it.
        It must not be used by a device or another additional address of the line. name and
        description, when given, are set too. Each change is one undo step (project_undo).
        Returns {node_id, additional_addresses}. This does not write the bus; a full download of
        the device writes the addresses."""
        from xknxeditor.proj import AdditionalAddressError

        require_project(ctx)

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            try:
                project.set_additional_address(dev_id, index, address)
                if name is not None:
                    project.set_additional_address_name(dev_id, index, name)
                if description is not None:
                    project.set_additional_address_description(
                        dev_id, index, description
                    )
            except IndexError as exc:
                raise ToolError(f"invalid index {index}; use 1 to 255") from exc
            except AdditionalAddressError as exc:
                raise ToolError(str(exc)) from exc
            return {
                "node_id": dev_id,
                "additional_addresses": [
                    _additional_address_dict(entry)
                    for entry in project.additional_addresses(dev_id)
                ],
            }

        return ctx.run_locked(_set)

    @tool
    def project_assign_additional_addresses(
        node_id: int | None = None,
        device_address: str | None = None,
        count: int | None = None,
    ) -> dict[str, Any]:
        """Assign the missing additional individual addresses of a project device (the
        tunnelling addresses of an IP interface) using node_id or device_address: entries
        are created up to count (default: as many as the device's application has) and every
        entry without an address gets one, the highest free device numbers of the device's line
        counting down from 255. One undo step (project_undo). Fails when the line has too few free
        addresses. Returns {node_id, additional_addresses}, each {index, address, name,
        description}."""
        from xknxeditor.proj import AdditionalAddressError

        require_project(ctx)

        def _assign() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            try:
                entries = project.assign_additional_addresses(device, count)
            except AdditionalAddressError as exc:
                raise ToolError(str(exc)) from exc
            return {
                "node_id": dev_id,
                "additional_addresses": [
                    _additional_address_dict(entry) for entry in entries
                ],
            }

        return ctx.run_locked(_assign)

    @tool
    def project_set_serial_number(
        serial_number: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Store a device's KNX serial number in the project using node_id or device_address.
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
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            project.set_device_commissioning(
                dev_id, serial_number=serial_to_project(serial) if serial else ""
            )
            return {"node_id": dev_id, "serial_number": serial_text(serial)}

        return ctx.run_locked(_set)

    @tool
    def project_set_com_object_flag(
        com_object_ref_id: str,
        flag: Flag,
        value: bool,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> dict[str, Any]:
        """Override a com-object flag (low-level fallback; prefer project_batch with op="set_com_object_flag").
        Pass ref_id from project_list_com_objects as com_object_ref_id with node_id or device_address;
        the object must have linkable=true. Undoable with project_undo; program the device to apply
        the change on the bus.

        Returns {ref_id, db_id, linkable, name, number, dpt, dpt_name, object_size, flags,
        description, priority}, or only {ref_id} if the updated object cannot be found. flags
        contains the six named flags."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            co = device.find_com_object(com_object_ref_id)
            if co is None:
                raise ToolError(
                    f"no com-object {com_object_ref_id!r} on device {dev_id}"
                )
            if co.db_id is None:
                raise ToolError(
                    f"com-object {com_object_ref_id!r} is not instantiated in the project "
                    "(not linkable/editable)"
                )
            project.set_flag(device, com_object_ref_id, flag, value)
            updated = _require_device(dev_id).find_com_object(com_object_ref_id)
            return (
                com_object_dict(updated)
                if updated is not None
                else {"ref_id": com_object_ref_id}
            )

        return ctx.run_locked(_set)

    @tool
    def project_set_parameter(
        parameter_ref_id: str | None = None,
        value: str | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        label: str | None = None,
        path: str | None = None,
        channel: str | None = None,
        subtree_id: str | None = None,
        value_label: str | None = None,
        tree_token: str | None = None,
    ) -> dict[str, Any]:
        """Set a single application parameter (low-level fallback; prefer project_setup_parameters to auto-activate gating selectors). Identify the device by ``node_id`` OR ``device_address`` (an
        individual address like "1.0.30"); pass exactly one. Pass the parameter's `ref_id` (from
        project_list_parameters, or project_parameter_tree) as the `parameter_ref_id` argument; use a
        `value` permitted by that parameter's `widget`.

        Alternatively, use semantic addressing: pass `path` (e.g. 'Steuerung 1 / Funktion') or `label`
        (with optional `channel`, `path` or `subtree_id` scope) instead of `parameter_ref_id`, and
        `value_label` (an enum choice's text) instead of raw `value`.

        When you configure a device's push-button / display function (e.g. a glass push button, room
        controller), ALWAYS also set its display symbols and key labels/name — do not leave them at
        their defaults.

        Returns {ref_id, requested, value, active, changed?, lost_links?, activated_com_objects, unlocked_parameters}:
        ``value`` is read back AFTER the edit (may be a normalized form of ``requested``, or null with ``active=false`` if
        the edit deactivated the parameter), ``changed`` lists other parameters a calculation changed
        as a side effect, ``lost_links`` lists group-address links this change dropped by
        deactivating their com-objects ({com_object_ref_id, group_address_id, address}),
        ``activated_com_objects`` lists com-objects newly visible after this edit, and
        ``unlocked_parameters`` lists parameters newly active after this edit. A rejected
        value raises with the reason and does NOT take effect on a later edit."""

        def _set() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            if parameters_in_plugin(device):
                raise ToolError(PLUGIN_PARAMETERS_ERROR.format(node_id=dev_id))

            target_ref = parameter_ref_id
            target_val = value

            if not target_ref:
                if not label and not path:
                    raise ToolError(
                        "project_set_parameter needs parameter_ref_id, path, or label"
                    )
                try:
                    resolved = resolve_in_scope(
                        list(device.get_ui()),
                        subtree_id,
                        label=label,
                        value_label=value_label,
                        channel=channel,
                        path=path,
                        node_id=dev_id,
                        tree_token=tree_token,
                    )
                except ValueError as exc:
                    raise ToolError(str(exc)) from exc
                target_ref = resolved["ref_id"]
                if target_val is None:
                    target_val = resolved.get("resolved_value")

            if target_val is None and value_label is not None and target_ref:
                for p in _collect_ui_parameters(device.get_ui()):
                    if p.ref_id == target_ref:
                        try:
                            target_val = resolve_enum_value(
                                p.widget, value_label, p.label
                            )
                        except ValueError as exc:
                            raise ToolError(str(exc)) from exc
                        break

            if target_val is None:
                raise ToolError(
                    "project_set_parameter needs value (or value_label with a label or parameter_ref_id)"
                )

            # Reject NON-editable targets (absent / Access.NONE / Access.READ); matches what the GUI
            # lets a user change. The core engine's active-set guard alone would still accept hidden-
            # but-internally-active and read-only parameters.
            require_editable_param(device, target_ref)

            prior_co_ids = {co.id for co in device.get_visible_com_objects()}
            prior_param_ids = {
                p.ref_id for p in _collect_ui_parameters(device.get_ui())
            }
            changes = project.edit_params(
                device, [(target_ref, target_val)], mode="edit"
            )
            current_device = project.find_device_by_node_id(dev_id) or device
            if hasattr(device, "last_lost_links"):
                current_device.last_lost_links = device.last_lost_links
            return param_change_result(
                current_device,
                target_ref,
                target_val,
                changes,
                prior_visible_co_ids=prior_co_ids,
                prior_active_param_ids=prior_param_ids,
            )

        return ctx.run_locked(_set)

    @tool
    def project_setup_parameters(
        parameters: list[dict[str, Any]],
        node_id: int | None = None,
        device_address: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Setup and configure parameters on a device, automatically resolving and enabling
        any required parent selector gates (Choose/When dependency chains) in the activation path.

        This eliminates manual discovery and sequential toggling of gating switches when configuring
        nested functions (e.g. channel mode, push-button actions, rocker settings). Each entry in
        ``parameters`` specifies:
        - target: ``ref_id``, ``path`` (e.g. 'Steuerung 1 / Funktion'), or ``label``
        - value: ``value`` (raw value) or ``value_label`` (enum choice text)

        If the target parameter is gated behind parent Choose/When conditions that are not yet active,
        the activation path is resolved, parent selector parameters are scheduled with satisfying values,
        and all edits are applied atomically in one transaction.

        Returns {status, planned_edits, applied, changed, activated_com_objects, unlocked_parameters}.
        If dry_run is True, returns planned_edits without applying the changes."""

        def _setup() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            dev_id = device.node_id
            if parameters_in_plugin(device):
                raise ToolError(PLUGIN_PARAMETERS_ERROR.format(node_id=dev_id))

            all_edits: list[tuple[str, str, str]] = []
            seen_refs: set[str] = set()

            for item in parameters:
                target_ref = item.get("parameter_ref_id") or item.get("ref_id")
                label = item.get("label")
                path = item.get("path")
                val = item.get("value")
                val_str = str(val) if val is not None else None
                val_label = item.get("value_label")

                edits = find_activation_path(
                    device,
                    target_ref_id=target_ref,
                    label=label,
                    path=path,
                    value=val_str,
                    value_label=val_label,
                )
                for ref, value_to_set, reason in edits:
                    if ref in seen_refs:
                        all_edits = [
                            (r, value_to_set if r == ref else v, desc)
                            for r, v, desc in all_edits
                        ]
                    else:
                        seen_refs.add(ref)
                        all_edits.append((ref, value_to_set, reason))

            planned = [
                {"ref_id": ref, "value": val, "reason": reason}
                for ref, val, reason in all_edits
            ]

            if dry_run:
                return {
                    "status": "planned",
                    "applied": False,
                    "planned_edits": planned,
                    "node_id": dev_id,
                    "device_address": device.individual_address,
                }

            if not all_edits:
                return {
                    "status": "no_changes",
                    "applied": False,
                    "planned_edits": [],
                    "node_id": dev_id,
                    "device_address": device.individual_address,
                }

            prior_co_ids = {co.id for co in device.get_visible_com_objects()}
            prior_param_ids = {
                p.ref_id for p in _collect_ui_parameters(device.get_ui())
            }

            edits_for_engine = [(ref, val) for ref, val, _ in all_edits]
            changes = project.edit_params(device, edits_for_engine, mode="edit")
            current_device = project.find_device_by_node_id(dev_id) or device

            activated_cos = [
                com_object_dict(co)
                for co in current_device.get_visible_com_objects()
                if co.id not in prior_co_ids
            ]
            param_pairs = _collect_ui_parameters_with_ancestry(
                list(current_device.get_ui())
            )
            unlocked = [
                _parameter_dict(p, include_choices=True, ancestry=anc)
                for p, anc in param_pairs
                if p.ref_id not in prior_param_ids
            ]

            side_effects = [
                {"ref_id": ref, "old": old, "new": new}
                for ref, (old, new) in sorted(changes.items())
                if ref not in seen_refs and old != new
            ]

            return {
                "status": "applied",
                "applied": True,
                "node_id": dev_id,
                "device_address": device.individual_address,
                "planned_edits": planned,
                "changed": side_effects,
                "activated_com_objects": activated_cos,
                "unlocked_parameters": unlocked,
            }

        return ctx.run_locked(_setup)

    @tool(read_only=True)
    def project_find_free_slots(
        node_id: int | None = None,
        device_address: str | None = None,
        slot_type: str | None = None,
        function_filter: str | None = None,
    ) -> dict[str, Any]:
        """Scan a multi-channel or modular device (actuators, touch panels, push-buttons, inputs)
        for free and used channels, controls, or slots.

        Identifies functional slots by inspecting UI containers and primary selector parameters
        (e.g. 'Channel function', 'Funktion', 'Betriebsart').

        Args:
            node_id: Internal device node ID.
            device_address: Individual address (e.g. '1.0.30'). Pass exactly one of node_id or device_address.
            slot_type: Optional substring filter on slot name/path (e.g. 'Steuerung', 'Channel', 'Kanal').
            function_filter: Optional filter to only return slots that currently have, or can be configured to,
                             the specified function (e.g. 'Schalten', 'Dimmen', 'Jalousie').

        Returns {node_id, device_address, total_slots, free_count, used_count, slots: [{slot, path, status, function, selector_ref_id, selector_label, supported_functions}]}."""

        def _scan() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            return _scan_device_slots(
                device, slot_type=slot_type, function_filter=function_filter
            )

        return ctx.run_locked(_scan)

    # --- topology mutations -----------------------------------------------

    @tool
    def project_create_area(area_number: int, name: str = "") -> dict[str, Any]:
        """Create a project topology area. Undoable with project_undo.
        Returns {area_id, area_number, name}; use area_id for project_create_line and area edits."""
        require_project(ctx)
        area_id = ctx.run_locked(lambda: project.create_area(area_number, name))
        return {"area_id": area_id, "area_number": area_number, "name": name}

    @tool
    def project_rename_area(area_id: int, name: str) -> dict[str, Any]:
        """Rename a project topology area, using an area's id from project_topology as area_id.
        Undoable with project_undo. Returns {area_id, name}."""
        ctx.run_locked(lambda: project.rename_area(area_id, "", name))
        return {"area_id": area_id, "name": name}

    @tool(destructive=True)
    def project_remove_area(area_id: int) -> dict[str, str]:
        """Remove a project topology area and its lines/devices. Use an area's id from
        project_topology as area_id. Undoable with project_undo; hardware is unchanged.
        Returns {status: "removed", area_id}, with area_id as a string."""
        ctx.run_locked(lambda: project.remove_area(area_id))
        return {"status": "removed", "area_id": str(area_id)}

    @tool
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

    @tool
    def project_rename_line(line_id: int, name: str) -> dict[str, Any]:
        """Rename a project topology line, using a line's id from project_topology as line_id.
        Undoable with project_undo. Returns {line_id, name}."""
        ctx.run_locked(lambda: project.rename_line(line_id, "", name))
        return {"line_id": line_id, "name": name}

    @tool(destructive=True)
    def project_remove_line(line_id: int) -> dict[str, str]:
        """Remove a project topology line and its devices. Use a line's id from project_topology as
        line_id. Undoable with project_undo; hardware is unchanged. Returns {status: "removed", line_id},
        with line_id as a string."""
        ctx.run_locked(lambda: project.remove_line(line_id))
        return {"status": "removed", "line_id": str(line_id)}

    # --- group address mutations ------------------------------------------

    @tool
    def project_create_group_address(
        address: str | None = None, name: str = ""
    ) -> dict[str, Any]:
        """Create a single project group address (low-level fallback; prefer project_batch to create GAs and link atomically). address follows project_info's group_address_style
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

    @tool
    def project_rename_group_address(
        name: str,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Rename a project group address using group_address_id or group_address string."""

        def _rename() -> dict[str, Any]:
            g = _require_ga(group_address_id, group_address)
            ga_id = g.id
            project.rename_group_address(ga_id, name)
            updated = project.get_group_address(ga_id)
            if updated is None:
                raise ToolError(f"no group address with id {ga_id}")
            return {"id": updated.id, "address": updated.address, "name": updated.name}

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
                    }
                )
                _walk(r.children, r.id, depth + 1)

        _walk(project.get_group_range_tree(), None, 0)
        return out

    @tool(read_only=True)
    def project_list_group_ranges(name_contains: str | None = None) -> dict[str, Any]:
        """List the group-address folders (main and middle groups), optionally filtering names by
        a case-insensitive substring. Returns {items, count}; each item has {id, address, name,
        description, parent_id, depth} (depth 0 = main group; name and description are "" when
        unset, parent_id is null for main groups). Pass ``id`` as group_range_id to
        project_rename_group_range."""

        def _read() -> list[dict[str, Any]]:
            needle = (name_contains or "").lower()
            return [
                r for r in _group_ranges() if not needle or needle in r["name"].lower()
            ]

        return _items(ctx.run_locked(_read))

    @tool
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

    @tool
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

    @tool
    def project_set_group_address_dpt(
        dpt: str | None = None,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a project group address's datapoint type, or clear it with dpt=null, using group_address_id
        or group_address string (e.g. '10/1/2')."""

        def _set() -> dict[str, Any]:
            g = _require_ga(group_address_id, group_address)
            ga_id = g.id
            try:
                project.set_group_address_dpt(ga_id, dpt)
            except ValueError as e:
                raise ToolError(str(e)) from e
            updated = project.get_group_address(ga_id)
            if updated is None:
                raise ToolError(f"no group address with id {ga_id}")
            return {
                "id": updated.id,
                "address": updated.address,
                "datapoint_type": updated.datapoint_type,
            }

        return ctx.run_locked(_set)

    @tool
    def project_set_group_address_security(
        mode: str,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, Any]:
        """Set a group address' KNX Data Secure mode using group_address_id or group_address string."""
        from xknxeditor.proj import GroupAddressSecurityError, ProjectPasswordRequired

        if mode not in ("Auto", "On", "Off"):
            raise ToolError('mode must be "Auto", "On" or "Off"')

        def _set() -> dict[str, Any]:
            g = _require_ga(group_address_id, group_address)
            ga_id = g.id
            try:
                project.set_group_address_security(ga_id, mode)
            except ProjectPasswordRequired as e:
                raise ToolError("set a project password first") from e
            except GroupAddressSecurityError as e:
                raise ToolError(str(e)) from e
            updated = project.get_group_address(ga_id)
            if updated is None:
                raise ToolError(f"no group address with id {ga_id}")
            return {
                "id": updated.id,
                "address": updated.address,
                "secured": updated.data_secure,
            }

        return ctx.run_locked(_set)

    @tool(destructive=True)
    def project_remove_group_address(
        group_address_id: int | None = None, group_address: str | None = None
    ) -> dict[str, str]:
        """Remove a group address and its links (low-level fallback; prefer project_batch with op="remove_group_address"). Uses group_address_id or group_address string."""
        g = _require_ga(group_address_id, group_address)
        ga_id = g.id
        ctx.run_locked(lambda: project.remove_group_address(ga_id))
        return {
            "status": "removed",
            "group_address_id": str(ga_id),
            "group_address": g.address,
        }

    @tool
    def project_link_com_object(
        com_object_ref_id: str,
        node_id: int | None = None,
        device_address: str | None = None,
        group_address_id: int | None = None,
        group_address: str | None = None,
        is_sending: bool = False,
    ) -> dict[str, Any]:
        """Create a single group-object link (low-level fallback; prefer project_batch to create GAs and link objects atomically). The
        device is identified by ``node_id`` (from project_list_devices) OR ``device_address`` (an
        individual address like "1.0.30"); the group address by ``group_address_id`` (from
        project_create_group_address or project_get_group_address) OR ``group_address`` (a printed address like "10/1/2" in the
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
        object already sends to these, so it now has several sending GAs). Linking the same pair again
        is idempotent (``already_linked: true``, no duplicate). Pass link_id as
        project_unlink_com_object's assignment_id. Program the device to apply the link on the bus."""
        require_project(ctx)

        def _link() -> dict[str, Any]:
            device = _require_device(node_id, device_address)
            ga = _require_ga(group_address_id, group_address)
            ga_id = ga.id
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
                project, device.node_id, com_object_ref_id, co, ga_id, is_sending
            )
            if result is None:
                raise ToolError(
                    f"could not link com-object to group address {ga_id} "
                    "(check the group address exists; a group address with Security On "
                    "only takes devices commissioned securely)"
                )
            result["device_address"] = device.individual_address
            result["group_address"] = ga.address
            return result

        return ctx.run_locked(_link)

    @tool(destructive=True)
    def project_unlink_com_object(
        assignment_id: int | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        com_object_ref_id: str | None = None,
        group_address_id: int | None = None,
        group_address: str | None = None,
    ) -> dict[str, str]:
        """Remove a group-object link (low-level fallback; prefer project_batch with op="unlink_com_object"). Remove by ``assignment_id``, OR directly by specifying
        ``node_id``/``device_address``, ``com_object_ref_id``, and ``group_address_id``/``group_address``."""

        def _unlink() -> dict[str, str]:
            target_id = assignment_id
            if target_id is None:
                device = _require_device(node_id, device_address)
                if not com_object_ref_id:
                    raise ToolError(
                        "unlink needs assignment_id, or com_object_ref_id with group_address"
                    )
                co = device.find_com_object(com_object_ref_id)
                if co is None or co.db_id is None:
                    raise ToolError(
                        f"no instantiated com-object {com_object_ref_id!r} on device {device.node_id}"
                    )
                ga = _require_ga(group_address_id, group_address)
                links = list(project.get_links_for_com_object(co.db_id))
                found = next(
                    (link for link in links if link.group_address_id == ga.id), None
                )
                if found is None:
                    raise ToolError(
                        f"no link found between com-object {com_object_ref_id!r} and group address {ga.address!r}"
                    )
                target_id = found.id
            project.unlink_com_object_from_ga(target_id)
            return {"status": "unlinked", "assignment_id": str(target_id)}

        return ctx.run_locked(_unlink)

    # --- undo / redo / history --------------------------------------------

    @tool
    def project_undo() -> dict[str, bool]:
        """Undo the last undoable project edit. Returns {undone: bool}. Bus programming, address
        assignment, and file replacements are outside project history and are not reverted."""
        return {"undone": ctx.run_locked(project.undo)}

    @tool
    def project_redo() -> dict[str, bool]:
        """Redo the last undone project edit. Returns {redone: bool}."""
        return {"redone": ctx.run_locked(project.redo)}

    @tool(read_only=True)
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

    @tool(read_only=True)
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

    @tool
    def project_jump_to(event_id: int) -> dict[str, str]:
        """Move the project undo/redo cursor to a history entry, passing id from project_history as
        event_id. This reverts or reapplies project edits; bus changes and file replacements are
        outside this history. Returns {status: "jumped", event_id}, with event_id as a string."""
        ctx.run_locked(lambda: project.jump_to(event_id))
        return {"status": "jumped", "event_id": str(event_id)}

    @tool(read_only=True)
    def project_diff(
        compare_against: str | None = None,
        only_differences: bool = True,
    ) -> dict[str, Any]:
        """Show changes made in the current session (dirty state since opening/saving),
        OR compare the current project against an external project file (.knxproj or .xknx).

        Args:
            compare_against: Optional path to a .knxproj or .xknx file. If omitted,
                             diffs the current uncommitted session edits against the open project baseline.
            only_differences: If True (default), omits unchanged elements to keep the diff concise.
        """
        require_project(ctx)
        if compare_against:
            return ctx.run_locked(
                lambda: _file_diff(project, compare_against, only_differences)
            )
        return ctx.run_locked(lambda: _session_diff(project, only_differences))
