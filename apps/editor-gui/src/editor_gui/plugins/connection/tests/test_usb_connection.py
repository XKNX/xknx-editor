"""Connections through KNX USB interfaces: chosen, kept, connected at startup, opened like the
KNXnet/IP ones, and the bus monitor on them (against the simulated interface of conftest)."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui
from xknx import XKNX
from xknx.io.connection import ConnectionType
from xknx.secure.data_secure import DataSecure
from xknx.telegram import GroupAddress, IndividualAddress

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionState
from editor_gui.plugins.connection.service import LinkInfo
from editor_gui.plugins.connection.usb import (
    ObservableUSBInterfaceThreaded,
    UsbBusmonitorConnection,
    UsbTarget,
    search_usb_interfaces,
    usb_device_name,
)
from editor_gui.plugins.connection.xknx_usb import USBDeviceInfo, find_usb_interfaces
from editor_gui.plugins.connection.xknx_usb import tunnel as usb_tunnel
from editor_gui.plugins.connection.xknx_usb.const import (
    DeviceFeature,
    DeviceFeatureService,
)
from editor_gui.plugins.connection.xknx_usb.frame import device_feature_frame
from editor_gui.plugins.project.tests._clicks import Clicks

from .conftest import SPACELOGIC_ENUMERATION, FakeHIDModule

_DEVICE = USBDeviceInfo(
    vendor_id=0x16DE,
    product_id=0x008E,
    path=b"usb-path-1",
    serial_number="00C501053FB2",
    manufacturer="KNX Manufacturer",
    product="KNX-USB Interface",
)


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usb_tunnel, "DEVICE_FEATURE_GET_TIMEOUT", 0.2)
    monkeypatch.setattr(usb_tunnel, "LOCAL_MANAGEMENT_TIMEOUT", 0.2)


def _data_secure() -> DataSecure:
    return DataSecure(
        group_key_table={GroupAddress("1/2/3"): bytes(16)},
        individual_address_table={IndividualAddress("1.0.4"): 0},
    )


async def _until(condition: Any, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        # set by the reader and connection threads, which can't set an asyncio.Event
        while not condition():
            await asyncio.sleep(0.01)


def _plugin() -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._log = SimpleNamespace(
        info=lambda *_a, **_k: None,
        debug=lambda *_a, **_k: None,
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
    )
    plugin._state = ConnectionState.DISCONNECTED
    plugin._error_message = None
    plugin._usb_target = None
    plugin._usb_info = None
    plugin._usb_devices = []
    plugin._usb_addresses = {}
    plugin._usb_settings_open = False
    plugin._usb_settings_shown = False
    plugin._usb_address_text = ""
    plugin._usb_address_write = None
    plugin._usb_address_busy = False
    plugin._usb_address_checking = False
    plugin._usb_address_error = None
    plugin._usb_address_free = None
    plugin._controller_ip = "192.168.1.10"
    plugin._multicast_group = "224.0.23.12"
    plugin._connection_type = ConnectionType.TUNNELING_TCP
    plugin._preferred_gateway_ip = None
    plugin._connect_at_startup = True
    plugin._selected_gateway = None
    plugin._gateway_info = None
    plugin._interface = None
    plugin._xknx = None
    plugin._config = None
    plugin._link_up = False
    plugin._session = 0
    plugin._unregister_link_state = None
    plugin._startup_connect = False
    plugin._retry_on_credentials = False
    plugin._credentials_error = None
    plugin._operation_restore = None
    plugin._busmonitor = None
    plugin._lost_since = None
    # waiting for a lost link to come back is not part of these tests
    plugin._run_async = lambda coro: coro.close()
    sent: list[bytes] = []
    received: list[bytes] = []
    events: list[str] = []
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(
            use_serial_number=True,
            busmonitor_active=False,
            dispatch_raw_cemi=received.append,
            dispatch_sent_cemi=sent.append,
            data_secure=lambda: None,
            set_connection=lambda xknx, loop: events.append(
                "connection" if xknx is not None else "no connection"
            ),
            dispatch_connected=lambda: events.append("connected"),
            dispatch_disconnected=lambda: events.append("disconnected"),
            dispatch_link_state=lambda up: events.append(f"link {up}"),
            interface_max_apdu_length=None,
            interface_name="",
            interface_address=None,
            link_info=None,
        ),
        project=SimpleNamespace(revision=0, loading=False, is_open=False),
    )
    plugin.test_sent = sent
    plugin.test_received = received
    plugin.test_events = events
    return plugin


class TestUsbTarget:
    def test_kept_in_the_settings(self) -> None:
        target = UsbTarget.of(_DEVICE)
        assert target.name == "KNX-USB Interface"
        assert target.label == "KNX-USB Interface (USB)"
        assert UsbTarget.from_settings(target.to_settings()) == target
        assert target.matches(_DEVICE)
        # on another port, with another path
        assert target.matches(
            USBDeviceInfo(0x16DE, 0x008E, b"other", serial_number="00C501053FB2")
        )
        assert not target.matches(
            USBDeviceInfo(0x16DE, 0x008E, b"usb-path-1", serial_number="other")
        )
        # without a serial number any of its kind
        assert UsbTarget(0x16DE, 0x008E, "", "x").matches(
            USBDeviceInfo(0x16DE, 0x008E, b"p", serial_number="any")
        )

    @pytest.mark.parametrize(
        "data",
        [None, "usb", {"vendor_id": "16de", "product_id": 142}, {"vendor_id": 1}],
    )
    def test_unreadable_settings(self, data: object) -> None:
        assert UsbTarget.from_settings(data) is None

    def test_defaults_for_missing_fields(self) -> None:
        target = UsbTarget.from_settings({"vendor_id": 1, "product_id": 2, "name": ""})
        assert target == UsbTarget(1, 2, "", "KNX USB Interface")

    def test_name(self) -> None:
        assert usb_device_name(USBDeviceInfo(1, 2, b"p", product="  ")) == (
            "KNX USB Interface"
        )


class TestSettings:
    def test_the_usb_interface_is_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._save_current_settings()
        stored = plugin_module.load_settings("connection")
        assert stored["usb_interface"]["serial_number"] == "00C501053FB2"
        loaded = _plugin()
        loaded._load_saved_settings()
        assert loaded._usb_target == UsbTarget.of(_DEVICE)
        assert loaded.is_usb
        assert loaded.connection_type == "usb"
        assert not loaded.is_routing
        assert loaded._connection_target == "KNX-USB Interface (USB)"
        assert loaded._mode_name == "usb"
        # a gateway chosen instead replaces it
        loaded._usb_target = None
        loaded._save_current_settings()
        assert plugin_module.load_settings("connection")["usb_interface"] is None


class TestPlugin:
    def _runner(self, plugin: Any) -> list[Any]:
        ran: list[Any] = []
        plugin._run_async = lambda coro: (ran.append(coro), coro.close())[0]
        plugin._save_current_settings = lambda: None
        plugin._busmonitor_blocks = lambda _op: False
        return ran

    def test_connect_to_usb(self) -> None:
        plugin = _plugin()
        ran = self._runner(plugin)
        plugin._usb_devices = [_DEVICE]
        assert not plugin.connect_to_usb_by_serial("nope")
        assert plugin.connect_to_usb_by_serial("")
        assert plugin._usb_target == UsbTarget.of(_DEVICE)
        assert plugin._state is ConnectionState.CONNECTING
        assert len(ran) == 1
        # not again while connecting
        plugin.connect_to_usb(_DEVICE)
        assert len(ran) == 1
        # a gateway connection forgets the USB interface
        plugin._state = ConnectionState.DISCONNECTED
        plugin.connect()
        assert plugin._usb_target is None
        assert plugin.usb_target is None

    def test_no_transport_switch_for_usb(self) -> None:
        plugin = _plugin()
        ran = self._runner(plugin)
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._state = ConnectionState.CONNECTED
        plugin.switch_connection_type(ConnectionType.TUNNELING)
        assert ran == []

    def test_autostart_connects_the_usb_interface(self) -> None:
        plugin = _plugin()
        connected: list[Any] = []
        plugin.connect_to_usb = lambda device, startup=False: connected.append(
            (device, startup)
        )
        target = UsbTarget.of(_DEVICE)
        plugin._autostart_usb(target)
        assert connected == []
        plugin._usb_devices = [_DEVICE]
        plugin._autostart_usb(target)
        assert connected == [(_DEVICE, True)]
        plugin._state = ConnectionState.CONNECTED
        plugin._autostart_usb(target)
        assert len(connected) == 1

    async def test_scan_lists_usb_interfaces_and_autostarts_one(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def no_gateways(*_args: Any, **_kwargs: Any) -> list[Any]:
            return []

        monkeypatch.setattr(plugin_module, "search_gateways", no_gateways)
        monkeypatch.setattr(plugin_module, "scan_addresses", lambda: [])
        plugin = _plugin()
        plugin._scanning = True
        plugin._autostart_retry_done = True
        plugin._usb_target = UsbTarget.of(_DEVICE)
        started: list[Any] = []
        plugin.connect_to_usb = lambda device, startup=False: started.append(device)
        # no gateway is tried in the USB interface's place
        plugin.connect = lambda **_kwargs: started.append("gateway")
        await plugin._scan_async(auto_connect=True)
        assert [d.serial_number for d in plugin.usb_devices] == ["00C501053FB2"]
        assert started == [plugin.usb_devices[0]]
        assert not plugin.scanning

    async def test_reopen_has_nothing_to_check(self) -> None:
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._config = object()
        assert await plugin._reopen_if_changed(0)

    def test_link_info(self) -> None:
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        # not connected: the device path is unknown
        link = plugin._link_info()
        assert link == LinkInfo(
            name="KNX-USB Interface", host="", port=0, connector="Usb"
        )
        assert link.options == 'Type=Usb;Name="KNX-USB Interface";AutoReconnect=True'
        assert link.endpoint_name == "KNX-USB Interface (USB)"
        assert LinkInfo("x", r"\\?\hid#vid_16de&pid_008e#6", 0, "Usb").options == (
            r'Type=Usb;DevicePath=\\?\hid#vid_16de&pid_008e#6;Name="x";AutoReconnect=True'
        )
        assert plugin._busmonitor_target == "KNX-USB Interface (USB)"

    def test_operation_connection_restores_the_usb_interface(self) -> None:
        plugin = _plugin()
        self._runner(plugin)
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._connect_for_operation("Gateway", "192.168.1.20", None)
        assert plugin._usb_target is None
        plugin._restore_connection_settings()
        assert plugin._usb_target == UsbTarget.of(_DEVICE)
        # and through a USB interface for the operations
        plugin._usb_target = None
        plugin._connect_usb_for_operation("KNX-USB Interface", _DEVICE)
        assert plugin._usb_target == UsbTarget.of(_DEVICE)
        assert plugin._operation_name == "KNX-USB Interface"
        plugin._restore_connection_settings()
        assert plugin._usb_target is None


class TestConnecting:
    async def test_connect_and_disconnect(self, fake_hid: FakeHIDModule) -> None:
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        await plugin._connect_async()
        try:
            assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
            connection = plugin._api.connection
            assert connection.interface_max_apdu_length == 55
            assert connection.interface_address == "1.0.2"
            assert connection.interface_name == "KNX-USB Interface"
            assert connection.link_info.connector == "Usb"
            assert connection.link_info.options == (
                'Type=Usb;DevicePath=usb-path-1;Name="KNX-USB Interface";'
                "AutoReconnect=True"
            )
            assert plugin.test_events == ["connection", "connected"]
            assert plugin._xknx.current_address == IndividualAddress("1.0.2")
            info = plugin.usb_info
            assert info is not None and info.bus_connected
            # the bus connection as it is now
            fake_hid.interface.send_frame(
                device_feature_frame(
                    DeviceFeatureService.INFO,
                    DeviceFeature.BUS_CONNECTION_STATUS,
                    b"\x00",
                )
            )
            await _until(lambda: not plugin.usb_info.bus_connected)
            # without a bus connection the link is down
            await _until(lambda: plugin.test_events[-1] == "link False")
            assert not plugin._link_up
            fake_hid.interface.send_frame(
                device_feature_frame(
                    DeviceFeatureService.INFO,
                    DeviceFeature.BUS_CONNECTION_STATUS,
                    b"\x01",
                )
            )
            await _until(lambda: plugin.test_events[-1] == "link True")
            assert plugin._link_up
            # frames from the bus reach the monitor
            fake_hid.interface.send_cemi(bytes.fromhex("2900bce0102f0012030080346a"))
            await _until(lambda: plugin.test_received)
        finally:
            await plugin._disconnect_async()
        assert plugin._state is ConnectionState.DISCONNECTED
        assert plugin.usb_info is None
        # closed as it is, without a reset
        assert b"\xf1" not in fake_hid.interface.written_cemi()
        assert fake_hid.closed == 1

    async def test_connect_fails(self, fake_hid: FakeHIDModule) -> None:
        plugin = _plugin()
        plugin._report_failure = lambda error, startup: None
        plugin._usb_target = UsbTarget(0x16DE, 0x008E, "other", "KNX-USB Interface")
        await plugin._connect_async()
        assert plugin._state is ConnectionState.ERROR
        assert "No KNX USB interface found" in plugin._error_message
        assert plugin._interface is None and plugin.usb_info is None

    async def test_the_interface_class(self, fake_hid: FakeHIDModule) -> None:
        data_secure = _data_secure()
        interface = ObservableUSBInterfaceThreaded(
            XKNX(), UsbTarget.of(_DEVICE), data_secure=data_secure
        )
        assert interface.usb_info() is None
        assert await interface.read_max_apdu_length() is None
        await interface.start()
        try:
            assert await interface.read_max_apdu_length() == 55
            # in place from the interface's setup on
            assert interface.xknx.cemi_handler.data_secure is data_secure
        finally:
            await interface.stop()

    async def test_connect_without_bus(self, fake_hid: FakeHIDModule) -> None:
        fake_hid.interface.features[DeviceFeature.BUS_CONNECTION_STATUS] = b"\x00"
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        await plugin._connect_async()
        try:
            assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
            # open, but the link is down until the interface reports the bus
            assert plugin.test_events == ["connection", "connected", "link False"]
            assert not plugin._link_up
            assert not plugin.usb_info.bus_connected
            fake_hid.interface.send_frame(
                device_feature_frame(
                    DeviceFeatureService.INFO,
                    DeviceFeature.BUS_CONNECTION_STATUS,
                    b"\x01",
                )
            )
            await _until(lambda: plugin._link_up)
            assert plugin.test_events[-1] == "link True"
            assert plugin.usb_info.bus_connected
        finally:
            await plugin._disconnect_async()

    async def test_unknown_individual_address(self, fake_hid: FakeHIDModule) -> None:
        del fake_hid.interface.properties[(0, 57)]  # PID_SUBNET_ADDR
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        await plugin._connect_async()
        try:
            assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
            assert plugin.usb_info.individual_address is None
            # nothing shown for the interface's address
            assert plugin._api.connection.interface_address is None
            assert plugin._xknx.current_address == IndividualAddress(0)
        finally:
            await plugin._disconnect_async()

    async def test_data_secure_with_an_unknown_individual_address(
        self, fake_hid: FakeHIDModule, caplog: pytest.LogCaptureFixture
    ) -> None:
        del fake_hid.interface.properties[(0, 57)]  # PID_SUBNET_ADDR
        plugin = _plugin()
        plugin._api.connection.data_secure = _data_secure
        plugin._usb_target = UsbTarget.of(_DEVICE)
        with caplog.at_level(logging.WARNING, logger="xknx.log"):
            await plugin._connect_async()
        try:
            # connected; Data Secure telegrams wait until the address is known (set up as
            # it would be after the interface's reset or a reconnect)
            assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
            assert "Data Secure telegrams are not sent until it is known" in caplog.text
        finally:
            await plugin._disconnect_async()


def _property_writes(fake_hid: FakeHIDModule) -> list[str]:
    """The M_PropWrite.req the editor sent to the interface, in hex."""
    return [cemi.hex() for cemi in fake_hid.interface.written_cemi() if cemi[0] == 0xF6]


async def _connected(fake_hid: FakeHIDModule, target: UsbTarget | None = None) -> Any:
    """A plugin connected through the simulated interface; its address changes run on the
    test's loop."""
    plugin = _plugin()
    plugin._usb_target = target or UsbTarget.of(_DEVICE)
    plugin._ensure_async_loop = asyncio.get_running_loop
    plugin._save_current_settings = lambda: None
    await plugin._connect_async()
    assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
    return plugin


