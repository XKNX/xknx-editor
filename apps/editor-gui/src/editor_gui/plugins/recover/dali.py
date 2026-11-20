"""Read DALI configuration with recovery and materialize its project backup."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast
from xml.etree import ElementTree as ET

from xknxeditor.dali import MdtDaliCommissioner, MdtDaliConnection
from xknxeditor.dali.configuration import compile_configuration
from xknxeditor.dali.configuration_readback import (
    merge_effects,
    merge_inputs,
    merge_scenes,
    merge_templates,
)
from xknxeditor.dali.inputs import InputDevice, InputInstance
from xknxeditor.dali.model import (
    DaliBusState,
    EcgState,
    Effect,
    EffectStep,
    GroupState,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)
from xknxeditor.dali.profiles import backup_blob_name, profile_for
from xknxeditor.dali.project_persistence import build_backup_store_xml
from xknxeditor.dali.properties import PropertyType
from xknxeditor.dali.service_operations import read_calibration, read_names

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.recover import RecoveredDevice
    from xknxeditor.recover.recover import AddinReader

_CAPTURE = "dali-recovery.json"


def _value(
    ui: DynamicUI, name: str, instance: str | None = None, *, active: bool = False
) -> str | None:
    ref = ui.find_parameter_ref("name", name, instance)
    if ref is None or (active and not ui.is_parameter_active(ref)):
        return None
    return ui.get_value(ref)


def channel_count(application: Application) -> int:
    """Read the channel count from the product's fixed application parameter."""
    ui = application.dynamic_ui()
    value = _value(ui, "DEV-Number_of_DaliChannels") if ui is not None else None
    count = int(value or "1")
    if count not in (1, 2):
        raise ValueError("Unsupported DALI channel count")
    return count


def addin_reader(application: Application) -> AddinReader | None:
    """Capture all supported channel tables before the management connection closes."""
    from editor_gui.plugins.recover import siemens, siemens_dali2

    profile = profile_for(application.id)
    if profile is None:
        return siemens.addin_reader(application) or siemens_dali2.addin_reader(
            application
        )
    count = channel_count(application)

    async def read(programmer: DeviceProgrammer) -> dict[str, dict[str, bytes]]:
        channels: list[dict[str, Any]] = []
        for channel in range(count):
            commissioner = MdtDaliCommissioner(
                MdtDaliConnection(programmer, channel, profile=profile)
            )
            state = await commissioner.scan()
            scenes = await commissioner.read_scenes()
            templates = await commissioner.read_templates()
            extended = profile.supports_extended_templates
            effects = await commissioner.read_effects() if extended else []
            inputs = await commissioner.read_inputs() if extended else ()
            gtin = (
                await commissioner.connection.read_table(PropertyType.GTIN, 128)
                if extended
                else b""
            )
            calibration = (
                [
                    asdict(await read_calibration(commissioner.connection, index))
                    for index in range(8)
                ]
                if extended
                else []
            )
            names = (
                {
                    section: await read_names(commissioner.connection, section)
                    for section in ("ecgs", "groups", "mds")
                }
                if extended
                else {}
            )
            channels.append(
                {
                    "state": asdict(state),
                    "scenes": [asdict(scene) for scene in scenes],
                    "templates": [asdict(template) for template in templates],
                    "effects": [asdict(effect) for effect in effects],
                    "inputs": [asdict(device) for device in inputs],
                    "gtin": gtin.hex(),
                    "calibration": calibration,
                    "names": names,
                }
            )
        capture = json.dumps(channels, default=_json_bytes).encode("utf-8")
        return {application.id: {_CAPTURE: capture}}

    return read


