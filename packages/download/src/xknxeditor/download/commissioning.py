"""Program a device's individual address before downloading an application.

A virgin device (or one whose address is unknown) has to be given its individual
address first. Two ways are supported, both delegating to ``xknx``'s network
management procedures:

- via programming mode: exactly one device on the bus must be in programming
  mode; its address is written by broadcast.
- via serial number: the target device is addressed by its serial number, so no
  programming mode is required and several devices may be on the bus.

:func:`download_individual_address` is the stand-alone address download; it can
also switch a device to programming mode by its current address first, to
overwrite that address without pressing the programming button.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError
from xknx.management.procedures.network.nm_individual_address_check import (
    nm_individual_address_check,
    nm_individual_address_check_conn,
)
from xknx.management.procedures.network.nm_individual_address_read import (
    nm_individual_address_read,
)
from xknx.management.procedures.network.nm_individual_address_serial_number_read import (
    nm_individual_address_serial_number_read,
)
from xknx.management.procedures.network.nm_individual_address_serial_number_write import (
    nm_individual_address_serial_number_write,
)
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress

from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

from .errors import (
    AddressInUseError,
    ProgrammingModeError,
    SerialNumberNotFoundError,
    UnsupportedProcedureError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from xknx import XKNX
    from xknx.telegram.address import IndividualAddressableType

    from xknxeditor.prod import MasterData

    from .data_secure import DeviceSecurity
    from .programmer import DeviceProgrammer
    from .resources import MaskResources

logger = logging.getLogger(__name__)

# Individual address of a factory-fresh / unprogrammed device.
DEFAULT_INDIVIDUAL_ADDRESS = IndividualAddress("15.15.255")

# Wait after writing the address before verifying it, so the device has adopted it.
_ADDRESS_ADOPTION_DELAY_S = 1.0

# Seconds each A_IndividualAddress_Read collects responses while waiting for programming mode.
_PROGRAMMING_MODE_POLL_S = 1.0

# Device Object PID_SERIAL_NUMBER, read after an address download to record the device.
_PID_SERIAL_NUMBER = 11
# BCU 1 masks (001xh) have no interface objects to read the serial number from.
_BCU1_MASK = 0x0010

# Programming mode in memory: prog_mode is bit 0, p_parity bit 7 inverts with every change.
_PROG_MODE_BIT = 0x01
_PROG_MODE_PARITY = 0x80


async def program_individual_address(
    xknx: XKNX,
    individual_address: IndividualAddressableType,
    *,
    serial_number: bytes | None = None,
) -> None:
    """Program ``individual_address`` into a device.

    With ``serial_number`` the device is addressed by serial number; otherwise
    the single device currently in programming mode is addressed. ``xknx`` has to
    be started.
    """
    address = IndividualAddress(individual_address)
    if serial_number is not None:
        await nm_individual_address_serial_number_write(xknx, serial_number, address)
        return
    await _write_address_via_programming_mode(xknx, address)


async def _write_address_via_programming_mode(
    xknx: XKNX, address: IndividualAddress
) -> None:
    """Write ``address`` to the single device in programming mode, without restarting it.

    The device keeps its new address and stays reachable, so a following
    application download can connect immediately. xknx's
    :func:`nm_individual_address_write` restarts the device at this point (via
    ``dm_restart_r_co``), which leaves it briefly unreachable and races the
    download's first point-to-point connect ("No ACK received"). The address is
    therefore written directly and the device is left running. Occupancy is not
    re-checked here: commissioning only runs when no device already answers at
    ``address``.
    """
    devices = await nm_individual_address_read(xknx, raise_if_multiple=True)
    if not devices:
        raise ManagementConnectionError("No device in programming mode detected.")

    await xknx.management.broadcast.send(
        payload=apci.IndividualAddressWrite(address=address),
    )
    logger.debug("Wrote address %s to the device in programming mode.", address)

    # Give the device time to adopt its new address before the verifying connect;
    # connecting too soon races the device and yields "No ACK received".
    await asyncio.sleep(_ADDRESS_ADOPTION_DELAY_S)

    async with xknx.management.connection(address=address) as connection:
        if not await nm_individual_address_check_conn(connection):
            raise ManagementConnectionError(
                f"No device answered at {address} after the address write."
            )


async def switch_programming_mode(
    xknx: XKNX,
    individual_address: IndividualAddressableType,
    enable: bool,
    *,
    security: DeviceSecurity | None = None,
    master: MasterData | None = None,
) -> None:
    """Switch the programming mode of the device at ``individual_address``.

    The mask's ``ProgrammingMode`` resource selects the Device Object property
    PID_PROGMODE or the memory octet at 60h (read, modify, write).
    """
    from .merge import mask_authorize_levels
    from .programmer import DeviceProgrammer
    from .resources import mask_resources
    from .session import apdu_overhead, management_session

    address = IndividualAddress(individual_address)
    manager = management_session(xknx, address, security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        mask_id = f"MV-{await programmer.read_device_descriptor():04X}"
        resources = mask_resources(master, mask_id)
        if resources is None:
            raise UnsupportedProcedureError(f"no resources known for mask {mask_id}")
        if security is None and mask_authorize_levels(
            master.raw if master is not None else None, mask_id
        ):
            await programmer.authorize()
        await set_programming_mode(programmer, resources, enable)
        logger.debug(
            "Switched programming mode of %s %s.", address, "on" if enable else "off"
        )
    finally:
        await manager.close()


async def set_programming_mode(
    programmer: DeviceProgrammer, resources: MaskResources, enable: bool
) -> None:
    """Switch the programming mode over an open connection: the mask's ``ProgrammingMode``
    resource is the Device Object property PID_PROGMODE or the memory octet at 60h (read,
    modify, write)."""
    prop = resources.property_ref(ResourceName.PROGRAMMING_MODE)
    memory = resources.memory_address(ResourceName.PROGRAMMING_MODE)
    if prop is not None:
        await programmer.write_property(*prop, bytes([int(enable)]))
    elif memory is not None:
        (current,) = await programmer.read_memory(memory, 1)
        if bool(current & _PROG_MODE_BIT) != enable:
            value = current ^ (_PROG_MODE_BIT | _PROG_MODE_PARITY)
            await programmer.connection.send_data(
                apci.MemoryWrite(address=memory, data=bytes([value]))
            )
    else:
        raise UnsupportedProcedureError("the mask has no programming mode resource")


async def download_individual_address(
    xknx: XKNX,
    individual_address: IndividualAddressableType,
    *,
    current_address: IndividualAddressableType | None = None,
    security: DeviceSecurity | None = None,
    current_security: DeviceSecurity | None = None,
    master: MasterData | None = None,
    waiting: Callable[[], None] | None = None,
    serial_number: bytes | None = None,
) -> bytes | None:
    """Download ``individual_address`` to the single device in programming mode.

    Waits until exactly one device is in programming mode (``waiting`` is called
    once while none is), refuses an address another device uses, writes the
    address, verifies it and restarts the device to end programming mode.

    With ``current_address`` the device at that address is switched to
    programming mode first (secured with ``current_security``), so its address is
    overwritten without pressing the programming button. ``security`` secures the
    verification at the new address.

    With ``serial_number`` the device is addressed by its KNX serial number
    instead, without programming mode and without a restart.

    Returns the device's serial number when it could be read, else ``None``.
    """
    address = IndividualAddress(individual_address)
    current = IndividualAddress(current_address) if current_address else None
    if current == address:
        raise ValueError(f"the device already has the address {address}")
    if serial_number is not None:
        if current is not None:
            raise ValueError("an overwrite addresses the device by its current address")
        await _write_by_serial_number(xknx, address, serial_number)
        return serial_number
    occupied = await nm_individual_address_check(xknx, address)
    if occupied and current is not None:
        raise AddressInUseError(
            f"The individual address {address} is already in use by another device."
        )
    if current is not None:
        await switch_programming_mode(
            xknx, current, True, security=current_security, master=master
        )
    devices = await wait_for_programming_mode(
        xknx, waiting if current is None else None
    )
    if current is not None and devices[0] != current:
        with contextlib.suppress(ManagementConnectionError, asyncio.TimeoutError):
            await switch_programming_mode(
                xknx, current, False, security=current_security, master=master
            )
        raise ProgrammingModeError(
            f"Device {devices[0]} is in programming mode instead of {current}."
        )
    if occupied and devices[0] != address:
        raise AddressInUseError(
            f"The individual address {address} is already in use by another device."
        )
    if devices[0] != address:
        await xknx.management.broadcast.send(
            payload=apci.IndividualAddressWrite(address=address),
        )
        logger.debug("Wrote address %s to device %s.", address, devices[0])
        await asyncio.sleep(_ADDRESS_ADOPTION_DELAY_S)
    return await _verify_and_restart(xknx, address, security)


async def _write_by_serial_number(
    xknx: XKNX, address: IndividualAddress, serial_number: bytes
) -> None:
    """Write ``address`` to the device with ``serial_number`` and verify it."""
    found = await nm_individual_address_serial_number_read(xknx, serial_number)
    if found is None:
        raise SerialNumberNotFoundError(
            f"The device with the serial number {serial_number.hex().upper()} "
            "could not be found."
        )
    if found == address:
        logger.debug(
            "Device %s already has the address %s.", serial_number.hex(), address
        )
        return
    if await nm_individual_address_check(xknx, address):
        raise AddressInUseError(
            f"The individual address {address} is already in use by another device."
        )
    await nm_individual_address_serial_number_write(xknx, serial_number, address)


async def wait_for_programming_mode(
    xknx: XKNX, waiting: Callable[[], None] | None
) -> list[IndividualAddress]:
    """Poll until exactly one device is in programming mode; without ``waiting``, poll once."""
    notified = False
    while True:
        devices = await nm_individual_address_read(
            xknx, timeout=_PROGRAMMING_MODE_POLL_S
        )
        if len(devices) > 1:
            raise ProgrammingModeError("More than one device is in programming mode.")
        if devices:
            return devices
        if waiting is None:
            raise ProgrammingModeError("No device is in programming mode.")
        if not notified:
            waiting()
            notified = True


async def _verify_and_restart(
    xknx: XKNX, address: IndividualAddress, security: DeviceSecurity | None
) -> bytes | None:
    """Read the device descriptor at ``address``, then its serial number, and restart it."""
    from .errors import DownloadError
    from .programmer import DeviceProgrammer
    from .session import apdu_overhead, management_session

    manager = management_session(xknx, address, security)
    try:
        connection = await manager.open()
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        mask = await programmer.read_device_descriptor()
        serial: bytes | None = None
        if mask & 0xFFF0 != _BCU1_MASK:
            with contextlib.suppress(ManagementConnectionError, DownloadError):
                serial = await programmer.read_property(0, _PID_SERIAL_NUMBER) or None
        await programmer.restart()
        return serial
    except (ManagementConnectionError, DownloadError) as exc:
        raise ManagementConnectionError(
            f"No device answered at {address} after the address write."
        ) from exc
    finally:
        await manager.close()
