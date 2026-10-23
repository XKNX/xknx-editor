"""Read-only DALI bus scan for the Siemens KNX/DALI Gateway plus N 141/03 (M-0001_A-9837).

Different protocol family from MDT/Theben: no Hawk-style property-table at pids 203-228 and no
3-byte command poll. Siemens drives the DALI bus via the standard KNX A_PropertyValue_Read /
A_PropertyValue_Write / A_FunctionProperty primitives on interface-object 35 (DALI line 1) or 36
(line 2 on the Twin / N141/32 variant). This module only implements the read-only scan — a
non-destructive beachhead for a later Siemens profile; writes are deliberately out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from xknxeditor.dali.connection import Programmer

# A_PropertyValue_Read encodes the element count in 4 bits — at most 15 elements per telegram.
_MAX_PROPERTY_ELEMENTS = 15

# Each DALI line holds up to 64 ECGs; every scan property is an array of 64 elements.
MAX_ECG_PER_LINE = 64

# Interface-object index per DALI line: line 0 lives at 35, line 1 (Twin only) at 36.
LINE_OBJECT_INDEX_BASE = 35


def object_index_for_line(line: int) -> int:
    """KNX interface-object index for ``line`` (0-based). Raises on an invalid line."""
    if line < 0 or line > 1:
        raise ValueError("Siemens N141/03 has 1 or 2 DALI lines; line must be 0 or 1")
    return LINE_OBJECT_INDEX_BASE + line


class DaliDeviceType(IntEnum):
    """Standard DALI device-type codes reported by the gateway in the Types array."""

    ECG = 0
    INVERTER = 1
    DISCHARGE = 2
    LV_HALO = 3
    DIMMABLE_LAMP = 4
    CONVERTER_10V = 5
    LED_MODULE = 6
    SWITCHING_FUNCTION = 7
    COLOUR_CONTROL = 8
    SEQUENCER = 9


@dataclass(slots=True)
class SiemensN141EcgRecord:
    """One ECG slot read back from the gateway's scan arrays."""

    slot: int  # 0-based DALI short address (0..63)
    present: bool  # false when the slot's short address is 0xFF (not assigned)
    short_address: int  # as reported (0..63 for present, 0xFF for unassigned)
    random_address: int  # 24-bit DALI random long address (big-endian in the raw frame)
    type: int  # DALI device type (first type byte of the 8-byte Types array)
    type_bytes: bytes  # the raw 8-byte Types array for this slot
    min_phys_dim_value: int  # per-ECG physical minimum dim level
    # Optional / feature-gated reads (0 / empty when the array was not requested or supported):
    cin: bytes = b""  # 8-byte Cin (converter info); only when with_cin
    connected_device: int = 0
    min_emergency_level: int = 0  # only when with_emergency
    max_emergency_level: int = 0  # only when with_emergency
    dev_type1_features: int = 0  # only when with_emergency
    failure_flags: int = 0  # only when with_emergency and interface_version >= 515
    colour_temp_phys_coolest: int = 0
    colour_temp_phys_warmest: int = 0
    colour_type_features: int = 0


@dataclass(slots=True)
class SiemensN141BusState:
    """A read-only snapshot of one DALI line (result of a scan)."""

    line: int  # 0-based line index
    object_index: int  # KNX interface-object the scan was read from
    ecgs: list[SiemensN141EcgRecord] = field(
        default_factory=list["SiemensN141EcgRecord"]
    )

    @property
    def present_ecgs(self) -> list[SiemensN141EcgRecord]:
        return [e for e in self.ecgs if e.present]


# Property ids on the DALI interface object (odx 35 / 36) that the vendor's plug-in reads during
# a bus scan. Values and byte sizes are the plug-in's own ImageInformation constants.
PROP_SHORT_ADDRESS = 51  # 1 B per ECG
PROP_RND = 52  # 3 B per ECG, 24-bit random long address
PROP_TYPES = 53  # 8 B per ECG
PROP_MIN_PHYS_DIM = 57  # 1 B per ECG
PROP_CIN = 58  # 8 B per ECG, gated by with_cin
PROP_CONNECTED_DEVICE = 70  # 1 B per ECG
PROP_MIN_EMERGENCY_LEVEL = 71  # 1 B per ECG, gated by with_emergency
PROP_MAX_EMERGENCY_LEVEL = 72  # 1 B per ECG, gated by with_emergency
PROP_DEV_TYPE1_FEATURES = 73  # 1 B per ECG, gated by with_emergency
PROP_FAILURE_FLAGS = 74  # 1 B per ECG, gated by with_emergency and firmware >= 515
PROP_COLOUR_TEMP_PHYS_COOLEST = 75  # 2 B per ECG
PROP_COLOUR_TEMP_PHYS_WARMEST = 76  # 2 B per ECG
PROP_COLOUR_TYPE_FEATURES = 77  # 1 B per ECG


async def _read_table(
    programmer: Programmer, object_index: int, property_id: int, element_size: int
) -> list[bytes]:
    """Read all 64 elements of a scan property as a list of raw per-ECG byte blocks."""
    out = bytearray()
    start = 1
    remaining = MAX_ECG_PER_LINE
    while remaining > 0:
        request = min(remaining, _MAX_PROPERTY_ELEMENTS)
        chunk = await programmer.read_property(
            object_index, property_id, count=request, start_index=start
        )
        got = len(chunk) // element_size
        if got == 0:
            # Device returned a short response — the property is absent or capped; stop.
            break
        out += chunk[: got * element_size]
        start += got
        remaining -= got
    return [bytes(out[i : i + element_size]) for i in range(0, len(out), element_size)]


