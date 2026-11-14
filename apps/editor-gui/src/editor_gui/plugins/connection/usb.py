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
from xknx.secure.data_secure import DataSecure
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.interface import ObservableKNXIPInterfaceThreaded
from editor_gui.plugins.connection.xknx_usb import (
    USBDeviceInfo,
    USBInterfaceInfo,
    USBTunnel,
    find_usb_interfaces,
)
from editor_gui.plugins.connection.xknx_usb.const import EMIID, SupportedEMIType

# Seconds between the attempts to open an unplugged interface again.
_RECONNECT_SECONDS = 3

# USB vendors whose HID devices the scan offers whatever their product id, as the KNX USB
# interfaces of these vendors (and the manufacturers using their ids) are not all among the
# known ones: Weinzierl Engineering, Insta, Tapko Technologies, ise.
KNX_USB_VENDOR_IDS = (0x0E77, 0x135E, 0x28C2, 0x2A07)

# USB interfaces (vendor id, product id) that forget their individual address when unplugged: the
# Siemens Interface OCI700 USB. The editor keeps the address such an interface had when it was
# closed and sets it again on connect.
VOLATILE_ADDRESS_USB_INTERFACES = frozenset({(0x0681, 0x0014)})


def usb_device_name(device: USBDeviceInfo) -> str:
    """The name an interface is shown with: its USB product name."""
    return device.product.strip() or "KNX USB Interface"


# The EMI types by their names in the KNX specification, in the order the connection prefers them.
_EMI_NAMES = ((EMIID.CEMI, "cEMI"), (EMIID.EMI2, "EMI2"), (EMIID.EMI1, "EMI1"))
_SUPPORTED_EMI = {
    EMIID.CEMI: SupportedEMIType.CEMI,
    EMIID.EMI2: SupportedEMIType.EMI2,
    EMIID.EMI1: SupportedEMIType.EMI1,
}


def emi_type_name(emi: EMIID) -> str:
    """The name of the EMI type a connection uses with an interface: cEMI, EMI2 or EMI1."""
    return dict(_EMI_NAMES)[emi]


def supported_emi_type_names(supported: SupportedEMIType) -> list[str]:
    """The names of the EMI types an interface supports, in the order the connection prefers."""
    return [name for emi, name in _EMI_NAMES if supported & _SUPPORTED_EMI[emi]]


def mask_version_text(mask_version: int | None) -> str:
    """A mask version as four hexadecimal digits (``07B0``); empty when it is unknown."""
    return "" if mask_version is None else f"{mask_version:04X}"


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

    @property
    def keeps_address(self) -> bool:
        """Whether the interface keeps its individual address when it is unplugged (see
        ``VOLATILE_ADDRESS_USB_INTERFACES``)."""
        return (self.vendor_id, self.product_id) not in VOLATILE_ADDRESS_USB_INTERFACES

    @property
    def key(self) -> str:
        """The interface as its kept individual address is stored: vendor and product id and
        serial number, e.g. ``0681:0014:00FD12345678``."""
        return f"{self.vendor_id:04X}:{self.product_id:04X}:{self.serial_number}"

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


def _find_usb_interfaces() -> list[USBDeviceInfo]:
    """The known KNX USB interfaces plugged in, and every HID device of a KNX USB vendor, so an
    interface missing from the known ones is offered too. Blocks while USB is enumerated."""
    devices = find_usb_interfaces()
    paths = {device.path for device in devices}
    for vendor_id in KNX_USB_VENDOR_IDS:
        for device in find_usb_interfaces(vendor_id):
            if device.path not in paths:
                paths.add(device.path)
                devices.append(device)
    return devices


async def search_usb_interfaces(
    on_error: Callable[[BaseException], None] | None = None,
) -> list[USBDeviceInfo]:
    """The KNX USB interfaces plugged into this computer; none when they cannot be listed."""
    try:
        devices = await asyncio.get_running_loop().run_in_executor(
            None, _find_usb_interfaces
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

    __slots__ = ("_data_secure", "_individual_address", "_target")

    def __init__(
        self,
        xknx: XKNX,
        target: UsbTarget,
        raw_cemi_callback: Callable[[bytes], None] | None = None,
        sent_cemi_callback: Callable[[bytes], None] | None = None,
        data_secure: DataSecure | None = None,
        individual_address: IndividualAddress | None = None,
        writes_blocked: Callable[[], bool] | None = None,
    ) -> None:
        """``data_secure`` is in place while the interface is set up, so an interface Data Secure
        can't work with (unknown individual address, EMI1/EMI2 only) is noticed on connect as
        after its reset or a reconnect. With ``individual_address`` the interface's own address is
        set to it on connect when it has another one; the connect fails when that can't be done."""
        super().__init__(
            xknx,
            ConnectionConfig(threaded=True),
            raw_cemi_callback=raw_cemi_callback,
            sent_cemi_callback=sent_cemi_callback,
            writes_blocked=writes_blocked,
        )
        self._target = target
        self._data_secure = data_secure
        self._individual_address = individual_address

    async def _start(self) -> None:
        """Open the interface (in the connection thread)."""
        # Data Secure comes from the project or the editor's keyring, not from an xknx keyring,
        # so the tunnel gets none to check its individual address against.
        self.xknx.cemi_handler.data_secure = self._data_secure
        self._interface = USBTunnel(
            self.xknx,
            cemi_received_callback=self.cemi_received,
            vendor_id=self._target.vendor_id,
            product_id=self._target.product_id,
            serial_number=self._target.serial_number or None,
            auto_reconnect=True,
            auto_reconnect_wait=_RECONNECT_SECONDS,
            individual_address=self._individual_address,
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
            # opened without a bus connection: the link is down until the bus is back
            if self._xknx.connection_manager.state is not XknxConnectionState.CONNECTED:
                self._on_state(False)

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
