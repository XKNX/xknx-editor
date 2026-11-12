"""KNX USB interfaces over MCP: found by the scan, connected through, shown in the status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.plugin import ConnectionState
from editor_gui.plugins.connection.usb import UsbTarget
from editor_gui.plugins.connection.xknx_usb import USBDeviceInfo, USBInterfaceInfo
from editor_gui.plugins.connection.xknx_usb.const import SupportedEMIType
from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data

_DEVICE = USBDeviceInfo(
    vendor_id=0x16DE,
    product_id=0x008E,
    path=b"usb-1",
    serial_number="00C501053FB2",
    manufacturer="KNX Manufacturer",
    product="KNX-USB Interface",
)


class _UsbConnection:
    """A connection plugin with one KNX USB interface plugged in."""

    def __init__(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        self.connected = False
        self.controller_ip = "192.168.1.1"
        self.multicast_group = "224.0.23.12"
        self.is_routing = False
        self.connection_type = "tunneling"
        self.scanning = False
        self.gateways: tuple[Any, ...] = ()
        self.gateway_info = None
        self.usb_devices = [_DEVICE]
        self.usb_target: UsbTarget | None = None
        self.usb_info: USBInterfaceInfo | None = None
        self.connect_requests: list[str] = []

    def scan(self) -> None:
        pass

    def connect_to_usb_by_serial(self, serial_number: str) -> bool:
        self.connect_requests.append(serial_number)
        if serial_number not in ("", _DEVICE.serial_number):
            return False
        self.usb_target = UsbTarget.of(_DEVICE)
        self.connection_type = "usb"
        self.state = ConnectionState.CONNECTED
        self.connected = True
        self.usb_info = USBInterfaceInfo(
            device=_DEVICE,
            individual_address=IndividualAddress("1.0.2"),
            max_apdu_length=55,
            supported_emi_types=SupportedEMIType.CEMI,
            bus_connected=True,
        )
        return True


def _server(tmp_path: Path, conn: Any) -> Any:
    ctx = _build_ctx(tmp_path)
    ctx.connection_plugin = conn
    return build_server(ctx)


async def test_the_scan_lists_usb_interfaces(tmp_path: Path) -> None:
    async with Client(_server(tmp_path, _UsbConnection())) as client:
        result = await _data(client, "connection_scan", timeout=0.1)
    assert result["usb_interfaces"] == [
        {
            "name": "KNX-USB Interface",
            "serial_number": "00C501053FB2",
            "vendor_id": 0x16DE,
            "product_id": 0x008E,
            "manufacturer": "KNX Manufacturer",
        }
    ]


async def test_connecting_through_a_usb_interface(tmp_path: Path) -> None:
    conn = _UsbConnection()
    async with Client(_server(tmp_path, conn)) as client:
        result = await _data(
            client, "connection_connect_usb", serial_number=" 00C501053FB2 ", timeout=1
        )
        status = await _data(client, "connection_status")
    assert result["connected"]
    assert conn.connect_requests == ["00C501053FB2"]
    assert status["connection_type"] == "usb"
    assert status["usb_interface"] == {
        "name": "KNX-USB Interface",
        "serial_number": "00C501053FB2",
        "vendor_id": 0x16DE,
        "product_id": 0x008E,
        "individual_address": "1.0.2",
        "max_apdu_length": 55,
        "bus_connected": True,
    }


async def test_an_unknown_address_and_no_bus(tmp_path: Path) -> None:
    conn = _UsbConnection()
    conn.connect_to_usb_by_serial("")
    conn.usb_info = USBInterfaceInfo(
        device=_DEVICE,
        individual_address=None,
        max_apdu_length=15,
        supported_emi_types=SupportedEMIType.CEMI,
        bus_connected=False,
    )
    async with Client(_server(tmp_path, conn)) as client:
        status = await _data(client, "connection_status")
    usb = status["usb_interface"]
    assert usb["individual_address"] is None
    assert usb["bus_connected"] is False


async def test_an_unknown_usb_interface_is_refused(tmp_path: Path) -> None:
    conn = _UsbConnection()
    async with Client(_server(tmp_path, conn)) as client:
        with pytest.raises(Exception, match="no scanned KNX USB interface with serial"):
            await client.call_tool("connection_connect_usb", {"serial_number": "nope"})
        conn.usb_devices = []
        conn.connect_to_usb_by_serial = lambda serial: False  # type: ignore[method-assign]
        with pytest.raises(Exception, match="no scanned KNX USB interface; run"):
            await client.call_tool("connection_connect_usb", {})
        # the status names a stored interface also while not connected
        conn.usb_target = UsbTarget.of(_DEVICE)
        status = await _data(client, "connection_status")
    assert status["usb_interface"] == {
        "name": "KNX-USB Interface",
        "serial_number": "00C501053FB2",
        "vendor_id": 0x16DE,
        "product_id": 0x008E,
    }
