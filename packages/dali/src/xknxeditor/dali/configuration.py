"""Compile saved scenes, effects and schedules to validated bus models."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from xknxeditor.dali.connection import DaliCommissioningError
from xknxeditor.dali.inputs import InputIdentity
from xknxeditor.dali.model import (
    EcgAssignment,
    Effect,
    EffectStep,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)

FUNCTIONS = {
    name: number
    for number, name in enumerate(
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
        )
    )
} | {"SetMaxOnValue": 16, "CallScene": 17, "CallEffect": 18}
COLOR_FUNCTIONS = {4: 2, 5: 1, 6: 8, 7: 4, 8: 16, 9: 32}
COLOR_FADE = {1: 0, 5: 7, 10: 9, 20: 11, 30: 12, 60: 14, 90: 15}
MODES = {"Disabled": 0, "Enabled": 1, "Controlled": 2}
COLOR_TYPES = {
    4: (2,),
    5: (1,),
    6: (8,),
    7: (4,),
    8: (16,),
    9: (32,),
    10: (2, 8),
    11: (2, 4),
    12: (2, 16),
    13: (2, 32),
}


def integer(value: str, maximum: int, minimum: int = 0) -> int:
    if (
        not value.isascii()
        or not value.isdecimal()
        or not minimum <= int(value) <= maximum
    ):
        raise DaliCommissioningError(
            f"invalid number {value!r}; expected {minimum} to {maximum}"
        )
    return int(value)


def _single(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def level(value: str) -> int:
    """Convert a saved percentage using the single-precision scene scale."""
    return round(_single(integer(value, 100) * _single(2.55)))


def color_bytes(kind: int, value: str) -> bytes:
    """Encode the saved colour space without converting it through RGB."""
    if kind in (0, 255):
        return bytes(4)
    if kind == 2:
        value = value.removesuffix(";0;0;0")
        return struct.pack("<H", integer(value, 65535)) + bytes(2)
    parts = value.split(";")
    if kind in (4, 8):
        if len(parts) != 4:
            raise DaliCommissioningError("invalid RGB colour")
        rgbw = [integer(v, 255) for v in parts]
        if kind == 4:
            rgbw[3] = 0
        return bytes(rgbw)
    if kind not in (1, 16, 32) or len(parts) != (4 if kind == 32 else 3):
        raise DaliCommissioningError("invalid colour encoding")
    values = [float(v.replace(",", ".")) for v in parts[:3]]
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise DaliCommissioningError("colour components must be between 0 and 1")
    if kind == 1:
        return struct.pack(
            "<HH", *(round(_single(_single(v) * 65535)) for v in values[:2])
        )
    return bytes(
        [
            *(round(_single(v) * 255) for v in values),
            integer(parts[3], 255) if kind == 32 else 0,
        ]
    )


def _slots(root: ET.Element, section: str, tag: str) -> dict[int, ET.Element]:
    container = root.find(section)
    if container is None:
        raise DaliCommissioningError(f"missing {section} configuration")
    result: dict[int, ET.Element] = {}
    for item in container.findall(tag):
        index = integer(item.get("index", ""), 15)
        if index in result:
            raise DaliCommissioningError(f"duplicate {section} index")
        result[index] = item
    if len(result) != 16:
        raise DaliCommissioningError(f"expected all 16 {section} slots")
    return result


def _target(root: ET.Element, item: ET.Element) -> int:
    kind = item.get("itemtype", "").rsplit(".", 1)[-1]
    section = {"Group": "groups", "ECG": "ecgs"}.get(kind)
    if section is None:
        raise DaliCommissioningError("invalid target type")
    index = integer(item.get("itemindex", ""), 15 if kind == "Group" else 63)
    found = [e for e in root.findall(section + "/*") if e.get("index") == str(index)]
    if len(found) != 1:
        raise DaliCommissioningError("target missing from configuration")
    if kind == "ECG" and integer(found[0].get("groupindex", "255"), 255) & 63 < 16:
        raise DaliCommissioningError(
            f"ECG {index + 1} belongs to a group; select its group instead"
        )
    return index if kind == "Group" else 16 + index


def _value(root: ET.Element, item: ET.Element) -> tuple[int, bytes, int]:
    target = _target(root, item)
    section, index = ("groups", target) if target < 16 else ("ecgs", target - 16)
    row = next(e for e in root.findall(section + "/*") if e.get("index") == str(index))
    modes = COLOR_TYPES.get(integer(row.get("ctype", "0"), 255), ())
    kind = integer(item.get("color-type", "0"), 255)
    flags = integer(item.get("setting", "0"), 255)
    if (
        "ctype" in row.attrib
        and kind not in (0, 255)
        and (
            kind not in modes
            or (len(modes) > 1 and bool(flags & 4) != (kind == modes[-1]))
        )
    ):
        raise DaliCommissioningError("colour encoding does not match target mode")
    return (
        level(item.get("value", "0")),
        color_bytes(kind, item.get("color", "")),
        flags,
    )


def scenes_from_xml(root: ET.Element) -> list[Scene]:
    result: list[Scene] = []
    for index, slot in _slots(root, "scenes", "Scene").items():
        scene = Scene(
            index,
            integer(slot.get("fadeTime", "0"), 255),
            integer(slot.get("knxScene", "0"), 64),
        )
        for item in slot.findall("SceneItem"):
            scene.values.append(
                SceneValue(index, _target(root, item), *_value(root, item))
            )
        result.append(scene)
    return result


def effects_from_xml(root: ET.Element) -> list[Effect]:
    result: list[Effect] = []
    for index, slot in _slots(root, "effects", "Effect").items():
        effect = Effect(index, integer(slot.get("mode", "0"), 255))
        for item in slot.findall("EffectStep"):
            effect.steps.append(
                EffectStep(
                    _target(root, item),
                    *_value(root, item),
                    integer(item.get("delay", "0"), 200),
                    integer(item.get("fadetime", "0"), 15),
                )
            )
        result.append(effect)
    return result


def template_command(item: ET.Element) -> TemplateCommand:
    func = FUNCTIONS.get(item.get("func", ""))
    if not func:
        raise DaliCommissioningError("unknown template function")
    value = item.get("value", "0")
    if func in COLOR_FUNCTIONS:
        encoded = color_bytes(COLOR_FUNCTIONS[func], value)
    elif func in (17, 18):
        scale = item.get(
            "scaleMode", "ScaleNoneScene" if func == 17 else "ScaleNoneEffectStop"
        )
        if scale not in (
            ("ScaleNoneScene",)
            if func == 17
            else ("ScaleNoneEffectStart", "ScaleNoneEffectStop")
        ):
            raise DaliCommissioningError("invalid call action")
        encoded = bytes(
            (integer(value, 15), int(scale == "ScaleNoneEffectStart"), 0, 0)
        )
    else:
        encoded = bytes(
            (round(integer(value, 100, 1 if func == 16 else 0) * 255 / 100), 0, 0, 0)
        )
    fade = item.get("fadeIn", "CFadeIn1" if func in COLOR_FUNCTIONS else "VFadeIn0")
    if func in COLOR_FUNCTIONS:
        if not fade.startswith("CFadeIn") or int(fade[7:]) not in COLOR_FADE:
            raise DaliCommissioningError("invalid colour fade time")
        fade_time = COLOR_FADE[int(fade[7:])]
    else:
        if not fade.startswith("VFadeIn"):
            raise DaliCommissioningError("invalid value fade time")
        fade_time = integer(fade[7:], 200)
        if fade_time not in (*range(11), *range(20, 201, 10)):
            raise DaliCommissioningError("invalid value fade time")
    if func in (2, 3, 17, 18):
        fade_time = 0
    time = item.get("time", "").split(":")
    weekdays = item.get("weekdays", "")
    if (
        len(time) != 2
        or any(len(part) != 2 for part in time)
        or len(weekdays) != 7
        or not set(weekdays) <= {"0", "1"}
    ):
        raise DaliCommissioningError("invalid command time or weekdays")
    return TemplateCommand(
        func,
        integer(time[0], 23),
        integer(time[1], 59),
        encoded,
        fade_time,
        int(weekdays, 2),
    )


def templates_from_xml(root: ET.Element) -> list[Template]:
    result: list[Template] = []
    for index, slot in _slots(root, "templates", "Tpl").items():
        mode = MODES.get(slot.get("mode", "Disabled"))
        if mode is None:
            raise DaliCommissioningError("invalid template mode")
        overwrite, reset = (
            slot.get("isOverwritable", "False"),
            slot.get("resetOnMidnight", "True"),
        )
        if overwrite not in ("True", "False") or reset not in ("True", "False"):
            raise DaliCommissioningError("invalid template option")
        targets = [_target(root, item) for item in slot.findall("Members/TplItem")]
        if len(targets) != len(set(targets)):
            raise DaliCommissioningError("duplicate template member")
        result.append(
            Template(
                index,
                mode,
                overwrite == "True",
                tuple(t for t in targets if t < 16),
                tuple(t - 16 for t in targets if t >= 16),
                [template_command(item) for item in slot.findall("Commands/TplCmd")],
                reset == "True",
            )
        )
    return result


@dataclass(frozen=True)
class Configuration:
    scenes: tuple[Scene, ...]
    effects: tuple[Effect, ...]
    templates: tuple[Template, ...]
    assignments: tuple[EcgAssignment, ...]
    inputs: bytes | None = None
    input_inventory: tuple[InputIdentity, ...] | None = None


def compile_configuration(data: bytes) -> Configuration:
    """Build all tables before opening a gateway connection."""
    try:
        root = ET.fromstring(data)
        if root.tag != "device" or root.get("type") not in ("e64pro", "gc16"):
            raise DaliCommissioningError("unsupported DALI configuration")
        extended = root.get("type") == "e64pro"
        expected_sections = {"groups": 16, "ecgs": 64}
        if extended:
            expected_sections.update(mds=8, pbs=8, gis=8)
            if len(root.findall("inputs")) != 1:
                raise DaliCommissioningError("missing or duplicate input inventory")
        for section, count in expected_sections.items():
            parents = root.findall(section)
            if len(parents) != 1:
                raise DaliCommissioningError(f"missing or duplicate {section}")
            indexes = [integer(item.get("index", ""), count - 1) for item in parents[0]]
            if len(indexes) != count or len(set(indexes)) != count:
                raise DaliCommissioningError(f"incomplete or duplicate {section} slots")
        assignments: list[EcgAssignment] = []
        physical: set[int] = set()
        logical_indexes: set[int] = set()
        for item in root.findall("ecgs/ECG"):
            address = integer(item.get("short-address", "255"), 255)
            if address == 255:
                continue
            index = integer(item.get("index", ""), 63)
            if address >= 64 or address in physical:
                raise DaliCommissioningError("invalid or duplicate ECG short address")
            physical.add(address)
            logical_indexes.add(index)
            group = integer(item.get("groupindex", "32"), 255)
            assignments.append(EcgAssignment(address, index, group))
        for item in root.findall("devecgs/DevECG"):
            address = integer(item.get("index", ""), 63)
            logical = integer(item.get("short-address", "255"), 255)
            if (
                address not in physical
                and logical < 64
                and logical not in logical_indexes
            ):
                assignments.append(EcgAssignment(address, 255, 32))
        result = Configuration(
            tuple(scenes_from_xml(root)),
            tuple(effects_from_xml(root)) if extended else (),
            tuple(templates_from_xml(root)),
            tuple(assignments),
            inputs_from_xml(root) if extended else None,
            input_inventory_from_xml(root) if extended else None,
        )
        validate_configuration(result)
        return result
    except (ET.ParseError, ValueError, OverflowError) as exc:
        raise DaliCommissioningError(f"invalid DALI configuration: {exc}") from exc


def validate_configuration(configuration: Configuration) -> None:
    from xknxeditor.dali.effects import effect_plan
    from xknxeditor.dali.inputs import validate_assignments

    assigned = [a.ets_index for a in configuration.assignments if a.ets_index != 255]
    if len(set(assigned)) != len(assigned) or any(
        a.group_index not in (*range(16), 16, 32, 255)
        for a in configuration.assignments
    ):
        raise DaliCommissioningError("invalid or duplicate ECG assignment")
    if sum(len(s.values) for s in configuration.scenes) > 1024:
        raise DaliCommissioningError("scene table capacity exceeded")
    if any(
        len({v.target_index for v in scene.values}) != len(scene.values)
        for scene in configuration.scenes
    ):
        raise DaliCommissioningError("duplicate scene target")
    if sum(len(t.commands) for t in configuration.templates) > 300:
        raise DaliCommissioningError("template command capacity exceeded")
    effect_plan(configuration.effects)
    if configuration.inputs is not None:
        validate_assignments(configuration.inputs)


def input_inventory_from_xml(root: ET.Element) -> tuple[InputIdentity, ...]:
    from xknxeditor.dali.input_configuration import input_type

    result: list[InputIdentity] = []
    for device in root.findall("inputs/DevInput"):
        address = integer(device.get("index", ""), 63)
        try:
            long_address = int(device.get("long-address", ""), 16)
        except ValueError as exc:
            raise DaliCommissioningError("invalid input long address") from exc
        if not 0 <= long_address <= 0xFFFFFF:
            raise DaliCommissioningError("invalid input long address")
        for instance in device.findall("Instances/DevInputInstance"):
            result.append(
                InputIdentity(
                    integer(instance.get("index", ""), 255),
                    address,
                    long_address,
                    input_type(instance),
                    integer(instance.get("number", ""), 254),
                    integer(instance.get("resolution", "0"), 255),
                )
            )
    return tuple(result)


def inputs_from_xml(root: ET.Element) -> bytes:
    """Compile physical-instance mappings, including explicit removals."""
    physical: dict[int, ET.Element] = {}
    addresses: set[int] = set()
    for device in root.findall("inputs/DevInput"):
        address = integer(device.get("index", ""), 63)
        if address in addresses:
            raise DaliCommissioningError("duplicate physical input address")
        addresses.add(address)
        start = integer(device.get("start-index", ""), 255)
        count = integer(device.get("noi", ""), 254, 1)
        instances = device.findall("Instances/DevInputInstance")
        if len(instances) != count:
            raise DaliCommissioningError("incomplete physical input inventory")
        numbers: set[int] = set()
        for instance in instances:
            index = integer(instance.get("index", ""), 255)
            number = integer(instance.get("number", ""), 254)
            if number in numbers:
                raise DaliCommissioningError("duplicate input instance number")
            numbers.add(number)
            if index in physical or not start <= index < start + count <= 256:
                raise DaliCommissioningError(
                    "invalid or duplicate physical input index"
                )
            physical[index] = instance
    table = bytearray(b"\xff" * (256 * 3))
    from xknxeditor.dali.input_configuration import (
        compatible_types,
        input_type,
        logical_layout,
    )

    linked: set[int] = set()
    removals: dict[int, int] = {}
    for (encoded, detail), (logical, counts) in logical_layout(root).items():
        ref = int(logical.get("ref-idx", "-1"))
        origin = int(logical.get("origin", "-1"))
        if not -1 <= ref <= 255 or not -1 <= origin <= 255:
            raise DaliCommissioningError("invalid input reference")
        if 0 <= origin < 254 and origin != ref:
            if origin in removals:
                raise DaliCommissioningError("conflicting input origins")
            removals[origin] = encoded
        if ref < 0 or ref >= 254:
            continue
        target = physical.get(ref)
        if (
            target is None
            or ref in linked
            or target.get("ets-index") != str(encoded)
            or target.get("ets-detail") != str(detail)
            or not compatible_types(input_type(logical), input_type(target))
        ):
            raise DaliCommissioningError("invalid or conflicting input link")
        linked.add(ref)
        table[ref * 3 : ref * 3 + 3] = bytes((encoded, detail, counts))
    for origin, encoded in removals.items():
        if origin not in linked:
            table[origin * 3 : origin * 3 + 3] = bytes((254, encoded, 0))
    for index, target in physical.items():
        if target.get("ets-index", "255") != "255" and index not in linked:
            raise DaliCommissioningError(
                "physical input has no reciprocal logical link"
            )
    return bytes(table)
