"""Byte layouts for the MDT DALI gateway property elements (matching the DCA property structs).

Each element maps to one entry of a KNX property array; field order == wire order (the DCA marshals
raw ``StructLayout`` structs, Pack=1). Multi-byte scalars are little-endian **except** the ECG long
address, which is 24-bit big-endian. Sizes must match :data:`xknxeditor.dali.properties.ELEMENT_SIZE`.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass


def encode_table(elements: Sequence[bytes]) -> bytes:
    """Concatenate per-element byte blobs into a flat property-array buffer."""
    return b"".join(elements)


def decode_table(data: bytes, element_size: int) -> list[bytes]:
    """Split a flat property-array buffer into ``element_size``-byte elements."""
    if element_size <= 0:
        raise ValueError("element_size must be positive")
    return [data[i : i + element_size] for i in range(0, len(data), element_size)]


@dataclass(frozen=True, slots=True)
class EcgProp:
    """EVG_PROP (203), 4 B: type, subtype, group index, ETS ECG number for this slot."""

    ecg_type: int
    sub_type: int
    group_index: int
    ets_ecg_index: int

    def encode(self) -> bytes:
        return bytes(
            (self.ecg_type & 0xFF, self.sub_type, self.group_index, self.ets_ecg_index)
        )

    @classmethod
    def decode(cls, data: bytes) -> EcgProp:
        return cls(data[0], data[1], data[2], data[3])


@dataclass(frozen=True, slots=True)
class EcgStatic:
    """EVG_STATIC (204), 8 B: 24-bit BE long address, min value, min/max colour temp (u16 LE)."""

    long_address: int
    min_value: int
    min_color_temp: int
    max_color_temp: int

    def encode(self) -> bytes:
        la = self.long_address & 0xFFFFFF
        return bytes(
            ((la >> 16) & 0xFF, (la >> 8) & 0xFF, la & 0xFF, self.min_value)
        ) + struct.pack("<HH", self.min_color_temp, self.max_color_temp)

    @classmethod
    def decode(cls, data: bytes) -> EcgStatic:
        la = (data[0] << 16) | (data[1] << 8) | data[2]
        min_ct, max_ct = struct.unpack("<HH", data[4:8])
        return cls(la, data[3], min_ct, max_ct)


@dataclass(frozen=True, slots=True)
class EcgStatus:
    """EVG_STATUS (205), 5 B: mode, alarm, DALI value, KNX value, dimm curve."""

    mode: int
    alarm: int
    dali_value: int
    knx_value: int
    dimm_curve: int

    @classmethod
    def decode(cls, data: bytes) -> EcgStatus:
        return cls(data[0], data[1], data[2], data[3], data[4])


@dataclass(frozen=True, slots=True)
class GroupDyn:
    """GROUP_DYN (206), 6 B: run hours (u32 LE), run seconds (u16 LE)."""

    run_hours: int
    run_seconds: int

    @classmethod
    def decode(cls, data: bytes) -> GroupDyn:
        hours, seconds = struct.unpack("<IH", data[0:6])
        return cls(hours, seconds)


@dataclass(frozen=True, slots=True)
class GroupStatus:
    """GROUP_STATUS (208), 8 B: mode, dimm curve, DALI value, KNX value, min/max colour temp."""

    mode: int
    dimm_curve: int
    dali_value: int
    knx_value: int
    min_color_temp: int
    max_color_temp: int

    @classmethod
    def decode(cls, data: bytes) -> GroupStatus:
        min_ct, max_ct = struct.unpack("<HH", data[4:8])
        return cls(data[0], data[1], data[2], data[3], min_ct, max_ct)


@dataclass(frozen=True, slots=True)
class GroupFailure:
    """GROUP_FAILURE (209), 6 B: ecg/converter counts, lamp/ecg/converter fail counts, fail rate."""

    ecg_count: int
    converter_count: int
    lamp_fail_count: int
    ecg_fail_count: int
    converter_fail_count: int
    fail_rate: int

    @classmethod
    def decode(cls, data: bytes) -> GroupFailure:
        return cls(data[0], data[1], data[2], data[3], data[4], data[5])


@dataclass(frozen=True, slots=True)
class CommissionSync:
    """COMMISSION_SYNC (224), 2 B: ETS ECG number + group index for a physical slot."""

    ets_index: int
    group_index: int

    def encode(self) -> bytes:
        return bytes((self.ets_index & 0xFF, self.group_index & 0xFF))

    @classmethod
    def decode(cls, data: bytes) -> CommissionSync:
        return cls(data[0], data[1])


@dataclass(frozen=True, slots=True)
class Command:
    """COMMAND (218), 3 B: command id + two parameters (also the poll readback layout)."""

    cmd: int
    param1: int = 0
    param2: int = 0

    def encode(self) -> bytes:
        return bytes((self.cmd & 0xFF, self.param1 & 0xFF, self.param2 & 0xFF))

    @classmethod
    def decode(cls, data: bytes) -> Command:
        return cls(data[0], data[1], data[2])


@dataclass(frozen=True, slots=True)
class ExtCommand:
    """EXT_COMMAND (227), 8 B: function, 4-byte colour, target (1=ECG/2=group), index, members-hi."""

    func: int
    color: bytes = b"\x00\x00\x00\x00"
    target: int = 0
    index: int = 0
    members_hi: int = 0

    def encode(self) -> bytes:
        return (
            bytes((self.func & 0xFF,))
            + self.color[:4].ljust(4, b"\x00")
            + bytes((self.target & 0xFF, self.index & 0xFF, self.members_hi & 0xFF))
        )


@dataclass(frozen=True, slots=True)
class SceneAssign:
    """SCENE_ASSIGN (212), 1 B: fade time for a scene."""

    fade_time: int

    def encode(self) -> bytes:
        return bytes((self.fade_time & 0xFF,))


@dataclass(frozen=True, slots=True)
class SceneItemValue:
    """SCENE_GROUP_VALUE (214), 8 B: scene, target index, level, 4-byte colour, flags."""

    scene: int
    item_index: int  # group 0-15, or 16 + ecg short address for an ECG target
    level: int
    color: bytes = b"\x00\x00\x00\x00"
    flags: int = 0

    def encode(self) -> bytes:
        return (
            bytes((self.scene & 0xFF, self.item_index & 0xFF, self.level & 0xFF))
            + self.color[:4].ljust(4, b"\x00")
            + bytes((self.flags & 0xFF,))
        )

    @classmethod
    def decode(cls, data: bytes) -> SceneItemValue:
        return cls(data[0], data[1], data[2], bytes(data[3:7]), data[7])


# The Scene_GroupValue table is terminated by an all-0xFF 8-byte record.
SCENE_TERMINATOR = b"\xff" * 8


@dataclass(frozen=True, slots=True)
class SceneKnxMap:
    """SCENE_KNX_MAP (228), 1 B: the DALI/KNX scene number (1-64) for a scene slot."""

    knx_scene: int

    def encode(self) -> bytes:
        return bytes((self.knx_scene & 0xFF,))


@dataclass(frozen=True, slots=True)
class TemplateProp:
    """COLOUR_C_EVENT (225), 10 B: one timed template command.

    Layout: template id, function, 4-byte colour value, fade time, weekday mask, then two packed
    bytes HM/MM carrying hour (5 bits), minute (6 bits, split across both), mode (2 bits) and the
    overwritable flag (bit 5 of MM).
    """

    tpl_index: int
    func: int
    value: bytes = b"\x00\x00\x00\x00"
    fade_time: int = 0
    weekdays: int = 0
    hour: int = 0
    minute: int = 0
    mode: int = 0
    overwritable: bool = False

    def encode(self) -> bytes:
        hm = (self.hour & 0x1F) | ((self.minute << 2) & 0xE0)
        mm = (self.minute & 0x07) | ((self.mode & 0x03) << 6)
        if self.overwritable:
            mm |= 0x20
        return (
            bytes((self.tpl_index & 0xFF, self.func & 0xFF))
            + self.value[:4].ljust(4, b"\x00")
            + bytes((self.fade_time & 0xFF, self.weekdays & 0xFF, hm, mm))
        )


# The Colour_C_Event table is terminated by an all-0xFF 10-byte record.
TEMPLATE_TERMINATOR = b"\xff" * 10


def encode_template_members(groups: Sequence[int], ecgs: Sequence[int]) -> bytes:
    """COLOUR_C_ASSIGN (226), 10 B: member bitmap — bytes 0-1 groups (0-15), bytes 2-9 ECGs (0-63)."""
    out = bytearray(10)
    for g in groups:
        out[g >> 3] |= 1 << (g & 7)
    for e in ecgs:
        out[(e >> 3) + 2] |= 1 << (e & 7)
    return bytes(out)
