"""KNX USB interfaces - KNX v01.03.04 - Basic and System Components - Couplers 09.03 - §3.

A copy of ``xknx.io.usb`` (branch ``usb-interface`` of the xknx fork, not yet in an xknx
release), adapted to xknx 4.1.1: kept identical to it but for the imports, the connection type
and what xknx 4.1.1 lacks (in ``compat``), so it can be dropped for ``xknx.io.usb`` once the
editor requires an xknx release with it.
"""

from .hid_device import USBDeviceInfo, find_usb_interfaces, is_knx_usb_interface
from .tunnel import USBInterfaceInfo, USBTunnel

__all__ = [
    "USBDeviceInfo",
    "USBInterfaceInfo",
    "USBTunnel",
    "find_usb_interfaces",
    "is_knx_usb_interface",
]
