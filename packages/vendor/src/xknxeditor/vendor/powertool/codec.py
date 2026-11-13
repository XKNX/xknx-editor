"""Compile push-button configuration into a complete PID container."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from .model import (
    EvaluatedChannel,
    PowerToolConfig,
    channel_exists,
    channel_number,
    float32,
)


@dataclass(frozen=True)
class HostProfile:
    individual_address: int = 0x1101
    short_associations: bool = False
    routing_count: int = 0x60
    retry_count: int = 0x33
    rate_time: int = 0x4268
    rate_count: int = 100
    mask_version: int = 0x07B0
    domain_address: int = 0
    repeater_present: bool = False
    property_order: Literal["mono", "windows"] = "mono"

    def __post_init__(self) -> None:
        for value, upper in (
            (self.individual_address, 65535),
            (self.routing_count, 255),
            (self.retry_count, 255),
            (self.rate_time, 65535),
            (self.rate_count, 65535),
            (self.domain_address, 65535),
        ):
            if type(value) is not int or not 0 <= value <= upper:
                raise ValueError("invalid host property")
        if self.mask_version not in (0x07B0, 0x17B0):
            raise ValueError("Unsupported PowerTool media mask")
        if self.property_order not in ("mono", "windows"):
            raise ValueError("Unsupported PowerTool property order")
        if self.property_order == "windows" and self.mask_version != 0x07B0:
            raise ValueError("Windows property order is unverified for this medium")


@dataclass(frozen=True)
class ObjectRole:
    key: str
    channel: int
    position: int
    index: int
    number: int
    name: str
    dpt: str
    size_code: int
    flags: int
    visible: bool


@dataclass(frozen=True)
class Association:
    role: str
    address: int
    sending: bool = False
    group_speaker: bool = False


@dataclass(frozen=True)
class Resource:
    index: int
    object_index: int
    data: bytes
    mask: bytes
    flags: int = 0


@dataclass(frozen=True)
class Property:
    object_index: int
    pid: int
    data: bytes


@dataclass(frozen=True)
class CompiledPowerToolImage:
    pid: bytes
    resources: tuple[Resource, ...]
    properties: tuple[Property, ...]
    objects: tuple[ObjectRole, ...]
    allocation: tuple[tuple[int, int], ...]


def _u16(value: int) -> bytes:
    if not 0 <= value <= 65535:
        raise ValueError("value exceeds 16 bits")
    return value.to_bytes(2, "big")


def crc(data: bytes) -> int:
    value = 0xFFFF
    for byte in data + b"\0\0":
        for bit in range(7, -1, -1):
            carry = value & 0x8000
            value = ((value << 1) | (byte >> bit & 1)) & 0xFFFF
            if carry:
                value ^= 0x1021
    return value


def float16_bytes(value: int | float) -> bytes:
    fraction, exponent = math.frexp(float32(100 * float32(value)))
    if fraction == 0:
        return b"\0\0"
    mantissa = int(fraction * 2048 + 0.5)
    exponent -= 11
    if mantissa < -2048 or mantissa > 2047:
        mantissa = int(mantissa / 2)
        exponent += 1
    if exponent < 0:
        mantissa >>= -exponent
        exponent = 0
    elif exponent > 15:
        mantissa = 2047 if mantissa > 0 else -2048
        exponent = 15
    return _u16(((mantissa & 0x800) << 4) | (exponent << 11) | (mantissa & 0x7FF))


DEFAULT_HOST = HostProfile()


def compile_image(
    config: PowerToolConfig,
    host: HostProfile = DEFAULT_HOST,
    associations: Sequence[Association] = (),
    flags: Mapping[str, int] | None = None,
) -> CompiledPowerToolImage:
    blocks = bytearray()
    objects: list[ObjectRole] = []
    allocation: list[tuple[int, int]] = [(0, 0)]
    function_table: list[tuple[int, int]] = []
    configured: set[int] = set()
    display_number = 0
    for channel, settings in enumerate(config.channels):
        if not channel_exists(config, channel) or settings.function == "inactive":
            continue
        evaluated = EvaluatedChannel(settings)
        function = evaluated.function
        number = channel_number(config.variant, channel)
        device_config = b"" if number >> 8 in configured else b"\x00\x05"
        configured.add(number >> 8)
        block = bytearray(function.config_offset + 2 + len(device_config))
        block[:3] = number.to_bytes(3, "big")
        base = len(allocation)
        block[3:5] = _u16(base)
        for obj in function.objects:
            if not evaluated.condition(obj.condition):
                continue
            dpt, size = obj.dpt, obj.size_code
            for condition, candidate_dpt, candidate_size in obj.types:
                if evaluated.condition(condition):
                    dpt, size = candidate_dpt, candidate_size
                    break
            role = f"channel_{channel}/{function.key}/{obj.key}"
            object_flags = obj.flags if flags is None else flags.get(role, obj.flags)
            if type(object_flags) is not int or not 0 <= object_flags <= 255:
                raise ValueError("invalid object flags")
            if not obj.visible:
                object_flags &= 3
            index = base + obj.position
            while len(allocation) <= index:
                allocation.append((0, 0))
            allocation[index] = (object_flags, size)
            objects.append(
                ObjectRole(
                    role,
                    channel,
                    obj.position,
                    index,
                    display_number,
                    obj.label,
                    dpt,
                    size,
                    object_flags,
                    obj.visible,
                )
            )
            if obj.visible:
                display_number += 1
        for i, f in enumerate(function.fields):
            if f.storage != "offset" or f.kind == "page" or not evaluated.exists(i):
                continue
            value = evaluated.image_value(i)
            if f.kind == "float":
                encoded = (
                    struct.pack(">f", value) if f.size == 4 else float16_bytes(value)
                )
            else:
                encoded = (int(value) & ((1 << (f.size * 8)) - 1)).to_bytes(
                    f.size, "big"
                )
            if f.address + f.size > function.config_offset:
                raise ValueError("field exceeds function block")
            if f.size == 1:
                block[f.address] = (block[f.address] & ~f.mask) | encoded[0]
            else:
                block[f.address : f.address + f.size] = encoded
        block[function.config_offset : function.config_offset + 2] = _u16(
            len(device_config)
        )
        block[function.config_offset + 2 :] = device_config
        function_table.append((28 + len(blocks), function.code))
        blocks.extend(block)
    version = 0x21 if config.variant == "01" else 0x22
    return assemble_image(
        blocks,
        function_table,
        objects,
        allocation,
        host,
        associations,
        flags,
        bytes.fromhex("00076108") + bytes([version]),
        bytes.fromhex("00076108") + bytes([version + 16]),
    )


def assemble_image(
    blocks: bytearray,
    function_table: list[tuple[int, int]],
    objects: list[ObjectRole],
    allocation: list[tuple[int, int]],
    host: HostProfile,
    associations: Sequence[Association],
    flags: Mapping[str, int] | None,
    main_id: bytes,
    secondary_id: bytes,
    pei_type: int = 255,
    module_data: bytes | None = None,
) -> CompiledPowerToolImage:
    if flags is not None and set(flags) - {o.key for o in objects}:
        raise ValueError("flags refer to an absent object")
    gap = len(blocks) % 2
    table_offset = 28 + len(blocks) + gap
    table = _u16(len(function_table)) + b"".join(
        struct.pack(">IH", *entry) for entry in function_table
    )
    header = struct.pack(">7I", 28, 0, 0, 0, 28 if blocks else 0, 0, table_offset)
    appl = header + blocks + bytes(gap) + table
    mask = b"\xff" * (28 + len(blocks)) + bytes(gap) + b"\xff" * len(table)
    pei = struct.pack(">8I", 32, 0, 0, 0, 0, 0, 0, 32) + (
        module_data if module_data is not None else b"\0\0"
    )
    by_role = {o.key: o for o in objects if o.visible}
    for assoc in associations:
        if (
            assoc.role not in by_role
            or type(assoc.address) is not int
            or not 1 <= assoc.address <= 65535
        ):
            raise ValueError(
                "association refers to an absent object or invalid address"
            )
    if len({(a.role, a.address) for a in associations}) != len(associations):
        raise ValueError("duplicate association")
    speaker_roles = {a.role for a in associations if a.group_speaker}
    if speaker_roles and host.mask_version != 0x17B0:
        raise ValueError("Group speakers require Powerline")
    if speaker_roles:
        objects = [
            replace(obj, flags=obj.flags | 72) if obj.key in speaker_roles else obj
            for obj in objects
        ]
        allocation = list(allocation)
        for obj in objects:
            if obj.key in speaker_roles:
                allocation[obj.index] = (obj.flags, obj.size_code)
    addresses = sorted({a.address for a in associations})
    address_index = {address: i + 1 for i, address in enumerate(addresses)}
    pairs: list[tuple[int, int, bool]] = []
    for obj in objects:
        links = [a for a in associations if a.role == obj.key]
        senders = [a for a in links if a.sending]
        if len(senders) > 1:
            raise ValueError("an object has more than one sending address")
        if links:
            sender = senders[0] if senders else links[0]
            links = [sender, *(a for a in links if a is not sender)]
            pairs.extend(
                (address_index[a.address], obj.index, a is sender) for a in links
            )
    pairs.sort(key=lambda p: not p[2])
    fmt = ">BB" if host.short_associations else ">HH"
    if host.short_associations and any(a > 255 or o > 255 for a, o, _ in pairs):
        raise ValueError("short association table index exceeds 255")
    assoc_data = _u16(len(pairs)) + b"".join(
        struct.pack(fmt, a, o) for a, o, _ in pairs
    )
    address_data = _u16(len(addresses)) + b"".join(_u16(a) for a in addresses)
    object_data = _u16(len(allocation) - 1) + bytes(
        v for pair in allocation[1:] for v in pair
    )
    resources = (
        Resource(2, 4, bytes(appl), mask),
        Resource(3, 5, pei, b"\xff" * len(pei)),
        Resource(4, 1, address_data, b"\xff" * len(address_data)),
        Resource(
            5, 2, assoc_data, b"\xff" * len(assoc_data), int(host.short_associations)
        ),
        Resource(6, 3, object_data, b"\xff" * len(object_data)),
    )
    properties = (
        Property(0, 52, bytes([host.retry_count])),
        Property(0, 51, bytes([host.routing_count])),
        Property(0, 85, _u16(host.rate_time)),
        Property(0, 86, _u16(host.rate_count)),
        Property(4, 16, bytes([pei_type])),
        Property(4, 13, main_id),
        Property(5, 16, bytes([pei_type])),
        Property(5, 13, secondary_id),
    )
    if host.property_order == "windows":
        properties = (properties[3], *properties[:3], *properties[4:])
    if host.mask_version == 0x17B0:
        speakers = bytearray(max(1, (len(addresses) + 7) // 8))
        for association in associations:
            if association.group_speaker:
                index = address_index[association.address] - 1
                speakers[index // 8] |= 1 << (index % 8)
        properties = (
            properties[0],
            Property(0, 73, bytes([239 if host.repeater_present else 255])),
            *properties[1:4],
            Property(1, 53, bytes(speakers)),
            *properties[4:],
        )
    descriptors = bytearray()
    payload = bytearray()
    start = 22 + 24 * len(resources)
    for r in resources:
        offset = start + len(payload)
        descriptors.extend(
            struct.pack(
                ">5I2H",
                r.index,
                0,
                len(r.data),
                offset,
                offset + len(r.data),
                r.flags,
                crc(r.data),
            )
        )
        payload.extend(r.data + r.mask)
    pid = struct.pack(
        ">6HIHI",
        16,
        host.individual_address,
        host.domain_address,
        host.mask_version,
        host.mask_version,
        5,
        22,
        len(properties),
        start + len(payload),
    )
    pid += descriptors + payload
    pid += b"".join(
        struct.pack(">3H", p.object_index, p.pid, len(p.data)) + p.data
        for p in properties
    )
    return CompiledPowerToolImage(
        bytes(pid), resources, properties, tuple(objects), tuple(allocation)
    )
