"""Merge gateway readback into the saved configuration without discarding metadata."""

from __future__ import annotations

import struct
from xml.etree import ElementTree as ET

from xknxeditor.dali.configuration import (
    COLOR_FADE,
    COLOR_FUNCTIONS,
    COLOR_TYPES,
    FUNCTIONS,
    MODES,
)
from xknxeditor.dali.connection import DaliCommissioningError
from xknxeditor.dali.input_configuration import (
    compatible_types,
    input_type,
    logical_layout,
)
from xknxeditor.dali.inputs import InputDevice
from xknxeditor.dali.model import Effect, Scene, Template


def color_text(kind: int, data: bytes) -> str:
    if kind == 2:
        return str(int.from_bytes(data[:2], "little"))
    if kind == 1:
        x, y = struct.unpack("<HH", data)
        return f"{x / 65535:.9f};{y / 65535:.9f};1.000000000"
    if kind in (4, 8):
        return ";".join(str(v) for v in data)
    if kind in (16, 32):
        result = ";".join(f"{v / 255:.9f}" for v in data[:3])
        return result + (";" + str(data[3]) if kind == 32 else "")
    return ""


def _item(
    root: ET.Element,
    parent: ET.Element,
    tag: str,
    target: int,
    level: int,
    color: bytes,
    flags: int,
) -> ET.Element | None:
    group = target < 16
    number = target if group else target - 16
    section = "groups" if group else "ecgs"
    target_item = next(
        e for e in root.findall(section + "/*") if e.get("index") == str(number)
    )
    if not group and int(target_item.get("groupindex", "255")) & 63 < 16:
        return None
    namespace = "Dalie64" if root.get("type") == "e64pro" else "Dali"
    attrs = {
        "itemtype": f"{namespace}.DcaApp." + ("Group" if group else "ECG"),
        "itemindex": str(number),
        "value": str(round(level * 100 / 255)),
        "setting": str(flags),
    }
    ctype = target_item.get("ctype")
    modes = COLOR_TYPES.get(int(ctype or "0"), ())
    if ctype is None:
        previous = next(
            (
                e
                for e in parent.findall(tag)
                if e.get("itemindex") == str(number)
                and e.get("itemtype", "").endswith(".Group" if group else ".ECG")
            ),
            None,
        )
        if previous is not None:
            kind = int(previous.get("color-type", "0"))
            modes = (kind,) if kind in (1, 2, 4, 8, 16, 32) else ()
    if modes:
        kind = modes[-1] if flags & 4 else modes[0]
        attrs.update({"color-type": str(kind), "color": color_text(kind, color)})
    return ET.SubElement(parent, tag, attrs)


def merge_scenes(data: bytes, scenes: list[Scene]) -> bytes:
    root = ET.fromstring(data)
    for scene in scenes:
        slot = next(
            e
            for e in root.findall("scenes/Scene")
            if e.get("index") == str(scene.index)
        )
        old_rows = slot.findall("SceneItem")
        slot.set("fadeTime", str(scene.fade_time))
        if scene.knx_mapping_available:
            slot.set("knxScene", str(scene.knx_scene))
        slot.set("isDirty", "False")
        for value in scene.values:
            _item(
                root,
                slot,
                "SceneItem",
                value.target_index,
                value.level,
                value.color,
                value.flags,
            )
        for row in old_rows:
            slot.remove(row)
    return ET.tostring(root, encoding="utf-16le")


def merge_effects(data: bytes, effects: list[Effect]) -> bytes:
    root = ET.fromstring(data)
    for effect in effects:
        slot = next(
            e
            for e in root.findall("effects/Effect")
            if e.get("index") == str(effect.index)
        )
        old_rows = slot.findall("EffectStep")
        slot.set("mode", str(effect.mode))
        slot.set("isDirty", "False")
        for step in effect.steps:
            item = _item(
                root,
                slot,
                "EffectStep",
                step.target_index,
                step.level,
                step.color,
                step.flags,
            )
            if item is None:
                continue
            item.set("delay", str(step.delay))
            item.set("fadetime", str(step.fade_time))
        for row in old_rows:
            slot.remove(row)
    return ET.tostring(root, encoding="utf-16le")


