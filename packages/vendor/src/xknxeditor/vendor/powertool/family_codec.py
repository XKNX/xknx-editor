"""Compile shared PowerTool schemas into complete PID resources."""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from typing import Any

from .codec import (
    DEFAULT_HOST,
    Association,
    CompiledPowerToolImage,
    HostProfile,
    ObjectRole,
    assemble_image,
    float16_bytes,
)
from .family import FamilyConfig, FamilyEvaluation, Node

PROFILES: dict[str, tuple[int, int, int, int]] = {
    "ObjektRTR": (0x6108, 0x40, 0x50, 3),
    "ObjektRTRunified": (0x6108, 0x43, 0x53, 3),
    "PraesenzStandard": (0x6131, 0x90, 0xA0, 4),
    "PraesenzKomfort": (0x6131, 0xB0, 0xC0, 4),
    "SoloRTR3": (0x6128, 0x70, 0x80, 2),
    "SoloRTR3oBE": (0x6128, 0x71, 0x81, 2),
}

OBJECT_TYPES = {
    "DPT_Ack": ("1.016", 0),
    "DPT_Bool": ("1.002", 0),
    "DPT_Colour_RGB": ("232.600", 9),
    "DPT_Enable": ("1.003", 0),
    "DPT_Enable_Control": ("2.003", 1),
    "DPT_Heat_Cool": ("1.100", 0),
    "DPT_Occupancy": ("1.018", 0),
    "DPT_SceneControl": ("18.001", 7),
    "DPT_Start": ("1.010", 0),
    "DPT_State": ("1.011", 0),
    "DPT_String_ASCII": ("16.000", 14),
    "DPT_Trigger": ("1.017", 0),
    "DPT_Value_1_Ucount": ("5.010", 7),
    "DPT_Value_2_Ucount": ("7.001", 8),
    "DPT_Value_4_Ucount": ("12.001", 10),
    "DPT_Value_AirQuality": ("9.008", 8),
    "DPT_Value_Humidity": ("9.007", 8),
    "DPT_Value_Lux": ("9.004", 8),
    "DPT_Value_Pres": ("9.006", 8),
    "DPT_Value_Wsp": ("9.005", 8),
    "DPT_Window_Door": ("1.019", 0),
    "DPT_Generic": ("", 0),
    "DPT_Switch": ("1.001", 0),
    "DPT_UpDown": ("1.008", 0),
    "DPT_Step": ("1.007", 0),
    "DPT_Alarm": ("1.005", 0),
    "DPT_Switch_Control": ("2.001", 1),
    "DPT_Control_Dimming": ("3.007", 3),
    "DPT_Scaling": ("5.001", 7),
    "DPT_Value_1_UCount": ("5.010", 7),
    "DPT_Value_1_Count": ("6", 7),
    "DPT_Value_2_Float": ("9", 8),
    "DPT_Value_Temp": ("9.001", 8),
    "DPT_TimePeriodSec": ("7.005", 8),
    "DPT_TimePeriod100MSec": ("7.004", 8),
    "DPT_Value_2_Count": ("8", 8),
    "DPT_Value_2_UCount": ("7", 8),
    "DPT_TimeOfDay": ("10.001", 9),
    "DPT_Date": ("11.001", 9),
    "DPT_Value_4_Float": ("14", 10),
    "DPT_Value_4_Count": ("13", 10),
    "DPT_Value_4_UCount": ("12", 10),
    "DPT_String_8859_1": ("16.001", 14),
    "DPT_Scene_Control": ("18.001", 7),
    "DPT_HVACMode": ("20.102", 7),
    "DPT_HVAC_Mode": ("20.102", 7),
}
FLAGS = {
    "ResponseUpdateEnable": 128,
    "TransmitEnable": 64,
    "ReadOnInitEnable": 32,
    "WriteEnable": 16,
    "ReadEnable": 8,
    "CommunicationEnable": 4,
}


def parameters(node: Node) -> list[Node]:
    return [p for child in node.parameters for p in (child, *parameters(child))]


def _write_panel_text(block: bytearray, node: Node, value: str, base: int) -> None:
    spec = node.spec
    address, size = spec["address"], spec["size"]
    if spec["extension"] == "DatapointConfig":
        data = bytes.fromhex(spec["binary"])
        if len(data) != size:
            raise ValueError("Invalid PowerTool datapoint size")
        block[address : address + size] = data
        return
    data = value.encode("utf-8")
    if len(data) > size:
        raise ValueError("PowerTool text exceeds available space")
    if spec["value_kind"] == "V_STRING":
        block[address : address + size] = data + bytes(size - len(data))
        return
    fixed = spec.get("fix_string_ptr", 0)
    pointer = fixed or len(block) - base
    end = base + pointer + size
    if end > len(block):
        block.extend(bytes(end - len(block)))
    block[address : address + 2] = pointer.to_bytes(2, "big")
    block[base + pointer : base + pointer + len(data)] = data


