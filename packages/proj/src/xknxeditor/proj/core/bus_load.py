"""How many devices a line or a segment has, and the bus current they draw.

A line counts the devices of all its segments; its bus current is that of its main segment, a
segment's that of its own devices. A device adds its hardware's bus current. A power supply
adds nothing, nor does a device without an individual address. A device whose hardware gives no
bus current (none, or 0) adds an estimated 10 mA, and the total is then estimated. A device on
an IoT segment, or one that is not a coupler on a line whose main segment is RF, needs no
estimate and adds nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from xknxeditor.proj.core.medium import MEDIUM_IOT, MEDIUM_RF

# The bus current a device without data is estimated to draw, and the one a power supply
# without data is shown with, in mA.
ESTIMATED_DEVICE_CURRENT = 10.0
ESTIMATED_POWER_SUPPLY_CURRENT = 320.0


@dataclass(frozen=True)
class Hardware:
    """What the catalog tells about a device's hardware: its bus current (mA, None or 0 for
    none), whether it is a power supply, and whether it is a coupler."""

    bus_current: float | None = None
    power_supply: bool = False
    coupler: bool = False


@dataclass(frozen=True)
class BusLoad:
    """The devices of a line or a segment and the bus current they draw (mA); ``estimated``
    when a device without data added the estimate."""

    devices: int = 0
    current: float = 0.0
    estimated: bool = False


def needs_estimate(
    hardware: Hardware | None, segment_medium: str, line_medium: str
) -> bool:
    """Whether a device without bus current data is estimated (see the module)."""
    if segment_medium == MEDIUM_IOT:
        return False
    coupler = hardware is not None and hardware.coupler
    return coupler or line_medium != MEDIUM_RF


def device_current(
    hardware: Hardware | None, *, has_address: bool, estimate: bool
) -> tuple[float, bool]:
    """What a device adds to its segment's bus current, and whether it is an estimate."""
    if hardware is not None and hardware.power_supply:
        return 0.0, False
    if not has_address:
        return 0.0, False
    if hardware is not None and hardware.bus_current:
        return hardware.bus_current, False
    if estimate:
        return ESTIMATED_DEVICE_CURRENT, True
    return 0.0, False


def shown_current(
    hardware: Hardware | None, *, estimate: bool
) -> tuple[float | None, bool]:
    """A device's own bus current as its details show it: its hardware's, else the estimate
    (that of a power supply for one) when it needs one, else None; and whether it is
    estimated."""
    if hardware is not None and hardware.bus_current:
        return hardware.bus_current, False
    if not estimate:
        return None, False
    if hardware is not None and hardware.power_supply:
        return ESTIMATED_POWER_SUPPLY_CURRENT, True
    return ESTIMATED_DEVICE_CURRENT, True


def total(parts: Iterable[tuple[float, bool]], devices: int) -> BusLoad:
    """The load of ``devices`` devices whose bus currents are ``parts``."""
    current = 0.0
    estimated = False
    for value, guess in parts:
        current += value
        estimated = estimated or guess
    return BusLoad(devices=devices, current=current, estimated=estimated)
