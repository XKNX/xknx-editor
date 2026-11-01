"""Generic plugin-parameter viewer that groups a device's raw application parameters by a vendor
mapping file into readable sections.

The alternative to porting the vendor's full WPF plug-in: build a flat, sectioned view of every
parameter the application program defines, keyed off the parameter's own name (which usually
encodes the plug-in's hierarchy — e.g. "c0.1dalLinGro_timeDimm" is channel 0, DALI line 1, group
section, timeDimm field). A JSON mapping file per vendor turns the prefix codes into human
section labels; parameters that do not match any pattern end up in a fallback bucket.

No special parser integration: this reads the raw ``ApplicationProgram.parameters`` /
``parameter_refs`` directly and never calls into ``parser_v2``. Values come from the device's
stored parameter instance refs (fall back to the parameter's default). Writing is NOT part of
this module — the view is read-only for now.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from xknxeditor.prod.parser_v2._name import apply_text_args, fill_name

if TYPE_CHECKING:
    from editor_gui.device import Device

_MAPPINGS_DIR = Path(__file__).parent / "plugin_mappings"


@dataclass(frozen=True, slots=True)
class _SectionPattern:
    regex: re.Pattern[str]
    label: str  # with "{1}", "{2}", … placeholders for captured groups


@dataclass(frozen=True, slots=True)
class PluginMapping:
    """A loaded vendor mapping: how to group an application's raw parameter names into sections."""

    app_id_prefixes: tuple[str, ...]
    title: str
    patterns: tuple[_SectionPattern, ...]
    # Optional name of a BLOB reader module under ``editor_gui.plugin_blob_readers``: when set,
    # the panel appends sections produced from ``device_binary_data`` (vendor plug-in state kept
    # outside the standard parameter DOM, e.g. the Siemens classic DCA sensor/presence config).
    blob_reader: str = ""

    def match(self, parameter_name: str) -> tuple[str, int] | None:
        """Return (section_label, prefix_end_offset) for ``parameter_name``, or ``None`` if no
        pattern matched. The offset lets callers derive a field label from the part of the name
        past the pattern — e.g. ``c0.5dalLinGro_timeDimm`` with the Groups pattern gives
        ``_timeDimm`` as the trailing field."""
        for p in self.patterns:
            m = p.regex.match(parameter_name)
            if m is None:
                continue
            label = p.label
            for i, grp in enumerate(m.groups(), start=1):
                label = label.replace(f"{{{i}}}", grp if grp is not None else "")
            return label, m.end()
        return None


@dataclass(slots=True)
class PluginParameter:
    """One parameter row in the sectioned view."""

    ref_id: str  # qualified ParameterRef id (same shape set_parameter expects)
    name: str  # raw parameter name from the XML (what the mapping matched against)
    label: str  # text to show the user (parameter text if any, else name)
    value: (
        str  # current value from the device DOM, falling back to the parameter default
    )
    default_value: str
    section: str  # section label from the mapping (or the fallback bucket)
    display_order: int  # from the ParameterRef, for stable sorting
    writable: bool  # access != None (writes still land in the standard parameter DOM)
    # Optional enum choices for a dropdown. Pairs of (raw_value, display_label); the panel
    # shows a combo when non-empty. Keeps the raw token under the display label so the write
    # path can route the original value back to the DOM.
    choices: tuple[tuple[str, str], ...] = ()
    # Optional grouping key within a section. When set, the panel nests the row under a
    # collapsible tree node with this label, so a long list (e.g. 16 scenes x many values)
    # becomes a short, browsable tree instead of one flat table. Empty = ungrouped (flat).
    group: str = ""
    # Stable identity of the group, independent of its displayed ``group`` text. The panel keys
    # the tree node's imgui id off this so editing an item's name (which changes ``group``) does
    # not collapse its expanded node. Falls back to ``group`` when empty.
    group_key: str = ""