def _write(block: bytearray, node: Node, value: int | float | str | None) -> None:
    spec = node.spec
    size, address, kind = spec["size"], spec["address"], spec["value_kind"]
    if kind == "V_PAGE" or not size:
        return
    if address + size > len(block):
        raise ValueError("PowerTool parameter exceeds its resource")
    if kind == "V_FLOAT":
        if not isinstance(value, (int, float)):
            raise ValueError("Invalid float value")
        data = struct.pack(">f", value) if size == 4 else float16_bytes(value)
    elif kind == "V_STRING":
        if not isinstance(value, str):
            raise ValueError("Invalid string value")
        text = value.encode("utf-16-be")[: size - 4]
        data = b"\xfe\xff" + text + bytes(size - 2 - len(text))
    else:
        if type(value) is not int:
            raise ValueError("Invalid integer value")
        data = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "big")
    if size == 1:
        block[address] = (block[address] & ~spec["mask"]) | data[0]
    else:
        block[address : address + size] = data


def parameter_written(evaluated: FamilyEvaluation, node: Node) -> bool:
    current: Node | None = node
    while current is not None and current.spec["kind"] == "parameter":
        if not evaluated.property(current, "Exists"):
            return False
        current = current.parent
    return True


def device_config(evaluated: FamilyEvaluation, channel: Node) -> bytes:
    block = bytearray.fromhex(channel.spec["device_config"])
    if not block:
        return b""
    assert channel.product is not None and channel.application is not None
    for root in (channel.product, channel, channel.application):
        for node in parameters(root):
            if node.spec["extension"] == "DeviceConfiguration" and evaluated.property(
                node, "Exists"
            ):
                _write(block, node, evaluated.image_value(node))
    return bytes(block)


def module_directory(
    schema: dict[str, Any], functions: list[tuple[int, int]]
) -> bytes | None:
    modules = schema.get("modules", {})
    selected = list(
        dict.fromkeys(code for _, code in functions if str(code) in modules)
    )
    if not selected:
        return None
    directory = bytearray(2 + 12 * max(8, len(selected)))
    directory[:2] = len(selected).to_bytes(2, "big")
    for i, code in enumerate(selected):
        module = modules[str(code)]
        start = 32 + len(directory)
        struct.pack_into(
            ">HHII",
            directory,
            2 + 12 * i,
            code,
            module["heap"],
            start + module["init"],
            start + module["destroy"],
        )
        directory.extend(bytes.fromhex(module["data"]))
    return bytes(directory)