class TestInterfaceAddress:
    """The individual address of the connected USB interface: set and checked."""

    async def test_set(self, fake_hid: FakeHIDModule) -> None:
        plugin = await _connected(fake_hid)
        try:
            fake_hid.interface.written.clear()
            plugin.test_events.clear()
            future = plugin.set_usb_interface_address(" 1.0.12 ")
            assert plugin.usb_address_busy
            await asyncio.wrap_future(future)
            assert not plugin.usb_address_busy
            # the connection was opened again and set it on the interface (Device Object)
            assert _property_writes(fake_hid) == [
                "f600080134100100",  # PID_COMM_MODE
                "f600000139100110",  # PID_SUBNET_ADDR
                "f60000013a10010c",  # PID_DEVICE_ADDR
            ]
            assert plugin._state is ConnectionState.CONNECTED
            assert plugin.usb_info.individual_address == IndividualAddress("1.0.12")
            assert plugin._api.connection.interface_address == "1.0.12"
            # the recordings go on
            assert "link False" in plugin.test_events
            assert "disconnected" not in plugin.test_events
            # connected again later: only read
            assert plugin._usb_address_to_set(plugin._usb_target) is None
        finally:
            await plugin._disconnect_async()

    async def test_the_same_address_writes_nothing(
        self, fake_hid: FakeHIDModule
    ) -> None:
        plugin = await _connected(fake_hid)
        try:
            fake_hid.interface.written.clear()
            future = plugin.set_usb_interface_address("1.0.2")
            assert future.done() and future.result() is None
            assert not fake_hid.interface.written
            assert not plugin.usb_address_busy
        finally:
            await plugin._disconnect_async()

    async def test_refused(self, fake_hid: FakeHIDModule) -> None:
        plugin = await _connected(fake_hid)
        reopened: list[str] = []
        plugin._run_async = lambda coro: (
            reopened.append(coro.cr_code.co_name),
            coro.close(),
        )
        # an interface in Security Mode: access denied
        fake_hid.interface.refused_writes[(0, 57)] = 0x0B
        try:
            with pytest.raises(ConnectionError, match="could not be set"):
                await asyncio.wrap_future(plugin.set_usb_interface_address("1.0.12"))
            assert not plugin.usb_address_busy
            assert (
                "Writing property PID_SUBNET_ADDR failed" in plugin._usb_address_error
            )
            # connected again with the address as it was
            assert reopened == ["_reopen"]
            assert fake_hid.interface.properties[(0, 58)] == b"\x02"
        finally:
            await plugin._disconnect_async()

    async def test_refused_before_it_starts(self, fake_hid: FakeHIDModule) -> None:
        plugin = _plugin()
        with pytest.raises(ValueError, match="not an individual address"):
            plugin.set_usb_interface_address("1.0")
        with pytest.raises(ValueError, match="Not connected through a USB interface"):
            plugin.set_usb_interface_address("1.0.12")
        plugin = await _connected(fake_hid)
        try:
            plugin._usb_address_busy = True
            with pytest.raises(ValueError, match="being set or checked"):
                plugin.set_usb_interface_address("1.0.12")
            with pytest.raises(ValueError, match="being set or checked"):
                plugin.check_usb_interface_address("1.0.12")
        finally:
            plugin._usb_address_busy = False
            await plugin._disconnect_async()

    def test_check(self) -> None:
        plugin = _plugin()
        answers: list[Future[Any]] = []

        def occupied(address: str) -> Future[Any]:
            assert address == "1.1.9"
            answers.append(Future())
            return answers[-1]

        plugin._api.connection.individual_address_occupied = occupied
        plugin.check_usb_interface_address("1.1.9")
        assert plugin._usb_address_checking
        answers[-1].set_result(True)
        assert not plugin._usb_address_checking
        assert plugin._usb_address_free == ("1.1.9", False)
        plugin.check_usb_interface_address("1.1.9")
        answers[-1].set_result(False)
        assert plugin._usb_address_free == ("1.1.9", True)
        plugin.check_usb_interface_address("1.1.9")
        answers[-1].set_exception(TimeoutError("no answer"))
        assert plugin._usb_address_free is None
        assert plugin._usb_address_error == (
            "The individual address could not be checked: no answer"
        )
        # without a connection
        plugin._api.connection.individual_address_occupied = lambda address: None
        with pytest.raises(ValueError, match="Not connected through a USB interface"):
            plugin.check_usb_interface_address("1.1.9")

    async def test_an_interface_that_forgets_its_address(
        self, fake_hid: FakeHIDModule
    ) -> None:
        enumeration = fake_hid.enumeration[0]
        enumeration["vendor_id"], enumeration["product_id"] = 0x0681, 0x0014
        target = UsbTarget(0x0681, 0x0014, "00C501053FB2", "OCI700")
        assert not target.keeps_address and UsbTarget.of(_DEVICE).keeps_address
        plugin = await _connected(fake_hid, target)
        saved: list[dict[str, str]] = []
        plugin._save_current_settings = lambda: saved.append(
            dict(plugin._usb_addresses)
        )
        try:
            await asyncio.wrap_future(plugin.set_usb_interface_address("1.0.12"))
            # kept when it was set
            assert saved[-1] == {"0681:0014:00C501053FB2": "1.0.12"}
        finally:
            await plugin._disconnect_async()
        # unplugged: forgotten
        fake_hid.interface.properties[(0, 57)] = b"\xff"
        fake_hid.interface.properties[(0, 58)] = b"\xff"
        fake_hid.interface.written.clear()
        await plugin._connect_async()
        try:
            # set again on connect
            assert _property_writes(fake_hid)[1:] == [
                "f600000139100110",
                "f60000013a10010c",
            ]
            assert plugin.usb_info.individual_address == IndividualAddress("1.0.12")
        finally:
            await plugin._disconnect_async()
        # an unreadable kept address is not set
        plugin._usb_addresses = {target.key: "nonsense"}
        assert plugin._usb_address_to_set(target) is None

    def test_kept_in_the_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
        plugin = _plugin()
        plugin._usb_addresses = {"0681:0014:": "1.1.250"}
        ConnectionPlugin._save_current_settings(plugin)
        plugin._usb_addresses = {}
        plugin._load_saved_settings()
        assert plugin._usb_addresses == {"0681:0014:": "1.1.250"}


