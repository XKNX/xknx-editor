"""Read a device's descriptive identity from standard Device Object properties.

Beyond the application id, most devices expose a few standard Device Object
(interface object 0) properties - serial number, manufacturer, order info,
hardware type - that help identify the exact product and enrich the recovered
project. All reads are best-effort: a device that does not expose a property
answers empty, which is reported as ``None`` rather than failing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.exceptions import XKNXException

from xknxeditor.download.errors import DownloadError

if TYPE_CHECKING:
    from xknxeditor.download import DeviceProgrammer

# Standard Device Object (interface object 0) property ids (KNX 3/7/3).
PID_SERIAL_NUMBER = 11
PID_MANUFACTURER_ID = 12
PID_ORDER_INFO = 15
PID_HARDWARE_TYPE = 78


@dataclass(frozen=True, slots=True)
class DeviceDossier:
    """Descriptive identity read from a device's Device Object (all optional)."""

    serial_number: str | None = None
    manufacturer_id: int | None = None
    order_info: str | None = None
    hardware_type: str | None = None


async def _read(
    programmer: DeviceProgrammer,
    property_id: int,
    *,
    attempts: int = 3,
    retry_delay: float = 0.1,
) -> bytes:
    """Read a Device Object property, tolerating both absent properties and transient misses.

    A property the device does not expose answers with an empty (non-erroring) response, returned
    as ``b""`` on the first try. A *failed* read (telegram lost on the tunnel, device busy) raises
    and is retried, so a transient miss does not blank a property the device actually has."""
    for attempt in range(max(attempts, 1)):
        try:
            return await programmer.read_property(0, property_id)
        except (DownloadError, XKNXException):
            if attempt + 1 < attempts:
                await asyncio.sleep(retry_delay)
    return b""


def printable_text(data: bytes) -> str | None:
    """Extract a text-ish property's meaningful string, else a hex dump.

    Some devices wrap the order number in structural bytes (e.g. a Hager actuator
    stores ``22 30 38 30 30 FF FF 02 0F 31`` where ``30 38 30 30`` = ``"0800"`` for
    an AKH-0800). Rather than a raw hex blob, return the longest run of printable
    ASCII (length >= 3), which is the order-number stem; a fully printable value
    (e.g. ``"ITR524-16A"``) is returned whole. Falls back to hex when there is no
    such run."""
    stripped = data.rstrip(b"\x00")
    if not stripped:
        return None
    best = b""
    run = b""
    for byte in stripped:
        if 0x20 <= byte <= 0x7E:
            run += bytes([byte])
            if len(run) > len(best):
                best = run
        else:
            run = b""
    text = best.decode("ascii").strip()
    if len(text) >= 3:
        return text
    return stripped.hex().upper()


async def read_dossier(programmer: DeviceProgrammer) -> DeviceDossier:
    """Read the descriptive Device Object properties, tolerating absent ones."""
    serial = await _read(programmer, PID_SERIAL_NUMBER)
    manufacturer = await _read(programmer, PID_MANUFACTURER_ID)
    order = await _read(programmer, PID_ORDER_INFO)
    hardware = await _read(programmer, PID_HARDWARE_TYPE)
    return DeviceDossier(
        serial_number=serial.hex().upper() if serial else None,
        manufacturer_id=int.from_bytes(manufacturer, "big") if manufacturer else None,
        order_info=printable_text(order),
        hardware_type=hardware.hex().upper() if hardware else None,
    )
