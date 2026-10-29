"""What an unload does besides its procedure: the device's programming mode, the additional
individual addresses of an IP device and, for an unload of everything, its individual address.

On the device connection, after the procedure, an unload of everything clears the additional
individual addresses (unless the interface in use is one of them) and reads the serial number.
Without one, it switches the programming mode on, so that the address can be written to the
device in programming mode; an unload that did not restart the device switches it off instead.
With the connection closed, the address the mask unloads to is written by serial number, or to
the device in programming mode, which is then restarted and has to leave programming mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError
from xknx.management.procedures.device.dm_restart_r_co import dm_restart_r_co
from xknx.management.procedures.network.nm_individual_address_read import (
    nm_individual_address_read,
)
from xknx.management.procedures.network.nm_individual_address_serial_number_write import (
    nm_individual_address_serial_number_write,
)
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress

from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

from .commissioning import set_programming_mode
from .errors import DownloadError, LoadStateError, ProgrammingModeError
from .partial import read_resource

if TYPE_CHECKING:
    from xknx import XKNX

    from xknxeditor.namespaces.intermediate.master_data_t import MasterData

    from .programmer import DeviceProgrammer
    from .resources import MaskResources

logger = logging.getLogger(__name__)

# The address an unloaded device has, unless the mask names another.
_DEFAULT_UNLOADED_ADDRESS = 0xFFFF
_UNLOADED_ADDRESS_FEATURE = "UnloadedIndividualAddress"
# The KNXnet/IP parameter object and its additional individual addresses (tunnels).
_KNXNET_IP_OBJECT = 11
_PID_ADDITIONAL_INDIVIDUAL_ADDRESSES = 53
_SERIAL_NUMBER_LENGTH = 6
# The pause between closing the device connection and writing the address.
_ADDRESS_WRITE_DELAY_S = 0.25
# How often the device is looked for in programming mode after its restart.
_PROGRAMMING_MODE_CHECKS = 4


def unloaded_address(
    master_data: MasterData | None, mask_version_id: str
) -> IndividualAddress:
    """The individual address an unload of everything gives the device: the mask's
    ``UnloadedIndividualAddress``, else 15.15.255."""
    if master_data is not None and master_data.mask_versions is not None:
        for mask_version in master_data.mask_versions.mask_version:
            if mask_version.id != mask_version_id:
                continue
            for configuration in mask_version.hawk_configuration_data:
                features = configuration.features
                for feature in features.feature if features is not None else ():
                    name = getattr(feature.name, "value", feature.name)
                    if name == _UNLOADED_ADDRESS_FEATURE:
                        return IndividualAddress(feature.value)
    return IndividualAddress(_DEFAULT_UNLOADED_ADDRESS)


def suspicious_serial_number(serial: bytes) -> bool:
    """Whether a serial number read from a device cannot tell the device apart: an unset or
    placeholder manufacturer part, a number part of only zeros or ones, or one that devices
    of its manufacturer are known to share (``suspicious_serials.json``). The environment
    variable ``XKNX_NO_SERIAL_NUMBER_CHECK`` switches the check off."""
    if os.environ.get("XKNX_NO_SERIAL_NUMBER_CHECK"):
        return False
    if len(serial) != _SERIAL_NUMBER_LENGTH:
        return True
    manufacturer = int.from_bytes(serial[:2], "big")
    number = int.from_bytes(serial[2:], "big")
    known = _suspicious_serials()
    if manufacturer in known.manufacturers or number in known.numbers:
        return True
    if manufacturer & 0xF0FF == 0 and number <= 0xFFFFFF:
        return True
    rule = known.by_manufacturer.get(manufacturer)
    return rule is not None and rule.matches(number)


@dataclass(frozen=True, slots=True)
class _ManufacturerRule:
    """The numbers devices of one manufacturer are known to share."""

    equals: frozenset[int]
    ranges: tuple[tuple[int, int], ...]
    masked: tuple[tuple[int, int], ...]
    lists: tuple[frozenset[int], ...]

    def matches(self, number: int) -> bool:
        return (
            number in self.equals
            or any(low <= number <= high for low, high in self.ranges)
            or any(number & mask == value for mask, value in self.masked)
            or any(number in numbers for numbers in self.lists)
        )


@dataclass(frozen=True, slots=True)
class _SuspiciousSerials:
    manufacturers: frozenset[int]
    numbers: frozenset[int]
    by_manufacturer: dict[int, _ManufacturerRule]


@functools.cache
def _suspicious_serials() -> _SuspiciousSerials:
    data = json.loads(
        (files("xknxeditor.download") / "suspicious_serials.json").read_text("utf-8")
    )
    lists = {name: frozenset(numbers) for name, numbers in data["lists"].items()}
    return _SuspiciousSerials(
        manufacturers=frozenset(data["manufacturer_words"]),
        numbers=frozenset(data["serial_numbers"]),
        by_manufacturer={
            int(word, 16): _ManufacturerRule(
                equals=frozenset(rule["equals"]),
                ranges=tuple((low, high) for low, high in rule["ranges"]),
                masked=tuple((mask, value) for mask, value in rule["masked"]),
                lists=tuple(lists[name] for name in rule["lists"]),
            )
            for word, rule in data["by_manufacturer"].items()
        },
    )


async def on_device_connection(
    programmer: DeviceProgrammer,
    resources: MaskResources | None,
    *,
    unload_address: bool,
    in_programming_mode: bool,
    restarted: bool,
    own_address: IndividualAddress | None,
) -> bytes | None:
    """The steps after the procedure on the device connection; returns the serial number to
    unload the address by, if any.

    ``unload_address``: an unload of everything that still has to unload the address.
    ``in_programming_mode``: the user put the device into programming mode. ``restarted``: the
    procedure restarted the device. ``own_address``: the address of the interface in use."""
    serial: bytes | None = None
    if unload_address:
        await clear_additional_addresses(programmer, own_address)
        serial = await read_serial_number(programmer, resources)
    if resources is None:
        return serial
    if unload_address and serial is None and not in_programming_mode:
        logger.info("unload: switching programming mode on to unload the address")
        await set_programming_mode(programmer, resources, True)
    elif not restarted and (not unload_address or serial is not None):
        logger.info("unload: switching programming mode off (not needed any more)")
        await set_programming_mode(programmer, resources, False)
    return serial


async def clear_additional_addresses(
    programmer: DeviceProgrammer, own_address: IndividualAddress | None
) -> None:
    """Set every additional individual address of an IP device to 15.15.255 (the elements
    stay), unless the interface in use is one of them."""
    try:
        index = await programmer.locate_object(_KNXNET_IP_OBJECT)
    except (LoadStateError, DownloadError, ManagementConnectionError):
        return
    count_data = await programmer.read_property(
        index, _PID_ADDITIONAL_INDIVIDUAL_ADDRESSES, start_index=0
    )
    count = int.from_bytes(count_data[:2], "big") if count_data else 0
    if count == 0:
        return
    data = await programmer.read_property(
        index, _PID_ADDITIONAL_INDIVIDUAL_ADDRESSES, count=count, start_index=1
    )
    addresses = [
        IndividualAddress(int.from_bytes(data[n : n + 2], "big"))
        for n in range(0, len(data) - 1, 2)
    ]
    if own_address is not None and own_address in addresses:
        logger.info(
            "unload: the additional addresses stay: they include %s, the address of the "
            "interface in use",
            own_address,
        )
        return
    logger.info("unload: clearing %d additional individual addresses", count)
    await programmer.write_property(
        index,
        _PID_ADDITIONAL_INDIVIDUAL_ADDRESSES,
        b"\xff\xff" * count,
        count=count,
        start_index=1,
    )


async def read_serial_number(
    programmer: DeviceProgrammer, resources: MaskResources | None
) -> bytes | None:
    """The device's serial number from the mask's ``DeviceSerialNumber`` resource, or
    ``None`` when there is none or it cannot tell the device apart."""
    if resources is None:
        return None
    try:
        serial = await read_resource(
            programmer, resources, ResourceName.DEVICE_SERIAL_NUMBER
        )
    except (DownloadError, ManagementConnectionError) as exc:
        logger.info("unload: no serial number read: %s", exc)
        return None
    if serial is None:
        return None
    serial = serial[:_SERIAL_NUMBER_LENGTH]
    if suspicious_serial_number(serial):
        logger.info("unload: ignoring the suspicious serial number %s", serial.hex())
        return None
    logger.info("unload: serial number %s", serial.hex().upper())
    return serial


async def write_unloaded_address(
    xknx: XKNX, address: IndividualAddress, serial: bytes | None
) -> None:
    """Write ``address`` (see :func:`unloaded_address`) by ``serial`` number, or to the device
    in programming mode, which is restarted and has to leave programming mode then."""
    await asyncio.sleep(_ADDRESS_WRITE_DELAY_S)
    if serial is not None:
        logger.info(
            "unload: writing %s to the device with serial number %s",
            address,
            serial.hex().upper(),
        )
        await nm_individual_address_serial_number_write(xknx, serial, address)
        return
    devices = await nm_individual_address_read(xknx, raise_if_multiple=True)
    if not devices:
        raise ProgrammingModeError("No device is in programming mode.")
    logger.info("unload: writing %s to the device in programming mode", address)
    await xknx.management.broadcast.send(
        payload=apci.IndividualAddressWrite(address=address)
    )
    with contextlib.suppress(ManagementConnectionError):
        async with xknx.management.connection(address=address) as connection:
            await dm_restart_r_co(connection)
    for _ in range(_PROGRAMMING_MODE_CHECKS):
        if address not in await nm_individual_address_read(xknx):
            return
    raise ProgrammingModeError(
        f"The device {address} is still in programming mode: switch its programming mode off."
    )
