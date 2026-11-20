"""Recover classic Siemens DALI property images into their configuration blob."""

from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import asdict, dataclass
from datetime import date
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Any, cast
from xml.etree import ElementTree as ET

from xknxeditor.dali.siemens import read_table, scan_n141_03

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.prod import Application
    from xknxeditor.recover import RecoveredDevice
    from xknxeditor.recover.recover import AddinReader

_CAPTURE = "siemens-dali-recovery.json"
_NS = "{http://www.siemens.com/knx/N141_21_Parameters}"
_IMEXPORT = "{http://www.siemens.com/knx/ImExportSchema.xsd}"


@dataclass(frozen=True)
class Property:
    """One property array and its destination in the configuration XML."""

    container: str
    object_index: int
    per_line: bool
    pid: int
    size: int
    count: int
    item: str
    leaf: str
    occurrence: int
    kind: str


@cache
def _mapping() -> dict[str, Any]:
    """Load the fixed property layout."""
    return json.loads(
        files("editor_gui.plugin_blob_readers.siemens_dali")
        .joinpath("recovery_properties.json")
        .read_text(encoding="utf-8")
    )


def properties() -> tuple[Property, ...]:
    """Return the property layouts for classic gateways."""
    return tuple(Property(*row) for row in _mapping()["properties"])


def enum_values(name: str) -> dict[str, int]:
    """Return runtime values for a property enumeration."""
    return _mapping()["enums"][name]


def line_count(app_id: str) -> int:
    """Return the number of lines for the classic application family."""
    family = app_id.upper().split("-")
    if len(family) >= 3 and family[0:2] == ["M", "0001_A"]:
        if family[2] == "9837":
            return 1
        if family[2] == "9834":
            return 2
    return 0


def supports(app_id: str) -> bool:
    return bool(line_count(app_id))


def _layout(count: int) -> list[tuple[Property, int]]:
    return [
        (prop, line)
        for prop in properties()
        for line in range(count if prop.per_line else 1)
    ]


def _key(prop: Property, line: int) -> str:
    return f"{prop.object_index + line}/{prop.pid}"


def addin_reader(application: Application) -> AddinReader | None:
    """Read stored configuration without triggering a DALI search or any writes."""
    count = line_count(application.id)
    if not count:
        return None

    async def read(programmer: DeviceProgrammer) -> dict[str, dict[str, bytes]]:
        identity = await read_table(programmer, 4, 246, 1, 7)
        expected_variant = 0x058B0301 if count == 1 else 0x058B0101
        if int.from_bytes(identity[:4], "big") != expected_variant:
            raise ValueError("Unsupported Siemens DALI firmware variant")
        version = int.from_bytes(identity[4:6], "big")
        if version != 1029:
            raise ValueError(f"Unsupported Siemens DALI interface version {version}")
        tables = {
            _key(prop, line): (
                await read_table(
                    programmer,
                    prop.object_index + line,
                    prop.pid,
                    prop.size,
                    prop.count,
                )
            ).hex()
            for prop, line in _layout(count)
        }
        scans = [
            asdict(
                await scan_n141_03(
                    programmer,
                    line,
                    with_cin=True,
                    with_emergency=True,
                    interface_version=version,
                )
            )
            for line in range(count)
        ]
        capture = {
            "version": 1,
            "lines": count,
            "interface_version": version,
            "tables": tables,
            "scans": scans,
        }
        data = json.dumps(capture, default=lambda value: value.hex()).encode()
        build_blob(data, application.id)
        return {application.id: {_CAPTURE: data}}

    return read


def _element(parent: ET.Element, tag: str, index: int = 0) -> ET.Element:
    children = parent.findall(_NS + tag)
    while len(children) <= index:
        children.append(ET.SubElement(parent, _NS + tag))
    return children[index]