def compile_family(
    config: FamilyConfig,
    host: HostProfile = DEFAULT_HOST,
    associations: Sequence[Association] = (),
    flags: Mapping[str, int] | None = None,
    *,
    program_ids: tuple[bytes, bytes] | None = None,
) -> CompiledPowerToolImage:
    evaluated = FamilyEvaluation(config)
    from .panel import validate

    validate(evaluated)
    blocks = bytearray()
    table: list[tuple[int, int]] = []
    allocation: list[tuple[int, int]] = [(0, 0)]
    objects: list[ObjectRole] = []
    configured: set[tuple[str, int]] = set()
    number = 0
    for index, channel in enumerate(evaluated.channels):
        if not evaluated.active(channel):
            continue
        app = channel.application
        assert (
            app is not None
            and channel.product is not None
            and channel.parent is not None
        )
        spec = app.spec
        device = device_config(evaluated, channel)
        group = (channel.parent.path, channel.spec["number"] >> 8)
        generic = spec["compiler"] == "GenericApplication"
        panel = spec["smart_touch"]
        if device:
            if not generic and group in configured:
                device = b""
            configured.add(group)
        length = (
            spec["param_block_size"]
            if generic
            else spec["config_offset"]
            if panel
            else spec["config_offset"] + 2 + len(device)
        )
        block = bytearray(length)
        channel_number = evaluated.channel_number(channel)
        if panel:
            block[:4] = b"\0" + (channel_number & 65535).to_bytes(2, "big") + b"\0"
        else:
            block[:3] = channel_number.to_bytes(3, "big")
        base = len(allocation)
        object_offset = 4 if panel else 3
        block[object_offset : object_offset + 2] = base.to_bytes(2, "big")
        for obj in app.objects:
            if not evaluated.property(obj, "Exists"):
                continue
            obj_type = str(evaluated.property(obj, "Type"))
            if obj_type not in OBJECT_TYPES:
                raise ValueError(f"Unverified PowerTool object type: {obj_type}")
            dpt, size = OBJECT_TYPES[obj_type]
            role_channel = config.channel_ids.get(channel.path, channel.path)
            key = f"{role_channel}/{app.key}/{obj.key}"
            visible = bool(evaluated.property(obj, "Visible"))
            default_flags = obj.spec["priority"] | sum(
                bit for prop, bit in FLAGS.items() if evaluated.property(obj, prop)
            )
            obj_flags = (
                default_flags if flags is None else flags.get(key, default_flags)
            )
            if type(obj_flags) is not int or not 0 <= obj_flags <= 255:
                raise ValueError("Invalid PowerTool object flags")
            if not visible:
                obj_flags &= 3
            slot = base + obj.spec["position"]
            while len(allocation) <= slot:
                allocation.append((0, 0))
            allocation[slot] = (obj_flags, size)
            objects.append(
                ObjectRole(
                    key,
                    index,
                    obj.spec["position"],
                    slot,
                    number,
                    obj.spec["label"],
                    dpt,
                    size,
                    obj_flags,
                    visible and obj_type != "DPT_Generic",
                )
            )
            if visible:
                number += 1
        reserved = False
        size_offset_value = 0
        for node in parameters(app):
            if node.spec["storage"] != "Offset" or not parameter_written(
                evaluated, node
            ):
                continue
            value = evaluated.image_value(node)
            if panel and node.spec["value_kind"] in ("V_STRING", "V_STRINGPTR"):
                if not isinstance(value, str):
                    raise ValueError("Invalid PowerTool string")
                _write_panel_text(block, node, value, length)
                continue
            if generic and node.spec["address"] == spec["config_size_offset"]:
                if type(value) is not int:
                    raise ValueError("Invalid PowerTool structure size")
                size_offset_value = value
            if node.spec["value_kind"] == "V_STRINGPTR":
                if not isinstance(value, str):
                    raise ValueError("Invalid PowerTool string")
                text = value.encode("utf-16-be")[: node.spec["size"] - 4]
                if device and not reserved:
                    block.extend(bytes(len(device) + 2))
                    reserved = True
                pointer = len(block) - length - (len(device) + 2 if device else 0)
                offset = node.spec["address"]
                block[offset : offset + 2] = pointer.to_bytes(2, "big")
                block.extend(b"\xfe\xff" + text + b"\0\0")
            else:
                _write(block, node, value)
        if panel:
            string_size = spec["string_ptr_length"]
            if string_size and any(
                n.key.endswith("+Par_ApplicationId") and parameter_written(evaluated, n)
                for n in parameters(app)
            ):
                if len(block) < length + string_size:
                    block.extend(bytes(length + string_size - len(block)))
                app_id = next(
                    n for n in parameters(app) if n.key.endswith("+Par_ApplicationId")
                )
                block[7:9] = (length + string_size - app_id.spec["address"]).to_bytes(
                    2, "big"
                )
        elif generic and device:
            if len(block) == length:
                block.extend(bytes(len(device) + 2))
            size_offset_value += len(block) - length
            offset = spec["config_size_offset"]
            block[offset : offset + 2] = size_offset_value.to_bytes(2, "big")
        if not panel and (device or not generic):
            offset = spec["config_offset"]
            block[offset : offset + 2] = len(device).to_bytes(2, "big")
            block[offset + 2 : offset + 2 + len(device)] = device
        table.append((28 + len(blocks), spec["function"]))
        blocks.extend(block)
    if program_ids is None:
        if config.program_ids is not None:
            program_ids = (
                bytes.fromhex(config.program_ids[0]),
                bytes.fromhex(config.program_ids[1]),
            )
        else:
            if config.database_code is not None or config.profile not in PROFILES:
                raise ValueError("PowerTool image requires the actual product programs")
            app_number, main, secondary, _ = PROFILES[config.profile]
            prefix = b"\0\x07" + app_number.to_bytes(2, "big")
            program_ids = (prefix + bytes([main]), prefix + bytes([secondary]))
    pei_type = evaluated.devices[0].spec.get(
        "pei_type0", 10 if config.profile in ("SoloRTR3", "SoloRTR3oBE") else 255
    )
    return assemble_image(
        blocks,
        table,
        objects,
        allocation,
        host,
        associations,
        flags,
        program_ids[0],
        program_ids[1],
        pei_type,
        module_directory(evaluated.schema, table),
    )
