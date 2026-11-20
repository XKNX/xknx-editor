"""Edit Siemens DALI configuration blobs and their dependent device settings."""

from __future__ import annotations

import json
import zlib
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from editor_gui.plugin_blob_readers.siemens_dali.reader import (
    parse_ref_id,
)

if TYPE_CHECKING:
    from editor_gui.plugin_blob_readers.siemens_dali.schema import (
        CollectionSpec,
        ItemTypeSpec,
    )
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
    old_value = leaf.get("Value", "")
    leaf.set("Value", new_value)
    _propagate_edit(blob_root, locator, old_value, new_value)
    return _encode(root)


_GROUP_FIELDS = {
    "BehaviourEmergencyOff",
    "BehaviourVoltageOff",
    "BehaviourVoltageOn",
    "BehaviourAfterBurnin",
    "EmergencyValue",
    "EmergencyValueMode",
    "MaxValue",
    "MinValue",
    "Mode",
    "OnOffPossibleDimm",
    "OnOffPossibleSetValue",
    "OnTime1",
    "OnTime2",
    "StartupValue",
    "StartupValueNight",
    "StartupMode",
    "TakeValueBehaviour",
    "TimeDimm",
    "TimeSetValue",
    "TimeSwitch",
    "ValueAfter1",
    "WarnBeforeOff",
    "IgnitionTime",
}
_DT8_GROUP_FIELDS = {
    "Enabled",
    "MinColourTemperature",
    "MaxColourTemperature",
    "TimeDimm",
    "TimeSetValue",
    "StartupMode",
    "StartupValue",
    "StartupValueNight",
    "EmergencyValueMode",
    "EmergencyValue",
    "BehaviourVoltageOff",
    "ValueVoltageOff",
    "BehaviourVoltageOn",
    "ValueVoltageOn",
    "BehaviourEmergencyOff",
    "ValueEmergencyOff",
}
_GROUP_COPY_FIELDS = {
    "Group": _GROUP_FIELDS,
    "DevType8group": _DT8_GROUP_FIELDS,
    "Minergy": {"Asignment"},
}