def _decode(prop: Property, raw: bytes) -> str:
    value = int.from_bytes(raw, "big", signed=prop.kind == "int")
    if prop.kind == "hex":
        return raw.hex().upper()
    if prop.kind.startswith("enum:"):
        enum = prop.kind.removeprefix("enum:")
        values = enum_values(enum)
        for token, number in values.items():
            if number == value:
                return token
        raise ValueError(f"Unknown Siemens {prop.leaf} value {value}")
    if prop.kind == "time":
        hour, minute, second = raw[0] & 31, raw[1], raw[2]
        if hour > 23 or minute > 59 or second > 59:
            raise ValueError("Invalid Siemens download time")
        return f"{hour:02}:{minute:02}:{second:02}"
    if prop.kind == "date":
        year = raw[2] + (1900 if raw[2] >= 90 else 2000)
        return date(year, raw[1], raw[0]).isoformat() + "T00:00:00"
    if prop.kind == "float":
        number = struct.unpack("!f", raw)[0]
        if not math.isfinite(number):
            raise ValueError("Invalid Siemens floating point property")
        return str(number)
    return str(value)


def build_blob(data: bytes, app_id: str) -> bytes:
    """Validate a complete capture before producing a BLOB_PARA payload."""
    raw_capture: object = json.loads(data)
    if not isinstance(raw_capture, dict):
        raise ValueError("Invalid Siemens DALI capture")
    capture = cast(dict[str, Any], raw_capture)
    count = line_count(app_id)
    if (
        not count
        or capture.get("version") != 1
        or capture.get("lines") != count
        or capture.get("interface_version") != 1029
        or not isinstance(capture.get("tables"), dict)
    ):
        raise ValueError("Invalid Siemens DALI capture")
    layout = _layout(count)
    tables = capture["tables"]
    if set(tables) != {_key(prop, line) for prop, line in layout}:
        raise ValueError("Incomplete Siemens DALI property image")
    blob = ET.Element(_NS + "Blob")
    for prop, line in layout:
        value = tables[_key(prop, line)]
        if not isinstance(value, str):
            raise ValueError("Invalid Siemens DALI property data")
        raw = bytes.fromhex(value)
        if len(raw) != prop.size * prop.count:
            raise ValueError(f"Incomplete Siemens property {_key(prop, line)}")
        container = _element(blob, prop.container, line)
        for index in range(prop.count):
            parent = _element(container, prop.item, index) if prop.item else container
            _element(parent, prop.leaf, prop.occurrence).set(
                "Value", _decode(prop, raw[index * prop.size : (index + 1) * prop.size])
            )
    _logical_slots(blob, count)
    _scene_catalog(blob, count)
    _scan_image(blob, capture.get("scans"), count)
    ET.register_namespace("n141", _NS[1:-1])
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<n141_21_ImExport:ImExport xmlns:n141_21_ImExport="{_IMEXPORT[1:-1]}">'
        "<n141_21_ImExport:N141_21>"
        + ET.tostring(blob, encoding="unicode")
        + "</n141_21_ImExport:N141_21></n141_21_ImExport:ImExport>"
    )
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(xml.encode("utf-16-le")) + compressor.flush()