def merge_templates(data: bytes, templates: list[Template]) -> bytes:
    root = ET.fromstring(data)
    funcs = {v: k for k, v in FUNCTIONS.items()}
    modes = {v: k for k, v in MODES.items()}
    fades = {v: k for k, v in COLOR_FADE.items()}
    namespace = "Dalie64" if root.get("type") == "e64pro" else "Dali"
    for template in templates:
        slot = next(
            e
            for e in root.findall("templates/Tpl")
            if e.get("index") == str(template.index)
        )
        if template.commands:
            slot.set("mode", modes[template.mode])
            slot.set("isOverwritable", str(template.overwritable))
            if root.get("type") == "e64pro":
                slot.set("resetOnMidnight", str(template.reset_on_midnight))
        slot.set("isDirty", "False")
        for name, tag in [("Members", "TplItem"), ("Commands", "TplCmd")]:
            parent = slot.find(name)
            if parent is None:
                parent = ET.SubElement(slot, name)
            for child in parent.findall(tag):
                parent.remove(child)
        members = slot.find("Members")
        commands = slot.find("Commands")
        assert members is not None and commands is not None
        for kind, numbers in [("Group", template.groups), ("ECG", template.ecgs)]:
            for index in numbers:
                ET.SubElement(
                    members,
                    "TplItem",
                    itemtype=f"{namespace}.DcaApp.{kind}",
                    itemindex=str(index),
                )
        for command in template.commands:
            kind = COLOR_FUNCTIONS.get(command.func)
            scale = "ScaleNormal"
            if kind is not None:
                value = color_text(kind, command.value)
                fade = "CFadeIn" + str(fades[command.fade_time])
            else:
                value = str(round(command.value[0] * 100 / 255))
                fade = "VFadeIn" + str(command.fade_time)
                if command.func in (17, 18):
                    value = str(command.value[0])
                    scale = (
                        "ScaleNoneScene"
                        if command.func == 17
                        else "ScaleNoneEffectStart"
                        if command.value[1]
                        else "ScaleNoneEffectStop"
                    )
            attrs = {
                "func": funcs[command.func],
                "value": value,
                "fadeIn": fade,
                "time": f"{command.hour:02}:{command.minute:02}",
                "weekdays": f"{command.weekdays:07b}",
            }
            if root.get("type") == "e64pro":
                attrs["scaleMode"] = scale
            ET.SubElement(commands, "TplCmd", attrs)
    return ET.tostring(root, encoding="utf-16le")


def merge_inputs(data: bytes, devices: tuple[InputDevice, ...]) -> bytes:
    root = ET.fromstring(data)
    layout = logical_layout(root)
    for logical, _ in layout.values():
        logical.set("ref-idx", "-1")
        logical.set("origin", "-1")
        logical.set("flags", "1")
    assigned: set[tuple[int, int]] = set()
    parent = root.find("inputs")
    if parent is None:
        parent = ET.SubElement(root, "inputs")
    old = {e.get("index"): e for e in parent.findall("DevInput")}
    for child in parent.findall("DevInput"):
        parent.remove(child)
    for device in devices:
        item = old.get(str(device.address))
        if item is None:
            item = ET.Element("DevInput", index=str(device.address))
        item.set("long-address", f"{device.long_address:06X}")
        item.set("start-index", str(device.start))
        item.set("noi", str(len(device.instances)))
        instances = item.find("Instances")
        if instances is None:
            instances = ET.SubElement(item, "Instances")
        for child in instances.findall("DevInputInstance"):
            instances.remove(child)
        for instance in device.instances:
            target = -1
            if instance.controller != 255:
                key = (instance.controller, instance.detail)
                logical = layout.get(key)
                if (
                    logical is None
                    or key in assigned
                    or instance.index >= 254
                    or not compatible_types(input_type(logical[0]), instance.kind)
                    or instance.count != logical[1]
                ):
                    raise DaliCommissioningError(
                        "input readback does not match the configured controller layout"
                    )
                assigned.add(key)
                logical[0].set("ref-idx", str(instance.index))
                logical[0].set("origin", str(instance.index))
                logical[0].set("flags", "4")
                target = instance.detail << 8 | instance.controller
            ET.SubElement(
                instances,
                "DevInputInstance",
                {
                    "index": str(instance.index),
                    "type": str(instance.kind),
                    "number": str(instance.number),
                    "resolution": str(instance.resolution),
                    "ets-index": str(instance.controller),
                    "ets-detail": str(instance.detail),
                    "target": str(target),
                },
            )
        parent.append(item)
    return ET.tostring(root, encoding="utf-16le")
