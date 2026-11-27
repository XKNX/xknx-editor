"""Group application parameters and saved plugin data into editable vendor sections."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.prod.parser_v2._name import apply_text_args, fill_name

if TYPE_CHECKING:
    from editor_gui.device import Device
    from xknxeditor.namespaces.intermediate.parameter_base_t import ParameterBase
    from xknxeditor.namespaces.intermediate.parameter_ref_t import ParameterRef

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
    provider: str = ""
    plugin_ids: tuple[str, ...] = ()
    blob_names: tuple[str, ...] = ()

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
    writable: bool  # Writes use the standard parameter state.
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
    raw_value: str | None = None


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
    collapsed: bool = False


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
        prefixes_raw = [data.get("app_id_prefix", "")]
    prefixes = tuple(str(p) for p in prefixes_raw if p)
    if not prefixes and not data.get("plugin_ids"):
        raise KeyError("app_id_prefix(es) missing")
    return PluginMapping(
        app_id_prefixes=prefixes,
        title=data.get("title", ""),
        patterns=patterns,
        blob_reader=str(data.get("blob_reader", "")),
        provider=str(data.get("provider", "")),
        plugin_ids=tuple(str(value).upper() for value in data.get("plugin_ids", [])),
        blob_names=tuple(str(value) for value in data.get("blob_names", [])),
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


def mapping_for_device(device: Device) -> PluginMapping | None:
    app = getattr(device, "app", None)
    if app is None:
        return None
    extension = getattr(
        getattr(getattr(app, "program", None), "static", None), "extension", None
    )
    identifiers = {
        str(getattr(extension, field, "")).upper()
        for field in ("ets_download_plugin", "ets_ui_plugin", "ets_data_handler")
    }
    blob_names = {row.name for row in getattr(device, "binary_data", ())}
    mapping = mapping_for(getattr(app, "id", None))
    # A mapping of a plug-in applies to an application of its numbers only when the
    # application names the plug-in, or the device holds its configuration: another version
    # with the same number (an older one without the plug-in) is programmed as it is.
    if mapping is not None and (
        not mapping.plugin_ids
        or identifiers.intersection(mapping.plugin_ids)
        or blob_names.intersection(mapping.blob_names)
    ):
        return mapping
    for path in sorted(_MAPPINGS_DIR.glob("*.json")):
        try:
            candidate = _load_mapping(str(path))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        if identifiers.intersection(candidate.plugin_ids) or blob_names.intersection(
            candidate.blob_names
        ):
            return candidate
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
    parameters_by_ref_id: Mapping[str, ParameterBase],
    parameter_refs: Iterable[ParameterRef],
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
        name = getattr(ref, "name", None) or getattr(param, "name", "") or ""
        if not name:
            continue
        match = mapping.match(name)
        if match is None:
            section_label = _FALLBACK_SECTION
            field_name = name
        else:
            section_label, prefix_end = match
            field_name = name[prefix_end:].lstrip("_") or name
        default_value = _text(param.value if ref.value is None else ref.value)
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
        writable = access is Access.READ_WRITE
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
    pending = list(getattr(mdefs, "module_def", []) or [])
    while pending:
        mdef = pending.pop()
        nested = getattr(mdef, "sub_module_defs", None)
        pending.extend(getattr(nested, "module_def", []) or [])
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
            instance = _IID_PATTERN.search(iid)
            out[instance.group(1) if instance is not None else iid] = mapping
    return out


_IID_PATTERN = re.compile(r"(MD-[^_]+_M-[^_]+_MI-[^_]+(?:_SM-[^_]+_M-[^_]+_MI-[^_]+)*)")


def collect_from_device(
    device: Device, mapping: PluginMapping
) -> list[PluginSectionView]:
    """Group every parameter, including unset module fields, and append saved plugin data."""
    if mapping.provider:
        from editor_gui.plugin_providers import provider_named

        return provider_named(mapping.provider).sections(device)
    sections_result: list[PluginSectionView] = []
    if mapping.patterns and device.app is not None:
        from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

        dynamic = device.dynamic_ui
        program = device.app.program
        index = dynamic.indexer if dynamic is not None else ApplicationIndexer(program)
        refs = (
            dynamic.parameter_ref_ids()
            if dynamic is not None
            else [ref for ref in index.parameter_refs if index.ref_owner[ref] is None]
        )
        stored = {
            ref.ref_id: ref.value
            for ref in device.parameter_instance_refs
            if ref.value is not None
        }
        text_args = _module_text_args(device, program) if dynamic is not None else {}
        resolved: list[ParameterRef] = []
        values: dict[str, str] = {}
        for full_ref in refs:
            local = dynamic.local_ref_id(full_ref) if dynamic is not None else full_ref
            ref = index.parameter_refs.get(local)
            if ref is None:
                continue
            parameter = index.parameters.get(ref.ref_id)
            if parameter is None:
                continue
            name = ref.name or parameter.name or ""
            label = ref.text or parameter.text or ""
            instance = _IID_PATTERN.search(full_ref)
            if instance is not None:
                arguments = text_args.get(instance.group(1), {})
                if not label or _is_placeholder_text(label):
                    match = mapping.match(name)
                    label = name[match[1] :].lstrip("_") or name if match else name
                label = _resolve_placeholders(label, arguments)
                counters = "/".join(re.findall(r"_MI-([^_]+)", instance.group(1)))
                label = f"[{counters}] {label}"
            resolved.append(replace(ref, id=full_ref, name=name, text=label))
            value = (
                dynamic.get_parameter_ref(full_ref)
                if dynamic is not None
                else stored.get(full_ref, index.default_value(local))
            )
            if value is not None:
                values[full_ref] = value
        sections_result = build_sections(
            parameters_by_ref_id=index.parameters,
            parameter_refs=resolved,
            stored_values=values,
            mapping=mapping,
            pt_by_id=dict(index.parameter_types),
        )
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
