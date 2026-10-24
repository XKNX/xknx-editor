"""KNX serial numbers that do not identify a device.

A serial number is 6 octets: the manufacturer id (2 octets) and a number (4 octets). Some
devices report a placeholder (all zeros or ones, text, a number their manufacturer gave many
devices) instead of their own. Such a serial number is not recorded for a device: it would
address the wrong device, or many.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import cache
from importlib.resources import files

# Manufacturer ids that are no manufacturer: text, test and placeholder values.
_PLACEHOLDER_MANUFACTURERS = frozenset(
    {
        0x0000,
        0xFFFF,
        0x2020,
        0x2034,
        0x2041,
        0x204D,
        0x2A4B,
        0x2E36,
        0x3030,
        *range(0x3130, 0x3139),
        0x3230,
        0x4144,
        0x4348,
        0x5656,
        0x8070,
        0x889D,
        0x9626,
    }
)

# Masked prefixes (D & FFFFFF00h) of numbers manufacturer 0081h gave many devices.
_SHARED_0081_PREFIXES = frozenset(
    {
        0x00000000,
        0x01100000,
        0x40404000,
        0xFB110000,
        0xFC110000,
        0xFD220000,
        0xFE010000,
        0xFF130100,
        0xFF130000,
        0xFF300000,
        0xFF310000,
    }
)

# Number ranges of manufacturer 00C9h that are shared.
_SHARED_00C9_RANGES = (
    (0x09C192E9, 0x09F8D36F),
    (0x10D9FAE9, 0x11113B6F),
    (0x122E8E69, 0x1265CEEF),
    (0x126751A9, 0x129E922F),
    (0x162C48E9, 0x1663896F),
    (0x17481929, 0x177F59AF),
    (0x170F55E9, 0x1746966F),
    (0x16D692A9, 0x170DD32F),
    (0x169DCF69, 0x16D50FEF),
    (0x16650C29, 0x169C4CAF),
    (0x15F385A9, 0x162AC62F),
    (0x15BAC269, 0x15F202EF),
    (0x1581FF29, 0x15B93FAF),
    (0x151078A9, 0x1547B92F),
    (0x13119B69, 0x1348DBEF),
    (0x138321E9, 0x13BA626F),
    (0x142D6BA9, 0x1464AC2F),
    (0x13F4A869, 0x142BE8EF),
    (0x134A5EA9, 0x13819F2F),
    (0x12D8D829, 0x131018AF),
    (0x12A014E9, 0x12D7556F),
    (0x11F5CB29, 0x122D0BAF),
    (0x114B8169, 0x1182C1EF),
    (0x11BD07E9, 0x11F4486F),
    (0x118444A9, 0x11BB852F),
    (0x1112BE29, 0x1149FEAF),
    (0x1780DC69, 0x17B81CEF),
    (0x1A2A0369, 0x1A6143EF),
    (0x1A9B89E9, 0x1AD2CA6F),
    (0x1A62C6A9, 0x1A9A072F),
    (0x1B0D1069, 0x1B4450EF),
    (0x1AD44D29, 0x1B0B8DAF),
    (0x0A6BDCA9, 0x0AA31D2F),
    (0x0CDC4069, 0x0D1380EF),
    (0x0A331969, 0x0A6A59EF),
    (0x0D4DC6E9, 0x0D85076F),
    (0x0D868A29, 0x0DBDCAAF),
    (0x0DBF4D69, 0x0DF68DEF),
    (0x0DF810A9, 0x0E2F512F),
    (0x0E30D3E9, 0x0E68146F),
    (0x0E699729, 0x0EA0D7AF),
    (0x0EA25A69, 0x0ED99AEF),
    (0x0EDB1DA9, 0x0F125E2F),
    (0x09FA5629, 0x0A3196AF),
    (0x10A137A9, 0x10D8782F),
    (0x07186BE9, 0x074FAC6F),
    (0x08343C29, 0x086B7CAF),
)


def _in(value: int, *ranges: tuple[int, int]) -> bool:
    return any(low <= value <= high for low, high in ranges)


@cache
def _shared_numbers(manufacturer: int) -> frozenset[int]:
    """The shared numbers of ``manufacturer`` in the bundled list."""
    data = json.loads(
        files(__package__).joinpath("shared_serial_numbers.json").read_text("utf-8")
    )
    return frozenset(int(value, 16) for value in data.get(f"{manufacturer:04X}", []))


# Per manufacturer: whether a number (never 0 or FFFFFFFFh here) is shared.
_SHARED_BY_MANUFACTURER: dict[int, Callable[[int], bool]] = {
    0x1153: lambda d: 0x30303030 <= d <= 0x39393939,
    0x0173: lambda d: d == 0xFFFF07B0,
    0x011E: lambda d: d <= 0x1BFFFF,
    0x00FB: lambda d: d in (0x200, 0x204),
    0x00C9: lambda d: _in(d, *_SHARED_00C9_RANGES),
    0x00BE: lambda d: d in (0x09F32CF5, 0x09F6AF65, 0x0A2FC0C5),
    0x00B6: lambda d: (
        d & 0xFF000000 == 0
        or _in(
            d,
            (0x1FFE0000, 0x1FFE06FF),
            (0x3EFE0000, 0x3EFE00FF),
            (0x3E010000, 0x3E0103FF),
        )
        or d in (0x3E01FFFF, 0x3D01FFCC)
    ),
    0x008F: lambda d: d == 1,
    0x0085: lambda d: d == 1,
    0x0083: lambda d: d == 0xFFFFCAFE or d <= 0x6FFFFFFF,
    0x0081: lambda d: d & 0xFFFFFF00 in _SHARED_0081_PREFIXES,
    0x0080: lambda d: d == 0x9000E291,
    0x0077: lambda d: d <= 0x1FFFFFFF,
    0x0076: lambda d: d & 0xFFFFFF == 0xFFFFFF,
    0x0071: lambda d: d == 0x1342,
    0x0062: lambda d: d == 1,
    0x001E: lambda d: d in (0x18000000, 0x15007152, 0x13000000, 0x1006CFDB),
    0x000E: lambda d: d <= 0x13,
    0x000B: lambda d: d == 0x710000,
    0x000A: lambda d: (
        d & 0xFFFF in (0x1F11, 0x2011, 0x2111, 0x2211) or d in _shared_numbers(0x000A)
    ),
    0x0008: lambda d: d == 0x10210043 or d in _shared_numbers(0x0008),
    0x0007: lambda d: (
        0xEE723860 <= d <= 0xEE723862
        or d
        in (0xEE72EB6D, 0xEE7233CF, 0xEE7233C8, 0x0016E360, 0x00DA0892, 0x00070183, 1)
    ),
    0x0002: lambda d: (
        d
        in (
            0x28052017,
            0x2A425250,
            0x422A5250,
            0x3500FF50,
            0x3600FF50,
            0x3800FF50,
            0x3900FF50,
            0x522A4250,
            1,
        )
        or _in(
            d,
            (0x01362FC6, 0x0138AAF6),
            (0x02160EC0, 0x02162248),
            (0xEE820B60, 0xEE8291FF),
        )
    ),
}


def is_suspicious_serial_number(serial_number: bytes | None) -> bool:
    """Whether ``serial_number`` (6 octets) does not identify one device."""
    if serial_number is None or len(serial_number) < 6:
        return False
    manufacturer = int.from_bytes(serial_number[0:2], "big")
    number = int.from_bytes(serial_number[2:6], "big")
    if manufacturer in _PLACEHOLDER_MANUFACTURERS:
        return True
    if number in (0, 0xFFFFFFFF):
        return True
    if manufacturer & 0xF0FF == 0 and number <= 0xFFFFFF:
        return True
    shared = _SHARED_BY_MANUFACTURER.get(manufacturer)
    return shared is not None and shared(number)
