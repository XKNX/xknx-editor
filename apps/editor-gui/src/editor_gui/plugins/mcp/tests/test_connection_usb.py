"""KNX USB interfaces over MCP: found by the scan, connected through, shown in the status, their
individual address set and checked."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from xknx.knxip.knxip_enum import KNXMedium
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.ip_server import IpServerSettings, OpenTunnel
from editor_gui.plugins.connection.mbus_batibus import (
    MbusBatibusFrame,
    MbusBatibusFrameLog,
)
from editor_gui.plugins.connection.plugin import ConnectionState
from editor_gui.plugins.connection.usb import UsbTarget
from editor_gui.plugins.connection.xknx_usb import (
    USBDeviceInfo,
    USBInterfaceInfo,
    USBTransferFrame,
)
from editor_gui.plugins.connection.xknx_usb.const import (
    EMIID,
    ProtocolID,
    SupportedEMIType,
)
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
        self.usb_local_device = False

    def scan(self) -> None:
        pass

    def connect_to_usb_by_serial(
        self, serial_number: str, *, local_device: bool | None = None
    ) -> bool:
        self.connect_requests.append(serial_number)
        if serial_number not in ("", _DEVICE.serial_number):
            return False
        if local_device is not None:
            self.usb_local_device = local_device
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
            transport_layer=self.usb_local_device,
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
        "medium": "TP",
        "local_device": False,
        "local_device_access": False,
    }
    assert status["local_device"] is None


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


async def test_an_rf_interface(tmp_path: Path) -> None:
    conn = _UsbConnection()
    conn.connect_to_usb_by_serial("")
    info = USBInterfaceInfo(
        device=_DEVICE,
        individual_address=IndividualAddress("1.0.2"),
        max_apdu_length=55,
        supported_emi_types=SupportedEMIType.CEMI,
        bus_connected=True,
        mask_version=0x2311,
        medium=KNXMedium.RF,
        rf_domain_address=bytes.fromhex("00fa12345678"),
    )
    conn.usb_info = info
    async with Client(_server(tmp_path, conn)) as client:
        status = await _data(client, "connection_status")
        conn.usb_info = USBInterfaceInfo(
            device=_DEVICE,
            individual_address=IndividualAddress("1.0.2"),
            max_apdu_length=55,
            supported_emi_types=SupportedEMIType.CEMI,
            bus_connected=True,
            medium=KNXMedium.RF,
        )
        unknown = await _data(client, "connection_status")
    assert status["usb_interface"]["medium"] == "RF"
    assert status["usb_interface"]["rf_domain_address"] == "00FA:12345678"
    assert unknown["usb_interface"]["rf_domain_address"] is None


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
        "local_device": False,
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

    def set_usb_interface_domain_address(self, text: str) -> Future[Any]:
        self.requests.append(("domain", text))
        if text == "x":
            raise ValueError("x is not an RF domain address.")
        if self.outcome.done() and self.outcome.exception() is None:
            assert self.usb_info is not None
            self.usb_info = USBInterfaceInfo(
                device=_DEVICE,
                individual_address=self.usb_info.individual_address,
                max_apdu_length=55,
                supported_emi_types=SupportedEMIType.CEMI,
                bus_connected=True,
                medium=KNXMedium.RF,
                rf_domain_address=bytes.fromhex("00fa00000042"),
            )
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


async def test_setting_the_rf_domain_address(tmp_path: Path) -> None:
    conn = _AddressConnection()
    conn.outcome.set_result(None)
    async with Client(_server(tmp_path, conn)) as client:
        result = await _data(
            client,
            "connection_set_usb_interface_domain_address",
            domain_address="00FA:00000042",
        )
        with pytest.raises(Exception, match="x is not an RF domain address"):
            await client.call_tool(
                "connection_set_usb_interface_domain_address", {"domain_address": "x"}
            )
        conn.outcome = Future()
        conn.outcome.set_exception(
            ConnectionError("The domain address could not be set: refused")
        )
        with pytest.raises(Exception, match="could not be set: refused"):
            await client.call_tool(
                "connection_set_usb_interface_domain_address",
                {"domain_address": "00FA:00000043"},
            )
        conn.outcome = Future()
        with pytest.raises(Exception, match=r"was not set within 0\.1 s"):
            await client.call_tool(
                "connection_set_usb_interface_domain_address",
                {"domain_address": "00FA:00000043", "timeout": 0.1},
            )
    assert result["rf_domain_address"] == "00FA:00000042"
    assert result["usb_interface"]["medium"] == "RF"
    assert conn.requests[0] == ("domain", "00FA:00000042")


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


async def test_connecting_to_the_interfaces_own_device(tmp_path: Path) -> None:
    conn = _UsbConnection()
    async with Client(_server(tmp_path, conn)) as client:
        await _data(client, "connection_connect_usb", local_device=True, timeout=1)
        status = await _data(client, "connection_status")
    assert conn.usb_local_device
    assert status["usb_interface"]["local_device"] is True
    assert status["usb_interface"]["local_device_access"] is True
    assert status["local_device"] == "1.0.2"


class _LocalDeviceConnection(_UsbConnection):
    """A connected USB interface whose connection is switched to local device access."""

    def __init__(self) -> None:
        super().__init__()
        self.connect_to_usb_by_serial("")
        self.outcome: Future[Any] = Future()
        self.requests: list[bool] = []
        self.busy = False

    def set_usb_local_device(self, local_device: bool) -> Future[Any]:
        self.requests.append(local_device)
        if self.busy:
            raise ValueError("The connection of the interface is being switched.")
        if self.outcome.done() and self.outcome.exception() is None:
            self.usb_local_device = local_device
        return self.outcome


async def test_switching_local_device_access(tmp_path: Path) -> None:
    conn = _LocalDeviceConnection()
    conn.outcome.set_result(None)
    async with Client(_server(tmp_path, conn)) as client:
        result = await _data(
            client, "connection_set_usb_local_device", local_device=True
        )
        conn.outcome = Future()
        conn.outcome.set_exception(
            ConnectionError(
                "The connection could not be switched: does not support the cEMI "
                "transport layer"
            )
        )
        with pytest.raises(Exception, match="could not be switched"):
            await client.call_tool(
                "connection_set_usb_local_device", {"local_device": False}
            )
        conn.outcome = Future()
        with pytest.raises(Exception, match=r"not switched within 0\.1 s"):
            await client.call_tool(
                "connection_set_usb_local_device",
                {"local_device": False, "timeout": 0.1},
            )
        conn.busy = True
        with pytest.raises(Exception, match="being switched"):
            await client.call_tool(
                "connection_set_usb_local_device", {"local_device": True}
            )
    assert result["local_device"] is True
    assert result["usb_interface"]["local_device"] is True
    assert conn.requests == [True, False, False, True]


class _FramesConnection(_UsbConnection):
    """A connected USB interface with M-Bus and BatiBus tunnels."""

    def __init__(self) -> None:
        super().__init__()
        self.connect_to_usb_by_serial("")
        self.log = MbusBatibusFrameLog()
        self.blocked = False

    def mbus_batibus_frames(
        self, *, since: int = 0, limit: int | None = None
    ) -> list[MbusBatibusFrame]:
        return self.log.frames(since=since, limit=limit)

    @property
    def mbus_batibus_last_id(self) -> int:
        return self.log.last_id

    def send_mbus_batibus_frame(
        self, protocol: int, service: int, body: bytes
    ) -> Future[None]:
        if self.blocked:
            raise ValueError(
                "Block Bus Writes is on (Settings > Connection): nothing is sent."
            )
        if not body:
            raise ValueError("Enter the data to send (e.g. 44 AE 0C).")
        self.log.add(
            USBTransferFrame(protocol=protocol, service=service, body=body), sent=True
        )
        done: Future[None] = Future()
        done.set_result(None)
        return done


async def test_mbus_and_batibus_frames(tmp_path: Path) -> None:
    conn = _FramesConnection()
    conn.log.add(
        USBTransferFrame(
            protocol=ProtocolID.M_BUS_TUNNEL, service=0, body=bytes.fromhex("44ae0c")
        ),
        sent=False,
    )
    async with Client(_server(tmp_path, conn)) as client:
        sent = await _data(
            client,
            "connection_send_mbus_batibus_frame",
            protocol="batibus",
            data="01 02",
            service=7,
        )
        listed = await _data(client, "connection_mbus_batibus_frames")
        newer = await _data(client, "connection_mbus_batibus_frames", since=1)
        newest = await _data(client, "connection_mbus_batibus_frames", limit=1)
        with pytest.raises(Exception, match="not hexadecimal data"):
            await client.call_tool(
                "connection_send_mbus_batibus_frame",
                {"protocol": "m_bus", "data": "xyz"},
            )
        with pytest.raises(Exception, match="Enter the data to send"):
            await client.call_tool(
                "connection_send_mbus_batibus_frame",
                {"protocol": "m_bus", "data": ""},
            )
        conn.blocked = True
        with pytest.raises(Exception, match="Block Bus Writes is on"):
            await client.call_tool(
                "connection_send_mbus_batibus_frame",
                {"protocol": "m_bus", "data": "01"},
            )
    assert sent["direction"] == "sent"
    assert (sent["protocol"], sent["service"], sent["data"]) == ("batibus", 7, "0102")
    assert listed["count"] == 2 and listed["last_id"] == 2
    received = listed["frames"][0]
    assert received["direction"] == "received"
    assert received["protocol"] == "m_bus"
    assert received["data"] == "44ae0c"
    assert received["manufacturer_code"] == 0
    assert received["time"].startswith("20")
    assert [frame["id"] for frame in newer["frames"]] == [2]
    assert [frame["id"] for frame in newest["frames"]] == [2]


async def test_no_group_telegrams_through_local_device_access(tmp_path: Path) -> None:
    from xknx import XKNX

    from editor_gui.plugins.connection.service import LocalDevice

    ctx = _build_ctx(tmp_path)
    ctx.connection_plugin = _UsbConnection()
    ctx.api.connection.set_connection(XKNX(), None)
    ctx.api.connection.local_device = LocalDevice("1.0.2")
    async with Client(build_server(ctx)) as client:
        with pytest.raises(Exception, match="group telegrams can't be sent"):
            await client.call_tool("monitor_send_read", {"address": "1/2/3"})


class _IpServerConnection(_UsbConnection):
    """A connected USB interface with a KNXnet/IP server that starts as told."""

    def __init__(self) -> None:
        super().__init__()
        self.connect_to_usb_by_serial("")
        self.ip_server_settings = IpServerSettings()
        self.ip_server_running = False
        self.ip_server_port: int | None = None
        self.ip_server_error: str | None = None
        self.outcome: Future[Any] = Future()
        self.outcome.set_result(None)

    def ip_server_refusal(self) -> str | None:
        return None

    def ip_server_tunnels(self) -> list[OpenTunnel]:
        if not self.ip_server_running:
            return []
        return [OpenTunnel(1, "127.0.0.1:50000", "TCP", "1.0.240")]

    def suggest_ip_server_addresses(self) -> list[str]:
        return ["1.0.250", "1.0.249"]

    def configure_ip_server(self, settings: IpServerSettings) -> Future[Any]:
        if settings.enabled and not settings.tunnel_addresses:
            raise ValueError("At least one tunnel address is needed.")
        self.ip_server_settings = settings
        if self.outcome.done() and self.outcome.exception() is None:
            self.ip_server_running = settings.enabled
            self.ip_server_port = settings.port if settings.enabled else None
        return self.outcome


async def test_the_knxnet_ip_server(tmp_path: Path) -> None:
    conn = _IpServerConnection()
    async with Client(_server(tmp_path, conn)) as client:
        off = await _data(client, "connection_ip_server")
        started = await _data(
            client,
            "connection_set_ip_server",
            enabled=True,
            port=3700,
            listen="localhost",
            tunnel_addresses=[" 1.0.240 ", "1.0.241"],
        )
        with pytest.raises(Exception, match="At least one tunnel address"):
            await client.call_tool("connection_set_ip_server", {"tunnel_addresses": []})
        conn.outcome = Future()
        conn.outcome.set_exception(
            ConnectionError("The KNXnet/IP server could not start: port in use")
        )
        with pytest.raises(Exception, match="could not start: port in use"):
            await client.call_tool("connection_set_ip_server", {"port": 3671})
        conn.outcome = Future()
        with pytest.raises(Exception, match=r"within 0\.1 s"):
            await client.call_tool(
                "connection_set_ip_server", {"enabled": False, "timeout": 0.1}
            )
    assert off["running"] is False and off["settings"]["enabled"] is False
    assert off["suggested_tunnel_addresses"] == ["1.0.250", "1.0.249"]
    assert off["tunnels"] == [] and off["refusal"] is None
    assert started["running"] is True and started["port"] == 3700
    assert started["settings"] == {
        "enabled": True,
        "port": 3700,
        "listen": "localhost",
        "discovery": True,
        "tcp": True,
        "tunnel_addresses": ["1.0.240", "1.0.241"],
        "name": "XKNX Editor",
    }
    assert started["tunnels"] == [
        {
            "channel": 1,
            "client": "127.0.0.1:50000",
            "protocol": "TCP",
            "individual_address": "1.0.240",
        }
    ]
