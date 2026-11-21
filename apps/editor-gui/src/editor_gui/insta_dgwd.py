"""Portable typed parameter exchange for supported Insta-platform DALI applications."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from xml.etree import ElementTree as ET

from editor_gui.device import Device
from xknxeditor.prod.script.values import check_value

_APP = re.compile(
    r"M-(0008_A-C030-21|0009_A-E80A-22|008E_A-C031-22)-[0-9A-F]{4}(?:-O000A)?$"
)
_KINDS = (
    "Color",
    "Date",
    "Float",
    "IPAddress",
    "Number",
    "Raw",
    "Restriction",
    "Text",
    "Time",
)
_TYPES = {"ParameterTypeType" + kind: kind for kind in _KINDS}
_TYPES.update(ParameterTypeTypeRawData="Raw", ParameterTypeTypeIpaddress="IPAddress")
_MAX_SIZE = 20 * 1024 * 1024


def supports(device: Device) -> bool:
    """Whether this application has a verified parameter exchange layout."""
    return bool(device.app and _APP.fullmatch(device.app.id.upper()))


@dataclass(frozen=True)
class Parameter:
    ref_id: str
    local_ref: str
    module: str
    kind: str
    value: str
    default: str
    type: object

    @property
    def key(self) -> tuple[str, str]:
        return self.module, "_".join(self.ref_id.split("_")[-2:])


def _parameters(device: Device) -> tuple[list[Parameter], int]:
    if (
        not supports(device)
        or (dynamic := device.dynamic_ui) is None
        or device.app is None
    ):
        raise ValueError("Unsupported DALI parameter exchange application")
    index = dynamic.indexer
    modules = device.get_module_instances()
    rows: list[Parameter] = []
    version = 0
    for full in dynamic.parameter_ref_ids():
        local = dynamic.local_ref_id(full)
        ref = index.parameter_refs[local]
        tc = index.type_of(local)
        if tc is None or type(tc).__name__ in ("object", "ParameterTypeTypePicture"):
            continue
        kind = _TYPES.get(type(tc).__name__)
        if kind is None or kind == "Date":
            raise ValueError(
                f"Unsupported DALI exchange parameter type: {type(tc).__name__}"
            )
        module = ""
        if index.ref_owner[local] is not None:
            module_id = next(
                (iid for iid, _ in modules if full.startswith(iid + "_")), None
            )
            if module_id is None or "_SM-" in full:
                raise ValueError("Unsupported DALI exchange module nesting")
            module = module_id.split("_", 2)[2]
        value = dynamic.get_parameter_ref(full)
        default = index.default_value(local)
        if value is None or default is None:
            raise ValueError(f"DALI exchange parameter has no value: {full}")
        if not module and ref.tag == "200005":
            version = int(value) >= 2
        rows.append(Parameter(full, local, module, kind, value, default, tc))
    if len({row.key for row in rows}) != len(rows):
        raise ValueError("Ambiguous DALI exchange parameter identifiers")
    return rows, int(version)


def _identity(device: Device) -> tuple[int, int, int, str]:
    if device.app is None:
        raise ValueError("Missing application")
    manufacturer, app = device.app.id.split("_", 1)
    parts = app.split("-")
    return int(manufacturer[2:], 16), int(parts[1], 16), int(parts[2], 16), parts[3]


def _export_value(row: Parameter) -> str:
    if row.kind == "Raw":
        return base64.b64decode(row.value, validate=True).hex().upper()
    if row.kind == "Color":
        return str((int(row.value.removeprefix("#"), 16) | 0xFF000000) - (1 << 32))
    if row.kind == "Float":
        number = Decimal(row.value)
        if not number.is_finite():
            raise ValueError("Invalid DALI exchange floating-point value")
        text = format(number, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return row.value


def export_configuration(device: Device) -> bytes:
    """Export all effective values, including hidden fields and module defaults."""
    rows, version = _parameters(device)
    manufacturer, number, app_version, fingerprint = _identity(device)
    root = ET.Element("DataContainer", DataVersion=str(version))
    block = ET.SubElement(
        root,
        "DataBlock",
        BlockId="",
        ManufacturerId=str(manufacturer),
        ApplicationNo=str(number),
        ApplicationVersion=str(app_version),
        ApplicationFingerprint=fingerprint,
    )
    top = ET.SubElement(block, "ParameterBlock", ParameterBlockId="")
    modules = ET.Element("ParameterBlocks")
    grouped: dict[str, list[Parameter]] = {"": []}
    for row in rows:
        grouped.setdefault(row.module, []).append(row)
    for module, values in grouped.items():
        target = (
            top
            if not module
            else ET.SubElement(
                modules,
                "ParameterBlock",
                ParameterBlockId=f"P-0000_DI-0_{device.app.id}_{module}"
                if device.app
                else "",
            )
        )
        containers = {
            kind: ET.SubElement(target, "Parameters" + kind) for kind in _KINDS
        }
        for row in values:
            ET.SubElement(
                containers[row.kind],
                "ParameterEntry" + row.kind,
                ParameterId=row.local_ref,
                Value=_export_value(row),
            )
    top.append(modules)
    ET.SubElement(
        ET.SubElement(block, "GroupAddresseBlock", GroupAddressBlockId=""),
        "GroupAddresses",
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def revision(device: Device) -> str:
    """Revision of the complete effective configuration for guarded imports."""
    return hashlib.sha256(export_configuration(device)).hexdigest()


class _TreeBuilder(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError("DALI exchange XML must not contain a document type")


def _module_key(value: str, manufacturer: int) -> str:
    if not value:
        return ""
    match = re.fullmatch(
        r"(?:P-[^_]+_DI-[^_]+_)?M-([0-9A-Fa-f]{4})_A-[^_]+_(MD-[^_]+_M-[^_]+_MI-[^_]+)",
        value,
    )
    if match is None or int(match[1], 16) != manufacturer:
        raise ValueError("Invalid DALI exchange module identity")
    return match[2]


def _import_value(row: Parameter, text: str, encoding: str) -> str:
    if row.kind == "Raw":
        text = base64.b64encode(bytes.fromhex(text)).decode("ascii")
    elif row.kind == "Color":
        number = int(text)
        if not -(1 << 31) <= number < (1 << 31):
            raise ValueError("Invalid DALI exchange color")
        text = f"#{number & 0xFFFFFF:06X}"
    return check_value(text, row.type, text_encoding=encoding)


def import_configuration(device: Device, data: bytes) -> list[tuple[str, str]]:
    """Validate a complete exchange document and return edits without mutating the device."""
    rows, version = _parameters(device)
    if len(data) > _MAX_SIZE:
        raise ValueError("DALI exchange file is too large")
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=_TreeBuilder()))
    except ET.ParseError as exc:
        raise ValueError("Invalid DALI exchange XML") from exc
    if root.tag != "DataContainer" or len(root) != 1 or root[0].tag != "DataBlock":
        raise ValueError("Invalid DALI exchange structure")
    loaded_version = int(root.get("DataVersion", "-1"))
    if not 0 <= loaded_version <= version:
        raise ValueError("Incompatible DALI exchange data version")
    manufacturer, number, app_version, _ = _identity(device)
    block = root[0]
    if (
        int(block.get("ManufacturerId", "-1")),
        int(block.get("ApplicationNo", "-1")),
    ) != (manufacturer, number):
        raise ValueError("DALI exchange belongs to another application")
    source_version = int(block.get("ApplicationVersion", "-1"))
    if device.app is None or source_version not in (
        app_version,
        *device.app.program.replaces_versions,
    ):
        raise ValueError("Incompatible DALI exchange application version")
    if [child.tag for child in block] != ["ParameterBlock", "GroupAddresseBlock"]:
        raise ValueError("Invalid DALI exchange blocks")
    top, addresses = block
    if (
        top.get("ParameterBlockId") != ""
        or len(addresses) != 1
        or addresses[0].tag != "GroupAddresses"
        or len(addresses[0])
    ):
        raise ValueError("Unsupported DALI exchange module or group addresses")
    entries: dict[tuple[str, str], tuple[str, str]] = {}
    seen_modules: set[str] = set()
    modules = top.find("ParameterBlocks")
    for target in [top, *(list(modules) if modules is not None else [])]:
        if target.tag != "ParameterBlock":
            raise ValueError("Invalid DALI exchange parameter block")
        module = _module_key(target.get("ParameterBlockId", ""), manufacturer)
        if module in seen_modules:
            raise ValueError("Duplicate DALI exchange module")
        seen_modules.add(module)
        expected = ["Parameters" + kind for kind in _KINDS]
        children = target.find("ParameterBlocks")
        if children is not None:
            if target is not top and len(children):
                raise ValueError("Unsupported DALI exchange module nesting")
            expected.append("ParameterBlocks")
        if [child.tag for child in target] != expected:
            raise ValueError("Invalid DALI exchange parameter containers")
        for kind, container in zip(_KINDS, target, strict=False):
            for entry in container:
                if (
                    entry.tag != "ParameterEntry" + kind
                    or len(entry)
                    or set(entry.attrib) != {"ParameterId", "Value"}
                ):
                    raise ValueError("Invalid DALI exchange parameter entry")
                key = module, "_".join(entry.attrib["ParameterId"].split("_")[-2:])
                if key in entries:
                    raise ValueError("Duplicate DALI exchange parameter")
                entries[key] = kind, entry.attrib["Value"]
    dynamic = device.dynamic_ui
    if dynamic is None:
        raise ValueError("Missing application parameters")
    original = {row.ref_id: row.value for row in rows}

    def values(parameters: list[Parameter]) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in parameters:
            if (entry := entries.get(row.key)) is not None:
                kind, text = entry
                if row.kind != kind:
                    raise ValueError("Incompatible DALI exchange parameter type")
                result[row.ref_id] = _import_value(row, text, dynamic.text_encoding)
            elif loaded_version < version:
                result[row.ref_id] = row.default
        return result

    with dynamic.simulation():
        dynamic.apply_parameter_values(values([row for row in rows if not row.module]))
        rows, _ = _parameters(device)
        known = {row.key for row in rows}
        if seen_modules != {"", *(row.module for row in rows)}:
            raise ValueError("Unknown or missing DALI exchange module")
        if not entries.keys() <= known:
            raise ValueError("Unknown DALI exchange parameter")
        if source_version == app_version and entries.keys() != known:
            raise ValueError("Incomplete DALI exchange configuration")
        result = values(rows)
    changes: list[tuple[str, str]] = []
    for row in rows:
        if row.ref_id not in result:
            continue
        value = result[row.ref_id]
        before = original.get(row.ref_id)
        if (
            before == value
            or (
                row.kind == "Float"
                and before is not None
                and float(before) == float(value)
            )
            or (
                row.kind == "Color"
                and before is not None
                and before.removeprefix("#").upper() == value.removeprefix("#").upper()
            )
        ):
            continue
        changes.append((row.ref_id, value))
    return changes
