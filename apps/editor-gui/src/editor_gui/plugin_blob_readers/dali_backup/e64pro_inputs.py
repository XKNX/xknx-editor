"""Logical and physical input assignments in the saved device configuration."""

from __future__ import annotations

from xml.etree import ElementTree as ET

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


def compatible(logical: ET.Element, physical: ET.Element) -> bool:
    a, b = input_type(logical), input_type(physical)
    return a in INPUT_TYPES.values() and (
        a == b or (a == 0 and b in (2, 6)) or (b == 0 and a in (2, 6))
    )


def physical_inputs(root: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    return [
        (device, instance)
        for device in root.findall("inputs/DevInput")
        for instance in device.findall("Instances/DevInputInstance")
    ]


def logical_inputs(root: ET.Element) -> list[ET.Element]:
    return [
        instance
        for section in INPUT_SECTIONS
        for instance in root.findall(f"{section}/*/Instances/EtsInstance")
    ]


def _encoding(
    section: str, controller: ET.Element, row: int, instance: ET.Element
) -> tuple[int, int]:
    from editor_gui.plugins.project.strings import S

    index = int(controller.get("index", "-1"))
    if not 0 <= index < 8:
        raise ValueError(S.E64_INVALID_SELECTION)
    subtype = input_type(instance)
    if section == "pbs":
        count = int(controller.get("noi", "0"))
        if count <= 0:
            raise ValueError(S.E64_INVALID_CONFIGURATION)
        number, detail = row % count, row // count
    elif section == "mds":
        number = row
        if controller.get("sensors-avail") == "1" and subtype == 4:
            number -= int(controller.get("nop", "0"))
        detail = int(subtype == 4)
    else:
        number, detail = 0, 0
    if not 0 <= number < 16 or not 0 <= detail < 16:
        raise ValueError(S.E64_INVALID_SELECTION)
    return (index << 3) | INPUT_SECTIONS[section], (number << 4) | detail


def assign(
    root: ET.Element, section: str, index: str, row: int, reference: str
) -> None:
    from editor_gui.plugins.project.strings import S

    if section not in INPUT_SECTIONS:
        raise ValueError(S.E64_INVALID_SELECTION)
    controllers = [e for e in root.findall(section + "/*") if e.get("index") == index]
    if len(controllers) != 1:
        raise ValueError(S.E64_INVALID_SELECTION)
    controller = controllers[0]
    instances = controller.findall("Instances/EtsInstance")
    if not 0 <= row < len(instances):
        raise ValueError(S.E64_INVALID_SELECTION)
    logical = instances[row]
    if logical.get("index") != str(row):
        raise ValueError(S.E64_INVALID_CONFIGURATION)
    encoded_index, detail = _encoding(section, controller, row, logical)
    physical = physical_inputs(root)
    selected: ET.Element | None = None
    if reference:
        if (
            not reference.isascii()
            or not reference.isdecimal()
            or str(int(reference)) != reference
            or not 0 <= int(reference) < 254
        ):
            raise ValueError(S.E64_INVALID_SELECTION)
        found = [
            (device, item)
            for device, item in physical
            if item.get("index") == reference
        ]
        if len(found) != 1 or not compatible(logical, found[0][1]):
            raise ValueError(S.E64_INPUT_INCOMPATIBLE)
        device, selected = found[0]
        if not 0 <= int(device.get("index", "-1")) < 64:
            raise ValueError(S.E64_INVALID_SELECTION)
        start = int(device.get("start-index", "-1"))
        count = int(device.get("noi", "0"))
        number = int(selected.get("number", "-1"))
        if not (
            0 <= start <= int(reference) < start + count <= 256 and 0 <= number < 255
        ):
            raise ValueError(S.E64_INVALID_CONFIGURATION)
        if (
            sum(
                e.get("number") == str(number)
                for e in device.findall("Instances/DevInputInstance")
            )
            != 1
        ):
            raise ValueError(S.E64_INVALID_CONFIGURATION)
        if any(
            e is not logical and e.get("ref-idx") == reference
            for e in logical_inputs(root)
        ):
            raise ValueError(S.E64_INPUT_ASSIGNED)
        if selected.get("ets-index", "255") != "255" and (
            selected.get("ets-index") != str(encoded_index)
            or selected.get("ets-detail") != str(detail)
        ):
            raise ValueError(S.E64_INPUT_ASSIGNED)
    previous = int(logical.get("ref-idx", "-1"))
    origin = int(logical.get("origin", "-1"))
    new = int(reference) if reference else -1
    if previous == new:
        if selected is not None:
            selected.set("ets-index", str(encoded_index))
            selected.set("ets-detail", str(detail))
        return
    for _, old in physical:
        if (
            old.get("index") == str(previous)
            and old.get("ets-index") == str(encoded_index)
            and old.get("ets-detail") == str(detail)
        ):
            old.set("ets-index", "255")
            old.set("ets-detail", "0")
    if new < 0:
        flags = 2 if 0 <= origin < 254 and 0 <= previous < 254 else 1
    elif new == origin:
        flags = 4
    else:
        flags = (
            6
            if 0 <= origin < 254
            and (0 <= previous < 254 or logical.get("flags") == "2")
            else 3
        )
    logical.set("ref-idx", str(new))
    logical.set("flags", str(flags))
    if selected is not None:
        selected.set("ets-index", str(encoded_index))
        selected.set("ets-detail", str(detail))