@dataclass(frozen=True, slots=True)
class CollectionBinding:
    """Hooks a section to a list container the user can edit structurally.

    The reader attaches this to sections that represent an array-like list in the plug-in
    state (sensors, groups, ECGs, …). The panel renders an Add form (type picker + name)
    above the table and a Remove button per row. ``container_locator`` identifies the
    container element inside the vendor-specific blob (e.g. the Siemens ``Presence[0]``);
    ``item_types`` is the optional dropdown. The exact writer handling is per-vendor; the
    panel just calls back with ``(device, binding, type_id, name)`` / ``(device, binding,
    item_id)``.
    """

    container_tag: str
    display_label: str
    container_locator: tuple[tuple[str, int], ...] = ()
    item_types: tuple[tuple[str, str], ...] = ()
    needs_name: bool = True


@dataclass(slots=True)
class PluginSectionView:
    section: str
    parameters: list[PluginParameter] = field(default_factory=list["PluginParameter"])
    # Optional: a Collection this section represents, used by the generic panel to render
    # Add/Remove controls without a Siemens-specific regex.
    collection: CollectionBinding | None = None


@lru_cache(maxsize=32)
def _load_mapping(path_str: str) -> PluginMapping:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    patterns = tuple(
        _SectionPattern(regex=re.compile(entry["regex"]), label=entry["label"])
        for entry in data.get("sections", [])
    )
    # Accept either a single ``app_id_prefix`` (backwards-compatible) or an
    # ``app_id_prefixes`` list for OEM chassis that reuses the same plug-in.
    prefixes_raw = data.get("app_id_prefixes")
    if prefixes_raw is None:
        prefixes_raw = [data["app_id_prefix"]]
    prefixes = tuple(str(p) for p in prefixes_raw if p)
    if not prefixes:
        raise KeyError("app_id_prefix(es) missing")
    return PluginMapping(
        app_id_prefixes=prefixes,
        title=data.get("title", ""),
        patterns=patterns,
        blob_reader=str(data.get("blob_reader", "")),
    )


def mapping_for(app_id: str | None) -> PluginMapping | None:
    """Return the first mapping file whose ``app_id_prefixes`` contains a prefix of ``app_id``."""
    if not app_id or not _MAPPINGS_DIR.is_dir():
        return None
    needle = app_id.upper()
    for path in sorted(_MAPPINGS_DIR.glob("*.json")):
        try:
            mapping = _load_mapping(str(path))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        for prefix in mapping.app_id_prefixes:
            if needle.startswith(prefix.upper()):
                return mapping
    return None


_FALLBACK_SECTION = "Other"
# Placeholder labels vendors use when the real parameter name is left to the plug-in UI; when a
# parameter's text attribute equals one of these (or starts with the "dummy-" prefix some vendors
# attach to a generated chain), prefer the trailing field-name fallback so each row stays
# distinguishable in the generic viewer.
_PLACEHOLDER_TEXTS: frozenset[str] = frozenset(
    {
        "CO switching parameter",
        "dummy",
        "Dummy",
    }
)
_PLACEHOLDER_PREFIXES: tuple[str, ...] = ("dummy-", "Dummy-", "dummy ", "Dummy ")