def _logical_slots(blob: ET.Element, count: int) -> None:
    for line in range(count):
        ecgs = _element(blob, "GenericEcg", line).findall(_NS + "GenericEcgEcg")
        addresses: set[int] = set()
        for index, ecg in enumerate(ecgs):
            address = int(
                _element(ecg, "GenericEcgEcgShortAddress").get("Value", "255")
            )
            group = int(_element(ecg, "GenericEcgEcgGroupAddress").get("Value", "255"))
            if address != 255 and not 0 <= address < 64:
                raise ValueError("Invalid Siemens DALI short address")
            if group != 255 and not 0 <= group < 16:
                raise ValueError("Invalid Siemens DALI group address")
            if address != 255:
                if address in addresses:
                    raise ValueError("Duplicate Siemens DALI short address")
                addresses.add(address)
            _element(ecg, "GenericEcgEcgNr").set(
                "Value", str(index + 1 if address != 255 else 0)
            )
        scenes = _element(blob, "Scenes", line).findall(_NS + "ScenesScene")
        for scene in scenes:
            for group_index in range(16):
                _element(scene, "ScenesSceneValueGroup", group_index).set(
                    "Value", "00000000"
                )
        for group_index in range(16):
            indices = [
                index
                for index, ecg in enumerate(ecgs)
                if _element(ecg, "GenericEcgEcgGroupAddress").get("Value")
                == str(group_index)
                and _element(ecg, "GenericEcgEcgShortAddress").get("Value") != "255"
            ]
            group = _element(_element(blob, "Group", line), "GroupGroup", group_index)
            _element(group, "GroupGroupNr").set(
                "Value", str(group_index + 1 if indices else 0)
            )
            if not indices:
                continue
            _copy_common_fields(
                group,
                [ecgs[index] for index in indices],
                "GenericEcgEcg",
                "GroupGroup",
                {"Nr", "ShortAddress", "GroupAddress", "EmergencyType"},
            )
            for container, item, group_container, group_item in (
                ("DevType8", "DevType8Ecg", "DevType8group", "DevType8groupGroup"),
                ("Minergy", "MinergyEcg", "Minergy", "MinergyGroup"),
            ):
                source = _element(blob, container, line).findall(_NS + item)
                target = _element(
                    _element(blob, group_container, line), group_item, group_index
                )
                _copy_common_fields(
                    target, [source[index] for index in indices], item, group_item
                )
            for scene in scenes:
                values = scene.findall(_NS + "ScenesSceneValue")
                common = {values[index].get("Value") for index in indices}
                if len(common) == 1:
                    _element(scene, "ScenesSceneValueGroup", group_index).set(
                        "Value", values[indices[0]].get("Value", "")
                    )
        sensor_container = _element(blob, "ConnectedSensors", line)
        sensors = sensor_container.findall(_NS + "ConnectedSensorsSensor")
        addressed: dict[int, ET.Element] = {}
        for sensor in sensors:
            encoded = int(
                _element(sensor, "ConnectedSensorsSensorShortAddress").get(
                    "Value", "255"
                )
            )
            if encoded != 255:
                address = encoded & 63
                if address in addressed:
                    raise ValueError("Duplicate Siemens sensor short address")
                addressed[address] = sensor
                _element(sensor, "ConnectedSensorsSensorShortAddress").set(
                    "Value", str(address)
                )
        sensor_container.clear()
        for address in range(64):
            sensor = addressed.get(address)
            if sensor is None:
                sensor = ET.Element(_NS + "ConnectedSensorsSensor")
                _element(sensor, "ConnectedSensorsSensorShortAddress").set(
                    "Value", "255"
                )
            _element(sensor, "ConnectedSensorsSensorActive").set(
                "Value", str(address in addressed).lower()
            )
            sensor_container.append(sensor)
        for item, field in (
            ("EffectsEffect", "EffectsEffectNr"),
            ("EffectsChannel", "EffectsChannelNr"),
            ("EffectsStep", "EffectsStepNr"),
            ("TimeSwitchChannel", "TimeSwitchChannelNr"),
            ("TimeSwitchAction", "TimeSwitchActionNr"),
        ):
            container = "Effects" if item.startswith("Effects") else "TimeSwitch"
            for index, child in enumerate(
                _element(blob, container).findall(_NS + item)
            ):
                selector = {
                    "EffectsEffect": "EffectsEffectEffectNumber",
                    "EffectsStep": "EffectsStepEffectIndex",
                    "TimeSwitchAction": "TimeSwitchActionChannel",
                }.get(item)
                active = (
                    selector is None or _element(child, selector).get("Value") != "255"
                )
                _element(child, field).set("Value", str(index + 1 if active else 0))


