"""BLOB_PARA reader for the Siemens N141/03 / N141/21 classic DALI gateways.

The Siemens DCA plug-in stores its full sensor / presence / brightness / ECG commissioning
state in a single ``BLOB_PARA`` entry under ``device_binary_data``: raw-deflate
(negative-window zlib) over UTF-16-LE XML in the ``n141_21_ImExport`` namespace. This
module decodes that blob into a flat list of ``PluginSectionView`` rows the generic viewer
can render; rows are read-only here (writable=False) because round-tripping a change back
to the ETS plug-in's binary format needs its own write pass.

Every leaf in the blob is surfaced -- the viewer does not pick which fields are "relevant".
Repeated tags inside a single container (ETS stores dual-channel presence configs flat
with duplicated tag names) are disambiguated with a ``#N`` suffix on the row label.

The XML schema is the same for the single-channel N141/03 and the two-channel N141/21: the
top-level ``<Blob>`` carries most sub-sections twice (channel/line 0 and 1). We treat them
uniformly as "Line {idx}" sections.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from xml.etree import ElementTree as ET

from editor_gui.plugin_blob_readers.siemens_dali.schema import (
    COLLECTIONS,
    CollectionSpec,
    choices_for_element,
    humanize_token,
    resolve_token,
)

if TYPE_CHECKING:
    from editor_gui.plugin_params import CollectionBinding, PluginSectionView


class _Push(Protocol):
    def __call__(
        self,
        label: str,
        rows: list[_Row],
        prefix: str = ...,
        collection: CollectionBinding | None = ...,
    ) -> None: ...


_N141_NS = "http://www.siemens.com/knx/N141_21_Parameters"
_NS = f"{{{_N141_NS}}}"


# Inlined sensor-type catalog (id -> name, address count, channel kinds). The data is kept in
# the module as compact tuples instead of shipping an external XML asset.
# Channel kinds follow the vendor DLL: "b" = binaryInput, "p" = presence, "l" = brightness.
SENSOR_TYPE_CATALOG: dict[str, tuple[str, int, str]] = {
    "id_03A3C2014B74": ("DALI pushbutton input 4-fold (5WG1 141-2AB71)", 4, "bbbb"),
    "id_03A54294358D": ("DALI PRO PB Coupler (EAN 4008321496461)", 4, "bbbb"),
    "id_03A3C20175BA": ("DALI combi sensor office (5WG1 141-2AB51)", 2, "pl"),
    "id_03A542926BC5": ("DALI PRO Sensor Coupler (EAN 4008321379269)", 2, "pl"),
    "id_03A542987234": ("DALI HIGHBAY Adapter (EAN 4008321774132)", 2, "pl"),
    "id_03AFA39B9A72": ("DALI LS/PD LI (EAN 4052899043954)", 2, "pl"),
    "id_03AFA3A920B4": ("DALI LS/PD CI (EAN 4052899930292)", 2, "pl"),
    "id_03AFA39D1860": ("DALI Sensorcoupler HF LS LI (EAN 4052899141728)", 2, "pl"),
    "id_03AFA39E731B": ("DALI Sensor Coupler E (EAN 4052899230491)", 2, "pl"),
    "id_0": ("DALI motion detector (6 mA)", 2, "pl"),
}


def sensor_type_label(sensor_id: str) -> str:
    """Human-readable sensor-type name (plus kind summary) for ``sensor_id``, falling back to
    the raw id if unknown."""
    entry = SENSOR_TYPE_CATALOG.get(sensor_id)
    if entry is None:
        return sensor_id or "(unknown)"
    name, _addr, kinds = entry
    kind_desc = {"b": "button", "p": "presence", "l": "brightness"}
    chan_desc = "+".join(sorted({kind_desc[k] for k in kinds}))
    return f"{name} [{chan_desc}]"


@dataclass(frozen=True, slots=True)
class _Row:
    label: str
    value: str
    # Optional (raw_token, display_label) pairs for enum fields; empty for plain scalars.
    choices: tuple[tuple[str, str], ...] = ()
    # Full XML tag (e.g. "PresenceChannelLockMode") so a future write path can look it up.
    tag: str = ""
    # Stable locator path from <Blob> to this leaf, e.g.
    # ("DaliDevice", 0, "DaliDeviceBehaviourAfterDownload", 0). Pairs of (tag, index); the
    # index disambiguates repeated siblings. Writer tools follow this path to apply edits.
    locator: tuple[tuple[str, int], ...] = ()


def read(binary_data: bytes) -> list[PluginSectionView]:
    """Decode a Siemens N141_21 BLOB_PARA payload into read-only sections."""
    from editor_gui.plugin_params import PluginParameter, PluginSectionView

    try:
        xml_text = zlib.decompress(binary_data, -15).decode("utf-16-le")
    except (zlib.error, UnicodeDecodeError):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    blob = _find_blob(root)
    if blob is None:
        return []

    # Build a parent-map so each leaf can report its locator path back to <Blob>.
    parent_map: dict[int, tuple[ET.Element, int]] = {}

    def _index_children(parent: ET.Element) -> None:
        counts: dict[str, int] = {}
        for child in parent:
            if callable(child.tag):  # skip comment/PI nodes
                continue
            idx = counts.get(child.tag, 0)
            counts[child.tag] = idx + 1
            parent_map[id(child)] = (parent, idx)
            _index_children(child)

    _index_children(blob)

    sections: list[PluginSectionView] = []

    def push(
        label: str,
        rows: list[_Row],
        prefix: str = "siemens_blob",
        collection: CollectionBinding | None = None,
    ) -> None:
        if not rows and collection is None:
            return
        params = [
            PluginParameter(
                ref_id=_locator_ref_id(prefix, r.locator)
                if r.locator
                else f"{prefix}:{label}:{i}:{r.label}",
                name=r.label,
                label=r.label,
                value=r.value,
                default_value="",
                section=label,
                display_order=i,
                # BLOB rows backed by a locator are writable through the dedicated writer
                # (editor_gui.plugin_blob_readers.siemens_dali.writer.apply_edit); without a
                # locator the row is read-only (synthetic rows like separators).
                writable=bool(r.locator),
                choices=r.choices,
            )
            for i, r in enumerate(rows)
        ]
        sections.append(
            PluginSectionView(section=label, parameters=params, collection=collection)
        )

    # Device-level: every leaf below DaliDevice except the per-line children (those get
    # their own sections below).
    dali_device = blob.find(f"{_NS}DaliDevice")
    if dali_device is not None:
        push(
            "Siemens Device (from DCA blob)",
            _all_rows(
                dali_device,
                parent_map,
                blob,
                prefix_strip="DaliDevice",
                skip={"DaliDeviceLine"},
            ),
        )
        for line_idx, line in enumerate(dali_device.findall(f"{_NS}DaliDeviceLine")):
            push(
                f"Siemens Line {line_idx} (from DCA blob)",
                _all_rows(line, parent_map, blob, prefix_strip="DaliDeviceLine"),
            )

    # SystemParameters0 and the single-occurrence sub-sections (Rtc, ScenePluginInfo, Effects,
    # LightControlTwoPoint, LightControlConstant, TimeSwitch) each get their own section.
    for tag in (
        "SystemParameters0",
        "Rtc",
        "ScenePluginInfo",
        "Effects",
        "LightControlTwoPoint",
        "LightControlConstant",
        "TimeSwitch",
    ):
        el = blob.find(f"{_NS}{tag}")
        if el is not None:
            push(
                f"Siemens {tag} (from DCA blob)",
                _all_rows(el, parent_map, blob, prefix_strip=tag),
            )

    # Generic collection rendering: one section per (line, collection) pair, with a
    # CollectionBinding that lets the panel surface Add/Remove without hard-coding.
    _emit_collections(blob, push, parent_map)

    # Catch-all for the remaining top-level containers (Group, GenericEcg, DevType0, DevType1,
    # DevType8, DevType8group, Minergy, Scenes). Each occurrence is emitted as its own line
    # section so the user can scroll through everything.
    _emit_generic_per_line(blob, push, parent_map)
    return sections


def _emit_collections(
    blob: ET.Element,
    push: _Push,
    parent_map: dict[int, tuple[ET.Element, int]],
) -> None:
    """Emit a per-line section for every registered Collection + a CollectionBinding so the
    panel can offer Add/Remove generically. Each occupied slot becomes a row (summary for
    sensor-type lists) or its own section (for channel-detail collections like Presence).
    """
    from editor_gui.plugin_params import CollectionBinding

    for container_tag, spec in COLLECTIONS.items():
        for line_idx, container in enumerate(blob.findall(f"{_NS}{container_tag}")):
            rows: list[_Row] = []
            for item_idx, item in enumerate(container.findall(f"{_NS}{spec.item_tag}")):
                activity = _value(item.find(f"{_NS}{spec.activity_field}"))
                if activity in ("", spec.activity_inactive_value):
                    continue
                nr = _value(item.find(f"{_NS}{spec.id_field}"))
                name = (
                    _value(item.find(f"{_NS}{spec.name_field}"))
                    if spec.name_field
                    else ""
                )
                # Summary label: "#nr: name" when both, else whichever is present.
                if nr and name:
                    label = f"#{nr}: {name}"
                elif nr:
                    label = f"#{nr}"
                elif name:
                    label = name
                else:
                    label = f"slot {item_idx}"
                # The row-level value is a brief descriptor; the SensorList uses the sensor
                # type while most other collections just show the activity field value.
                if container_tag == "SensorList":
                    sid = _value(item.find(f"{_NS}SensorListSensorId"))
                    value = sensor_type_label(sid)
                else:
                    value = _humanize_summary(item, spec)
                item_locator = _locator_of(item, parent_map, blob)
                rows.append(
                    _Row(
                        label=label,
                        value=value,
                        tag=spec.item_tag,
                        locator=item_locator,
                    )
                )
            container_locator = _locator_of(container, parent_map, blob)
            item_types = tuple((t.type_id, t.label) for t in spec.item_types)
            binding = CollectionBinding(
                container_tag=container_tag,
                display_label=spec.display_label,
                container_locator=container_locator,
                item_types=item_types,
                needs_name=spec.name_field is not None,
            )
            push(
                f"Siemens Line {line_idx} - {spec.display_label} (from DCA blob)",
                rows,
                collection=binding,
            )


def _humanize_summary(item: ET.Element, spec: CollectionSpec) -> str:
    """Short descriptor for a Collection item when it has no sensor-type attached."""
    parts: list[str] = []
    for leaf in item:
        if callable(leaf.tag) or not leaf.tag.startswith(_NS):
            continue
        raw_tag = leaf.tag[len(_NS) :]
        raw_val = _value(leaf)
        if not raw_val or raw_val in ("0", spec.activity_inactive_value):
            continue
        # Skip the activity/id/name fields — they're already in the row label.
        if raw_tag in (spec.activity_field, spec.id_field, spec.name_field):
            continue
        parts.append(
            f"{raw_tag[len(spec.item_tag) :].lstrip('_') or raw_tag}={raw_val}"
        )
        if len(parts) >= 3:
            break
    return "  ".join(parts) or "(no details)"


def _emit_generic_per_line(
    blob: ET.Element,
    push: _Push,
    parent_map: dict[int, tuple[ET.Element, int]],
) -> None:
    """Dump every top-level per-line container we did not special-case above."""
    handled = {
        "DaliDevice",
        "SystemParameters0",
        "Rtc",
        "ScenePluginInfo",
        "Effects",
        "LightControlTwoPoint",
        "LightControlConstant",
        "TimeSwitch",
        "SensorList",
        "Presence",
        "Brightness",
        "ConnectedSensors",
        "ConnectedEcgs",
        "FoundSensors",
        "FoundEcgs",
        "BinaryInput",
    }
    counts: dict[str, int] = {}
    for child in blob:
        if callable(child.tag) or not child.tag.startswith(_NS):
            continue
        tag = child.tag[len(_NS) :]
        if tag in handled:
            continue
        idx = counts.get(tag, 0)
        counts[tag] = idx + 1
        rows = _all_rows(child, parent_map, blob, prefix_strip=tag)
        if rows:
            push(f"Siemens Line {idx} - {tag} (from DCA blob)", rows)


def _all_rows(
    container: ET.Element,
    parent_map: dict[int, tuple[ET.Element, int]],
    blob_root: ET.Element,
    *,
    prefix_strip: str = "",
    skip: set[str] | None = None,
    flatten: bool = True,
) -> list[_Row]:
    """Return every leaf under ``container`` as a (label, value) row.

    ``prefix_strip`` is stripped from the start of each tag before it becomes the row label.
    Repeated tags (ETS sometimes stores two parallel sets of fields in the same container
    for dual-channel presence/brightness configs) are disambiguated with a ``#N`` suffix so
    both copies stay visible. Enum tokens are translated via the schema catalog extracted
    from the plug-in DLLs; non-enum fields (numbers, strings) pass through unchanged.
    ``skip`` excludes a tag (e.g. nested container that gets its own section).
    ``flatten=False`` keeps the raw tag suffix without stripping. ``parent_map`` + ``blob_root``
    are used to compute each leaf's locator path back to <Blob> so a writer can round-trip
    edits.
    """
    skip = skip or set()
    counts: dict[str, int] = {}
    rows: list[_Row] = []
    for leaf, path in _walk(container, (), skip):
        raw_tag = str(leaf.tag)[len(_NS) :]
        suffix = raw_tag
        if flatten and prefix_strip and suffix.startswith(prefix_strip):
            suffix = suffix[len(prefix_strip) :]
        if not suffix:
            suffix = raw_tag
        label = _humanize(suffix)
        if path:
            label = f"{' / '.join(path)} / {label}"
        occurrence = counts.get(label, 0) + 1
        counts[label] = occurrence
        if occurrence > 1:
            label = f"{label} #{occurrence}"
        raw = _value(leaf)
        display = resolve_token(raw_tag, raw)
        enum_choices = choices_for_element(raw_tag)
        choices: tuple[tuple[str, str], ...] = ()
        if enum_choices is not None:
            choices = tuple((tok, humanize_token(tok)) for tok in enum_choices)
        locator = _locator_of(leaf, parent_map, blob_root)
        rows.append(
            _Row(
                label=label,
                value=display,
                choices=choices,
                tag=raw_tag,
                locator=locator,
            )
        )
    return rows


def _locator_of(
    leaf: ET.Element,
    parent_map: dict[int, tuple[ET.Element, int]],
    blob_root: ET.Element,
) -> tuple[tuple[str, int], ...]:
    """Walk up from ``leaf`` to ``blob_root`` via ``parent_map`` and return a stable path
    ``((tag, idx), …)`` from the top-level child of <Blob> down to the leaf. The root <Blob>
    is NOT included so the first component is the leaf's section (e.g. ``DaliDevice``)."""
    path: list[tuple[str, int]] = []
    cur = leaf
    while True:
        entry = parent_map.get(id(cur))
        if entry is None:
            break
        parent, idx = entry
        if callable(cur.tag):  # skip comment/PI nodes
            break
        tag = cur.tag[len(_NS) :] if cur.tag.startswith(_NS) else cur.tag
        path.append((tag, idx))
        if parent is blob_root:
            break
        cur = parent
    path.reverse()
    return tuple(path)