async def scan_n141_03(
    programmer: Programmer,
    line: int = 0,
    *,
    with_cin: bool = False,
    with_emergency: bool = False,
    interface_version: int = 0,
) -> SiemensN141BusState:
    """Read-only scan of one DALI line on a Siemens KNX/DALI Gateway plus N 141/03.

    Does not trigger a fresh DALI discovery — it reads the arrays the gateway already holds for the
    line, in the same order the vendor's plug-in reads them (short address, random address, types,
    min physical dim, optional converter info / emergency / colour-temperature). ``with_cin`` /
    ``with_emergency`` toggle optional array reads; ``interface_version`` ≥ 515 additionally
    enables failure flags.
    """
    odx = object_index_for_line(line)

    short = await _read_table(programmer, odx, PROP_SHORT_ADDRESS, 1)
    rnd = await _read_table(programmer, odx, PROP_RND, 3)
    types = await _read_table(programmer, odx, PROP_TYPES, 8)
    min_phys = await _read_table(programmer, odx, PROP_MIN_PHYS_DIM, 1)
    connected = await _read_table(programmer, odx, PROP_CONNECTED_DEVICE, 1)
    cin: list[bytes] = []
    if with_cin:
        cin = await _read_table(programmer, odx, PROP_CIN, 8)
    min_emg: list[bytes] = []
    max_emg: list[bytes] = []
    dev_type1: list[bytes] = []
    failure: list[bytes] = []
    if with_emergency:
        min_emg = await _read_table(programmer, odx, PROP_MIN_EMERGENCY_LEVEL, 1)
        max_emg = await _read_table(programmer, odx, PROP_MAX_EMERGENCY_LEVEL, 1)
        dev_type1 = await _read_table(programmer, odx, PROP_DEV_TYPE1_FEATURES, 1)
        if interface_version >= 515:
            failure = await _read_table(programmer, odx, PROP_FAILURE_FLAGS, 1)
    cool = await _read_table(programmer, odx, PROP_COLOUR_TEMP_PHYS_COOLEST, 2)
    warm = await _read_table(programmer, odx, PROP_COLOUR_TEMP_PHYS_WARMEST, 2)
    colour_type = await _read_table(programmer, odx, PROP_COLOUR_TYPE_FEATURES, 1)

    records: list[SiemensN141EcgRecord] = []
    for slot in range(MAX_ECG_PER_LINE):
        sa = short[slot][0] if slot < len(short) else 0xFF
        records.append(
            SiemensN141EcgRecord(
                slot=slot,
                # 0xFF marks an empty slot (short address not assigned on this physical position).
                present=sa != 0xFF,
                short_address=sa,
                random_address=(
                    (rnd[slot][0] << 16) | (rnd[slot][1] << 8) | rnd[slot][2]
                    if slot < len(rnd) and len(rnd[slot]) == 3
                    else 0
                ),
                type=types[slot][0] if slot < len(types) and types[slot] else -1,
                type_bytes=types[slot] if slot < len(types) else b"",
                min_phys_dim_value=min_phys[slot][0] if slot < len(min_phys) else 0,
                cin=cin[slot] if slot < len(cin) else b"",
                connected_device=connected[slot][0] if slot < len(connected) else 0,
                min_emergency_level=min_emg[slot][0] if slot < len(min_emg) else 0,
                max_emergency_level=max_emg[slot][0] if slot < len(max_emg) else 0,
                dev_type1_features=dev_type1[slot][0] if slot < len(dev_type1) else 0,
                failure_flags=failure[slot][0] if slot < len(failure) else 0,
                colour_temp_phys_coolest=(
                    (cool[slot][0] << 8) | cool[slot][1]
                    if slot < len(cool) and len(cool[slot]) == 2
                    else 0
                ),
                colour_temp_phys_warmest=(
                    (warm[slot][0] << 8) | warm[slot][1]
                    if slot < len(warm) and len(warm[slot]) == 2
                    else 0
                ),
                colour_type_features=colour_type[slot][0]
                if slot < len(colour_type)
                else 0,
            )
        )
    return SiemensN141BusState(line=line, object_index=odx, ecgs=records)


# App-id prefixes that identify the N 141/03 generation (classic plug-in family). The modern
# N 141/14 and N 141/32 generation uses a different protocol family and is not covered here.
N141_03_APP_ID_PREFIXES: tuple[str, ...] = ("M-0001_A-9837",)


def is_siemens_n141_03(app_id: str | None) -> bool:
    """Whether ``app_id`` is a Siemens N 141/03 KNX/DALI Gateway plus application."""
    if not app_id:
        return False
    upper = app_id.upper()
    return any(upper.startswith(prefix.upper()) for prefix in N141_03_APP_ID_PREFIXES)


__all__ = [
    "MAX_ECG_PER_LINE",
    "N141_03_APP_ID_PREFIXES",
    "PROP_CIN",
    "PROP_COLOUR_TEMP_PHYS_COOLEST",
    "PROP_COLOUR_TEMP_PHYS_WARMEST",
    "PROP_COLOUR_TYPE_FEATURES",
    "PROP_CONNECTED_DEVICE",
    "PROP_DEV_TYPE1_FEATURES",
    "PROP_FAILURE_FLAGS",
    "PROP_MAX_EMERGENCY_LEVEL",
    "PROP_MIN_EMERGENCY_LEVEL",
    "PROP_MIN_PHYS_DIM",
    "PROP_RND",
    "PROP_SHORT_ADDRESS",
    "PROP_TYPES",
    "DaliDeviceType",
    "SiemensN141BusState",
    "SiemensN141EcgRecord",
    "is_siemens_n141_03",
    "object_index_for_line",
    "scan_n141_03",
]