class TestInterfaceSettingsDialog:
    """Gateway menu > USB Interface Settings…"""

    async def test_set_and_check_in_the_dialog(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = await _connected(fake_hid)
        started: list[tuple[str, str]] = []
        plugin.set_usb_interface_address = lambda text: started.append(("set", text))
        plugin.check_usb_interface_address = lambda text: started.append(
            ("check", text)
        )
        ctx = imgui.create_context()
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        clicks = Clicks(monkeypatch, plugin_module)
        edited: list[bool] = []
        pressed: set[str] = set()
        real_button = imgui.button
        monkeypatch.setattr(
            imgui,
            "is_item_deactivated_after_edit",
            lambda: bool(edited) and edited.pop(),
        )

        def button(label: str, *args: Any) -> bool:
            if label in pressed:
                pressed.discard(label)
                return True
            return bool(real_button(label, *args))

        monkeypatch.setattr(imgui, "button", button)

        def frames(count: int = 2) -> None:
            for _ in range(count):
                imgui.new_frame()
                plugin.render_window()
                imgui.end_frame()

        plugin._select_open = plugin._select_shown = plugin._progmode_open = False
        try:
            plugin.open_usb_settings()
            assert plugin._usb_address_text == "1.0.2"
            frames()
            plugin._usb_address_text = "1.0.12"
            edited.append(True)
            frames()
            pressed.add("Address Free?##usb_address_free")
            frames()
            assert started == [("set", "1.0.12"), ("check", "1.0.12")]
            # what the dialog shows: a check's outcome, an error, a running change
            for free in (True, False):
                plugin._usb_address_free = ("1.0.12", free)
                frames()
            plugin._usb_address_error = "The individual address could not be set: x"
            frames()
            plugin._usb_address_busy = True
            frames()
            plugin._usb_address_busy = False
            plugin._usb_target = UsbTarget(0x0681, 0x0014, "", "OCI700")
            frames()
            # a reason it can't start is shown in the dialog
            plugin.set_usb_interface_address = (
                ConnectionPlugin.set_usb_interface_address.__get__(plugin)
            )
            plugin._usb_address_text = "nonsense"
            edited.append(True)
            frames()
            assert "is not an individual address" in plugin._usb_address_error
            clicks.button = "Close"
            frames()
            assert not plugin._usb_settings_open
            frames()
            assert not plugin._usb_settings_shown
            # the connection no longer goes through a USB interface: it closes
            plugin.open_usb_settings()
            frames()
            plugin._usb_target = None
            frames()
            assert not plugin._usb_settings_open and not plugin._usb_settings_shown
        finally:
            imgui.destroy_context(ctx)
            await plugin._disconnect_async()

    def test_the_menu_entry_opens_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plugin = _plugin()
        plugin._state = ConnectionState.CONNECTED
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._gateway_info = None
        plugin._progmode_open = False
        plugin.open_settings = None
        plugin.offered_transports = lambda: []
        ctx = imgui.create_context()
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        clicks = Clicks(monkeypatch, plugin_module)
        try:
            clicks.items.add("USB Interface Settings…")
            imgui.new_frame()
            imgui.begin("menu")
            plugin.render_menu()
            imgui.end()
            imgui.end_frame()
            assert plugin._usb_settings_open
        finally:
            imgui.destroy_context(ctx)


class TestBusmonitor:
    async def test_frames_and_link_state(self, fake_hid: FakeHIDModule) -> None:
        frames: list[bytes] = []
        states: list[bool] = []
        monitor = UsbBusmonitorConnection(
            UsbTarget.of(_DEVICE), on_frame=frames.append, on_state=states.append
        )
        assert not monitor.connected
        await monitor.connect()
        assert monitor.connected
        assert monitor.individual_address == IndividualAddress("1.0.2")
        assert fake_hid.interface.properties[(8, 52)] == b"\x01"
        busmon = bytes.fromhex("2b0703010304020300bce0102f0012030080346a")
        fake_hid.interface.send_cemi(busmon)
        await _until(lambda: frames == [busmon])
        fake_hid.interface.unplugged.set()
        await _until(lambda: states == [False])
        fake_hid.interface.unplugged.clear()
        await _until(lambda: states == [False, True], timeout=6.0)
        await monitor.disconnect()
        assert not monitor.connected

    async def test_without_bus(self, fake_hid: FakeHIDModule) -> None:
        fake_hid.interface.features[DeviceFeature.BUS_CONNECTION_STATUS] = b"\x00"
        states: list[bool] = []
        monitor = UsbBusmonitorConnection(
            UsbTarget.of(_DEVICE), on_frame=lambda _f: None, on_state=states.append
        )
        await monitor.connect()
        try:
            # open, but the link is down until the interface reports the bus
            assert monitor.connected
            assert states == [False]
            fake_hid.interface.send_frame(
                device_feature_frame(
                    DeviceFeatureService.INFO,
                    DeviceFeature.BUS_CONNECTION_STATUS,
                    b"\x01",
                )
            )
            await _until(lambda: states == [False, True])
        finally:
            await monitor.disconnect()

    async def test_without_state_callback(self, fake_hid: FakeHIDModule) -> None:
        from xknx.core.connection_state import XknxConnectionState

        monitor = UsbBusmonitorConnection(
            UsbTarget.of(_DEVICE), on_frame=lambda _f: None
        )
        await monitor.connect()
        # nobody to tell
        monitor._state_changed(XknxConnectionState.DISCONNECTED)
        await monitor.disconnect()
        assert not monitor.connected


async def test_search(fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch) -> None:
    assert [d.path for d in await search_usb_interfaces()] == [b"usb-path-1"]
    assert find_usb_interfaces()
    errors: list[BaseException] = []
    import sys

    monkeypatch.setitem(sys.modules, "hid", None)
    assert await search_usb_interfaces(errors.append) == []
    assert len(errors) == 1


async def test_search_offers_any_interface_of_a_knx_usb_vendor(
    fake_hid: FakeHIDModule,
) -> None:
    def device(path: bytes, vendor_id: int, product_id: int) -> dict[str, Any]:
        return {
            **SPACELOGIC_ENUMERATION,
            "path": path,
            "vendor_id": vendor_id,
            "product_id": product_id,
        }

    fake_hid.enumeration += [
        device(b"weinzierl-unlisted", 0x0E77, 0x4711),
        device(b"insta-known", 0x135E, 0x0020),
        device(b"other-vendor", 0x1234, 0x0001),
    ]
    # the known ones first, each once; another vendor's HID device is no interface
    assert [d.path for d in await search_usb_interfaces()] == [
        b"usb-path-1",
        b"insta-known",
        b"weinzierl-unlisted",
    ]


def test_the_service_checks_an_individual_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib
    from unittest.mock import MagicMock

    from editor_gui.plugins.connection.service import ConnectionService

    # the module: its package exports the function under the module's name
    procedure = importlib.import_module(
        "xknx.management.procedures.network.nm_individual_address_check"
    )

    service = ConnectionService()
    service.set_logger(MagicMock())
    # without a connection
    assert service.individual_address_occupied("1.1.9") is None
    checked: list[tuple[Any, str]] = []

    async def check(xknx: Any, address: str) -> bool:
        checked.append((xknx, address))
        return True

    def run_now(coro: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(asyncio.run(coro))
        return future

    monkeypatch.setattr(procedure, "nm_individual_address_check", check)
    xknx = MagicMock()
    service._xknx = xknx  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(service, "run_async", run_now)
    future = service.individual_address_occupied("1.1.9")
    assert future is not None and future.result() is True
    assert checked == [(xknx, "1.1.9")]
