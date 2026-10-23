"""Writer for Siemens N141_21 BLOB_PARA payloads.

The reader returns ``PluginParameter.ref_id`` values shaped like
``siemens_blob:Tag[idx]/Tag[idx]/...`` -- a stable locator path from the top-level child of
``<Blob>`` down to a single leaf. :func:`apply_edit` decodes that path, mutates the ``Value``
attribute of the leaf in a fresh ElementTree parse of the blob, and re-encodes the payload
exactly as ETS writes it (raw-deflate over UTF-16-LE XML, ``<?xml ... encoding="UTF-8"?>``
prolog preserved).

:func:`add_sensor` / :func:`remove_sensor` operate on the structured sensor list the ETS DCA
plug-in exposes under its "Sensors" tree node: fill the first free ``SensorListSensor`` slot
with a Pushbutton / Dual-sensor instance, and populate the matching ``BinaryInputChannel`` /
``PresenceChannel`` / ``BrightnessChannel`` slots so the plug-in sees the sensor configured.

The write path is independent of the reader cache; callers must invalidate their panel/row
caches after a successful edit.
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from editor_gui.plugin_blob_readers.siemens_dali.reader import (
    SENSOR_TYPE_CATALOG,
    parse_ref_id,
)

if TYPE_CHECKING:
    from editor_gui.plugin_blob_readers.siemens_dali.schema import CollectionSpec
    from editor_gui.plugin_params import CollectionBinding

_N141_NS = "http://www.siemens.com/knx/N141_21_Parameters"
_NS = f"{{{_N141_NS}}}"
_IMEXPORT_NS = "http://www.siemens.com/knx/ImExportSchema.xsd"


class EditError(RuntimeError):
    """Raised when a BLOB edit cannot be applied (bad locator, locked element, …)."""


def apply_edit(blob: bytes, ref_id: str, new_value: str) -> bytes:
    """Return a new BLOB bytes payload with the leaf at ``ref_id`` set to ``new_value``.

    ``ref_id`` is a :func:`siemens_dali.reader.read` row's ref_id (``siemens_blob:...``).
    ``new_value`` is the raw vendor token (e.g. ``BI_TRUE``) or a plain scalar; it is written
    verbatim to the ``Value=`` attribute. Raises :class:`EditError` on an invalid locator.
    """
    locator = parse_ref_id(ref_id)
    if locator is None:
        raise EditError(f"not a siemens_blob ref: {ref_id!r}")
    root, blob_root = _decode(blob)
    leaf = _resolve_locator(blob_root, locator)
    if leaf is None:
        raise EditError(f"locator did not resolve: {locator}")
    if leaf.get("Value") is None:
        raise EditError(f"target has no Value attribute: {locator}")
    leaf.set("Value", new_value)
    return _encode(root)


def add_item(
    blob: bytes,
    binding: CollectionBinding,
    type_id: str,
    name: str,
) -> bytes:
    """Generic Add: fill the first free slot of ``binding.container_tag`` with ``name`` /
    ``type_id``. Dispatches to the Siemens-specific allocator for sensor-type side effects
    when the container is ``SensorList``; other containers just mark the slot active."""
    from editor_gui.plugin_blob_readers.siemens_dali.schema import collection_for

    spec = collection_for(binding.container_tag)
    if spec is None:
        raise EditError(f"unknown collection: {binding.container_tag}")
    root, blob_root = _decode(blob)
    container = _resolve_locator(blob_root, binding.container_locator)
    if container is None:
        raise EditError(f"container not found: {binding.container_locator}")
    item = _find_free_item_slot(container, spec)
    if item is None:
        raise EditError("no free slot")
    # Allocate a new sensor-number across the line and apply type-specific side effects.
    line_idx = binding.container_locator[-1][1] if binding.container_locator else 0
    if binding.container_tag == "SensorList":
        used = {
            int(_value(s.find(f"{_NS}{spec.id_field}")) or "0")
            for s in container.findall(f"{_NS}{spec.item_tag}")
        }
        next_nr = max([n for n in used if n > 0], default=0) + 1
        _ensure_children(item, spec.activity_field, *_name_and_type_fields(spec))
        _set_leaf_value(item, spec.id_field, str(next_nr))
        if spec.name_field:
            _set_leaf_value(item, spec.name_field, name)
        if type_id:
            _set_leaf_value(item, "SensorListSensorId", type_id)
        # Side effects per sensor type.
        from editor_gui.plugin_blob_readers.siemens_dali.schema import SENSOR_TYPES

        type_spec = next((t for t in SENSOR_TYPES if t.type_id == type_id), None)
        if type_spec:
            for target_container, count in type_spec.allocates:
                for _ in range(count):
                    _allocate_in_sibling(
                        blob_root, line_idx, target_container, next_nr, name
                    )
    else:
        # Non-sensor container: mark the slot active by stamping its id/name/seed fields.
        _ensure_children(item, spec.activity_field)
        if spec.activity_field != spec.id_field:
            _ensure_children(item, spec.id_field)
        if spec.name_field:
            _ensure_children(item, spec.name_field)
            _set_leaf_value(item, spec.name_field, name)
        # Use "1" as a default activation value when the field is numeric; the enum-case
        # (ConnectedSensors.Active = BI_TRUE) is handled via the "active" value lookup.
        activation = "BI_TRUE" if "Active" in spec.activity_field else "1"
        _set_leaf_value(item, spec.activity_field, activation)
        for field_tag, field_value in spec.seed_fields:
            _ensure_children(item, field_tag)
            _set_leaf_value(item, field_tag, field_value)
    return _encode(root)


def remove_item(blob: bytes, binding: CollectionBinding, item_id: str) -> bytes:
    """Generic Remove: clear every slot whose id_field equals ``item_id`` on the referenced
    container. For SensorList this also frees matching BinaryInput / Presence / Brightness
    channel slots on the same line."""
    from editor_gui.plugin_blob_readers.siemens_dali.schema import collection_for

    spec = collection_for(binding.container_tag)
    if spec is None:
        raise EditError(f"unknown collection: {binding.container_tag}")
    root, blob_root = _decode(blob)
    container = _resolve_locator(blob_root, binding.container_locator)
    if container is None:
        raise EditError(f"container not found: {binding.container_locator}")
    removed = False
    for item in container.findall(f"{_NS}{spec.item_tag}"):
        if _value(item.find(f"{_NS}{spec.id_field}")) != item_id:
            continue
        _set_leaf_value(item, spec.activity_field, spec.activity_inactive_value)
        if spec.id_field != spec.activity_field:
            _set_leaf_value(item, spec.id_field, "0")
        if spec.name_field:
            _set_leaf_value(item, spec.name_field, "")
        if binding.container_tag == "SensorList":
            _set_leaf_value(item, "SensorListSensorId", "")
            line_idx = (
                binding.container_locator[-1][1] if binding.container_locator else 0
            )
            _release_channels(blob_root, line_idx, int(item_id))
        removed = True
        break
    if not removed:
        raise EditError(f"item {item_id} not found in {binding.container_tag}")
    return _encode(root)


def _find_free_item_slot(
    container: ET.Element, spec: CollectionSpec
) -> ET.Element | None:
    for item in container.findall(f"{_NS}{spec.item_tag}"):
        activity = _value(item.find(f"{_NS}{spec.activity_field}"))
        if activity in ("", spec.activity_inactive_value):
            return item
    return None


def _name_and_type_fields(spec: CollectionSpec) -> tuple[str, ...]:
    fields: list[str] = []
    if spec.name_field:
        fields.append(spec.name_field)
    # SensorList carries a type field too.
    if spec.container_tag == "SensorList":
        fields.append("SensorListSensorId")
    return tuple(fields)


def _allocate_in_sibling(
    blob_root: ET.Element,
    line_idx: int,
    container_tag: str,
    sensor_nr: int,
    name: str,
) -> None:
    """Shared helper for sensor side-effects: find the first free slot in a sibling
    collection on ``line_idx`` and stamp it with ``sensor_nr`` (+ name where applicable)."""
    from editor_gui.plugin_blob_readers.siemens_dali.schema import collection_for

    spec = collection_for(container_tag)
    if spec is None:
        return
    siblings = blob_root.findall(f"{_NS}{container_tag}")
    if line_idx >= len(siblings):
        return
    container = siblings[line_idx]
    item = _find_free_item_slot(container, spec)
    if item is None:
        return
    _ensure_children(item, spec.activity_field)
    _set_leaf_value(item, spec.activity_field, str(sensor_nr))
    if spec.name_field:
        _ensure_children(item, spec.name_field)
        _set_leaf_value(item, spec.name_field, name)
    for field_tag, field_value in spec.seed_fields:
        _ensure_children(item, field_tag)
        _set_leaf_value(item, field_tag, field_value)


def add_sensor(
    blob: bytes,
    line_idx: int,
    sensor_name: str,
    sensor_id: str,
) -> bytes:
    """Insert a new sensor into ``line_idx``'s SensorList and populate the matching channel
    slots (BinaryInputChannel for pushbuttons, Presence/BrightnessChannel for combi sensors).

    ``sensor_id`` must be one of :data:`siemens_dali.reader.SENSOR_TYPE_CATALOG` keys. Returns the
    re-encoded blob. Raises :class:`EditError` when the SensorList is full or the sensor type
    cannot be placed (channels full).
    """
    if sensor_id not in SENSOR_TYPE_CATALOG:
        raise EditError(f"unknown sensor id: {sensor_id!r}")
    _name, _addr_count, kinds = SENSOR_TYPE_CATALOG[sensor_id]
    root, blob_root = _decode(blob)
    sensor_lists = blob_root.findall(f"{_NS}SensorList")
    if line_idx >= len(sensor_lists):
        raise EditError(f"line index out of range: {line_idx}")
    sensor_list = sensor_lists[line_idx]

    # 1. First free SensorListSensor slot (SensorListSensorNr Value="0").
    sensor_slot = _find_free_sensor_slot(sensor_list)
    if sensor_slot is None:
        raise EditError("no free sensor slot")

    # Next free sensor number across the line (max + 1).
    used = {
        int(_value(s.find(f"{_NS}SensorListSensorNr")) or "0")
        for s in sensor_list.findall(f"{_NS}SensorListSensor")
    }
    next_nr = max([n for n in used if n > 0], default=0) + 1

    _ensure_children(
        sensor_slot, "SensorListSensorNr", "SensorListSensorName", "SensorListSensorId"
    )
    _set_leaf_value(sensor_slot, "SensorListSensorNr", str(next_nr))
    _set_leaf_value(sensor_slot, "SensorListSensorName", sensor_name)
    _set_leaf_value(sensor_slot, "SensorListSensorId", sensor_id)

    # 2. Populate per-kind channel slots. "b"=binaryInput, "p"=presence, "l"=brightness.
    for kind in kinds:
        if kind == "b":
            _occupy_binary_input(blob_root, line_idx, next_nr)
        elif kind == "p":
            _occupy_presence(blob_root, line_idx, next_nr, sensor_name)
        elif kind == "l":
            _occupy_brightness(blob_root, line_idx, next_nr, sensor_name)
    return _encode(root)


def remove_sensor(blob: bytes, line_idx: int, sensor_nr: int) -> bytes:
    """Clear the SensorList slot and all channel entries bound to ``sensor_nr`` on
    ``line_idx``. Returns the re-encoded blob. Raises :class:`EditError` when ``sensor_nr``
    is 0 or not found on that line."""
    if sensor_nr <= 0:
        raise EditError(f"invalid sensor_nr: {sensor_nr}")
    root, blob_root = _decode(blob)
    sensor_lists = blob_root.findall(f"{_NS}SensorList")
    if line_idx >= len(sensor_lists):
        raise EditError(f"line index out of range: {line_idx}")
    sensor_list = sensor_lists[line_idx]

    for slot in sensor_list.findall(f"{_NS}SensorListSensor"):
        nr_el = slot.find(f"{_NS}SensorListSensorNr")
        if nr_el is None or (nr_el.get("Value") or "0") != str(sensor_nr):
            continue
        _set_leaf_value(slot, "SensorListSensorNr", "0")
        _set_leaf_value(slot, "SensorListSensorName", "")
        _set_leaf_value(slot, "SensorListSensorId", "")
        break
    else:
        raise EditError(f"sensor_nr {sensor_nr} not found on line {line_idx}")

    _release_channels(blob_root, line_idx, sensor_nr)
    return _encode(root)


# -- internals ---------------------------------------------------------------------------


def _decode(blob: bytes) -> tuple[ET.Element, ET.Element]:
    try:
        xml_text = zlib.decompress(blob, -15).decode("utf-16-le")
    except (zlib.error, UnicodeDecodeError) as exc:
        raise EditError(f"blob decompress failed: {exc}") from exc
    ET.register_namespace("n141_21_ImExport", _IMEXPORT_NS)
    ET.register_namespace("n141", _N141_NS)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EditError(f"blob xml parse failed: {exc}") from exc
    blob_root = _find_blob(root)
    if blob_root is None:
        raise EditError("no <Blob> element inside the payload")
    return root, blob_root


def _encode(root: ET.Element) -> bytes:
    out_xml = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    if not out_xml.startswith("<?xml"):
        out_xml = '<?xml version="1.0" encoding="UTF-8"?>' + out_xml
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    return compressor.compress(out_xml.encode("utf-16-le")) + compressor.flush()


def _find_blob(root: ET.Element) -> ET.Element | None:
    for el in root.iter():
        if el.tag == f"{_NS}Blob":
            return el
    return None


def _resolve_locator(
    blob_root: ET.Element, locator: tuple[tuple[str, int], ...]
) -> ET.Element | None:
    cur = blob_root
    for tag, idx in locator:
        candidates = [c for c in cur if _local(c) == tag]
        if idx >= len(candidates):
            return None
        cur = candidates[idx]
    return cur


def _local(el: ET.Element) -> str:
    tag = el.tag
    if not callable(tag) and tag.startswith(_NS):
        return tag[len(_NS) :]
    return str(tag)


def _value(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return el.get("Value", "")


def _find_free_sensor_slot(sensor_list: ET.Element) -> ET.Element | None:
    for slot in sensor_list.findall(f"{_NS}SensorListSensor"):
        nr_el = slot.find(f"{_NS}SensorListSensorNr")
        if nr_el is None or (nr_el.get("Value") or "0") == "0":
            return slot
    return None


def _ensure_children(parent: ET.Element, *tags: str) -> None:
    """ETS sometimes leaves empty sensor slots as ``<SensorListSensor />`` self-closed with
    no children. Materialize the three expected children (SensorListSensor{Nr,Name,Id}) so
    the ``Value=`` attribute can be set."""
    for tag in tags:
        if parent.find(f"{_NS}{tag}") is None:
            el = ET.SubElement(parent, f"{_NS}{tag}")
            el.set("Value", "")


def _set_leaf_value(parent: ET.Element, tag: str, value: str) -> None:
    el = parent.find(f"{_NS}{tag}")
    if el is None:
        el = ET.SubElement(parent, f"{_NS}{tag}")
    el.set("Value", value)


def _occupy_binary_input(blob_root: ET.Element, line_idx: int, sensor_nr: int) -> None:
    """Occupy ONE free BinaryInputChannel slot on ``line_idx``. The caller invokes this once
    per button kind on the sensor type (``kinds="bbbb"`` → called four times for a 4-fold
    pushbutton), so the counter stays here per invocation."""
    binary_inputs = blob_root.findall(f"{_NS}BinaryInput")
    if line_idx >= len(binary_inputs):
        return
    bi = binary_inputs[line_idx]
    for ch in bi.findall(f"{_NS}BinaryInputChannel"):
        nr_el = ch.find(f"{_NS}BinaryInputChannelSensorNr")
        if nr_el is not None and (nr_el.get("Value") or "0") != "0":
            continue
        _ensure_children(
            ch, "BinaryInputChannelSensorNr", "BinaryInputChannelLongPressTime"
        )
        _set_leaf_value(ch, "BinaryInputChannelSensorNr", str(sensor_nr))
        _set_leaf_value(ch, "BinaryInputChannelLongPressTime", "5")
        return


def _occupy_presence(
    blob_root: ET.Element, line_idx: int, sensor_nr: int, name: str
) -> None:
    presences = blob_root.findall(f"{_NS}Presence")
    if line_idx >= len(presences):
        return
    pres = presences[line_idx]
    for ch in pres.findall(f"{_NS}PresenceChannel"):
        nr_el = ch.find(f"{_NS}PresenceChannelSensorNr")
        if nr_el is not None and (nr_el.get("Value") or "0") != "0":
            continue
        _ensure_children(
            ch,
            "PresenceChannelName",
            "PresenceChannelSensorNr",
            "PresenceChannelChannelNr",
        )
        _set_leaf_value(ch, "PresenceChannelName", name or "Presence")
        _set_leaf_value(ch, "PresenceChannelSensorNr", str(sensor_nr))
        _set_leaf_value(ch, "PresenceChannelChannelNr", "1")
        return


def _occupy_brightness(
    blob_root: ET.Element, line_idx: int, sensor_nr: int, name: str
) -> None:
    brights = blob_root.findall(f"{_NS}Brightness")
    if line_idx >= len(brights):
        return
    bri = brights[line_idx]
    for ch in bri.findall(f"{_NS}BrightnessChannel"):
        nr_el = ch.find(f"{_NS}BrightnessChannelSensorNr")
        if nr_el is not None and (nr_el.get("Value") or "0") != "0":
            continue
        _ensure_children(
            ch,
            "BrightnessChannelName",
            "BrightnessChannelSensorNr",
            "BrightnessChannelChannelNr",
        )
        _set_leaf_value(ch, "BrightnessChannelName", name or "Brightness")
        _set_leaf_value(ch, "BrightnessChannelSensorNr", str(sensor_nr))
        _set_leaf_value(ch, "BrightnessChannelChannelNr", "1")
        return


def _release_channels(blob_root: ET.Element, line_idx: int, sensor_nr: int) -> None:
    """Clear every PresenceChannel / BrightnessChannel / BinaryInputChannel on ``line_idx``
    whose SensorNr equals ``sensor_nr``."""
    for section_tag, child_tag, sensor_field in (
        ("Presence", "PresenceChannel", "PresenceChannelSensorNr"),
        ("Brightness", "BrightnessChannel", "BrightnessChannelSensorNr"),
        ("BinaryInput", "BinaryInputChannel", "BinaryInputChannelSensorNr"),
    ):
        sections = blob_root.findall(f"{_NS}{section_tag}")
        if line_idx >= len(sections):
            continue
        for ch in sections[line_idx].findall(f"{_NS}{child_tag}"):
            nr_el = ch.find(f"{_NS}{sensor_field}")
            if nr_el is None:
                continue
            if (nr_el.get("Value") or "0") != str(sensor_nr):
                continue
            nr_el.set("Value", "0")