def _copy_common_fields(
    target: ET.Element,
    sources: list[ET.Element],
    source_prefix: str,
    target_prefix: str,
    exclude: set[str] | None = None,
) -> None:
    for leaf in sources[0]:
        suffix = leaf.tag.removeprefix(_NS + source_prefix)
        if exclude and suffix in exclude:
            continue
        values = [source.find(leaf.tag) for source in sources]
        if all(
            value is not None and value.get("Value") == leaf.get("Value")
            for value in values
        ):
            _element(target, target_prefix + suffix).set("Value", leaf.get("Value", ""))


def _scene_catalog(blob: ET.Element, count: int) -> None:
    """Rebuild the shared scene list from active per-line scene configurations."""
    scenes: dict[int, set[str]] = {}
    for line in range(count):
        for scene in _element(blob, "Scenes", line).findall(_NS + "ScenesScene"):
            encoded = int(_element(scene, "ScenesSceneSceneNumber").get("Value", "255"))
            if encoded & 0x40:
                continue
            fade_time = _element(scene, "ScenesSceneFadeTime").get("Value", "")
            scenes.setdefault(encoded & 63, set()).add(fade_time)
    catalog = _element(blob, "ScenePluginInfo")
    for index in range(32):
        item = _element(catalog, "ScenePluginInfoScene", index)
        _element(item, "ScenePluginInfoSceneKnxSceneNumber").set("Value", "255")
    for index, (number, fade_times) in enumerate(sorted(scenes.items())):
        item = _element(catalog, "ScenePluginInfoScene", index)
        _element(item, "ScenePluginInfoSceneKnxSceneNumber").set("Value", str(number))
        if len(fade_times) == 1:
            _element(item, "ScenePluginInfoSceneFadeTime").set(
                "Value", next(iter(fade_times))
            )


