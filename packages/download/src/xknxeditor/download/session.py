"""Management sessions to one device: connection-oriented or connectionless, plain or secured."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.management.management import P2PConnection
    from xknx.telegram import Telegram
    from xknx.telegram.address import IndividualAddress
    from xknx.telegram.apci import APCI

    from .data_secure import DeviceSecurity
    from .programmer import BusConnection, ConnectionManager


class XknxBusConnection:
    """A point-to-point connection of ``xknx`` as a :class:`BusConnection`.

    ``xknx`` checks the answer to a request service against the response type the service
    declares; a request here names the response it expects, which also covers services that
    declare none (a memory write answered by its echo, a function property command).
    """

    def __init__(self, connection: P2PConnection) -> None:
        self._connection = connection

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        """Send a payload and, by default, wait for the transport layer ACK."""
        await self._connection.send_data(payload, wait_for_ack)

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        """Send a payload and return the device's response, which must be an ``expected``."""
        telegram = await self._connection.request(payload)
        if expected is not None and not isinstance(telegram.payload, expected):
            raise ManagementConnectionError(
                f"Received unexpected telegram: {telegram.payload}"
            )
        return telegram


async def connect(xknx: XKNX, address: IndividualAddress) -> BusConnection:
    """Open a connection-oriented point-to-point connection to ``address``."""
    return XknxBusConnection(await xknx.management.connect(address))


class XknxConnectionManager:
    """Open/close point-to-point connections to one device via ``xknx``."""

    def __init__(self, xknx: XKNX, address: IndividualAddress) -> None:
        """Initialize for a target individual address."""
        self._xknx = xknx
        self._address = address
        self._connection: BusConnection | None = None

    async def open(self) -> BusConnection:
        """Open a fresh connection to the device."""
        self._connection = await connect(self._xknx, self._address)
        return self._connection

    async def close(self) -> None:
        """Close the current connection, tolerating a peer that already dropped it."""
        if self._connection is None:
            return
        self._connection = None
        with contextlib.suppress(ManagementConnectionError):
            await self._xknx.management.disconnect(self._address)


def management_session(
    xknx: XKNX,
    address: IndividualAddress,
    security: DeviceSecurity | None,
    *,
    connectionless: bool = False,
) -> ConnectionManager:
    """A connection manager for ``address``, secured with the Tool Key when ``security`` is set."""
    from .connectionless import ConnectionlessManager

    transport: ConnectionManager | None = (
        ConnectionlessManager(xknx, address) if connectionless else None
    )
    if security is None:
        return transport or XknxConnectionManager(xknx, address)
    from .data_secure import SecureProgrammingError
    from .secure_session import SecureConnectionManager

    if security.address != address:
        raise SecureProgrammingError(
            f"security material is for {security.address}, not the download "
            f"target {address}"
        )
    return SecureConnectionManager(xknx, address, security, transport=transport)


def apdu_overhead(security: DeviceSecurity | None) -> int:
    """Wire APDU overhead a secure session adds around each plaintext APDU."""
    if security is None:
        return 0
    from .data_secure import SECURE_APDU_OVERHEAD

    return SECURE_APDU_OVERHEAD
