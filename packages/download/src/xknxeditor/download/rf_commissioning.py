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

from .errors import (
    AddressInUseError,
    DownloadError,
    ProgrammingModeError,
    SerialNumberNotFoundError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from xknx import XKNX
    from xknx.telegram.apci import APCI

    from .data_secure import CemiSecurer, DeviceSecurity, SecureManagement

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


def _system_broadcast_frame(
    xknx: XKNX, payload: APCI, system_broadcast: bool = True
) -> CEMILData:
    """The L_Data of ``payload`` as system broadcast from this connection (without
    ``system_broadcast`` as broadcast, for a coupler that routes it on)."""
    return CEMILData(
        flags=CEMIFlags(
            priority=CEMIPriority.SYSTEM, system_broadcast=system_broadcast
        ),
        src_addr=xknx.current_address,
        dst_addr=GroupAddress(0),
        tpci=TDataBroadcast(),
        payload=payload,
    )


async def send_system_broadcast(xknx: XKNX, payload: APCI) -> None:
    """Send ``payload`` as system broadcast and wait for the interface's L_Data.con, as xknx's
    ``CEMIHandler.send_telegram`` does for any other telegram. Raises ``ConfirmationError`` when
    the interface does not confirm it."""
    await _send_frame(xknx, _system_broadcast_frame(xknx, payload))


async def _send_frame(xknx: XKNX, data: CEMILData) -> None:
    """Send the L_Data ``data`` as it is and wait for the interface's L_Data.con."""
    cemi = CEMIFrame(code=CEMIMessageCode.L_DATA_REQ, data=data)
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


async def _send(xknx: XKNX, payload: APCI, system_broadcast: bool) -> None:
    """Send ``payload`` as system broadcast, or as broadcast for a coupler of the tool's own RF
    domain that routes it on (03.05.03 §2.3.1.4)."""
    if system_broadcast:
        await send_system_broadcast(xknx, payload)
    else:
        await xknx.management.broadcast.send(payload)


async def _devices_in_programming_mode(
    xknx: XKNX, system_broadcast: bool = True
) -> list[IndividualAddress]:
    """The individual addresses of the devices in programming mode (a system broadcast
    A_IndividualAddress_Read), collected for one poll interval."""
    async with xknx.management.broadcast.context() as context:
        await _send(xknx, apci.IndividualAddressRead(), system_broadcast)
        return [
            telegram.source_address
            async for telegram in context.receive(
                apci.IndividualAddressResponse, timeout=_PROGRAMMING_MODE_POLL_S
            )
        ]


async def wait_for_rf_programming_mode(
    xknx: XKNX, waiting: Callable[[], None] | None, *, system_broadcast: bool = True
) -> IndividualAddress:
    """Poll until exactly one device is in programming mode, also one of another or no Domain
    Address; without ``waiting``, poll once. ``waiting`` is called once while none is."""
    notified = False
    while True:
        devices = await _devices_in_programming_mode(xknx, system_broadcast)
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
    system_broadcast: bool = True,
) -> tuple[IndividualAddress, bytes | None]:
    """Give the device in programming mode ``domain_address`` and ``individual_address``.

    Waits until exactly one device is in programming mode (``waiting`` is called once while none
    is); ``check`` may refuse it by raising, before anything is written. The Domain Address is
    written, then the individual address where the device has another one, both as system
    broadcast (without ``system_broadcast`` as broadcast, for a coupler of the tool's own RF
    domain to route on). The device is verified connectionless at its new address (its serial
    number read to record it) and, with ``restart``, restarted, which ends its programming mode.

    Returns the individual address the device had and its serial number (``None`` when it could
    not be read). Raises ``ManagementConnectionError`` when no device answers at the new address.
    """
    from .programmer import DeviceProgrammer
    from .session import apdu_overhead, management_session

    current = await wait_for_rf_programming_mode(
        xknx, waiting, system_broadcast=system_broadcast
    )
    if check is not None:
        check(current)
    await _send(
        xknx, apci.DomainAddressWrite(domain_address=domain_address), system_broadcast
    )
    logger.debug("Wrote domain address %s to %s.", domain_address.hex(), current)
    if current != individual_address:
        await _send(
            xknx,
            apci.IndividualAddressWrite(address=individual_address),
            system_broadcast,
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
    xknx: XKNX,
    serial_number: bytes,
    domain_address: bytes,
    *,
    system_broadcast: bool = True,
) -> None:
    """Give the device with ``serial_number`` ``domain_address``, then verify it answers by
    serial number (in that domain). Raises ``SerialNumberNotFoundError`` when it does not.
    ``system_broadcast`` as for :func:`domain_and_individual_address_write2`."""
    from xknx.management.procedures.network.nm_individual_address_serial_number_read import (
        nm_individual_address_serial_number_read,
    )

    for _ in range(_SERIAL_NUMBER_ATTEMPTS):
        await _send(
            xknx,
            apci.DomainAddressSerialNumberWrite(
                serial=serial_number, domain_address=domain_address
            ),
            system_broadcast,
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


# Seconds to wait for the answer to a system broadcast S-A_Sync request.
_SYNC_TIMEOUT_S = 3.0
_SYNC_POLL_S = 0.05


class _BroadcastSecurer:
    """The ``xknx.cemi_handler.data_secure`` hook while a device is reached by secured system
    broadcasts: the device's secured answers to them (to the broadcast address) are taken by the
    session, everything else goes to the securer that was installed before."""

    def __init__(
        self, management: SecureManagement, previous: CemiSecurer | None
    ) -> None:
        self._management = management
        self._previous = previous

    def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
        if self._previous is None:
            return cemi_data
        return self._previous.outgoing_cemi(cemi_data)

    def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
        from xknx.exceptions import DataSecureError
        from xknx.secure.data_secure_asdu import SecurityALService

        from .data_secure import SecureProgrammingError

        payload = cemi_data.payload
        if (
            cemi_data.dst_addr == GroupAddress(0)
            and isinstance(payload, apci.SecureAPDU)
            and payload.scf.tool_access
        ):
            try:
                if payload.scf.service is SecurityALService.S_A_SYNC_RES:
                    self._management.decode_sync_response_cemi(cemi_data)
                    return cemi_data
                return self._management.unwrap_cemi(cemi_data)
            except SecureProgrammingError as exc:
                raise DataSecureError(str(exc)) from exc
        if self._previous is None:
            return cemi_data
        return self._previous.received_cemi(cemi_data)


async def secure_addresses_by_serial_number(
    xknx: XKNX,
    serial_number: bytes,
    individual_address: IndividualAddress,
    security: DeviceSecurity,
    *,
    domain_address: bytes | None = None,
    address_in_use: Callable[[], Awaitable[bool]] | None = None,
    system_broadcast: bool = True,
) -> None:
    """Give the device with ``serial_number`` ``domain_address`` and ``individual_address``
    with KNX Data Secure (KNX v02.01.01 - Configuration Procedures 03.05.03 - §1.5.4.3): the
    Sequence Numbers are synchronised by system broadcast (DM_SecureSync_SBC, KNX v02.01.02 -
    Management Procedures 03.05.02 - §4.1) with the Tool Key, else its alternatives such as the
    FDSK, then A_DomainAddressSerialNumber_Write and A_IndividualAddressSerialNumber_Write go
    as S-A_Data with the SBC flag set (NM_DomainAddressSerialNumber_Secure_Write, §2.13). Each
    is verified by serial number; an individual address the device has already is not written
    again, and with ``address_in_use`` one another device uses is refused
    (``AddressInUseError``). Raises ``SecureProgrammingError`` when the device answers the
    synchronisation with none of the keys, ``SerialNumberNotFoundError`` when it is not found
    with its new domain address, ``ManagementConnectionError`` when it does not take the
    individual address. Without ``system_broadcast`` the services go as broadcasts, for a
    coupler that routes them on."""
    from .data_secure import SecureManagement, remove_securer

    management = SecureManagement(
        device=security, tool_address=xknx.current_address, serial_number=serial_number
    )
    handler = xknx.cemi_handler
    securer = _BroadcastSecurer(management, handler.data_secure)
    handler.data_secure = securer  # type: ignore[assignment]
    try:
        await _secure_sync(xknx, management, security, serial_number, system_broadcast)
        if domain_address is not None:
            await _send_frame(
                xknx,
                management.wrap_cemi(
                    _system_broadcast_frame(
                        xknx,
                        apci.DomainAddressSerialNumberWrite(
                            serial=serial_number, domain_address=domain_address
                        ),
                        system_broadcast,
                    )
                ),
            )
            await asyncio.sleep(_SERIAL_NUMBER_VERIFY_DELAY_S)
        current = await _serial_number_read(xknx, serial_number)
        if current is None:
            raise SerialNumberNotFoundError(
                f"The device with the serial number {serial_number.hex().upper()} "
                "could not be found."
            )
        if current == individual_address:
            return
        if address_in_use is not None and await address_in_use():
            raise AddressInUseError(
                f"The individual address {individual_address} is already in use by "
                "another device."
            )
        await _send_frame(
            xknx,
            management.wrap_cemi(
                _system_broadcast_frame(
                    xknx,
                    apci.IndividualAddressSerialWrite(
                        address=individual_address, serial=serial_number
                    ),
                    system_broadcast,
                )
            ),
        )
        await asyncio.sleep(_ADDRESS_ADOPTION_DELAY_S)
        found = await _serial_number_read(xknx, serial_number)
        if found != individual_address:
            raise ManagementConnectionError(
                f"The device with the serial number {serial_number.hex().upper()} answers "
                f"at {found} instead of {individual_address}."
            )
    finally:
        remove_securer(handler, securer)


async def _secure_sync(
    xknx: XKNX,
    management: SecureManagement,
    security: DeviceSecurity,
    serial_number: bytes,
    system_broadcast: bool = True,
) -> None:
    """Synchronise the Sequence Numbers by system broadcast (or broadcast) with the first key
    the device answers to."""
    from .data_secure import SecureProgrammingError

    loop = asyncio.get_running_loop()
    for key in security.keys:
        management.use_key(key)
        management.arm_sync()
        await _send_frame(
            xknx,
            management.build_sync_request_cemi(
                _system_broadcast_frame(
                    xknx, apci.IndividualAddressRead(), system_broadcast
                )
            ),
        )
        deadline = loop.time() + _SYNC_TIMEOUT_S
        while not management.synchronized and loop.time() < deadline:
            await asyncio.sleep(_SYNC_POLL_S)
        if management.synchronized:
            return
    raise SecureProgrammingError(
        f"The device with the serial number {serial_number.hex().upper()} did not answer "
        "the secure synchronisation with its Tool Key or FDSK."
    )


async def _serial_number_read(
    xknx: XKNX, serial_number: bytes
) -> IndividualAddress | None:
    """The individual address the device with ``serial_number`` answers with (None: none)."""
    from xknx.management.procedures.network.nm_individual_address_serial_number_read import (
        nm_individual_address_serial_number_read,
    )

    return await nm_individual_address_serial_number_read(xknx, serial_number)
