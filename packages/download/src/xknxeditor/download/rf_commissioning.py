"""The Domain Address and individual address of a KNX RF device.

A new KNX RF device's Domain Address is unknown, so it only receives system broadcasts (KNX
v02.01.01 - Configuration Procedures 03.05.03 - §2.3.1.1):

- :func:`domain_and_individual_address_write2` is NM_DomainAndIndividualAddress_Write2 (KNX
  v02.01.02 - Management Procedures 03.05.02 - §2.10) for the device in programming mode;
- :func:`domain_address_serial_number_write` is NM_DomainAddressSerialNumber_Write (§2.12) for
  the device with a known KNX Serial Number; its individual address is then written by serial
  number as on any other medium.

xknx 4.1.1 sends no system broadcasts: :func:`send_system_broadcast` sends one as XKNX/xknx#2039
does (``TDataSystemBroadcast``, the cEMI "System Broadcast" flag 0). Replace it with xknx's once
the editor requires an xknx release with that PR. The answers are system broadcasts too; xknx
4.1.1 hands them to its broadcast contexts like any broadcast.

The device answers with its new Domain Address, which only an interface with the same Domain
Address receives: the caller gives the interface that one first.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from xknx.cemi import cemi_handler
from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.cemi.flags import CEMIFlags, CEMIPriority
from xknx.exceptions import ConfirmationError, ManagementConnectionError
from xknx.telegram import GroupAddress, IndividualAddress, apci
from xknx.telegram.tpci import TDataBroadcast

from .errors import DownloadError, ProgrammingModeError, SerialNumberNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable

    from xknx import XKNX
    from xknx.telegram.apci import APCI

    from .data_secure import DeviceSecurity

logger = logging.getLogger(__name__)

# Seconds each A_IndividualAddress_Read collects answers while waiting for programming mode.
_PROGRAMMING_MODE_POLL_S = 1.0
# Wait after writing the addresses before verifying them, so the device has adopted them.
_ADDRESS_ADOPTION_DELAY_S = 1.0
# NM_DomainAddressSerialNumber_Write: the wait before the verification, and how often it is tried.
_SERIAL_NUMBER_VERIFY_DELAY_S = 1.0
_SERIAL_NUMBER_ATTEMPTS = 2
# Device Object PID_SERIAL_NUMBER, read after the addresses are written to record the device.
_PID_SERIAL_NUMBER = 11


async def send_system_broadcast(xknx: XKNX, payload: APCI) -> None:
    """Send ``payload`` as system broadcast and wait for the interface's L_Data.con, as xknx's
    ``CEMIHandler.send_telegram`` does for any other telegram. Raises ``ConfirmationError`` when
    the interface does not confirm it."""
    cemi = CEMIFrame(
        code=CEMIMessageCode.L_DATA_REQ,
        data=CEMILData(
            flags=CEMIFlags(priority=CEMIPriority.SYSTEM, system_broadcast=True),
            src_addr=xknx.current_address,
            dst_addr=GroupAddress(0),
            tpci=TDataBroadcast(),
            payload=payload,
        ),
    )
    handler = xknx.cemi_handler
    # The handler's own lock and confirmation event, so this frame takes its turn with the others.
    async with handler._send_lock:  # pyright: ignore[reportPrivateUsage]
        confirmed = handler._l_data_confirmation_event  # pyright: ignore[reportPrivateUsage]
        confirmed.clear()
        await xknx.knxip_interface.send_cemi(cemi)
        try:
            async with asyncio.timeout(cemi_handler.REQUEST_TO_CONFIRMATION_TIMEOUT):
                await confirmed.wait()
        except TimeoutError:
            raise ConfirmationError(
                f"L_DATA_CON Data Link Layer confirmation timed out for {cemi}"
            ) from None


async def _devices_in_programming_mode(xknx: XKNX) -> list[IndividualAddress]:
    """The individual addresses of the devices in programming mode (a system broadcast
    A_IndividualAddress_Read), collected for one poll interval."""
    async with xknx.management.broadcast.context() as context:
        await send_system_broadcast(xknx, apci.IndividualAddressRead())
        return [
            telegram.source_address
            async for telegram in context.receive(
                apci.IndividualAddressResponse, timeout=_PROGRAMMING_MODE_POLL_S
            )
        ]


async def wait_for_rf_programming_mode(
    xknx: XKNX, waiting: Callable[[], None] | None
) -> IndividualAddress:
    """Poll until exactly one device is in programming mode, also one of another or no Domain
    Address; without ``waiting``, poll once. ``waiting`` is called once while none is."""
    notified = False
    while True:
        devices = await _devices_in_programming_mode(xknx)
        if len(devices) > 1:
            raise ProgrammingModeError("More than one device is in programming mode.")
        if devices:
            return devices[0]
        if waiting is None:
            raise ProgrammingModeError("No device is in programming mode.")
        if not notified:
            waiting()
            notified = True


async def domain_and_individual_address_write2(
    xknx: XKNX,
    domain_address: bytes,
    individual_address: IndividualAddress,
    *,
    waiting: Callable[[], None] | None = None,
    check: Callable[[IndividualAddress], None] | None = None,
    security: DeviceSecurity | None = None,
    restart: bool = True,
) -> tuple[IndividualAddress, bytes | None]:
    """Give the device in programming mode ``domain_address`` and ``individual_address``.

    Waits until exactly one device is in programming mode (``waiting`` is called once while none
    is); ``check`` may refuse it by raising, before anything is written. The Domain Address is
    written, then the individual address where the device has another one, both as system
    broadcast. The device is verified connectionless at its new address (its serial number read
    to record it) and, with ``restart``, restarted, which ends its programming mode.

    Returns the individual address the device had and its serial number (``None`` when it could
    not be read). Raises ``ManagementConnectionError`` when no device answers at the new address.
    """
    from .programmer import DeviceProgrammer
    from .session import apdu_overhead, management_session

    current = await wait_for_rf_programming_mode(xknx, waiting)
    if check is not None:
        check(current)
    await send_system_broadcast(
        xknx, apci.DomainAddressWrite(domain_address=domain_address)
    )
    logger.debug("Wrote domain address %s to %s.", domain_address.hex(), current)
    if current != individual_address:
        await send_system_broadcast(
            xknx, apci.IndividualAddressWrite(address=individual_address)
        )
        logger.debug("Wrote address %s to %s.", individual_address, current)
    await asyncio.sleep(_ADDRESS_ADOPTION_DELAY_S)

    manager = management_session(
        xknx, individual_address, security, connectionless=True
    )
    try:
        connection = await manager.open()
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        await programmer.read_device_descriptor()
        serial: bytes | None = None
        with contextlib.suppress(ManagementConnectionError, DownloadError):
            serial = await programmer.read_property(0, _PID_SERIAL_NUMBER) or None
        if restart:
            await programmer.restart()
        return current, serial
    except (ManagementConnectionError, DownloadError) as exc:
        raise ManagementConnectionError(
            f"No device answered at {individual_address} after the address write."
        ) from exc
    finally:
        await manager.close()


async def domain_address_serial_number_write(
    xknx: XKNX, serial_number: bytes, domain_address: bytes
) -> None:
    """Give the device with ``serial_number`` ``domain_address``, then verify it answers by
    serial number (in that domain). Raises ``SerialNumberNotFoundError`` when it does not."""
    from xknx.management.procedures.network.nm_individual_address_serial_number_read import (
        nm_individual_address_serial_number_read,
    )

    for _ in range(_SERIAL_NUMBER_ATTEMPTS):
        await send_system_broadcast(
            xknx,
            apci.DomainAddressSerialNumberWrite(
                serial=serial_number, domain_address=domain_address
            ),
        )
        await asyncio.sleep(_SERIAL_NUMBER_VERIFY_DELAY_S)
        if (
            await nm_individual_address_serial_number_read(xknx, serial_number)
            is not None
        ):
            logger.debug(
                "Wrote domain address %s to the device with serial number %s.",
                domain_address.hex(),
                serial_number.hex(),
            )
            return
    raise SerialNumberNotFoundError(
        f"The device with the serial number {serial_number.hex().upper()} "
        "could not be found."
    )
