"""Offline DCA operations on IPAS e64 Pro and Theben P64 V2 project backups."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from xknxeditor.dali.profiles import profile_for

from . import COLOUR_FADE, EDITABLE_ENUMS, parse_device

if TYPE_CHECKING:
    from editor_gui.device import Device

SLOTS = {"scenes": "Scene", "effects": "Effect", "templates": "Tpl"}
TARGETS = {"groups": "Dalie64.DcaApp.Group", "ecgs": "Dalie64.DcaApp.ECG"}
ROW_TAG = {"scenes": "SceneItem", "effects": "EffectStep", "templates": "TplCmd"}
BACKUP_NAME = "Dalie64-Backup-Store"


@lru_cache(maxsize=4)
def document(data: bytes) -> ET.Element:
    """Shared read-only snapshot. Writers copy before touching it."""
    from editor_gui.plugins.project.strings import S

    root = parse_device(data)
    if root is None or root.get("type") != "e64pro":
        raise ValueError(S.E64_INVALID_CONFIGURATION)
    return root


def supports(device: Device) -> bool:
    profile = profile_for(device.app.id if device.app is not None else None)
    return profile is not None and profile.backup_blob_base == BACKUP_NAME


def revision(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def backup(device: Device) -> tuple[str, bytes] | None:
    if not supports(device):
        return None
    for entry in device.binary_data:
        if entry.name == BACKUP_NAME and entry.data:
            return entry.name, bytes(entry.data)
    return None


def import_configuration(data: bytes, address: str, project: str) -> bytes:
    """Read a complete DCA device XML and bind its header to the destination."""
    from editor_gui.plugins.project.strings import S

    root = deepcopy(document(data))
    for container, tag in {"groups": "Group", "ecgs": "ECG", **SLOTS}.items():
        sections = root.findall(container)
        if len(sections) != 1:
            raise ValueError(S.E64_INCOMPLETE_CONFIGURATION)
        indexes = [entry.get("index") for entry in sections[0].findall(tag)]
        if (
            (container in TARGETS and not indexes)
            or any(
                index is None or not re.fullmatch(r"0|[1-9][0-9]*", index)
                for index in indexes
            )
            or len(set(indexes)) != len(indexes)
        ):
            raise ValueError(S.E64_INVALID_CONFIGURATION)
    for section in ("effects", "templates"):
        _check_capacity(root, section)
    root.set("IndividualAddress", address)
    root.set("Project", project)
    return ET.tostring(root, encoding="unicode").encode("utf-16le")


def export_configuration(data: bytes) -> bytes:
    """Serialize a project backup as standalone UTF-8 device XML."""
    root = deepcopy(document(data))
    for section, tag in SLOTS.items():
        if section in ("effects", "templates"):
            _check_capacity(root, section)
        for slot in root.findall(f"{section}/{tag}"):
            # Standalone exports flag populated slots, independent of their project state.
            slot.set("isDirty", str(bool(rows(slot, section))))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def slot_element(root: ET.Element, section: str, index: str) -> ET.Element:
    from editor_gui.plugins.project.strings import S

    if section not in SLOTS:
        raise ValueError(S.E64_INVALID_SELECTION)
    found = [
        e
        for e in root.findall(f"{section}/{SLOTS[section]}")
        if e.get("index") == index
    ]
    if len(found) != 1:
        raise ValueError(S.E64_INVALID_SELECTION)
    return found[0]


def export_slot(data: bytes, section: str, index: str) -> bytes:
    root = document(data)
    slot = deepcopy(slot_element(root, section, index))
    if section in ("effects", "templates"):
        _check_capacity(root, section)
    return ET.tostring(slot, encoding="utf-8", xml_declaration=True)


def _import_slot(root: ET.Element, section: str, index: str, data: str) -> None:
    from editor_gui.plugins.project.strings import S

    old = slot_element(root, section, index)
    try:
        imported = ET.fromstring(
            data,
            parser=ET.XMLParser(
                target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
            ),
        )
    except (ValueError, ET.ParseError) as exc:
        raise ValueError(S.E64_INVALID_CONFIGURATION) from exc
    if imported.tag != SLOTS[section]:
        raise ValueError(S.E64_INVALID_CONFIGURATION)
    targets = (
        imported.findall("Members/TplItem")
        if section == "templates"
        else rows(imported, section)
    )
    for target in targets:
        kind = next(
            (k for k, v in TARGETS.items() if v == target.get("itemtype")), None
        )
        if kind is None or not any(
            e.get("index") == target.get("itemindex") for e in root.findall(kind + "/*")
        ):
            raise ValueError(S.E64_INVALID_SELECTION)
    if section != "effects":
        keys = [(e.get("itemtype"), e.get("itemindex")) for e in targets]
        if len(keys) != len(set(keys)):
            raise ValueError(S.E64_INVALID_CONFIGURATION)
    if section != "templates" and imported.get("index", index) != index:
        fields = (
            ("description", "knxScene")
            if section == "scenes"
            else ("description", "mode")
        )
        for name in fields:
            if name in old.attrib:
                imported.set(name, old.attrib[name])
            else:
                imported.attrib.pop(name, None)
    if section == "templates":
        for command in imported.findall("Commands/TplCmd"):
            if command.get("func") == "SetMaxOnValue" and command.get("value") == "0":
                command.set("value", "100")
    _validate_slot(imported, section, root)
    if imported.get("description", "") != old.get("description", ""):
        _validate_description(imported.get("description", ""))
    imported.set("index", index)
    imported.set("isDirty", "True")
    parent = root.find(section)
    assert parent is not None
    position = list(parent).index(old)
    parent.remove(old)
    parent.insert(position, imported)
    if section in ("effects", "templates"):
        _check_capacity(root, section)


def _validate_slot(slot: ET.Element, section: str, root: ET.Element) -> None:
    from editor_gui.plugins.project.strings import S

    headers = {
        "scenes": ("knxScene", "fadeTime"),
        "effects": ("mode",),
        "templates": ("mode", "isOverwritable", "resetOnMidnight"),
    }
    for key in headers[section]:
        if key in slot.attrib:
            _set(deepcopy(slot), key, slot.attrib[key], section, True)
    for item in rows(slot, section):
        fields = (
            ("func", "value", "fadeIn", "time", "weekdays", "scaleMode")
            if section == "templates"
            else ("value", "fadetime", "delay")
            if section == "effects"
            else ("value",)
        )
        for key in fields:
            if key in item.attrib:
                _set(deepcopy(item), key, item.attrib[key], section, False)
        if section != "templates":
            if "setting" in item.attrib:
                _number(item.attrib["setting"], 255)
            mode = item.get("color-type")
            if mode is not None and mode != "0":
                if mode not in colour_modes(root, item):
                    raise ValueError(S.E64_INVALID_COLOR)
                _colour_value(mode, item.get("color", ""))


DESCRIPTION_SECTIONS = (
    "groups",
    "ecgs",
    "mds",
    "pbs",
    "gis",
    "scenes",
    "effects",
    "templates",
)


def _description(root: ET.Element, section: str, index: str, value: str) -> None:
    from editor_gui.plugins.project.strings import S

    if section not in (*DESCRIPTION_SECTIONS, "inputs"):
        raise ValueError(S.E64_INVALID_SELECTION)
    found = [e for e in root.findall(section + "/*") if e.get("index") == index]
    if len(found) != 1:
        raise ValueError(S.E64_INVALID_SELECTION)
    if found[0].get("description", "") == value:
        return
    _xml_text(value)
    _validate_description(value)
    found[0].set("description", value)
    if section in SLOTS:
        found[0].set("isDirty", "True")


def description_parameters(
    device: Device, before: bytes | None, after: bytes
) -> list[tuple[str, str]]:
    """Resolve changed controller descriptions to their application parameter mirrors."""
    old = document(before) if before else ET.Element("device")
    new = document(after)
    changed: dict[tuple[str, str], str] = {}
    for section in ("groups", "ecgs", "mds", "gis", "pbs"):
        previous = {
            e.get("index"): e.get("description", "")
            for e in old.findall(section + "/*")
        }
        for e in new.findall(section + "/*"):
            index, value = e.get("index", ""), e.get("description", "")
            if "description" in e.attrib and previous.get(index) != value:
                changed[section, index] = value
    if not changed:
        return []
    return [
        (ref, changed[key])
        for key, ref in _description_bindings(device).items()
        if key in changed and device.get_param_value(ref) != changed[key]
    ]


def _description_bindings(device: Device) -> dict[tuple[str, str], str]:
    dynamic = device.dynamic_ui
    if dynamic is None or device.app is None:
        return {}
    definitions = device.app.tree_builder().idx.module_defs
    arguments = device.get_module_instance_arguments()
    bindings: dict[tuple[str, str], str] = {}
    kinds = {
        "ModuleECG": ("ecgs", "DCE1-Description"),
        "ModuleGRP": ("groups", "DCA1-Description"),
        "ModuleMD": ("mds", "DCM1-Description"),
        "ModuleGI": ("gis", "DCG1-Description"),
    }
    for instance, ref in device.get_module_instances():
        definition = definitions.get(ref)
        kind = kinds.get(definition.name) if definition is not None else None
        if kind is None:
            continue
        args = arguments.get(instance, [])
        index = int(args[-1][1]) - 1 if args else 0
        param = dynamic.find_parameter_ref("name", kind[1], instance)
        if param is not None:
            bindings[kind[0], str(index)] = param
    for index in range(8):
        param = dynamic.find_parameter_ref("name", f"DPB{index + 1}-Description")
        if param is not None:
            bindings["pbs", str(index)] = param
    return bindings


def template_mode_parameters(
    device: Device,
    before: bytes | None,
    after: bytes,
    *,
    imported_slots: frozenset[str] = frozenset(),
) -> list[tuple[str, str]]:
    """Resolve changed template modes to their application parameters."""
    dynamic = device.dynamic_ui
    if dynamic is None:
        return []
    previous = (
        {
            e.get("index"): e.get("mode")
            for e in document(before).findall("templates/Tpl")
        }
        if before
        else {}
    )
    edits: list[tuple[str, str]] = []
    for template in document(after).findall("templates/Tpl"):
        index, mode = template.get("index", ""), template.get("mode")
        if mode is None or (
            previous.get(index) == mode and index not in imported_slots
        ):
            continue
        _number(index, 15)
        if mode not in TEMPLATE_MODES:
            from editor_gui.plugins.project.strings import S

            raise ValueError(S.E64_INVALID_VALUE)
        ref = dynamic.find_parameter_ref("name", f"PROP-TEMPLATE_MODE.{int(index) + 1}")
        if ref is not None and device.get_param_value(ref) != TEMPLATE_MODES[mode]:
            edits.append((ref, TEMPLATE_MODES[mode]))
    return edits


def sync_parameters(device: Device, values: dict[str, str]) -> bytes | None:
    """Update stored descriptions and template modes affected by parameter edits."""
    entry = backup(device)
    if entry is None or device.dynamic_ui is None or not values:
        return None
    root = deepcopy(document(entry[1]))
    modes = {v: k for k, v in TEMPLATE_MODES.items()}
    changed = False
    for template in root.findall("templates/Tpl"):
        index = template.get("index", "")
        if not index.isdecimal() or not 0 <= int(index) < 16:
            continue
        ref = device.dynamic_ui.find_parameter_ref(
            "name", f"PROP-TEMPLATE_MODE.{int(index) + 1}"
        )
        value = values.get(ref) if ref is not None else None
        if value is None:
            continue
        if value not in modes:
            from editor_gui.plugins.project.strings import S

            raise ValueError(S.E64_INVALID_VALUE)
        if template.get("mode") != modes[value]:
            template.set("mode", modes[value])
            template.set("isDirty", "True")
            changed = True
    for (section, index), ref in _description_bindings(device).items():
        value = values.get(ref)
        if value is None:
            continue
        row = root.find(f"{section}/*[@index='{index}']")
        if row is not None and row.get("description", "") != value:
            _xml_text(value)
            row.set("description", value)
            changed = True
    return ET.tostring(root, encoding="utf-16le") if changed else None


def ecg_names(device: Device) -> dict[int, str]:
    entry = backup(device)
    if entry is None:
        return {}
    return {
        int(e.attrib["index"]): e.get("description", "")
        for e in document(entry[1]).findall("ecgs/ECG")
        if e.get("index", "").isdecimal()
    }


def _xml_text(value: str) -> None:
    from editor_gui.plugins.project.strings import S

    if any(
        (cp < 32 and cp not in (9, 10, 13))
        or 0xD800 <= cp <= 0xDFFF
        or cp in (0xFFFE, 0xFFFF)
        for cp in map(ord, value)
    ):
        raise ValueError(S.E64_INVALID_VALUE)


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


def edit_from_ref(data: bytes, ref_id: str, value: str) -> Edit | None:
    """Translate a detail row reference to the structured edit addressing."""
    from editor_gui.plugins.project.strings import S

    from . import nested_leaves

    parts = ref_id.split(":")
    if len(parts) not in (5, 7) or parts[0] != "dali_backup":
        raise ValueError(S.E64_INVALID_SELECTION)
    section, index = parts[2:4]
    if len(parts) == 5 and parts[4] == "description":
        return Edit(section, index, "description", value=value)
    if section not in SLOTS:
        return None
    if len(parts) == 5:
        return Edit(section, index, "header", field=parts[4], value=value)
    slot = slot_element(document(data), section, index)
    leaves = nested_leaves(slot)
    if parts[4] != "leaf" or not parts[5].isdecimal() or int(parts[5]) >= len(leaves):
        raise ValueError(S.E64_INVALID_SELECTION)
    target = leaves[int(parts[5])]
    sequence = rows(slot, section)
    if target not in sequence:
        raise ValueError(S.E64_UNSUPPORTED_OPERATION)
    return Edit(section, index, "set", sequence.index(target), parts[6], value)


def rows(slot: ET.Element, section: str) -> list[ET.Element]:
    tag = ROW_TAG[section]
    return slot.findall("Commands/" + tag if section == "templates" else tag)


def _number(text: str, maximum: int, minimum: int = 0) -> None:
    from editor_gui.plugins.project.strings import S

    if (
        not text.isascii()
        or not text.isdecimal()
        or not minimum <= int(text) <= maximum
    ):
        raise ValueError(S.E64_NUMBER_RANGE.format(minimum=minimum, maximum=maximum))


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
    name: ("0", "VFadeIn0") for name in ("SetValue", "SetMinValue", "SetMaxValue")
}
COMMAND_DEFAULTS.update(
    {
        func: (DEFAULT_COLOURS[mode], "CFadeIn1")
        for func, mode in TEMPLATE_COLOURS.items()
    }
)
COMMAND_DEFAULTS.update(
    SetMaxOnValue=("100", "VFadeIn0"),
    CallScene=("0", "VFadeIn0"),
    CallEffect=("0", "VFadeIn0"),
)
CALL_FUNCTIONS = {"CallScene": "scenes", "CallEffect": "effects"}
TEMPLATE_MODES = {"Disabled": "0", "Enabled": "1", "Controlled": "2"}
SCALE_MODES = {
    "CallScene": ("ScaleNoneScene",),
    "CallEffect": ("ScaleNoneEffectStop", "ScaleNoneEffectStart"),
}
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
    from editor_gui.plugins.project.strings import S

    if kind == "2":
        _number(value, 65535, 1)
        return
    if kind not in COLOURS:
        raise ValueError(S.E64_INVALID_COLOR)
    values = value.split(";")
    if kind in BYTE_COLOUR_MODES:
        # RGB also stores four bytes, but only exposes the first three channels.
        if len(values) != 4:
            raise ValueError(S.E64_INVALID_COLOR)
        for component in values:
            _number(component, 255)
        return
    white = kind == "32"
    if len(values) != (4 if white else 3) or not all(
        re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", v) and 0 <= float(v) <= 1
        for v in values[:3]
    ):
        raise ValueError(S.E64_INVALID_COLOR)
    if white:
        _number(values[3], 255)


def _set_colour(root: ET.Element, item: ET.Element, mode: str, value: str) -> None:
    from editor_gui.plugins.project.strings import S

    modes = colour_modes(root, item)
    if mode not in modes:
        raise ValueError(S.E64_INVALID_COLOR)
    if mode not in VERIFIED_COLOUR_MODES:
        raise ValueError(S.E64_INVALID_COLOR)
    _colour_value(mode, value)
    flags = int(item.get("setting", "0")) & ~4
    if len(modes) > 1 and mode != "2":
        flags |= 4
    item.attrib.update({"color-type": mode, "color": value, "setting": str(flags)})


def _check_capacity(root: ET.Element, section: str, additional: int = 0) -> None:
    from editor_gui.plugins.project.strings import S

    if section == "templates":
        path, limit, message = "templates/Tpl/Commands/TplCmd", 300, S.E64_COMMAND_LIMIT
    else:
        path, limit, message = "effects/Effect/EffectStep", 500, S.E64_EFFECT_LIMIT
    if len(root.findall(path)) + additional > limit:
        raise ValueError(message)


def _validate_description(value: str) -> None:
    from editor_gui.plugins.project.strings import S

    maximum = 60
    if len(value.encode("utf-16le")) // 2 > maximum:
        raise ValueError(S.E64_DESCRIPTION_LIMIT.format(maximum=maximum))


def _set(
    element: ET.Element, field: str, value: str, section: str, header: bool
) -> None:
    from editor_gui.plugins.project.strings import S

    if header:
        allowed = {
            "scenes": {"description", "knxScene", "fadeTime"},
            "effects": {"description", "mode"},
            "templates": {"description", "mode", "isOverwritable", "resetOnMidnight"},
        }
    else:
        allowed = {
            "scenes": {"value", "keepValue", "keepColor", "setting"},
            "effects": {
                "value",
                "keepValue",
                "keepColor",
                "fadetime",
                "delay",
                "setting",
            },
            "templates": {"value", "time", "weekdays", "func", "fadeIn", "scaleMode"},
        }
    if field not in allowed[section]:
        raise ValueError(S.E64_UNSUPPORTED_OPERATION)
    _xml_text(value)
    if field in (
        "keepValue",
        "keepColor",
        "isOverwritable",
        "resetOnMidnight",
    ) and value not in ("True", "False"):
        raise ValueError(S.E64_INVALID_VALUE)
    if field in ("keepValue", "keepColor"):
        mask = 2 if field == "keepValue" else 1
        flags = int(element.get("setting", "0"))
        if field == "keepColor" and "color" not in element.attrib:
            raise ValueError(S.E64_INVALID_COLOR)
        element.set("setting", str(flags | mask if value == "True" else flags & ~mask))
        return
    if field == "description":
        _validate_description(value)
    if field == "setting":
        _number(value, 255)
    if field == "knxScene":
        _number(value, 64, 1)
    if field == "value" and (
        section != "templates"
        or element.get("func")
        in ("SetValue", "SetMaxOnValue", "SetMinValue", "SetMaxValue")
    ):
        _number(
            value,
            100,
            1
            if section == "templates" and element.get("func") == "SetMaxOnValue"
            else 0,
        )
    if field == "mode" and value not in (
        TEMPLATE_MODES if section == "templates" else ("0", "1")
    ):
        raise ValueError(S.E64_INVALID_VALUE)
    if (
        field in ("fadeTime", "fadetime", "delay", "func")
        and value not in EDITABLE_ENUMS[field]
    ):
        raise ValueError(S.E64_INVALID_VALUE)
    if field == "time" and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError(S.E64_TIME_FORMAT)
    if field == "weekdays" and not re.fullmatch("[01]{7}", value):
        raise ValueError(S.E64_WEEKDAYS_FORMAT)
    if field == "fadeIn" and value not in fade_choices(element):
        raise ValueError(S.E64_INVALID_VALUE)
    if field == "func" and value != element.get("func"):
        if value not in COMMAND_DEFAULTS:
            raise ValueError(S.E64_UNSUPPORTED_OPERATION)
        level, fade = COMMAND_DEFAULTS[value]
        element.attrib.update(
            value=level,
            fadeIn=fade,
            scaleMode=SCALE_MODES.get(value, ("ScaleNormal",))[0],
        )
    if field == "scaleMode" and value not in SCALE_MODES.get(
        element.get("func", ""), ("ScaleNormal",)
    ):
        raise ValueError(S.E64_INVALID_VALUE)
    if field == "value" and section == "templates":
        if element.get("func") in CALL_FUNCTIONS:
            _number(value, 15)
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
    from editor_gui.plugins.project.strings import S

    root = deepcopy(document(data))
    if edit.action in (
        "description",
        "assign",
        "import",
        "import_descriptions",
        "clear_descriptions",
    ):
        if edit.action == "description":
            _description(root, edit.section, edit.slot, edit.value)
        elif edit.action == "import_descriptions":
            from .e64pro_descriptions import parse_descriptions

            for index, value in parse_descriptions(
                root, edit.section, edit.value, edit.field
            ).items():
                _description(root, edit.section, index, value)
        elif edit.action == "clear_descriptions":
            if edit.section not in DESCRIPTION_SECTIONS:
                raise ValueError(S.E64_INVALID_SELECTION)
            for item in root.findall(edit.section + "/*"):
                _description(root, edit.section, item.get("index", ""), "")
        elif edit.action == "assign":
            from .e64pro_inputs import assign

            assign(root, edit.section, edit.slot, edit.row, edit.index)
        else:
            _import_slot(root, edit.section, edit.slot, edit.value)
        return (
            data
            if ET.tostring(root) == ET.tostring(document(data))
            else ET.tostring(root, encoding="unicode").encode("utf-16le")
        )
    slot = slot_element(root, edit.section, edit.slot)
    section, action = edit.section, edit.action
    if action == "member" and section != "templates":
        raise ValueError(S.E64_UNSUPPORTED_OPERATION)
    members = section == "templates" and action == "member"
    parent = slot
    if (section in ("effects", "templates") and action == "add") or (
        section == "templates" and action in ("copy", "insert")
    ):
        _check_capacity(root, section, additional=1)
    if section == "templates" and action != "header":
        container = "Members" if members else "Commands"
        existing = slot.find(container)
        parent = existing if existing is not None else ET.SubElement(slot, container)
    children = parent.findall("TplItem" if members else ROW_TAG[section])
    if action == "header":
        _set(slot, edit.field, edit.value, section, True)
    elif section == "templates" and action in ("clear", "sort_time", "sort_function"):
        if action == "clear":
            for child in children:
                parent.remove(child)
        else:
            functions = {
                name: n
                for n, name in enumerate(
                    (
                        "None",
                        "SetValue",
                        "SetMinValue",
                        "SetMaxValue",
                        "ColourTemp",
                        "ColourXY",
                        "ColourRGBW",
                        "ColourRGB",
                        "ColourHSV",
                        "ColourHSVW",
                        "SetMaxOnValue",
                        "CallScene",
                        "CallEffect",
                    )
                )
            }

            def key(child: ET.Element) -> tuple[int, ...]:
                value = child.get("time", "")
                _set(deepcopy(child), "time", value, section, False)
                if child.get("func", "") not in functions:
                    raise ValueError(S.E64_INVALID_VALUE)
                hour, minute = (int(part) for part in value.split(":"))
                func = functions[child.get("func", "")]
                return (
                    (func, hour, minute)
                    if action == "sort_function"
                    else (hour, minute, func)
                )

            ordered = iter(sorted(children, key=key))
            parent[:] = [
                next(ordered) if child in children else child for child in parent
            ]
    elif action in ("add", "member", "insert"):
        if action == "insert" and (
            section != "templates" or not 0 <= edit.row < len(children)
        ):
            raise ValueError(S.E64_INVALID_SELECTION)
        if section == "templates" and not members:
            command = ET.Element(
                "TplCmd",
                func="SetValue",
                value="0",
                scaleMode="ScaleNormal",
                fadeIn="VFadeIn0",
                time="00:00",
                weekdays="1111111",
            )
            if action == "insert":
                parent.insert(list(parent).index(children[edit.row]), command)
            else:
                parent.append(command)
        else:
            if edit.target not in TARGETS or not any(
                e.get("index") == edit.index for e in root.findall(edit.target + "/*")
            ):
                raise ValueError(S.E64_INVALID_SELECTION)
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
                raise ValueError(S.E64_INVALID_VALUE)
            if members and edit.value == "False":
                if match is None:
                    return data
                parent.remove(match)
            else:
                if match is not None and section != "effects":
                    if members:
                        return data
                    raise ValueError(S.E64_DUPLICATE_TARGET)
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
            raise ValueError(S.E64_INVALID_SELECTION)
        child = children[edit.row]
        if action == "set":
            if edit.field == "color" and section in ("scenes", "effects"):
                _set_colour(root, child, child.get("color-type", ""), edit.value)
            else:
                _set(child, edit.field, edit.value, section, False)
        elif action == "colour" and section in ("scenes", "effects"):
            _set_colour(root, child, edit.field, edit.value)
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
            raise ValueError(S.E64_UNSUPPORTED_OPERATION)
    if ET.tostring(root) == ET.tostring(document(data)):
        return data
    slot.set("isDirty", "True")
    return ET.tostring(root, encoding="unicode").encode("utf-16le")