def _scan_image(blob: ET.Element, raw: object, count: int) -> None:
    if not isinstance(raw, list) or len(cast(list[object], raw)) != count:
        raise ValueError("Incomplete Siemens DALI scan image")
    scans = cast(list[Any], raw)
    fields = {
        "ShortAddress": ("short_address", 1),
        "Rnd": ("random_address", 3),
        "Types": ("type_bytes", 8),
        "MinPhysDimmValue": ("min_phys_dim_value", 1),
        "Cin": ("cin", 8),
        "ConnectedDevice": ("connected_device", 1),
        "MinEmergencyLevel": ("min_emergency_level", 1),
        "MaxEmergencyLevel": ("max_emergency_level", 1),
        "DevType1Features": ("dev_type1_features", 1),
        "ColourTempPhysCoolest": ("colour_temp_phys_coolest", 2),
        "ColourTempPhysWarmest": ("colour_temp_phys_warmest", 2),
        "ColourTypeFeatures": ("colour_type_features", 1),
    }
    for line, scan in enumerate(scans):
        if not isinstance(scan, dict):
            raise ValueError("Invalid Siemens DALI scan image")
        row = cast(dict[str, Any], scan)
        ecgs = row.get("ecgs")
        if (
            row.get("line") != line
            or row.get("object_index") != 35 + line
            or not isinstance(ecgs, list)
            or len(cast(list[object], ecgs)) != 64
        ):
            raise ValueError("Incomplete Siemens DALI scan image")
        seen: set[int] = set()
        container = _element(blob, "ConnectedEcgs", line)
        for address in range(64):
            item = _element(container, "ConnectedEcgsEcg", address)
            _element(item, "ConnectedEcgsEcgActive").set("Value", "false")
            _element(item, "ConnectedEcgsEcgShortAddress").set("Value", "255")
        for slot, ecg in enumerate(cast(list[Any], ecgs)):
            if not isinstance(ecg, dict):
                raise ValueError("Invalid Siemens DALI scan record")
            record = cast(dict[str, Any], ecg)
            address = record.get("short_address")
            if (
                record.get("slot") != slot
                or type(address) is not int
                or address not in (*range(64), 255)
            ):
                raise ValueError("Invalid Siemens DALI scan address")
            if address != 255:
                if address in seen:
                    raise ValueError("Duplicate Siemens DALI scan address")
                seen.add(address)
            if address == 255:
                continue
            item = _element(container, "ConnectedEcgsEcg", address)
            _element(item, "ConnectedEcgsEcgActive").set(
                "Value", str(address != 255).lower()
            )
            _element(item, "ConnectedEcgsEcgDummy").set("Value", "false")
            for suffix, (field, size) in fields.items():
                value = record.get(field)
                if field in ("type_bytes", "cin"):
                    if not isinstance(value, str) or len(bytes.fromhex(value)) != size:
                        raise ValueError("Invalid Siemens DALI scan field")
                    text = value.upper()
                else:
                    if type(value) is not int or not 0 <= value < 1 << (8 * size):
                        raise ValueError("Invalid Siemens DALI scan field")
                    text = (
                        value.to_bytes(size, "big").hex().upper()
                        if field == "random_address"
                        else str(value)
                    )
                _element(item, "ConnectedEcgsEcg" + suffix).set("Value", text)

        logical_ecgs = _element(blob, "GenericEcg", line).findall(_NS + "GenericEcgEcg")
        inverters = _element(blob, "DevType1", line).findall(_NS + "DevType1Ecg")
        physical = container.findall(_NS + "ConnectedEcgsEcg")
        for index, logical in enumerate(logical_ecgs):
            address = int(
                _element(logical, "GenericEcgEcgShortAddress").get("Value", "255")
            )
            if address == 255:
                continue
            if address not in seen:
                _element(logical, "GenericEcgEcgDevTypes").set(
                    "Value", _configured_types(blob, line, index, logical).hex().upper()
                )
                continue
            source = physical[address]
            types = bytes.fromhex(
                _element(source, "ConnectedEcgsEcgTypes").get("Value", "")
            )
            inverter = int(
                _element(inverters[index], "DevType1EcgInverterShortAddress").get(
                    "Value", "255"
                )
            )
            if 1 not in types and inverter != 255 and inverter in seen:
                paired = physical[inverter]
                inverter_types = bytes.fromhex(
                    _element(paired, "ConnectedEcgsEcgTypes").get("Value", "")
                )
                if 1 in inverter_types:
                    types = inverter_types
            _element(logical, "GenericEcgEcgDevTypes").set(
                "Value", bytes(t for t in types if t <= 8).hex().upper()
            )
            _element(logical, "GenericEcgEcgCin").set(
                "Value", _element(source, "ConnectedEcgsEcgCin").get("Value", "")
            )


def _configured_types(
    blob: ET.Element, line: int, index: int, logical: ET.Element
) -> bytes:
    """Restore types implied by configuration when physical metadata is absent."""
    types: list[int] = []
    for number in (0, 1, 8):
        if number == 8:
            line_config = _element(_element(blob, "DaliDevice"), "DaliDeviceLine", line)
            if (
                _element(logical, "GenericEcgEcgGroupAddress").get("Value") != "255"
                or _element(line_config, "DaliDeviceLineLineMode").get("Value")
                != "LINEMODE_NORMAL"
            ):
                continue
        container = _element(blob, f"DevType{number}", line)
        ecg = _element(container, f"DevType{number}Ecg", index)
        if _element(ecg, f"DevType{number}EcgEnabled").get("Value") == "BI_TRUE":
            types.append(number)
    return bytes(types)


def apply_recovered(
    project: ProjectService, device: Device, recovered: RecoveredDevice
) -> None:
    """Persist the validated image inside the recovery service's undo step."""
    if device.app is None or not supports(device.app.id) or not recovered.addin_data:
        return
    if set(recovered.addin_data) != {device.app.id} or set(
        recovered.addin_data[device.app.id]
    ) != {_CAPTURE}:
        raise ValueError("Recovered Siemens DALI data does not match this application")
    payload = build_blob(recovered.addin_data[device.app.id][_CAPTURE], device.app.id)
    project.persist_device_blob(device, "BLOB_PARA", payload)
