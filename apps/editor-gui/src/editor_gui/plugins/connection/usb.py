"""Connections through KNX USB interfaces."""

from __future__ import annotations

import asyncio
import sys
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from xknx import XKNX
from xknx.core.connection_state import XknxConnectionState
from xknx.exceptions import CommunicationError
from xknx.io.connection import ConnectionConfig
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.interface import ObservableKNXIPInterfaceThreaded
from editor_gui.plugins.connection.xknx_usb import (
    USBDeviceInfo,
    USBInterfaceInfo,
    USBTunnel,
    find_usb_interfaces,
)

# Seconds between the attempts to open an unplugged interface again.
_RECONNECT_SECONDS = 3


def usb_device_name(device: USBDeviceInfo) -> str:
    """The name an interface is shown with: its USB product name."""
    return device.product.strip() or "KNX USB Interface"


def cancel_hidapi_atexit() -> None:
    """Cancel hidapi's exit-time teardown on macOS, if hidapi was loaded.

    hidapi schedules ``hid_exit`` through a ``weakref.finalize`` on its module, run at interpreter
    shutdown. On macOS it binds the IOHIDManager to the run loop of the thread that imported it
    (here a background USB scan thread); by interpreter shutdown that run loop is gone, so
    ``IOHIDManagerClose`` unschedules from a freed run loop and the process dies with a trace trap
    on quit. Detaching the finalizer leaves the manager for the exiting process to reclaim instead.
    Called right after a scan imports hidapi and again on app shutdown, so it runs whenever hidapi
    is loaded. The crash is specific to macOS (IOKit/CoreFoundation); Linux (hidraw / libusb) and
    Windows keep hidapi's normal teardown.
    """
    if sys.platform != "darwin":
        return
    hid = sys.modules.get("hid")
    if hid is None:
        return
    # weakref.finalize._registry maps each live finalizer to its info; untyped, so go through
    # getattr to keep it out of the strict type check.
    registry: Any = getattr(weakref.finalize, "_registry", {})
    for finalizer in list(registry):
        peeked = finalizer.peek()
        if peeked is not None and peeked[0] is hid:
            finalizer.detach()


@dataclass(frozen=True, slots=True)
class UsbTarget:
    """The KNX USB interface connected to (or to connect to), as it is stored."""

    vendor_id: int
    product_id: int
    serial_number: str
    name: str

    @classmethod
    def of(cls, device: USBDeviceInfo) -> UsbTarget:
        return cls(
            vendor_id=device.vendor_id,
            product_id=device.product_id,
            serial_number=device.serial_number,
            name=usb_device_name(device),
        )

    @property
    def label(self) -> str:
        """The interface as the connection target is shown, e.g. ``KNX-USB Interface (USB)``."""
        return f"{self.name} (USB)"

    def matches(self, device: USBDeviceInfo) -> bool:
        """Whether ``device`` is this interface (on any port)."""
        return (
            device.vendor_id == self.vendor_id
            and device.product_id == self.product_id
            and (not self.serial_number or device.serial_number == self.serial_number)
        )

    def to_settings(self) -> dict[str, object]:
        return {
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "serial_number": self.serial_number,
            "name": self.name,
        }

    @classmethod
    def from_settings(cls, data: object) -> UsbTarget | None:
        """The stored interface, or None when there is none (or it is unreadable)."""
        if not isinstance(data, dict):
            return None
        fields = cast(dict[str, object], data)
        vendor_id = fields.get("vendor_id")
        product_id = fields.get("product_id")
        if not isinstance(vendor_id, int) or not isinstance(product_id, int):
            return None
        serial_number = fields.get("serial_number")
        name = fields.get("name")
        return cls(
            vendor_id=vendor_id,
            product_id=product_id,
            serial_number=serial_number if isinstance(serial_number, str) else "",
            name=name if isinstance(name, str) and name else "KNX USB Interface",
        )


