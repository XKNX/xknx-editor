"""Read a device's identity, load states and programmed group communication over the bus.

All reads are read-only and best-effort: a property the device does not expose (or a read that
fails) leaves its field ``None`` instead of failing the whole readout.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xknx.exceptions import XKNXException
from xknx.profile.const import (
    ResourceDevicePropertyId,
    ResourceGenericPropertyId,
    ResourceObjectType,
)

from xknxeditor.download.errors import DownloadError
from xknxeditor.download.load_state import LoadState

if TYPE_CHECKING:
    from collections.abc import Callable

    from xknx import XKNX

    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.download.data_secure import DeviceSecurity
    from xknxeditor.prod import Application
    from xknxeditor.recover import AppId, RawGroupCommunication
    from xknxeditor.recover.tables_decode import DecodedGroupObject

_APPLICATION_ID = re.compile(r"(M-[0-9A-F]{4})_A-([0-9A-F]{4})-([0-9A-F]{2})")
# Group object flags in display order: read, write, communication, transmit, update, init.
_FLAG_LETTERS = "RWCTUI"


@dataclass(frozen=True, slots=True)
class GroupObjectInfo:
    number: int
    size: str | None
    flags: str | None
    group_addresses: tuple[int, ...]


@dataclass(slots=True)
class DeviceInfo:
    individual_address: str
    mask_version: int | None = None
    manufacturer_id: int | None = None
    order_number: bytes | None = None
    serial_number: bytes | None = None
    hardware_type: bytes | None = None
    firmware_version: int | None = None
    programming_mode: bool | None = None
    error_code: int | None = None
    application: AppId | None = None
    application_load_state: LoadState | None = None
    address_table_load_state: LoadState | None = None
    association_table_load_state: LoadState | None = None
    group_object_table_load_state: LoadState | None = None
    group_objects: list[GroupObjectInfo] = field(default_factory=list)
    group_communication_error: str | None = None
    # The tables were not read: the device's application is unknown and it is not System B.
    application_unknown: bool = False


def same_application(application_id: str, app_id: AppId) -> bool:
    """Whether a catalog application id (``M-00FA_A-A012-51-9712``) is the one a device reports."""
    match = _APPLICATION_ID.match(application_id)
    return (
        match is not None
        and match.group(1) == app_id.manufacturer_id
        and int(match.group(2), 16) == app_id.application_number
        and int(match.group(3), 16) == app_id.application_version
    )


def hex_value(data: bytes | None) -> str | None:
    """Raw property bytes as ``$`` followed by upper-case hex."""
    return f"${data.hex().upper()}" if data else None


def serial_text(data: bytes | None) -> str | None:
    """A serial number as ``MMMM:SSSSSSSS`` (manufacturer part, then the rest)."""
    if not data:
        return None
    text = data.hex().upper()
    return f"{text[:4]}:{text[4:]}" if len(data) == 6 else text


def firmware_text(version: int | None) -> str | None:
    """A PID_VERSION value as ``[magic] version.revision``."""
    if version is None:
        return None
    return f"[{version >> 11}] {(version >> 6) & 0x1F}.{version & 0x3F}"


def size_text(object_size: str | None) -> str | None:
    """A ``ComObjectSize`` (``"1 Bit"``, ``"2 Bytes"``) as ``"1 bit"`` / ``"2 Byte"``."""
    if object_size is None:
        return None
    count, _, unit = object_size.partition(" ")
    return f"{count} {'bit' if unit.lower() == 'bit' else 'Byte'}"


def flags_text(group_object: DecodedGroupObject) -> str:
    """The flags as ``RWCTUI`` with ``-`` for each flag that is not set."""
    states = (
        group_object.read,
        group_object.write,
        group_object.communication,
        group_object.transmit,
        group_object.update,
        group_object.read_on_init,
    )
    return "".join(
        letter if state else "-"
        for letter, state in zip(_FLAG_LETTERS, states, strict=True)
    )


async def _property(
    programmer: DeviceProgrammer, object_index: int, property_id: int
) -> bytes | None:
    for attempt in range(3):
        try:
            return await programmer.read_property(object_index, property_id) or None
        except (DownloadError, XKNXException):
            if attempt < 2:
                await asyncio.sleep(0.1)
    return None


async def _load_state(
    programmer: DeviceProgrammer, object_type: int
) -> LoadState | None:
    try:
        return await programmer.read_load_state(
            await programmer.locate_object(object_type)
        )
    except (DownloadError, XKNXException):
        return None


def group_objects(tables: RawGroupCommunication) -> list[GroupObjectInfo]:
    """The group objects that have group addresses, with the addresses in table order."""
    addresses: dict[int, list[int]] = {}
    for link in tables.links:
        addresses.setdefault(link.group_object_number, []).append(link.group_address)
    result: list[GroupObjectInfo] = []
    for number in sorted(addresses):
        decoded = tables.group_objects.get(number)
        result.append(
            GroupObjectInfo(
                number=number,
                size=size_text(decoded.object_size) if decoded else None,
                flags=flags_text(decoded) if decoded else None,
                group_addresses=tuple(addresses[number]),
            )
        )
    return result


def is_system_b(mask_version: int | None) -> bool:
    """Whether a mask version is a System B profile (``x7B0``), which keeps table locations."""
    return mask_version is not None and mask_version & 0x0FFF == 0x07B0


async def read_device_info(
    xknx: XKNX,
    address: str,
    resolve_application: Callable[[AppId], Application | None],
    *,
    group_communication: bool,
    security: DeviceSecurity | None = None,
    interface_max_apdu: int | None = None,
) -> DeviceInfo:
    """Read what a device reports about itself; with ``group_communication`` also its tables.

    The tables are located through the application the device reports, as resolved by
    ``resolve_application``. System B devices are read without one when it cannot be resolved.
    With ``security`` (the device's tool key) every request is sent secured.
    """
    from xknx.telegram import IndividualAddress

    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.download.programmer import (
        DEFAULT_MAX_APDU_LENGTH,
        MAX_COMMUNICATION_APDU_LENGTH,
    )
    from xknxeditor.download.session import apdu_overhead, management_session
    from xknxeditor.recover import read_application_id, read_group_communication

    info = DeviceInfo(individual_address=address)
    manager = management_session(xknx, IndividualAddress(address), security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        info.mask_version = await programmer.read_device_descriptor()
        manufacturer = await _property(
            programmer, 0, ResourceGenericPropertyId.PID_MANUFACTURER_ID
        )
        info.manufacturer_id = (
            int.from_bytes(manufacturer, "big") if manufacturer else None
        )
        info.order_number = await _property(
            programmer, 0, ResourceGenericPropertyId.PID_ORDER_INFO
        )
        info.serial_number = await _property(
            programmer, 0, ResourceGenericPropertyId.PID_SERIAL_NUMBER
        )
        info.hardware_type = await _property(
            programmer, 0, ResourceDevicePropertyId.PID_HARDWARE_TYPE
        )
        version = await _property(programmer, 0, ResourceGenericPropertyId.PID_VERSION)
        info.firmware_version = int.from_bytes(version[:2], "big") if version else None
        progmode = await _property(programmer, 0, ResourceDevicePropertyId.PID_PROGMODE)
        info.programming_mode = bool(progmode[0] & 0x01) if progmode else None
        error = await _property(programmer, 0, ResourceGenericPropertyId.PID_ERROR_CODE)
        info.error_code = error[0] if error else None
        info.application = await read_application_id(programmer)
        info.application_load_state = await _load_state(
            programmer, ResourceObjectType.OBJECT_APPLICATION_PROGRAM
        )
        info.address_table_load_state = await _load_state(
            programmer, ResourceObjectType.OBJECT_ADDRESS_TABLE
        )
        info.association_table_load_state = await _load_state(
            programmer, ResourceObjectType.OBJECT_ASSOCIATION_TABLE
        )
        info.group_object_table_load_state = await _load_state(
            programmer, ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE
        )
        if group_communication:
            application = (
                resolve_application(info.application)
                if info.application is not None
                else None
            )
            if application is None and not is_system_b(info.mask_version):
                info.application_unknown = True
                return info
            # Larger frames only pay off for the table reads.
            limit = MAX_COMMUNICATION_APDU_LENGTH
            if interface_max_apdu is not None:
                limit = min(limit, interface_max_apdu)
            with contextlib.suppress(DownloadError, XKNXException):
                device_max = await programmer.read_max_apdu_length()
                programmer.max_apdu_length = max(
                    DEFAULT_MAX_APDU_LENGTH, min(device_max, limit)
                )
            try:
                tables = await read_group_communication(
                    programmer, application, linked_objects_only=True
                )
            except Exception as exc:
                info.group_communication_error = str(exc) or type(exc).__name__
            else:
                info.group_objects = group_objects(tables)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()
    return info