def _locator_ref_id(prefix: str, locator: tuple[tuple[str, int], ...]) -> str:
    """Encode a locator path into a ref_id the writer can parse back."""
    parts = [f"{tag}[{idx}]" for tag, idx in locator]
    return f"{prefix}:" + "/".join(parts)


def parse_ref_id(ref_id: str) -> tuple[tuple[str, int], ...] | None:
    """Decode a ``siemens_blob:tag[i]/tag[j]/...`` ref_id back into a locator path."""
    if not ref_id.startswith("siemens_blob:"):
        return None
    body = ref_id[len("siemens_blob:") :]
    if not body or ("/" not in body and "[" not in body):
        return None
    out: list[tuple[str, int]] = []
    for part in body.split("/"):
        if "[" not in part or not part.endswith("]"):
            return None
        tag, _, idx_str = part[:-1].partition("[")
        if not idx_str.isdigit():
            return None
        out.append((tag, int(idx_str)))
    return tuple(out)


_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _humanize(tag: str) -> str:
    """Split a CamelCase/PascalCase tag into spaced words for readable row labels."""
    return _CAMEL_SPLIT.sub(" ", tag).strip() or tag


def _walk(
    node: ET.Element, path: tuple[str, ...], skip: set[str]
) -> Iterator[tuple[ET.Element, tuple[str, ...]]]:
    for child in node:
        if not child.tag.startswith(_NS):
            continue
        local = child.tag[len(_NS) :]
        if local in skip:
            continue
        if child.get("Value") is not None:
            yield child, path
        elif len(child) > 0:
            yield from _walk(child, (*path, _humanize(local)), skip)


def _find_blob(root: ET.Element) -> ET.Element | None:
    for el in root.iter():
        if el.tag == f"{_NS}Blob":
            return el
    return None


def _value(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return el.get("Value", "")