def _address(value: str, limit: int, label: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise EditError(f"Invalid {label}") from exc
    if not (0 <= number < limit or number == 255):
        raise EditError(f"Invalid {label}")
    return number


def _sensor_number(value: str) -> int:
    try:
        number = int(value or "0")
    except ValueError as exc:
        raise EditError("Invalid Siemens sensor number") from exc
    if not 0 <= number <= 64:
        raise EditError("Invalid Siemens sensor number")
    return number


def _propagate_edit(
    blob: ET.Element,
    locator: tuple[tuple[str, int], ...],
    old_value: str,
    value: str,
) -> None:
    """Keep shared group and scene settings in their device property slots."""
    if len(locator) != 3:
        return
    (section, line), (item, index), (field, occurrence) = locator
    if section == "ScenePluginInfo" and item == "ScenePluginInfoScene":
        if field not in (
            "ScenePluginInfoSceneKnxSceneNumber",
            "ScenePluginInfoSceneFadeTime",
        ):
            return
        new_number = (
            _address(value, 64, "KNX scene number")
            if field.endswith("KnxSceneNumber")
            else None
        )
        entry = _resolve_locator(blob, locator[:2])
        if entry is None:
            return
        number = (
            old_value
            if field.endswith("KnxSceneNumber")
            else _value(entry.find(_NS + "ScenePluginInfoSceneKnxSceneNumber"))
        )
        try:
            old_number = int(number)
        except ValueError:
            return
        if not 0 <= old_number < 64:
            return
        for scene in blob.findall(_NS + "Scenes/" + _NS + "ScenesScene"):
            config = scene.find(_NS + "ScenesSceneSceneNumber")
            if config is None:
                continue
            encoded = _address(config.get("Value", "255"), 256, "DALI scene number")
            if encoded & 64 or encoded & 63 != old_number:
                continue
            if new_number is not None:
                config.set(
                    "Value",
                    "255" if new_number == 255 else str((encoded & 128) | new_number),
                )
            else:
                _set_leaf_value(scene, "ScenesSceneFadeTime", value)
        return
    ecgs = _resolve_locator(blob, (("GenericEcg", line),))
    if ecgs is None:
        return
    if section == "GenericEcg" and field == "GenericEcgEcgGroupAddress":
        group = _address(value, 16, "DALI group address")
        if group != 255:
            _copy_group_to_ecg(blob, line, group, index)
        return
    source_prefix, target_section, target_item = {
        "Group": ("GroupGroup", "GenericEcg", "GenericEcgEcg"),
        "DevType8group": ("DevType8groupGroup", "DevType8", "DevType8Ecg"),
        "Minergy": ("MinergyGroup", "Minergy", "MinergyEcg"),
        "Scenes": ("ScenesSceneValueGroup", "Scenes", "ScenesSceneValue"),
    }.get(section, ("", "", ""))
    if not source_prefix or not field.startswith(source_prefix):
        return
    if (
        section != "Scenes"
        and field[len(source_prefix) :] not in _GROUP_COPY_FIELDS[section]
    ):
        return
    group = occurrence if section == "Scenes" else index
    for ecg_index, ecg in enumerate(ecgs.findall(_NS + "GenericEcgEcg")):
        if _value(ecg.find(_NS + "GenericEcgEcgGroupAddress")) != str(group):
            continue
        target = (
            (("Scenes", line), ("ScenesScene", index), (target_item, ecg_index))
            if section == "Scenes"
            else (
                (target_section, line),
                (target_item, ecg_index),
                (target_item + field[len(source_prefix) :], occurrence),
            )
        )
        leaf = _resolve_locator(blob, target)
        if leaf is not None:
            leaf.set("Value", value)


def _copy_group_to_ecg(blob: ET.Element, line: int, group: int, ecg: int) -> None:
    for section, item, target_section, target_item in (
        ("Group", "GroupGroup", "GenericEcg", "GenericEcgEcg"),
        ("DevType8group", "DevType8groupGroup", "DevType8", "DevType8Ecg"),
        ("Minergy", "MinergyGroup", "Minergy", "MinergyEcg"),
    ):
        source = _resolve_locator(blob, ((section, line), (item, group)))
        target = _resolve_locator(blob, ((target_section, line), (target_item, ecg)))
        if source is None or target is None:
            continue
        for leaf in source:
            suffix = leaf.tag.removeprefix(_NS + item)
            if suffix not in _GROUP_COPY_FIELDS[section]:
                continue
            dest = target.find(_NS + target_item + suffix)
            if dest is not None and leaf.get("Value") is not None:
                dest.set("Value", leaf.get("Value", ""))
    scenes = _resolve_locator(blob, (("Scenes", line),))
    if scenes is not None:
        for scene in scenes.findall(_NS + "ScenesScene"):
            groups = scene.findall(_NS + "ScenesSceneValueGroup")
            values = scene.findall(_NS + "ScenesSceneValue")
            if group < len(groups) and ecg < len(values):
                values[ecg].set("Value", groups[group].get("Value", "00000000"))


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
        from editor_gui.plugin_blob_readers.siemens_dali.schema import SENSOR_TYPES

        type_spec = next((t for t in SENSOR_TYPES if t.type_id == type_id), None)
        if type_spec is None:
            raise EditError("Unknown Siemens sensor type")
        _check_sensor_capacity(blob_root, line_idx, container, type_spec)
        used = {
            _sensor_number(_value(s.find(f"{_NS}{spec.id_field}")))
            for s in container.findall(f"{_NS}{spec.item_tag}")
        }
        next_nr = container.findall(_NS + spec.item_tag).index(item) + 1
        if next_nr in used:
            raise EditError("Duplicate sensor number")
        _ensure_children(item, spec.activity_field, *_name_and_type_fields(spec))
        _set_leaf_value(item, spec.id_field, str(next_nr))
        if spec.name_field:
            _set_leaf_value(item, spec.name_field, name)
        if type_id:
            _set_leaf_value(item, "SensorListSensorId", type_id)
        channel_number = 0
        paired: list[ET.Element] = []
        for target_container, count in type_spec.allocates:
            for _ in range(count):
                channel_number += 1
                allocated = _allocate_in_sibling(
                    blob_root, line_idx, target_container, next_nr, channel_number, name
                )
                if target_container == "BinaryInput":
                    paired.append(allocated)
        binary = _resolve_locator(blob_root, (("BinaryInput", line_idx),))
        if binary is not None:
            for first, second in zip(paired[::2], paired[1::2], strict=True):
                channels = list(binary.findall(_NS + "BinaryInputChannel"))
                _set_leaf_value(
                    first,
                    "BinaryInputChannelConnectedIndex",
                    str(channels.index(second)),
                )
                _set_leaf_value(
                    second,
                    "BinaryInputChannelConnectedIndex",
                    str(channels.index(first)),
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


def _check_sensor_capacity(
    blob: ET.Element, line: int, sensors: ET.Element, new_type: ItemTypeSpec
) -> None:
    from editor_gui.plugin_blob_readers.siemens_dali.schema import SENSOR_TYPES

    current, addresses = new_type.max_current, new_type.address_count
    catalog = {sensor.type_id: sensor for sensor in SENSOR_TYPES}
    for sensor in sensors.findall(_NS + "SensorListSensor"):
        if _sensor_number(_value(sensor.find(_NS + "SensorListSensorNr"))) == 0:
            continue
        sensor_type = catalog.get(_value(sensor.find(_NS + "SensorListSensorId")))
        if sensor_type is None:
            raise EditError("Cannot determine capacity for an unknown Siemens sensor")
        current += sensor_type.max_current
        addresses += sensor_type.address_count
    ecgs = _resolve_locator(blob, (("GenericEcg", line),))
    if ecgs is None:
        raise EditError("Missing GenericEcg table")
    for ecg in ecgs.findall(_NS + "GenericEcgEcg"):
        if _value(ecg.find(_NS + "GenericEcgEcgNr")) in ("", "0"):
            continue
        current += 2
        if _value(ecg.find(_NS + "GenericEcgEcgEmergencyType")) == "EMERG_TYPE_2DEV":
            current += 2
    if current > 190:
        raise EditError("DALI line current exceeds 190 mA")
    if addresses > 64:
        raise EditError("DALI sensor address capacity exceeds 64")


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
            number = _sensor_number(item_id)
            if number == 0:
                raise EditError("Cannot remove an inactive Siemens sensor slot")
            _set_leaf_value(item, "SensorListSensorId", "")
            line_idx = (
                binding.container_locator[-1][1] if binding.container_locator else 0
            )
            _release_channels(blob_root, line_idx, number)
        if binding.container_tag in ("BinaryInput", "Presence", "Brightness"):
            _reset_channel(item, spec.item_tag)
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


@cache
def _channel_defaults() -> dict[str, list[list[str]]]:
    return json.loads(
        files("editor_gui.plugin_blob_readers.siemens_dali")
        .joinpath("sensor_defaults.json")
        .read_text(encoding="utf-8")
    )


def _reset_channel(item: ET.Element, tag: str) -> None:
    item.clear()
    for name, value in _channel_defaults()[tag]:
        ET.SubElement(item, _NS + name, {"Value": value})


def _allocate_in_sibling(
    blob_root: ET.Element,
    line_idx: int,
    container_tag: str,
    sensor_nr: int,
    channel_number: int,
    name: str,
) -> ET.Element:
    from editor_gui.plugin_blob_readers.siemens_dali.schema import collection_for

    spec = collection_for(container_tag)
    container = _resolve_locator(blob_root, ((container_tag, line_idx),))
    if spec is None or container is None:
        raise EditError(f"Missing {container_tag} table")
    item = _find_free_item_slot(container, spec)
    if item is None:
        raise EditError(f"No free {container_tag} channel")
    _reset_channel(item, spec.item_tag)
    _set_leaf_value(item, spec.activity_field, str(sensor_nr))
    _set_leaf_value(item, spec.item_tag + "ChannelNr", str(channel_number))
    _set_leaf_value(item, spec.item_tag + "Channel", "1")
    if spec.name_field:
        for leaf in item.findall(_NS + spec.name_field):
            leaf.set("Value", name)
    if container_tag == "Presence":
        for suffix, values in (
            ("BrightnessLimit", ("10", "0")),
            ("IntervalPresence", ("0", "5")),
            ("CntDuringInterval", ("1", "3")),
        ):
            for leaf, value in zip(
                item.findall(_NS + spec.item_tag + suffix), values, strict=True
            ):
                leaf.set("Value", value)
    return item


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


def _ensure_children(parent: ET.Element, *tags: str) -> None:
    """Materialize the requested fields in empty sensor slots."""
    for tag in tags:
        if parent.find(f"{_NS}{tag}") is None:
            el = ET.SubElement(parent, f"{_NS}{tag}")
            el.set("Value", "")


def _set_leaf_value(parent: ET.Element, tag: str, value: str) -> None:
    el = parent.find(f"{_NS}{tag}")
    if el is None:
        el = ET.SubElement(parent, f"{_NS}{tag}")
    el.set("Value", value)


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
            _reset_channel(ch, child_tag)
