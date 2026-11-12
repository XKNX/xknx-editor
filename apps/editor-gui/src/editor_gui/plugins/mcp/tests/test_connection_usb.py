"""KNX USB interfaces over MCP: found by the scan, connected through, shown in the status, their
individual address set and checked."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.plugin import ConnectionState
from editor_gui.plugins.connection.usb import UsbTarget
from editor_gui.plugins.connection.xknx_usb import USBDeviceInfo, USBInterfaceInfo
from editor_gui.plugins.connection.xknx_usb.const import EMIID, SupportedEMIType
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
        "keeps_address": True,
        "individual_address": "1.0.2",
        "max_apdu_length": 55,
        "bus_connected": True,
        "emi_type": "cEMI",
        "supported_emi_types": ["cEMI"],
        "mask_version": None,
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


async def test_an_emi1_interface(tmp_path: Path) -> None:
    conn = _UsbConnection()
    conn.connect_to_usb_by_serial("")
    conn.usb_info = USBInterfaceInfo(
        device=_DEVICE,
        individual_address=IndividualAddress("1.0.200"),
        max_apdu_length=15,
        supported_emi_types=SupportedEMIType.EMI1,
        bus_connected=True,
        mask_version=0x0012,
        emi_type=EMIID.EMI1,
    )
    async with Client(_server(tmp_path, conn)) as client:
        status = await _data(client, "connection_status")
    usb = status["usb_interface"]
    assert usb["emi_type"] == "EMI1"
    assert usb["supported_emi_types"] == ["EMI1"]
    assert usb["mask_version"] == "0012"


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
        "keeps_address": True,
    }


class _AddressConnection(_UsbConnection):
    """A connected USB interface whose address changes and checks end as given."""

    def __init__(self) -> None:
        super().__init__()
        self.connect_to_usb_by_serial("")
        self.outcome: Future[Any] = Future()
        self.requests: list[tuple[str, str]] = []

    def set_usb_interface_address(self, text: str) -> Future[Any]:
        self.requests.append(("set", text))
        if text == "x":
            raise ValueError("x is not an individual address (area.line.device).")
        return self.outcome

    def check_usb_interface_address(self, text: str) -> Future[Any]:
        self.requests.append(("check", text))
        if text == "x":
            raise ValueError("x is not an individual address (area.line.device).")
        return self.outcome


async def test_setting_the_interface_address(tmp_path: Path) -> None:
    conn = _AddressConnection()
    conn.outcome.set_result(None)
    async with Client(_server(tmp_path, conn)) as client:
        result = await _data(
            client, "connection_set_usb_interface_address", individual_address="1.0.2 "
        )
        with pytest.raises(Exception, match="x is not an individual address"):
            await client.call_tool(
                "connection_set_usb_interface_address", {"individual_address": "x"}
            )
        conn.outcome = Future()
        conn.outcome.set_exception(
            ConnectionError("The individual address could not be set: refused")
        )
        with pytest.raises(Exception, match="could not be set: refused"):
            await client.call_tool(
                "connection_set_usb_interface_address", {"individual_address": "1.0.9"}
            )
        conn.outcome = Future()
        with pytest.raises(Exception, match=r"was not set within 0\.1 s"):
            await client.call_tool(
                "connection_set_usb_interface_address",
                {"individual_address": "1.0.9", "timeout": 0.1},
            )
    assert result["individual_address"] == "1.0.2"
    assert result["usb_interface"]["individual_address"] == "1.0.2"
    assert conn.requests[0] == ("set", "1.0.2 ")


async def test_checking_an_individual_address(tmp_path: Path) -> None:
    conn = _AddressConnection()
    async with Client(_server(tmp_path, conn)) as client:
        conn.outcome.set_result(True)
        used = await _data(
            client, "connection_check_individual_address", individual_address="1.1.9"
        )
        conn.outcome = Future()
        conn.outcome.set_result(False)
        free = await _data(
            client, "connection_check_individual_address", individual_address="1.1.9"
        )
        with pytest.raises(Exception, match="x is not an individual address"):
            await client.call_tool(
                "connection_check_individual_address", {"individual_address": "x"}
            )
        conn.outcome = Future()
        conn.outcome.set_exception(OSError("bus gone"))
        with pytest.raises(Exception, match="the check failed: bus gone"):
            await client.call_tool(
                "connection_check_individual_address", {"individual_address": "1.1.9"}
            )
        conn.outcome = Future()
        with pytest.raises(Exception, match=r"no result within 0\.1 s"):
            await client.call_tool(
                "connection_check_individual_address",
                {"individual_address": "1.1.9", "timeout": 0.1},
            )
    assert used == {"individual_address": "1.1.9", "free": False}
    assert free == {"individual_address": "1.1.9", "free": True}