def _is_placeholder_text(text: str) -> bool:
    if text in _PLACEHOLDER_TEXTS:
        return True
    return any(text.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def build_sections(
    parameters_by_ref_id: dict[str, _ParameterLike],
    parameter_refs: list[_ParameterRefLike],
    stored_values: dict[str, str],
    mapping: PluginMapping,
    pt_by_id: dict[str, object] | None = None,
) -> list[PluginSectionView]:
    """Group every parameter-ref of an application into sections keyed by its parameter's name."""
    types = pt_by_id or {}
    sections: dict[str, PluginSectionView] = {}
    for ref in parameter_refs:
        param = parameters_by_ref_id.get(ref.ref_id)
        if param is None:
            continue
        name = getattr(param, "name", "") or ""
        if not name:
            continue
        match = mapping.match(name)
        if match is None:
            section_label = _FALLBACK_SECTION
            field_name = name
        else:
            section_label, prefix_end = match
            field_name = name[prefix_end:].lstrip("_") or name
        default_value = _text(ref.value) or _text(param.value)
        current = stored_values.get(ref.id, default_value)
        text_attr = _text(getattr(ref, "text", None)) or _text(
            getattr(param, "text", None)
        )
        # Many apps carry an identical placeholder text on every parameter ("CO switching
        # parameter"); fall back to the trailing field name so each row is distinguishable.
        label = (
            text_attr
            if text_attr and not _is_placeholder_text(text_attr)
            else field_name
        )
        access = getattr(ref, "access", None) or getattr(param, "access", None)
        writable = access is not None and access.name != "NONE"
        row = PluginParameter(
            ref_id=ref.id,
            name=name,
            label=label,
            value=current,
            default_value=default_value,
            section=section_label,
            display_order=int(getattr(ref, "display_order", 0) or 0),
            writable=bool(writable),
            choices=_enum_choices(param, types),
        )
        sections.setdefault(
            section_label, PluginSectionView(section=section_label)
        ).parameters.append(row)
    for view in sections.values():
        view.parameters.sort(key=lambda p: (p.display_order, p.name))
    # Keep mapping order; fallback bucket last.
    ordered: list[PluginSectionView] = []
    seen: set[str] = set()
    for p in mapping.patterns:
        for key, view in sections.items():
            if key in seen:
                continue
            label_tpl = p.label
            # Any section label the pattern can produce starts with the template's first literal
            # characters up to the first "{": fast enough and avoids re-running the regex.
            prefix = label_tpl.split("{", 1)[0]
            if key.startswith(prefix):
                ordered.append(view)
                seen.add(key)
    for key, view in sections.items():
        if key not in seen and key != _FALLBACK_SECTION:
            ordered.append(view)
            seen.add(key)
    if _FALLBACK_SECTION in sections:
        ordered.append(sections[_FALLBACK_SECTION])
    return ordered


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _enum_choices(
    param: object, pt_by_id: dict[str, object]
) -> tuple[tuple[str, str], ...]:
    """The (raw value, label) pairs of ``param``'s ParameterType if it is an enumeration.

    Reads the type restriction's ``enumeration`` directly (by attribute, not by matching a
    version-specific binding class), so the viewer shows human labels + a dropdown the way the
    normal tree does (``3`` -> ``RGBW Colour``) instead of the raw stored integer, across every
    schema version. Empty when the type is not an enumeration or is unknown (plain numeric/text
    parameters keep their free input).
    """
    pt_id = getattr(param, "parameter_type", None)
    if not pt_id:
        return ()
    pt = pt_by_id.get(str(pt_id))
    restriction = getattr(pt, "choice", None)
    enumeration = getattr(restriction, "enumeration", None)
    if not enumeration:
        return ()
    ordered = sorted(
        enumeration,
        key=lambda e: getattr(e, "display_order", None) or 0,
    )
    choices: list[tuple[str, str]] = []
    for e in ordered:
        value = getattr(e, "value", None)
        if value is None:
            continue
        choices.append((str(value), str(getattr(e, "text", "") or "")))
    return tuple(choices)


def _resolve_placeholders(label: str, text_args: dict[str, str]) -> str:
    """Substitute ``{{Name}}`` placeholders in ``label`` from ``text_args`` (module arguments).

    ``{{GRP_NO}}`` and friends come from a module instance's resolved arguments (allocators
    applied). Any placeholder with no argument is then dropped by :func:`fill_name` so a stray
    ``{{...}}`` never reaches the user. Labels without ``{{`` are returned unchanged.
    """
    if "{{" not in label:
        return label
    return fill_name(apply_text_args(label, text_args), "")


def _module_text_args(device: Device, program: object) -> dict[str, dict[str, str]]:
    """Per module-instance ``{argument name: value}`` for resolving ``{{Name}}`` placeholders.

    Values come from the device's resolved module arguments (``get_module_instance_arguments``,
    allocators applied), the argument names from the application's module-def ``<Argument>``
    declarations. Keyed by instance id (``MD-1_M-2_MI-1``). Best-effort: any failure yields an
    empty map so a label simply keeps / drops its placeholder instead of crashing the viewer.
    """
    try:
        args_by_iid = device.get_module_instance_arguments()
    except Exception:
        return {}
    if not args_by_iid:
        return {}
    name_by_arg_id: dict[str, str] = {}
    mdefs = getattr(program, "module_defs", None)
    for mdef in getattr(mdefs, "module_def", []) or []:
        container = getattr(mdef, "arguments", None)
        for arg in getattr(container, "argument", []) or []:
            arg_id = str(getattr(arg, "id", "") or "")
            arg_name = str(getattr(arg, "name", "") or "")
            if not arg_id or not arg_name:
                continue
            name_by_arg_id[arg_id] = arg_name
            # get_module_instance_arguments keys args relative to the module ("MD-1_A-3"), so also
            # index by that suffix (the module-def id is the full app-prefixed form).
            pos = arg_id.find("_MD-")
            if pos != -1:
                name_by_arg_id[arg_id[pos + 1 :]] = arg_name
    out: dict[str, dict[str, str]] = {}
    for iid, pairs in args_by_iid.items():
        mapping: dict[str, str] = {}
        for arg_id, value in pairs:
            name = name_by_arg_id.get(str(arg_id))
            if name:
                mapping[name] = str(value)
        if mapping:
            out[iid] = mapping
    return out


_IID_PATTERN = re.compile(r"(MD-[0-9]+_M-[0-9]+_MI-[0-9]+)")


def collect_from_device(
    device: Device, mapping: PluginMapping
) -> list[PluginSectionView]:
    """Walk the device's application program and group every ParameterRef into mapping sections.

    Returns an empty list when the application has no static parameters (e.g. ETS3-only entries
    that never carry a parameter section) or when the device has not resolved its program yet.
    """
    program = device.app.program if device.app is not None else None
    static = getattr(program, "static", None) if program is not None else None
    if static is None:
        return []
    parameters_container = getattr(static, "parameters", None)
    refs_container = getattr(static, "parameter_refs", None)
    if parameters_container is None or refs_container is None:
        return []
    by_id: dict[str, _ParameterLike] = {}
    pt_container = getattr(static, "parameter_types", None)
    pt_list: Iterable[object] = (
        getattr(pt_container, "parameter_type", []) or []
        if pt_container is not None
        else []
    )
    pt_by_id: dict[str, object] = {}
    for pt in pt_list:
        pt_id = str(getattr(pt, "id", "") or "")
        if pt_id:
            pt_by_id[pt_id] = pt
    choice_items: Iterable[object] = getattr(parameters_container, "choice", []) or []
    for item in choice_items:
        # Union: has a .parameter list of UnionParameter; plain Parameter: has .id directly.
        children = getattr(item, "parameter", None)
        if children is not None and isinstance(children, Iterable):
            kids: Iterable[object] = children  # pyright: ignore[reportUnknownVariableType]
            for p in kids:
                pid = str(getattr(p, "id", "") or "")
                if pid:
                    by_id[pid] = p  # type: ignore[assignment]
        else:
            pid = str(getattr(item, "id", "") or "")
            if pid:
                by_id[pid] = item  # type: ignore[assignment]
    # Module-def nested parameters: an application often declares its per-channel/group/button
    # parameters once inside a <ModuleDef> template; the project stores instance refs that resolve
    # back to those template ids with the "_M-x_MI-y" fragment stripped.
    module_defs_container = getattr(program, "module_defs", None)
    module_defs_list: Iterable[object] = (
        getattr(module_defs_container, "module_def", []) or []
        if module_defs_container is not None
        else []
    )
    for mdef in module_defs_list:
        mdef_static = getattr(mdef, "static", None)
        if mdef_static is None:
            continue
        md_params = getattr(mdef_static, "parameters", None)
        if md_params is None:
            continue
        md_choice: Iterable[object] = getattr(md_params, "choice", []) or []
        for item in md_choice:
            children = getattr(item, "parameter", None)
            if children is not None and isinstance(children, Iterable):
                kids: Iterable[object] = children  # pyright: ignore[reportUnknownVariableType]
                for p in kids:
                    pid = str(getattr(p, "id", "") or "")
                    if pid:
                        by_id[pid] = p  # type: ignore[assignment]
            else:
                pid = str(getattr(item, "id", "") or "")
                if pid:
                    by_id[pid] = item  # type: ignore[assignment]
    refs = list(getattr(refs_container, "parameter_ref", []))
    ref_param_ids: set[object] = {getattr(r, "ref_id", None) for r in refs}
    stored: dict[str, str] = {}
    for pir in device.parameter_instance_refs:
        value = getattr(pir, "value", None)
        ref = getattr(pir, "ref_id", None)
        if ref is not None and value is not None:
            stored[str(ref)] = str(value)
    sections_result = build_sections(
        parameters_by_ref_id=by_id,
        parameter_refs=refs,
        stored_values=stored,
        mapping=mapping,
        pt_by_id=pt_by_id,
    )
    # Module instances: the application's parameter_refs only list templates (one per module
    # definition), while the project stores instance refs like "..._MD-N_M-x_MI-y_P-M_R-z". Those
    # never resolve via the template refs, so add synthetic rows for them, resolving the template
    # parameter by stripping the "_M-x_MI-y" fragment.
    text_args_by_iid = _module_text_args(device, program)
    _add_module_instance_rows(
        sections_result,
        device,
        by_id,
        ref_param_ids,
        mapping,
        pt_by_id,
        text_args_by_iid,
    )
    # Vendor BLOB readers (e.g. Siemens N141/03's BLOB_PARA): append the plug-in's own
    # binary-stored sensor / commissioning state as additional read-only sections.
    if mapping.blob_reader:
        sections_result.extend(_read_blob_sections(device, mapping.blob_reader))
    return sections_result


def _read_blob_sections(device: Device, reader_name: str) -> list[PluginSectionView]:
    """Dispatch to a vendor-specific BLOB reader under ``editor_gui.plugin_blob_readers``."""
    try:
        import importlib

        module = importlib.import_module(
            f"editor_gui.plugin_blob_readers.{reader_name}"
        )
    except ImportError:
        return []
    read = getattr(module, "read", None)
    if not callable(read):
        return []
    out: list[PluginSectionView] = []
    for entry in getattr(device, "binary_data", []) or []:
        data = getattr(entry, "data", None)
        if not data:
            continue
        entry_name = str(getattr(entry, "name", "") or "")
        # Readers accept an optional entry name (used by multi-blob vendors to disambiguate a
        # channel and route edits back); older readers that only take data still work.
        try:
            sections = cast(
                "list[PluginSectionView]", _call_reader(read, data, entry_name)
            )
        except Exception:
            continue
        if sections:
            out.extend(sections)
    return out


def _call_reader(read: object, data: bytes, name: str) -> object:
    """Call a blob reader, passing the entry ``name`` only when its signature accepts it.

    Deciding by signature (not by catching ``TypeError``) avoids masking a genuine ``TypeError``
    raised inside a reader that does accept ``name``.
    """
    import inspect

    reader = cast("Callable[..., object]", read)
    try:
        params = inspect.signature(reader).parameters
        accepts_name = "name" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (ValueError, TypeError):
        accepts_name = False
    return reader(data, name=name) if accepts_name else reader(data)


_MODULE_REF_PATTERN = re.compile(
    r"^(.*?)_MD-([0-9]+)_M-[0-9]+_MI-([0-9]+)_((?:U?P)-[0-9]+)_R-[0-9]+$"
)


def _add_module_instance_rows(
    sections: list[PluginSectionView],
    device: Device,
    by_id: dict[str, _ParameterLike],
    ref_param_ids: set[object],
    mapping: PluginMapping,
    pt_by_id: dict[str, object],
    text_args_by_iid: dict[str, dict[str, str]],
) -> None:
    """Attach rows for stored module-instance refs that the template-only pass skipped."""
    section_lookup = {s.section: s for s in sections}
    appended: dict[str, PluginSectionView] = {}
    for pir in device.parameter_instance_refs:
        full_ref = getattr(pir, "ref_id", None)
        value = getattr(pir, "value", None)
        if not full_ref or value is None:
            continue
        if full_ref in ref_param_ids:
            continue
        m = _MODULE_REF_PATTERN.match(str(full_ref))
        if m is None:
            continue
        app_prefix, md, mi, p_part = m.group(1), m.group(2), m.group(3), m.group(4)
        template_pid = f"{app_prefix}_MD-{md}_{p_part}"
        param = by_id.get(template_pid)
        if param is None:
            continue
        name = getattr(param, "name", "") or ""
        if not name:
            continue
        match = mapping.match(name)
        if match is None:
            section_label = _FALLBACK_SECTION
            field_name = name
        else:
            section_label, prefix_end = match
            field_name = name[prefix_end:].lstrip("_") or name
        default_value = _text(param.value)
        text_attr = _text(getattr(param, "text", None))
        base_label = (
            text_attr
            if text_attr and not _is_placeholder_text(text_attr)
            else field_name
        )
        # Resolve {{GRP_NO}} etc. from this instance's module arguments (the group/ECG number),
        # so the user sees "Group 3, Description" instead of the raw template placeholder.
        iid_match = _IID_PATTERN.search(str(full_ref))
        if iid_match is not None:
            base_label = _resolve_placeholders(
                base_label, text_args_by_iid.get(iid_match.group(1), {})
            )
        # Module instances share one template, so prefix the row with the instance index to
        # distinguish e.g. ECG 1 vs ECG 2 within the same section.
        row_label = f"[{mi}] {base_label}"
        row = PluginParameter(
            ref_id=str(full_ref),
            name=name,
            label=row_label,
            value=str(value),
            default_value=default_value,
            section=section_label,
            display_order=int(mi),
            writable=True,
            choices=_enum_choices(param, pt_by_id),
        )
        view = section_lookup.get(section_label)
        if view is None:
            view = appended.get(section_label)
            if view is None:
                view = PluginSectionView(section=section_label)
                appended[section_label] = view
                section_lookup[section_label] = view
        view.parameters.append(row)
    for section_label, view in appended.items():
        if section_label == _FALLBACK_SECTION:
            sections.append(view)
        else:
            # Insert new mapping sections before the fallback bucket if present.
            if sections and sections[-1].section == _FALLBACK_SECTION:
                sections.insert(len(sections) - 1, view)
            else:
                sections.append(view)
    for view in sections:
        view.parameters.sort(key=lambda p: (p.display_order, p.name))


# --- structural protocols used only for type hints (duck-typed; the real objects are from
# ``xknxeditor.namespaces.intermediate``). Kept here so this module does not force an import of the
# large namespace stack when nothing in the GUI happens to touch it.
class _ParameterLike(Protocol):
    name: str | None
    value: str | None
    text: str | None
    access: object | None


class _ParameterRefLike(Protocol):
    id: str
    ref_id: str
    value: str | None
    text: str | None
    access: object | None
    display_order: int | None
