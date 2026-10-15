"""Open a Tool-Key secured point-to-point session for a download.

Wraps a point-to-point connection so that all its management traffic is KNX
Data Secure protected with the device's Tool Key: it installs
:class:`ToolKeyCemiSecure` on ``xknx.cemi_handler.data_secure`` (the same hook
xknx uses for group Data Secure, where each frame's transport-layer sequence
number is already assigned), opens the connection and runs the S-A_Sync
exchange, then restores the previous hook when the connection closes.

The :class:`DeviceProgrammer` sees a plain :class:`BusConnection`; securing and
unwrapping happen transparently on the CEMI path underneath it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Final

from xknx.exceptions import ManagementConnectionError, ManagementConnectionTimeout
from xknx.telegram.apci import DeviceDescriptorRead, SecureAPDU

from .data_secure import (
    CemiSecurer,
    DeviceSecurity,
    SecureManagement,
    SecureProgrammingError,
    ToolKeyCemiSecure,
)

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.address import IndividualAddress

    from .programmer import BusConnection, ConnectionManager

logger = logging.getLogger(__name__)

# The S-AL user starts a 6 s timeout for the S-A_Sync response, and the device
# will not answer two requests within 1 s of each other (3/3/7 5.3.2).
_SYNC_RETRY_DELAY: Final = 1.0
_SYNC_ATTEMPTS: Final = 3
# The armed hook replaces this outgoing APDU with the S-A_Sync request, so the
# device never sees it - it is only a vehicle to drive one connection-oriented
# request/response over the transport layer.
_SYNC_TRIGGER: Final = DeviceDescriptorRead(descriptor=0)


class SecureConnectionManager:
    """Open/close a Tool-Key secured connection to one device via ``xknx``.

    Implements the same open/close contract as the plain connection manager, so
    a Load Procedure runner can use it unchanged.
    """

    def __init__(
        self,
        xknx: XKNX,
        address: IndividualAddress,
        security: DeviceSecurity,
        *,
        transport: ConnectionManager | None = None,
    ) -> None:
        """Initialize for a target address and its Tool Key security material.

        ``transport`` opens the underlying channel; by default a connection-oriented
        xknx management connection."""
        self._xknx = xknx
        self._transport = transport
        self._address = address
        self._security = security
        self._management = SecureManagement(
            device=security, tool_address=xknx.current_address
        )
        self._connection: BusConnection | None = None
        self._previous_secure: CemiSecurer | None = None
        self._installed = False

    async def open(self) -> BusConnection:
        """Install the securer, open the connection and run S-A_Sync."""
        self._install()
        try:
            if self._transport is not None:
                self._connection = await self._transport.open()
            else:
                self._connection = await self._xknx.management.connect(self._address)
            await self._synchronize(self._connection)
        except BaseException:
            self._restore()
            raise
        return self._connection

    async def close(self) -> None:
        """Close the connection and restore the previous CEMI securer."""
        try:
            if self._connection is not None:
                self._connection = None
                if self._transport is not None:
                    await self._transport.close()
                else:
                    with contextlib.suppress(ManagementConnectionError):
                        await self._xknx.management.disconnect(self._address)
        finally:
            self._restore()

    def _install(self) -> None:
        self._previous_secure = self._xknx.cemi_handler.data_secure
        hook = ToolKeyCemiSecure(
            self._management, self._address, previous=self._previous_secure
        )
        self._xknx.cemi_handler.data_secure = hook  # type: ignore[assignment]
        self._installed = True

    def _restore(self) -> None:
        if self._installed:
            self._xknx.cemi_handler.data_secure = self._previous_secure  # type: ignore[assignment]
            self._installed = False

    @property
    def active_key(self) -> bytes:
        """The key the device answered with (the Tool Key or one of its alternatives)."""
        return self._management.active_key

    def change_tool_key(self, key: bytes) -> None:
        """The next request writes the Tool Key ``key``; see :meth:`SecureManagement.change_tool_key`."""
        self._management.change_tool_key(key)

    async def _synchronize(self, connection: BusConnection) -> None:
        """Run the S-A_Sync exchange, retrying within the device's answer rules. The key that
        worked last is tried first, then the Tool Key and its alternatives (3/5/2 4.1, Note 18)."""
        keys = [self._management.active_key]
        keys += [key for key in self._security.keys if key not in keys]
        for number, key in enumerate(keys):
            if number:
                logger.info("S-A_Sync: trying the next key for %s", self._address)
            self._management.use_key(key)
            if await self._synchronize_with(connection):
                return
        raise SecureProgrammingError(
            "no S-A_Sync response received - the device answered none of the known keys "
            "(Tool Key, FDSK); check the device address and the device certificate"
        )

    async def _synchronize_with(self, connection: BusConnection) -> bool:
        for attempt in range(_SYNC_ATTEMPTS):
            self._management.arm_sync()
            try:
                await connection.request(_SYNC_TRIGGER, SecureAPDU)
            except ManagementConnectionTimeout:
                logger.warning(
                    "S-A_Sync response not received (attempt %d/%d)",
                    attempt + 1,
                    _SYNC_ATTEMPTS,
                )
                await asyncio.sleep(_SYNC_RETRY_DELAY)
                continue
            if self._management.synchronized:
                return True
            await asyncio.sleep(_SYNC_RETRY_DELAY)
        return False