async def search_usb_interfaces(
    on_error: Callable[[BaseException], None] | None = None,
) -> list[USBDeviceInfo]:
    """The KNX USB interfaces plugged into this computer; none when they cannot be listed."""
    try:
        devices = await asyncio.get_running_loop().run_in_executor(
            None, find_usb_interfaces
        )
    except (CommunicationError, OSError) as err:
        if on_error is not None:
            on_error(err)
        return []
    # The scan imported hidapi (in the executor thread); cancel its exit-time teardown right away,
    # so quitting is safe however the scan and the shutdown interleave (see cancel_hidapi_atexit).
    cancel_hidapi_atexit()
    return devices


class ObservableUSBInterfaceThreaded(ObservableKNXIPInterfaceThreaded):
    """A connection through a KNX USB interface, observable as the KNXnet/IP ones are."""

    __slots__ = ("_target",)

    def __init__(
        self,
        xknx: XKNX,
        target: UsbTarget,
        raw_cemi_callback: Callable[[bytes], None] | None = None,
        sent_cemi_callback: Callable[[bytes], None] | None = None,
    ) -> None:
        super().__init__(
            xknx,
            ConnectionConfig(threaded=True),
            raw_cemi_callback=raw_cemi_callback,
            sent_cemi_callback=sent_cemi_callback,
        )
        self._target = target

    async def _start(self) -> None:
        """Open the interface (in the connection thread)."""
        self.xknx.cemi_handler.data_secure_init(keyring=None)
        self._interface = USBTunnel(
            self.xknx,
            cemi_received_callback=self.cemi_received,
            vendor_id=self._target.vendor_id,
            product_id=self._target.product_id,
            serial_number=self._target.serial_number or None,
            auto_reconnect=True,
            auto_reconnect_wait=_RECONNECT_SECONDS,
        )
        await self._interface.connect()

    def usb_info(self) -> USBInterfaceInfo | None:
        """What the connected interface reported about itself."""
        if isinstance(self._interface, USBTunnel):
            return self._interface.info
        return None

    async def read_max_apdu_length(self) -> int | None:
        """The maximum APDU length the interface reported."""
        info = self.usb_info()
        return info.max_apdu_length if info is not None else None


class UsbBusmonitorConnection:
    """A KNX USB interface in bus monitor mode, as :class:`BusmonitorConnection` for KNXnet/IP.

    ``on_frame`` receives every raw cEMI frame (L_Busmon.ind) on the event loop's thread;
    ``on_state`` receives ``True``/``False`` when the interface is back or lost after
    :meth:`connect` returned."""

    def __init__(
        self,
        target: UsbTarget,
        *,
        on_frame: Callable[[bytes], None],
        on_state: Callable[[bool], None] | None = None,
    ) -> None:
        self.target = target
        self._on_frame = on_frame
        self._on_state = on_state
        self._xknx = XKNX()
        self._tunnel: USBTunnel | None = None
        self._unregister: Callable[[], None] | None = None

    @property
    def connected(self) -> bool:
        return self._tunnel is not None

    @property
    def individual_address(self) -> IndividualAddress:
        return self._xknx.current_address

    async def connect(self) -> None:
        """Open the interface in bus monitor mode. Raises :class:`CommunicationError`."""
        tunnel = USBTunnel(
            self._xknx,
            cemi_received_callback=self._on_frame,
            vendor_id=self.target.vendor_id,
            product_id=self.target.product_id,
            serial_number=self.target.serial_number or None,
            auto_reconnect=True,
            auto_reconnect_wait=_RECONNECT_SECONDS,
            busmonitor=True,
        )
        await tunnel.connect()
        self._tunnel = tunnel
        if self._on_state is not None:
            self._unregister = (
                self._xknx.connection_manager.register_connection_state_changed_cb(
                    self._state_changed
                )
            )

    def _state_changed(self, state: XknxConnectionState) -> None:
        if self._on_state is None:
            return
        if state is XknxConnectionState.CONNECTED:
            self._on_state(True)
        elif state is XknxConnectionState.DISCONNECTED:
            self._on_state(False)

    async def disconnect(self) -> None:
        if self._unregister is not None:
            self._unregister()
            self._unregister = None
        tunnel, self._tunnel = self._tunnel, None
        if tunnel is not None:
            await tunnel.stop_reconnect()
            await tunnel.disconnect()
