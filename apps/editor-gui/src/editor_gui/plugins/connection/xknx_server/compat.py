"""
What ``xknx.io.server`` takes from other parts of xknx that xknx 4.1.1 lacks.

The server comes with the xknx PRs #2025 and #2029, which also add to ``xknx.cemi.CEMIHandler``:
``send_cemi_frame()`` (send a raw cEMI frame under the send lock of ``send_telegram()`` and
return its L_Data.con), and ``register_cemi_received_cb()`` / ``unregister_cemi_received_cb()``
(a raw receive callback run before a frame is handled; returning True takes the frame). Here
``CEMIHandlerWithHooks`` adds them to xknx 4.1.1's ``CEMIHandler``, as they are there. The
editor's ``DiagnosticCEMIHandler`` derives from it; ``hooked_cemi_handler()`` puts one in place of
a plain ``CEMIHandler``. ``usb_interface_info()`` stands for ``KNXIPInterface.usb_interface_info()``.
Drop this module for them once the editor requires an xknx release with those PRs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from xknx.cemi.cemi_frame import CEMIFrame
from xknx.cemi.cemi_handler import REQUEST_TO_CONFIRMATION_TIMEOUT, CEMIHandler
from xknx.cemi.const import CEMIMessageCode
from xknx.exceptions import ConfirmationError

if TYPE_CHECKING:
    from xknx import XKNX

    from editor_gui.plugins.connection.xknx_usb import USBInterfaceInfo

logger = logging.getLogger("xknx.cemi")


class CEMIHandlerWithHooks(CEMIHandler):
    """xknx 4.1.1's ``CEMIHandler`` with the raw send and receive of the xknx PRs."""

    __slots__ = ("_cemi_received_cbs", "_raw_confirmation")

    def __init__(self, xknx: XKNX) -> None:
        """Initialize CEMIHandlerWithHooks class."""
        super().__init__(xknx)
        # the L_Data.con awaited by `send_cemi_frame()`
        self._raw_confirmation: asyncio.Future[bytes] | None = None
        self._cemi_received_cbs: list[Callable[[bytes], bool | None]] = []

    def register_cemi_received_cb(
        self, cemi_received_cb: Callable[[bytes], bool | None]
    ) -> Callable[[], None]:
        """
        Register a callback for the raw cEMI frames received from the interface.

        It is called with every frame before the frame is handled - also with frames
        xknx can't parse - except the L_Data.con answering a `send_cemi_frame()`,
        which that call returns instead. A callback returning True takes the frame:
        it is not handled further. Returns the unregister function.
        """
        self._cemi_received_cbs.append(cemi_received_cb)
        return partial(self.unregister_cemi_received_cb, cemi_received_cb)

    def unregister_cemi_received_cb(
        self, cemi_received_cb: Callable[[bytes], bool | None]
    ) -> None:
        """Unregister a callback for the raw cEMI frames received from the interface."""
        if cemi_received_cb in self._cemi_received_cbs:
            self._cemi_received_cbs.remove(cemi_received_cb)

    async def send_cemi_frame(self, cemi: CEMIFrame) -> bytes:
        """
        Send a cEMI L_Data.req frame as it is and return the raw L_Data.con for it.

        Unlike `send_telegram()` neither the source address nor Data Secure is
        applied - for frames of another client, e.g. a tunnel of a KNXnet/IP server.
        Sends are serialized with `send_telegram()`, as the L_Data.con is matched
        to its request by order only. Raise CommunicationError or ConversionError
        when the interface can't send the frame, ConfirmationError when no
        L_Data.con arrives in time.
        """
        # the lock of xknx 4.1.1's CEMIHandler, which `send_telegram()` holds as well
        send_lock: asyncio.Lock = self._send_lock  # pyright: ignore[reportPrivateUsage]
        async with send_lock:
            confirmation: asyncio.Future[bytes] = (
                asyncio.get_running_loop().create_future()
            )
            self._raw_confirmation = confirmation
            try:
                await self.xknx.knxip_interface.send_cemi(cemi)
                async with asyncio.timeout(REQUEST_TO_CONFIRMATION_TIMEOUT):
                    return await confirmation
            except TimeoutError:
                raise ConfirmationError(
                    f"L_DATA_CON Data Link Layer confirmation timed out for {cemi}"
                ) from None
            finally:
                self._raw_confirmation = None

    def handle_raw_cemi(self, raw_cemi: bytes) -> None:
        """Parse and handle incoming raw CEMI Frames."""
        if self._raw_confirmation is not None and raw_cemi[:1] == bytes(
            (CEMIMessageCode.L_DATA_CON.value,)
        ):
            if not self._raw_confirmation.done():
                self._raw_confirmation.set_result(raw_cemi)
            logger.debug("Incoming CEMI confirmation: %s", raw_cemi.hex())
            return
        for cemi_received_cb in self._cemi_received_cbs:
            try:
                if cemi_received_cb(raw_cemi):
                    return
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Unexpected error in cEMI received callback")
        super().handle_raw_cemi(raw_cemi)


def hooked_cemi_handler(xknx: XKNX) -> CEMIHandlerWithHooks:
    """
    Return the cEMI handler of `xknx`, with the raw send and receive.

    A plain ``CEMIHandler`` of xknx 4.1.1 is replaced by a ``CEMIHandlerWithHooks``
    with its Data Secure; any other handler has to have them already. Raise TypeError
    when it hasn't.
    """
    handler: Any = xknx.cemi_handler
    if isinstance(handler, CEMIHandlerWithHooks):
        return handler
    if type(handler) is not CEMIHandler:
        raise TypeError(
            f"The cEMI handler {type(handler).__name__} has no raw send and receive"
        )
    hooked = CEMIHandlerWithHooks(xknx)
    hooked.data_secure = handler.data_secure
    xknx.cemi_handler = hooked
    return hooked


def usb_interface_info(xknx: XKNX) -> USBInterfaceInfo | None:
    """Return what a connected KNX USB interface reported about itself (the editor's
    connection's ``usb_info()``), None for any other connection."""
    usb_info: Callable[[], USBInterfaceInfo | None] | None = getattr(
        xknx.knxip_interface, "usb_info", None
    )
    return usb_info() if usb_info is not None else None