def _json_bytes(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Unsupported DALI capture value: {type(value).__name__}")


def _state(data: dict[str, Any]) -> DaliBusState:
    return DaliBusState(
        channel=data["channel"],
        firmware=tuple(data["firmware"]) if data["firmware"] is not None else None,
        ecgs=[EcgState(**ecg) for ecg in data["ecgs"]],
        groups=[GroupState(**group) for group in data["groups"]],
    )


def _scenes(rows: list[dict[str, Any]]) -> list[Scene]:
    return [
        Scene(
            row["index"],
            row["fade_time"],
            row["knx_scene"],
            [
                SceneValue(**(value | {"color": bytes.fromhex(value["color"])}))
                for value in row["values"]
            ],
            row["knx_mapping_available"],
        )
        for row in rows
    ]


def _effects(rows: list[dict[str, Any]]) -> list[Effect]:
    return [
        Effect(
            row["index"],
            row["mode"],
            [
                EffectStep(**(step | {"color": bytes.fromhex(step["color"])}))
                for step in row["steps"]
            ],
        )
        for row in rows
    ]


def _templates(rows: list[dict[str, Any]]) -> list[Template]:
    return [
        Template(
            row["index"],
            row["mode"],
            row["overwritable"],
            tuple(row["groups"]),
            tuple(row["ecgs"]),
            [
                TemplateCommand(**(cmd | {"value": bytes.fromhex(cmd["value"])}))
                for cmd in row["commands"]
            ],
            row["reset_on_midnight"],
        )
        for row in rows
    ]


def _inputs(rows: list[dict[str, Any]]) -> tuple[InputDevice, ...]:
    return tuple(
        InputDevice(
            row["address"],
            row["long_address"],
            row["start"],
            tuple(InputInstance(**instance) for instance in row["instances"]),
        )
        for row in rows
    )


def _group_color(ui: DynamicUI, instance: str, prefix: str) -> str:
    kind = int(_value(ui, prefix + "CC-ColourControlType", instance) or "0")
    result = {1: 4, 2: 7, 3: 6, 4: 5, 5: 11, 6: 10}.get(kind, 0)
    if kind in (2, 3, 5, 6):
        name = "CC-RGBInputTypeU" if kind in (2, 5) else "CC-RGBWInputTypeU"
        if _value(ui, prefix + name, instance, active=True) == "2":
            result = {7: 8, 6: 9, 11: 12, 10: 13}[result]
    return str(result)


def _logical_parameters(root: ET.Element, device: Device, channel: int) -> None:
    ui, app = device.dynamic_ui, device.app
    if ui is None or app is None:
        return
    definitions = app.tree_builder().idx.module_defs
    arguments = ui.get_module_instance_arguments()
    for instance, ref in ui.get_module_instances():
        definition = definitions.get(ref)
        if definition is None:
            continue
        match = re.fullmatch(r"Module(GRP|ECG|MD|GI)(?:_CH([12]))?", definition.name)
        if match is None or int(match[2] or "1") != channel + 1:
            continue
        args = arguments.get(instance, [])
        index = int(args[-1][1]) - 1 if args else 0
        section, prefix, tag = {
            "GRP": ("groups", "DCA1-", "Group"),
            "ECG": ("ecgs", "DCE1-", "ECG"),
            "MD": ("mds", "DCM1-", "MdInput"),
            "GI": ("gis", "DCG1-", "GenInput"),
        }[match[1]]
        item = _slot(root, section, tag, index)
        description = _value(ui, prefix + "Description", instance)
        if description is not None:
            item.set("description", description)
        if section == "groups":
            item.set("ctype", _group_color(ui, instance, prefix))
            for field, name in (
                ("cmode", "CC-IsSlave"),
                ("couple", "AssignMasterGroup"),
            ):
                value = _value(ui, prefix + name, instance)
                if value is not None:
                    item.set(field, value)
        elif section == "ecgs":
            value = _value(ui, prefix + "ColourControlType", instance, active=True)
            item.set("ctype", value or "0")
        elif section == "mds":
            sensors = int(_value(ui, prefix + "WithBrightness", instance) or "0")
            counts = [0, 0]
            for i, kind in enumerate(("M", "B")):
                for suffix in ("0", "1"):
                    value = _value(
                        ui,
                        prefix + f"NumberofInstances{kind}{suffix}",
                        instance,
                        active=True,
                    )
                    if value is not None:
                        counts[i] = int(value)
            if sensors == 2:
                counts[1] = 0
            elif sensors == 3:
                counts[0] = 0
            _motion_layout(item, *counts)
            for field, name in (
                ("wclc", "WithConstantLightControl"),
                ("wclbct", "CLBrightnessCorrectionType"),
                ("consumption", "CurrentConsumption"),
            ):
                value = _value(ui, prefix + name, instance)
                if value is not None:
                    item.set(field, value)
        else:
            _instances(item, ["GenericSensor"])
            item.set("noi", "1")
            item.set("type", _value(ui, prefix + "TypeOfInputSignal", instance) or "1")
    if root.get("type") == "e64pro":
        for index in range(8):
            prefix = f"DPB{index + 1}-"
            fold = _value(ui, prefix + "NumberOfButton", active=True)
            count = _value(ui, prefix + "NumberOfInstances", active=True)
            if fold is None or count is None:
                continue
            item = _slot(root, "pbs", "BtnInput", index)
            item.set("description", _value(ui, prefix + "Description") or "")
            _button_layout(item, int(fold), int(count))
    for template in root.findall("templates/Tpl"):
        mode = _value(
            ui, "PROP-TEMPLATE_MODE." + str(int(template.get("index", "0")) + 1)
        )
        if mode is not None:
            template.set(
                "mode", {"0": "Disabled", "1": "Enabled", "2": "Controlled"}[mode]
            )


def _slot(root: ET.Element, section: str, tag: str, index: int) -> ET.Element:
    maximum = 64 if section == "ecgs" else 16 if section == "groups" else 8
    if not 0 <= index < maximum:
        raise ValueError("Invalid recovered DALI controller index")
    parent = root.find(section)
    if parent is None:
        parent = ET.SubElement(root, section)
    item = parent.find(f"{tag}[@index='{index}']")
    return item if item is not None else ET.SubElement(parent, tag, index=str(index))


def _instances(item: ET.Element, types: list[str]) -> None:
    previous = item.find("Instances")
    if previous is not None:
        item.remove(previous)
    parent = ET.SubElement(item, "Instances")
    for index, kind in enumerate(types):
        ET.SubElement(
            parent,
            "EtsInstance",
            {
                "index": str(index),
                "type": kind,
                "ref-idx": "-1",
                "origin": "-1",
                "flags": "1",
            },
        )


def _motion_layout(item: ET.Element, presence: int, brightness: int) -> None:
    if not 0 <= presence <= 15 or not 0 <= brightness <= 15:
        raise ValueError("Invalid recovered motion controller layout")
    item.set("nop", str(presence))
    item.set("nob", str(brightness))
    item.set(
        "sensors-avail",
        "1"
        if presence and brightness
        else "2"
        if presence
        else "3"
        if brightness
        else "0",
    )
    _instances(item, ["MotionSensor"] * presence + ["BrightnessSensor"] * brightness)


def _button_layout(item: ET.Element, buttons: int, count: int) -> None:
    if not 1 <= buttons <= 4 or not 1 <= count <= 15:
        raise ValueError("Invalid recovered button controller layout")
    item.set("fold", str(buttons))
    item.set("noi", str(count))
    _instances(item, ["PushButton"] * (2 * buttons * count))


def _input_layout(root: ET.Element, devices: tuple[InputDevice, ...]) -> None:
    seen: dict[int, int] = {}
    for device in devices:
        for instance in device.instances:
            code = instance.controller
            if code == 255:
                continue
            if code & 7 > 2 or code >> 3 >= 8:
                raise ValueError("Invalid recovered input controller")
            if code in seen:
                if seen[code] != instance.count:
                    raise ValueError("Inconsistent recovered input controller layout")
                continue
            seen[code] = instance.count
            section, tag = (
                ("mds", "MdInput"),
                ("pbs", "BtnInput"),
                ("gis", "GenInput"),
            )[code & 7]
            item = _slot(root, section, tag, code >> 3)
            if section == "mds":
                _motion_layout(item, instance.count & 15, instance.count >> 4)
            elif section == "pbs":
                _button_layout(item, instance.count & 15, instance.count >> 4)
            else:
                _instances(item, ["GenericSensor"])
                item.set("noi", "1")


def apply_recovered(
    project: ProjectService, device: Device, recovered: RecoveredDevice
) -> None:
    """Store validated channel backups inside the caller's device undo step."""
    app = device.app
    profile = profile_for(app.id if app is not None else None)
    if profile is None or app is None or not recovered.addin_data:
        return
    if set(recovered.addin_data) != {app.id} or set(recovered.addin_data[app.id]) != {
        _CAPTURE
    }:
        raise ValueError("Recovered DALI data does not match this application")
    raw: object = json.loads(recovered.addin_data[app.id][_CAPTURE])
    count = channel_count(app)
    if not isinstance(raw, list):
        raise ValueError("Incomplete recovered DALI channels")
    channels = cast(list[object], raw)
    if len(channels) != count:
        raise ValueError("Incomplete recovered DALI channels")
    blobs: list[tuple[str, bytes]] = []
    for channel, channel_data in enumerate(channels):
        if not isinstance(channel_data, dict):
            raise ValueError("Incomplete recovered DALI configuration")
        row = cast(dict[str, Any], channel_data)
        required = {"state", "scenes", "templates"}
        if profile.supports_extended_templates:
            required |= {"effects", "inputs", "gtin", "calibration", "names"}
        if not required <= row.keys():
            raise ValueError("Incomplete recovered DALI configuration")
        state = _state(row["state"])
        if state.channel != channel:
            raise ValueError("Invalid recovered DALI channel order")
        if sorted(ecg.slot for ecg in state.ecgs) != list(range(64)):
            raise ValueError("Incomplete recovered DALI gear")
        scenes, templates = _scenes(row["scenes"]), _templates(row["templates"])
        if sorted(scene.index for scene in scenes) != list(range(16)) or sorted(
            template.index for template in templates
        ) != list(range(16)):
            raise ValueError("Incomplete recovered DALI configuration")
        root = ET.fromstring(
            build_backup_store_xml(
                state=state, individual_address=device.individual_address
            )
        )
        for ecg in state.ecgs:
            item = root.find(f"devecgs/DevECG[@index='{ecg.slot}']")
            assert item is not None
            item.set("min-ctemp", str(ecg.min_color_temp))
            item.set("max-ctemp", str(ecg.max_color_temp))
        if profile.supports_extended_templates:
            root.set("type", "e64pro")
            root.attrib.pop("version", None)
            for index in range(8):
                _motion_layout(_slot(root, "mds", "MdInput", index), 0, 0)
                button = _slot(root, "pbs", "BtnInput", index)
                button.set("fold", "0")
                button.set("noi", "0")
                _instances(button, [])
                generic = _slot(root, "gis", "GenInput", index)
                generic.set("noi", "1")
                _instances(generic, ["GenericSensor"])
            effects = _effects(row["effects"])
            if sorted(effect.index for effect in effects) != list(range(16)):
                raise ValueError("Incomplete recovered DALI effects")
            parent = ET.SubElement(root, "effects")
            for effect in effects:
                ET.SubElement(parent, "Effect", index=str(effect.index))
        _logical_parameters(root, device, channel)
        payload = merge_scenes(ET.tostring(root, encoding="utf-16le"), scenes)
        payload = merge_templates(payload, templates)
        if profile.supports_extended_templates:
            payload = merge_effects(payload, _effects(row["effects"]))
            inputs = _inputs(row["inputs"])
            root = ET.fromstring(payload)
            _input_layout(root, inputs)
            payload = merge_inputs(ET.tostring(root, encoding="utf-16le"), inputs)
            root = ET.fromstring(payload)
            gtin = bytes.fromhex(row["gtin"])
            if len(gtin) != 128 * 6 or len(row["calibration"]) != 8:
                raise ValueError("Incomplete recovered DALI restore data")
            for path, offset in (("devecgs/DevECG", 0), ("inputs/DevInput", 64)):
                for item in root.findall(path):
                    start = (int(item.get("index", "0")) + offset) * 6
                    item.set(
                        "gtin", str(int.from_bytes(gtin[start : start + 6], "big"))
                    )
            for index, calibration in enumerate(row["calibration"]):
                item = _slot(root, "mds", "MdInput", index)
                item.set("calibration", "1")
                for attr, field in (
                    ("vmin", "minimum_level"),
                    ("mmin", "minimum_lux"),
                    ("bmin", "minimum_sensor"),
                    ("vmax", "maximum_level"),
                    ("mmax", "maximum_lux"),
                    ("bmax", "maximum_sensor"),
                ):
                    item.set(attr, str(calibration[field]))
            name_counts = {"ecgs": 64, "groups": 16, "mds": 8}
            if not isinstance(row["names"], dict):
                raise ValueError("Incomplete recovered DALI descriptions")
            name_tables = cast(dict[str, Any], row["names"])
            if set(name_tables) != set(name_counts):
                raise ValueError("Incomplete recovered DALI descriptions")
            for section, size in name_counts.items():
                if not isinstance(name_tables[section], list):
                    raise ValueError("Incomplete recovered DALI descriptions")
                names = cast(list[Any], name_tables[section])
                if len(names) != size or any(
                    not isinstance(name, str) for name in names
                ):
                    raise ValueError("Incomplete recovered DALI descriptions")
                for item in root.findall(section + "/*"):
                    item.set("description", names[int(item.get("index", "0"))])
            root.set("CanRestore", "True")
            payload = ET.tostring(root, encoding="utf-16le")
        compile_configuration(payload)
        blobs.append((backup_blob_name(profile.backup_blob_base, channel), payload))
    for name, payload in blobs:
        project.persist_dali_backup(device, name, payload)
