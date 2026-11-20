"""Offline DCA operations on the IPAS e64 Pro project backup."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from . import COLOUR_FADE, EDITABLE_ENUMS

if TYPE_CHECKING:
    from editor_gui.device import Device

SLOTS = {"scenes": "Scene", "effects": "Effect", "templates": "Tpl"}
TARGETS = {"groups": "Dalie64.DcaApp.Group", "ecgs": "Dalie64.DcaApp.ECG"}
ROW_TAG = {"scenes": "SceneItem", "effects": "EffectStep", "templates": "TplCmd"}
BACKUP_NAME = "Dalie64-Backup-Store"


@lru_cache(maxsize=4)
def document(data: bytes) -> ET.Element:
    """Shared read-only snapshot. Writers copy before touching it."""
    try:
        parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
        )
        root = ET.fromstring(data, parser=parser)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError("Invalid DALI backup") from exc
    if root.tag != "device" or root.get("type") != "e64pro":
        raise ValueError("Expected an e64pro project backup")
    return root


def supports(device: Device) -> bool:
    return device.app is not None and device.app.id.startswith("M-00EF_A-DA11")


def backup(device: Device) -> tuple[str, bytes] | None:
    if not supports(device):
        return None
    for entry in device.binary_data:
        if entry.name == BACKUP_NAME and entry.data:
            return entry.name, bytes(entry.data)
    return None


def import_configuration(data: bytes, address: str, project: str) -> bytes:
    """Read a complete ETS DCA XML export and bind its header to the destination."""
    root = deepcopy(document(data))
    for container, tag in {"groups": "Group", "ecgs": "ECG", **SLOTS}.items():
        sections = root.findall(container)
        if len(sections) != 1:
            raise ValueError("Expected a complete e64 Pro device configuration")
        indexes = [entry.get("index") for entry in sections[0].findall(tag)]
        if (
            (container in TARGETS and not indexes)
            or any(
                index is None or not re.fullmatch(r"0|[1-9][0-9]*", index)
                for index in indexes
            )
            or len(set(indexes)) != len(indexes)
        ):
            raise ValueError(f"Invalid indexes in e64 Pro configuration: {container}")
    root.set("IndividualAddress", address)
    root.set("Project", project)
    return ET.tostring(root, encoding="unicode").encode("utf-16le")


def export_configuration(data: bytes) -> bytes:
    """Serialize a project backup in the UTF-8 form used by ETS device exports."""
    root = deepcopy(document(data))
    for section, tag in SLOTS.items():
        for slot in root.findall(f"{section}/{tag}"):
            # ETS flags populated slots in standalone exports, independent of their project state.
            slot.set("isDirty", str(bool(rows(slot, section))))
    return ET.tostring(root, encoding="utf-8")


@dataclass(frozen=True)
class Edit:
    section: str
    slot: str
    action: str
    row: int = -1
    field: str = ""
    value: str = ""
    target: str = ""
    index: str = ""


def rows(slot: ET.Element, section: str) -> list[ET.Element]:
    tag = ROW_TAG[section]
    return slot.findall("Commands/" + tag if section == "templates" else tag)


def _number(text: str, maximum: int, minimum: int = 0) -> None:
    if (
        not text.isascii()
        or not text.isdecimal()
        or not minimum <= int(text) <= maximum
    ):
        raise ValueError(f"Expected {minimum}â€“{maximum}")


# XML encodings used by the e64 Pro backup. RGBW/HSVW store white as a byte.
COLOURS = {"1": "xyY", "2": "K", "4": "RGB", "8": "RGBW", "16": "HSV", "32": "HSVW"}
_GROUP_COLOURS = {
    "4": ("2",),
    "5": ("1",),
    "6": ("8",),
    "7": ("4",),
    "8": ("16",),
    "9": ("32",),
    "10": ("2", "8"),
    "11": ("2", "4"),
    "12": ("2", "16"),
    "13": ("2", "32"),
}
# Observed in e64 Pro ETS exports.
BYTE_COLOUR_MODES = frozenset(("4", "8"))
VERIFIED_COLOUR_MODES = frozenset(COLOURS)
DEFAULT_COLOURS = {
    "1": "0.3300;0.3300;1.0000",
    "2": "3000",
    "4": "0;0;0;0",
    "8": "0;0;0;0",
    "16": "0.0000;0.0000;0.0000",
    "32": "0.0000;0.0000;0.0000;0",
}
# The vendor TplCmd serializes the same colour value as a scene item.
TEMPLATE_COLOURS = {
    "ColourTemp": "2",
    "ColourXY": "1",
    "ColourRGBW": "8",
    "ColourRGB": "4",
    "ColourHSV": "16",
    "ColourHSVW": "32",
}
# A transition between command types must initialise a compatible value and fade family.
COMMAND_DEFAULTS = {
    name: ("0", "VFadeIn0")
    for name in ("SetValue", "SetMinValue", "SetMaxValue", "SetMaxOnValue")
}
COMMAND_DEFAULTS.update(
    {
        func: (DEFAULT_COLOURS[mode], "CFadeIn1")
        for func, mode in TEMPLATE_COLOURS.items()
    }
)
LEGACY_KELVIN_SUFFIX = ";0;0;0"


def colour_modes(root: ET.Element, item: ET.Element) -> tuple[str, ...]:
    kind = next((k for k, v in TARGETS.items() if v == item.get("itemtype")), None)
    if kind is None:
        return ()
    target = next(
        (
            e
            for e in root.findall(kind + "/*")
            if e.get("index") == item.get("itemindex")
        ),
        None,
    )
    return _GROUP_COLOURS.get(target.get("ctype", ""), ()) if target is not None else ()


def _colour_value(kind: str, value: str) -> None:
    if kind == "2":
        _number(value, 65535, 1)
        return
    if kind not in COLOURS:
        raise ValueError("Unknown colour space")
    values = value.split(";")
    if kind in BYTE_COLOUR_MODES:
        # RGB also stores four bytes, but only exposes the first three channels.
        if len(values) != 4:
            raise ValueError("Expected four colour bytes")
        for component in values:
            _number(component, 255)
        return
    white = kind == "32"
    if len(values) != (4 if white else 3) or not all(
        re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", v) and 0 <= float(v) <= 1
        for v in values[:3]
    ):
        raise ValueError("Expected three components in the range 0-1")
    if white:
        _number(values[3], 255)


def _set(
    element: ET.Element, field: str, value: str, section: str, header: bool
) -> None:
    if header:
        allowed = {
            "scenes": {"description", "knxScene", "fadeTime"},
            "effects": {"description", "mode"},
            "templates": {"description", "isOverwritable", "resetOnMidnight"},
        }
    else:
        allowed = {
            "scenes": {"value", "color", "keepValue", "keepColor"},
            "effects": {
                "value",
                "color",
                "keepValue",
                "keepColor",
                "fadetime",
                "delay",
            },
            "templates": {"value", "time", "weekdays", "func", "fadeIn"},
        }
    if field not in allowed[section]:
        raise ValueError("Unsupported field")
    if any(
        (cp < 32 and cp not in (9, 10, 13))
        or 0xD800 <= cp <= 0xDFFF
        or cp in (0xFFFE, 0xFFFF)
        for cp in map(ord, value)
    ):
        raise ValueError("Value contains characters unsupported by XML")
    if field in (
        "keepValue",
        "keepColor",
        "isOverwritable",
        "resetOnMidnight",
    ) and value not in ("True", "False"):
        raise ValueError("Expected a Boolean")
    if field in ("keepValue", "keepColor"):
        mask = 2 if field == "keepValue" else 1
        flags = int(element.get("setting", "0"))
        if field == "keepColor" and "color" not in element.attrib:
            raise ValueError("No colour configured")
        element.set("setting", str(flags | mask if value == "True" else flags & ~mask))
        return
    if field == "knxScene":
        _number(value, 64, 1)
    if field == "value" and (
        section != "templates"
        or element.get("func")
        in ("SetValue", "SetMaxOnValue", "SetMinValue", "SetMaxValue")
    ):
        _number(value, 100)
    if field == "mode" and value not in ("0", "1"):
        raise ValueError("Unknown effect mode")
    if (
        field in ("fadeTime", "fadetime", "delay", "func")
        and value not in EDITABLE_ENUMS[field]
    ):
        raise ValueError("Unsupported choice")
    if field == "time" and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Expected HH:MM")
    if field == "weekdays" and not re.fullmatch("[01]{7}", value):
        raise ValueError("Expected seven weekdays")
    if field == "fadeIn" and value not in fade_choices(element):
        raise ValueError("Unsupported fade time")
    if field == "color":
        _colour_value(element.get("color-type", ""), value)
    if field == "func" and value != element.get("func"):
        if value not in COMMAND_DEFAULTS:
            raise ValueError("Unsupported command conversion")
        level, fade = COMMAND_DEFAULTS[value]
        element.attrib.update(value=level, fadeIn=fade, scaleMode="ScaleNormal")
    if field == "value" and section == "templates":
        mode = TEMPLATE_COLOURS.get(element.get("func", ""))
        if mode == "2":
            keep_suffix = value.endswith(LEGACY_KELVIN_SUFFIX) or element.get(
                "value", ""
            ).endswith(LEGACY_KELVIN_SUFFIX)
            value = value.removesuffix(LEGACY_KELVIN_SUFFIX)
            _colour_value(mode, value)
            if keep_suffix:
                value += LEGACY_KELVIN_SUFFIX
        elif mode is not None:
            _colour_value(mode, value)
    element.set(field, value)


def fade_choices(cmd: ET.Element) -> list[str]:
    if cmd.get("func") in TEMPLATE_COLOURS or cmd.get("fadeIn", "").startswith(
        "CFadeIn"
    ):
        return ["CFadeIn" + v.split()[0] for v in COLOUR_FADE.values()]
    return ["VFadeIn" + str(s) for s in (*range(11), *range(20, 201, 10))]


def apply(data: bytes, edit: Edit) -> bytes:
    if edit.section not in SLOTS:
        raise ValueError("Unknown DCA section")
    root = deepcopy(document(data))
    slot = next(
        (
            e
            for e in root.findall(f"{edit.section}/{SLOTS[edit.section]}")
            if e.get("index") == edit.slot
        ),
        None,
    )
    if slot is None:
        raise ValueError("Unknown slot")
    section, action = edit.section, edit.action
    if action == "member" and section != "templates":
        raise ValueError("Only templates have member checkboxes")
    members = section == "templates" and action == "member"
    parent = slot
    if (
        section == "effects"
        and action == "add"
        and len(root.findall("effects/Effect/EffectStep")) >= 500
    ):
        raise ValueError("All 500 effect steps are in use")
    if section == "templates" and action != "header":
        container = "Members" if members else "Commands"
        existing = slot.find(container)
        parent = existing if existing is not None else ET.SubElement(slot, container)
    children = parent.findall("TplItem" if members else ROW_TAG[section])
    if action == "header":
        _set(slot, edit.field, edit.value, section, True)
    elif action in ("add", "member"):
        if section == "templates" and not members:
            ET.SubElement(
                parent,
                "TplCmd",
                func="SetValue",
                value="0",
                scaleMode="ScaleNormal",
                fadeIn="VFadeIn0",
                time="00:00",
                weekdays="1111111",
            )
        else:
            if edit.target not in TARGETS or not any(
                e.get("index") == edit.index for e in root.findall(edit.target + "/*")
            ):
                raise ValueError("Unknown target")
            kind = TARGETS[edit.target]
            match = next(
                (
                    e
                    for e in children
                    if e.get("itemtype") == kind and e.get("itemindex") == edit.index
                ),
                None,
            )
            if members and edit.value not in ("True", "False"):
                raise ValueError("Expected a Boolean")
            if members and edit.value == "False":
                if match is None:
                    return data
                parent.remove(match)
            else:
                if match is not None and section != "effects":
                    if members:
                        return data
                    raise ValueError("Target already belongs to the scene")
                attrs = {"itemtype": kind, "itemindex": edit.index}
                if not members:
                    attrs.update(value="0", setting="0")
                    colour_types = colour_modes(root, ET.Element("target", attrs))
                    colour_type = next(
                        (
                            mode
                            for mode in colour_types
                            if mode in VERIFIED_COLOUR_MODES
                        ),
                        None,
                    )
                    if colour_type is not None:
                        attrs.update(
                            {
                                "color-type": colour_type,
                                "color": DEFAULT_COLOURS[colour_type],
                            }
                        )
                    if section == "effects":
                        attrs.update(fadetime="0", delay="0")
                ET.SubElement(parent, "TplItem" if members else ROW_TAG[section], attrs)
    else:
        if not 0 <= edit.row < len(children):
            raise ValueError("Unknown row")
        child = children[edit.row]
        if action == "set":
            _set(child, edit.field, edit.value, section, False)
        elif action == "colour" and section in ("scenes", "effects"):
            if edit.field not in colour_modes(root, child):
                raise ValueError("Unsupported colour space for this target")
            if edit.field not in VERIFIED_COLOUR_MODES:
                raise ValueError("Unverified e64 Pro colour encoding")
            _colour_value(edit.field, edit.value)
            # Preserve the two keep flags and vendor bits when switching colour space.
            child.attrib.update({"color-type": edit.field, "color": edit.value})
        elif action == "remove":
            parent.remove(child)
        elif action == "copy" and section == "templates":
            parent.insert(list(parent).index(child) + 1, deepcopy(child))
        elif action in ("up", "down") and section == "effects":
            other = edit.row + (-1 if action == "up" else 1)
            if not 0 <= other < len(children):
                return data
            position = list(parent).index(children[other])
            parent.remove(child)
            parent.insert(position, child)
        else:
            raise ValueError("Unsupported DCA operation")
    if ET.tostring(root) == ET.tostring(document(data)):
        return data
    slot.set("isDirty", "True")
    return ET.tostring(root, encoding="unicode").encode("utf-16le")
