"""Program a device's individual address before downloading an application.

A virgin device (or one whose address is unknown) has to be given its individual
address first. Two ways are supported, both delegating to ``xknx``'s network
management procedures:

- via programming mode: exactly one device on the bus must be in programming
  mode; its address is written by broadcast.
- via serial number: the target device is addressed by its serial number, so no
  programming mode is required and several devices may be on the bus.

A KNX RF device (``domain_address`` given) gets its domain address the same way, with system
broadcasts (see :mod:`.rf_commissioning`).

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

from .communication_path import (
    BroadcastPath,
    KnxMedium,
    SecureSystemBroadcastBlocked,
    broadcast_path,
)
from .errors import (
    AddressInUseError,
    ProgrammingModeError,
    SerialNumberMismatchError,
    SerialNumberNotFoundError,
    UnsupportedProcedureError,
)
from .rf_commissioning import (
    domain_address_serial_number_write,
    domain_and_individual_address_write2,
    ip_addresses_by_serial_number,
    secure_addresses_by_serial_number,
)
from .serial_numbers import suspicious_serial_number

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from typing import Any

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
    domain_address: bytes | None = None,
    security: DeviceSecurity | None = None,
    ip_system_broadcast: bool = False,
) -> bytes | None:
    """Program ``individual_address`` into a device. Returns the serial number read from the
    device in programming mode (to record it, also over another one stored for the device),
    else None.

    With ``serial_number`` the device is addressed by serial number; otherwise
    the single device currently in programming mode is addressed. A KNX RF device
    gets ``domain_address`` first: by serial number with ``security`` secured with
    KNX Data Secure. A KNX IP device gets its ``domain_address`` (see
    :func:`.rf_commissioning.ip_domain_address`) by serial number only, by system broadcast
    when it takes them (``ip_system_broadcast``). The couplers on the way pass the
    broadcasts meanwhile (see :func:`.communication_path.broadcast_path`). ``xknx`` has to be
    started. A serial number that cannot tell the device apart is not used (see
    :func:`distinct_serial_number`), nor one while a device is in programming mode (see
    :func:`_serial_number_unless_programming_mode`).
    """
    address = IndividualAddress(individual_address)
    serial_number = await _serial_number_unless_programming_mode(
        xknx, distinct_serial_number(serial_number), domain_address, security
    )
    medium = _domain_medium(domain_address, serial_number)
    async with broadcast_path(
        xknx,
        address,
        system_broadcast=_system_broadcast(medium, ip_system_broadcast),
        medium=medium,
    ) as path:
        if medium is KnxMedium.IP and domain_address is not None:
            assert serial_number is not None
            await _ip_addresses(
                xknx, address, domain_address, serial_number, security, path
            )
        elif domain_address is not None and medium is KnxMedium.RF:
            await _program_rf_addresses(
                xknx, address, domain_address, serial_number, security, path
            )
        elif serial_number is not None:
            await nm_individual_address_serial_number_write(
                xknx, serial_number, address
            )
        else:
            return await _write_address_via_programming_mode(xknx, address)
    return None


async def _serial_number_unless_programming_mode(
    xknx: XKNX,
    serial_number: bytes | None,
    domain_address: bytes | None,
    security: DeviceSecurity | None,
) -> bytes | None:
    """``serial_number``, unless a device is in programming mode: then None, so the address
    goes to that device. The devices in programming mode are looked for first, and the serial
    number is only used while none is; a serial number stored for the device does not keep the
    device whose programming button was pressed from getting the address (its serial number is
    read and recorded afterwards). Two devices in programming mode raise
    ``ProgrammingModeError``. A device secured with ``security`` (its device certificate has the
    serial number) has to be the one with ``serial_number``; another one raises
    ``SerialNumberMismatchError`` before its address is written. A KNX RF device (6-octet
    ``domain_address``) is looked for in programming mode by its own path."""
    if serial_number is None or (
        domain_address is not None and len(domain_address) == 6
    ):
        return serial_number
    devices = await nm_individual_address_read(xknx, timeout=_PROGRAMMING_MODE_POLL_S)
    if not devices:
        return serial_number
    if len(devices) > 1:
        raise ProgrammingModeError("More than one device is in programming mode.")
    (found,) = devices
    if security is not None:
        found_serial = await _plain_serial_number(xknx, found)
        if found_serial != serial_number:
            raise SerialNumberMismatchError(
                f"The device {found} in programming mode has the serial number "
                f"{_serial_text(found_serial)}, not {_serial_text(serial_number)} of the "
                "device's certificate."
            )
    logger.info(
        "device %s is in programming mode: the address goes to it, not to the device with "
        "the serial number %s",
        found,
        serial_number.hex().upper(),
    )
    return None


def _serial_text(serial_number: bytes | None) -> str:
    return "unknown" if serial_number is None else serial_number.hex().upper()


async def _plain_serial_number(xknx: XKNX, address: IndividualAddress) -> bytes | None:
    """The KNX Serial Number of the device at ``address``, read in the clear; None when it
    cannot be read."""
    from .errors import DownloadError
    from .programmer import DeviceProgrammer
    from .session import management_session

    manager = management_session(xknx, address, None)
    try:
        connection = await manager.open()
        programmer = DeviceProgrammer(connection)
        return await programmer.read_property(0, _PID_SERIAL_NUMBER) or None
    except (ManagementConnectionError, DownloadError):
        return None
    finally:
        await manager.close()


async def _program_rf_addresses(
    xknx: XKNX,
    address: IndividualAddress,
    domain_address: bytes,
    serial_number: bytes | None,
    security: DeviceSecurity | None,
    path: BroadcastPath,
) -> None:
    """The domain and individual address of a KNX RF device for its first download; the plain
    services as system broadcasts or broadcasts, as ``path`` says."""
    system_broadcast = path.system_broadcast
    if serial_number is not None:
        serial = serial_number

        async def plain() -> None:
            await domain_address_serial_number_write(
                xknx, serial, domain_address, system_broadcast=system_broadcast
            )
            await nm_individual_address_serial_number_write(xknx, serial, address)

        if security is None:
            await plain()
            return
        await _secured(
            path,
            domain_address,
            secure_addresses_by_serial_number(
                xknx,
                serial,
                address,
                security,
                domain_address=domain_address,
                system_broadcast=system_broadcast,
            ),
            plain,
        )
        return
    # Left running, as the TP path, so the download can connect right away.
    await domain_and_individual_address_write2(
        xknx,
        domain_address,
        address,
        restart=False,
        system_broadcast=system_broadcast,
        switch_rf_mode=path.switch_rf_mode,
    )


def distinct_serial_number(serial_number: bytes | None) -> bytes | None:
    """``serial_number`` to address a device by, or ``None`` when it cannot tell the device
    apart (see :func:`.serial_numbers.suspicious_serial_number`): other devices may have it as
    well and would take the address too, so the address goes through programming mode."""
    if serial_number is None or not suspicious_serial_number(serial_number):
        return serial_number
    logger.warning(
        "the serial number %s cannot tell the device apart; the address is downloaded "
        "through programming mode",
        serial_number.hex().upper(),
    )
    return None


def _domain_medium(
    domain_address: bytes | None, serial_number: bytes | None
) -> KnxMedium:
    """The medium of a device by its domain address: 6 octets KNX RF, 4 or 21 octets KNX IP
    (written by serial number only, so without one the device is commissioned as on TP)."""
    if domain_address is None:
        return KnxMedium.TP
    if len(domain_address) == 6:
        return KnxMedium.RF
    if len(domain_address) in (4, 21) and serial_number is not None:
        return KnxMedium.IP
    return KnxMedium.TP


def _system_broadcast(medium: KnxMedium, ip_system_broadcast: bool) -> bool:
    """Whether the commissioning services are system broadcasts: towards KNX RF, and towards a
    KNX IP device that takes them."""
    if medium is KnxMedium.RF:
        return True
    return medium is KnxMedium.IP and ip_system_broadcast


async def _secured(
    path: BroadcastPath,
    domain_address: bytes | None,
    secured: Coroutine[Any, Any, None],
    plain: Callable[[], Awaitable[None]],
) -> None:
    """Run the ``secured`` commissioning. When the device answers none of its keys because a
    coupler on the way does not pass secured system broadcasts, the addresses are written in
    the clear (``plain``) instead; a domain address with a key (the backbone key of an IP
    device) never is: :class:`SecureSystemBroadcastBlocked` names the coupler."""
    from .data_secure import SecureProgrammingError

    try:
        await secured
    except SecureProgrammingError:
        blocker = path.secure_restriction
        if blocker is None:
            raise
        if domain_address is not None and len(domain_address) == 21:
            raise SecureSystemBroadcastBlocked(blocker) from None
        logger.warning(
            "The coupler %s does not pass secured system broadcasts: the addresses are "
            "written in the clear",
            blocker,
        )
        await plain()


async def _ip_addresses(
    xknx: XKNX,
    address: IndividualAddress,
    domain_address: bytes,
    serial_number: bytes,
    security: DeviceSecurity | None,
    path: BroadcastPath,
    address_in_use: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """The domain and individual address of a KNX IP device by serial number: secured with
    ``security`` when given (the domain address with the backbone key is never written in the
    clear), else in the clear."""
    from .data_secure import SecureProgrammingError

    async def plain() -> None:
        await ip_addresses_by_serial_number(
            xknx,
            serial_number,
            address,
            domain_address[:4],
            system_broadcast=path.system_broadcast,
            close_system_broadcast=path.close_system_broadcast,
        )

    if security is None:
        if len(domain_address) == 21:
            raise SecureProgrammingError(
                "The backbone key is only written to a device secured with its Tool Key."
            )
        await plain()
        return
    await _secured(
        path,
        domain_address,
        secure_addresses_by_serial_number(
            xknx,
            serial_number,
            address,
            security,
            domain_address=domain_address,
            address_in_use=address_in_use,
            system_broadcast=path.system_broadcast,
            close_system_broadcast=path.close_system_broadcast,
        ),
        plain,
    )


async def _write_address_via_programming_mode(
    xknx: XKNX, address: IndividualAddress
) -> bytes | None:
    """Write ``address`` to the single device in programming mode, without restarting it, and
    return its serial number (None when it cannot be read).

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
    return await _plain_serial_number(xknx, address)


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
    domain_address: bytes | None = None,
    ip_system_broadcast: bool = False,
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
    instead, without programming mode and without a restart; not with one that cannot tell the
    device apart (see :func:`distinct_serial_number`), nor while a device is in programming mode,
    which then gets the address (see :func:`_serial_number_unless_programming_mode`).

    A KNX RF device gets ``domain_address`` as well, and is reached by system
    broadcasts, also with another or no domain address (see :mod:`.rf_commissioning`). By
    serial number with ``security``, and for the overwrite of a device that uses
    ``current_security``, the addresses are written secured with KNX Data Secure. A KNX IP
    device gets its ``domain_address`` (its routing multicast address, also with the backbone
    key) by serial number, also when it is overwritten secured, by system broadcast when it
    takes them (``ip_system_broadcast``). The couplers on the way pass the broadcasts meanwhile
    (see :func:`.communication_path.broadcast_path`).

    Returns the device's serial number when it could be read, else ``None``.
    """
    address = IndividualAddress(individual_address)
    current = IndividualAddress(current_address) if current_address else None
    if current == address:
        raise ValueError(f"the device already has the address {address}")
    if serial_number is not None and current is not None:
        raise ValueError("an overwrite addresses the device by its current address")
    serial_number = await _serial_number_unless_programming_mode(
        xknx, distinct_serial_number(serial_number), domain_address, security
    )
    by_serial = serial_number is not None or (
        current is not None and current_security is not None
    )
    medium = _domain_medium(domain_address, serial_number if by_serial else None)
    if by_serial and domain_address is not None and len(domain_address) in (4, 21):
        medium = KnxMedium.IP
    async with broadcast_path(
        xknx,
        address,
        system_broadcast=_system_broadcast(medium, ip_system_broadcast),
        medium=medium,
    ) as path:
        if medium is KnxMedium.IP and domain_address is not None:
            return await _download_ip(
                xknx,
                address,
                domain_address,
                current=current,
                security=security,
                current_security=current_security,
                serial_number=serial_number,
                path=path,
            )
        if medium is KnxMedium.RF and domain_address is not None:
            return await _download_rf(
                xknx,
                address,
                domain_address,
                current=current,
                security=security,
                current_security=current_security,
                master=master,
                waiting=waiting,
                serial_number=serial_number,
                path=path,
            )
        return await _download_tp(
            xknx,
            address,
            current=current,
            security=security,
            current_security=current_security,
            master=master,
            waiting=waiting,
            serial_number=serial_number,
        )


async def _download_tp(
    xknx: XKNX,
    address: IndividualAddress,
    *,
    current: IndividualAddress | None,
    security: DeviceSecurity | None,
    current_security: DeviceSecurity | None,
    master: MasterData | None,
    waiting: Callable[[], None] | None,
    serial_number: bytes | None,
) -> bytes | None:
    """The address download of any other device: by serial number, else through programming
    mode (see :func:`download_individual_address`)."""
    if serial_number is not None:
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


async def _download_rf(
    xknx: XKNX,
    address: IndividualAddress,
    domain_address: bytes,
    *,
    current: IndividualAddress | None,
    security: DeviceSecurity | None,
    current_security: DeviceSecurity | None,
    master: MasterData | None,
    waiting: Callable[[], None] | None,
    serial_number: bytes | None,
    path: BroadcastPath,
) -> bytes | None:
    """The address download of a KNX RF device: by serial number (secured with ``security``
    when given), as a secured overwrite of a device that uses ``current_security`` (a device in
    Security Mode takes a new address only secured, 03.05.03 §1.5.4.3.2), else through
    programming mode. The plain services go as system broadcasts or broadcasts, as ``path``
    says."""
    system_broadcast = path.system_broadcast

    async def address_in_use() -> bool:
        return await nm_individual_address_check(xknx, address)

    def plain(serial: bytes) -> Callable[[], Awaitable[None]]:
        async def write() -> None:
            await domain_address_serial_number_write(
                xknx, serial, domain_address, system_broadcast=system_broadcast
            )
            await _write_by_serial_number(xknx, address, serial)

        return write

    if serial_number is not None:
        if security is None:
            await plain(serial_number)()
            return serial_number
        await _secured(
            path,
            domain_address,
            secure_addresses_by_serial_number(
                xknx,
                serial_number,
                address,
                security,
                domain_address=domain_address,
                address_in_use=address_in_use,
                system_broadcast=system_broadcast,
            ),
            plain(serial_number),
        )
        return serial_number
    if current is not None and current_security is not None:
        serial = await _read_serial_number(xknx, current, current_security)
        await _secured(
            path,
            domain_address,
            secure_addresses_by_serial_number(
                xknx,
                serial,
                address,
                current_security,
                domain_address=domain_address,
                address_in_use=address_in_use,
                system_broadcast=system_broadcast,
            ),
            plain(serial),
        )
        return await _verify_and_restart(xknx, address, security or current_security)
    occupied = await address_in_use()
    if occupied and current is not None:
        raise AddressInUseError(
            f"The individual address {address} is already in use by another device."
        )
    if current is not None:
        await switch_programming_mode(
            xknx, current, True, security=current_security, master=master
        )
    return await _download_rf_addresses(
        xknx,
        domain_address,
        address,
        current=current,
        occupied=occupied,
        waiting=waiting,
        security=security,
        current_security=current_security,
        master=master,
        path=path,
    )


async def _download_ip(
    xknx: XKNX,
    address: IndividualAddress,
    domain_address: bytes,
    *,
    current: IndividualAddress | None,
    security: DeviceSecurity | None,
    current_security: DeviceSecurity | None,
    serial_number: bytes | None,
    path: BroadcastPath,
) -> bytes | None:
    """The address download of a KNX IP device by serial number: the given one, or for the
    overwrite of a device that uses ``current_security`` the one read from it; it is then
    verified at its new address and restarted."""

    async def address_in_use() -> bool:
        return await nm_individual_address_check(xknx, address)

    if serial_number is not None:
        await _ip_addresses(
            xknx, address, domain_address, serial_number, security, path, address_in_use
        )
        return serial_number
    assert current is not None and current_security is not None
    serial = await _read_serial_number(xknx, current, current_security)
    await _ip_addresses(
        xknx, address, domain_address, serial, current_security, path, address_in_use
    )
    return await _verify_and_restart(xknx, address, security or current_security)


async def _read_serial_number(
    xknx: XKNX, address: IndividualAddress, security: DeviceSecurity
) -> bytes:
    """The KNX Serial Number of the device at ``address``, read secured."""
    from .errors import DownloadError
    from .programmer import DeviceProgrammer
    from .session import apdu_overhead, management_session

    manager = management_session(xknx, address, security)
    try:
        connection = await manager.open()
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        serial = await programmer.read_property(0, _PID_SERIAL_NUMBER)
    except (ManagementConnectionError, DownloadError) as exc:
        raise SerialNumberNotFoundError(
            f"The serial number of the device at {address} could not be read: {exc}"
        ) from exc
    finally:
        await manager.close()
    if not serial:
        raise SerialNumberNotFoundError(
            f"The device at {address} has no serial number to address it by."
        )
    return serial


async def _download_rf_addresses(
    xknx: XKNX,
    domain_address: bytes,
    address: IndividualAddress,
    *,
    current: IndividualAddress | None,
    occupied: bool,
    waiting: Callable[[], None] | None,
    security: DeviceSecurity | None,
    current_security: DeviceSecurity | None,
    master: MasterData | None,
    path: BroadcastPath,
) -> bytes | None:
    """The domain and individual address of the KNX RF device in programming mode, with the
    checks of the TP path: the device switched by ``current`` must be the one, and a device that
    uses ``address`` must be the one in programming mode."""

    def check(found: IndividualAddress) -> None:
        if current is not None and found != current:
            raise ProgrammingModeError(
                f"Device {found} is in programming mode instead of {current}."
            )
        if occupied and found != address:
            raise AddressInUseError(
                f"The individual address {address} is already in use by another device."
            )

    try:
        _, serial = await domain_and_individual_address_write2(
            xknx,
            domain_address,
            address,
            waiting=waiting if current is None else None,
            check=check,
            security=security,
            system_broadcast=path.system_broadcast,
            switch_rf_mode=path.switch_rf_mode,
        )
    except ProgrammingModeError:
        if current is not None:
            with contextlib.suppress(ManagementConnectionError, asyncio.TimeoutError):
                await switch_programming_mode(
                    xknx, current, False, security=current_security, master=master
                )
        raise
    return serial


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
