"""Shared logical input addressing for saved configurations and bus readback."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from xknxeditor.dali.connection import DaliCommissioningError

INPUT_SECTIONS = {"mds": 0, "pbs": 1, "gis": 2}
INPUT_TYPES = {
    "GenericSensor": 0,
    "PushButton": 1,
    "ValueInSensor": 2,
    "MotionSensor": 3,
    "BrightnessSensor": 4,
    "EventingSensor": 6,
}


def input_type(item: ET.Element) -> int:
    raw = item.get("type", "")
    return int(raw) if raw.isdecimal() else INPUT_TYPES.get(raw, 255)


def compatible_types(logical: int, physical: int) -> bool:
    return logical in INPUT_TYPES.values() and (
        logical == physical
        or (logical == 0 and physical in (2, 6))
        or (physical == 0 and logical in (2, 6))
    )


def input_encoding(
    section: str, controller: ET.Element, row: int, instance: ET.Element
) -> tuple[int, int, int]:
    index = int(controller.get("index", "-1"))
    if section not in INPUT_SECTIONS or not 0 <= index < 8 or row < 0:
        raise DaliCommissioningError("invalid input controller")
    subtype = input_type(instance)
    if section == "pbs":
        count = int(controller.get("noi", "0"))
        buttons = int(controller.get("fold", "0"))
        if not 1 <= count <= 15 or not 1 <= buttons <= 4 or subtype != 1:
            raise DaliCommissioningError("invalid button controller layout")
        number, detail = row % count, row // count
        coded_count = buttons | count << 4
    elif section == "mds":
        presence = int(controller.get("nop", "0"))
        brightness = int(controller.get("nob", "0"))
        if (
            not 0 <= presence <= 15
            or not 0 <= brightness <= 15
            or subtype not in (3, 4)
        ):
            raise DaliCommissioningError("invalid motion controller layout")
        number = row
        if controller.get("sensors-avail") == "1" and subtype == 4:
            number -= presence
        detail = int(subtype == 4)
        coded_count = presence | brightness << 4
        if number >= (brightness if subtype == 4 else presence):
            raise DaliCommissioningError("input instance exceeds controller layout")
    else:
        if row or subtype not in (0, 2, 6):
            raise DaliCommissioningError("invalid generic controller layout")
        number, detail, coded_count = 0, 0, 1
    if not 0 <= number < 16 or not 0 <= detail < 16:
        raise DaliCommissioningError("invalid logical input instance")
    return (index << 3) | INPUT_SECTIONS[section], (number << 4) | detail, coded_count


def logical_layout(root: ET.Element) -> dict[tuple[int, int], tuple[ET.Element, int]]:
    result: dict[tuple[int, int], tuple[ET.Element, int]] = {}
    for section in INPUT_SECTIONS:
        seen: set[int] = set()
        for controller in root.findall(section + "/*"):
            index = int(controller.get("index", "-1"))
            if index in seen:
                raise DaliCommissioningError("duplicate input controller")
            seen.add(index)
            for row, instance in enumerate(controller.findall("Instances/EtsInstance")):
                if instance.get("index") != str(row):
                    raise DaliCommissioningError("invalid logical input row")
                code, detail, count = input_encoding(section, controller, row, instance)
                if (code, detail) in result:
                    raise DaliCommissioningError("duplicate logical input instance")
                result[code, detail] = instance, count
    return result
