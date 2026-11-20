"""Local device access: a USB connection that reaches only the interface's own device through its
cEMI Transport Layer. Chosen and kept, connected, shown, switched on the open connection; the
operations on other devices, group telegrams, broadcasts and the bus monitor refused before they
start; the individual address downloaded as the interface's own."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui
from xknx import XKNX
from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIErrorCode, CEMIMessageCode
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, tpci
from xknx.telegram.apci import DeviceDescriptorRead, GroupValueRead

import xknxeditor.download.commissioning as commissioning
from editor_gui import settings as app_settings
from editor_gui.device import Device
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionState
from editor_gui.plugins.connection.service import (
    BusmonitorState,
    ConnectionService,
    LinkInfo,
    LocalDevice,
)
from editor_gui.plugins.connection.strings import S
from editor_gui.plugins.connection.usb import UsbTarget
from editor_gui.plugins.project.tests._clicks import Clicks
from xknxeditor.download.scope import DownloadScope

from .conftest import FakeHIDModule
from .test_usb_connection import _DEVICE, _connected, _plugin, _property_writes

_ACTUATOR = Device(node_id=1, name="Actuator", app=None, individual_address="1.0.2")
_OTHER = Device(node_id=2, name="Sensor", app=None, individual_address="1.1.9")


def _service(local: str | None = "1.0.2") -> ConnectionService:
    """A service connected to the own device at ``local`` (None: its address unknown)."""
    service = ConnectionService()
    service.set_logger(MagicMock())
    service.set_connection(XKNX(), None)
    service.local_device = LocalDevice(local)
    return service


def _group_read() -> bytes:
    return CEMIFrame(
        code=CEMIMessageCode.L_DATA_REQ,
        data=CEMILData.init_from_telegram(
            Telegram(
                destination_address=GroupAddress("1/2/3"), payload=GroupValueRead()
            )
        ),
    ).to_knx()


class TestRefusals:
    """What the connection refuses while it reaches only the own device."""

    def test_nothing_is_refused_on_the_bus(self) -> None:
        service = ConnectionService()
        service.set_logger(MagicMock())
        service.set_connection(XKNX(), None)
        assert service.local_device_refusal("1.1.9") is None
        assert service.local_device_refusal(reaches="group") is None
        assert service.local_device_range_refusal("1.1.0", "1.1.255") is None
        # without a connection neither
        service.local_device = LocalDevice("1.0.2")
        service.set_connection(None, None)
        assert service.local_device is None
        assert service.local_device_refusal("1.1.9") is None

    def test_other_devices_groups_broadcasts_and_the_bus_monitor(self) -> None:
        service = _service()
        assert service.local_device_refusal("1.0.2", " 1.0.2") is None
        assert service.local_device_refusal("1.1.9") == (
            "Through local device access only the USB interface's own device 1.0.2 can be "
            "reached, not 1.1.9."
        )
        assert "group telegrams can't be sent" in (
            service.local_device_refusal(reaches="group") or ""
        )
        assert "broadcasts can't be sent" in (
            service.local_device_refusal(reaches="broadcast") or ""
        )
        assert "the bus monitor can't run" in (
            service.local_device_refusal(reaches="bus_monitor") or ""
        )

    def test_an_unknown_address_takes_any_device(self) -> None:
        service = _service(None)
        assert service.local_device_refusal("1.1.9") is None
        assert "(address unknown)" in (
            service.local_device_refusal(reaches="group") or ""
        )
        assert service.local_device_range_refusal("1.1.9", "1.1.9") is None
        assert service.local_device_range_refusal("1.1.9", "1.1.10") is not None

    def test_a_scan_only_of_the_own_device(self) -> None:
        service = _service()
        assert service.local_device_range_refusal("1.0.2", "1.0.2") is None
        assert service.local_device_range_refusal("1.0.0", "1.0.255") == (
            "Through local device access only the USB interface's own device 1.0.2 can be "
            "reached: scan from 1.0.2 to 1.0.2."
        )

    def test_a_refusal_is_told_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _service()
        assert service.local_device_refused("restart", "1.1.9")
        reason = service.take_refusal()
        assert reason is not None and "not 1.1.9" in reason
        assert service.take_refusal() is None
        assert not service.local_device_refused("restart", "1.0.2")
        assert service.take_refusal() is None
        # an old one is not told any more
        service.refuse("restart", "old")
        later = time.monotonic() + 60
        monkeypatch.setattr(time, "monotonic", lambda: later)
        assert service.take_refusal() is None

    def test_operations_on_other_devices_do_not_start(self) -> None:
        service = _service()
        refused = [
            service.program_device(_OTHER, DownloadScope.FULL),
            service.evaluate_device(_OTHER, DownloadScope.FULL),
            service.device_info(
                _OTHER, group_communication=False, resolve_application=lambda _: None
            ),
            service.restart_device(_OTHER),
            service.master_reset_device(_OTHER, 2),
            service.run_dali(_OTHER, lambda _c: cast(Any, None)),
            service.run_siemens_dali(_OTHER, lambda _c: cast(Any, None)),
            service.individual_address_occupied("1.1.9"),
        ]
        assert refused == [None] * len(refused)
        assert "not 1.1.9" in (service.take_refusal() or "")
        # and nothing holds the bus
        assert service.busy_operation is None

    def test_broadcasts_and_group_telegrams_are_not_sent(self) -> None:
        service = _service()
        assert service.scan_programming_mode() is None
        assert "broadcasts" in (service.take_refusal() or "")
        assert service.read_programming_mode_devices() is None
        assert service.assign_individual_address_by_serial(bytes(6), "1.0.3") is None
        assert "broadcasts" in (service.take_refusal() or "")
        assert service.send_cemi(_group_read()) is None
        assert "group telegrams" in (service.take_refusal() or "")
        assert service.send_group_telegram("1/2/3", GroupValueRead()) is None
        point_to_point = CEMIFrame(
            code=CEMIMessageCode.L_DATA_REQ,
            data=CEMILData.init_from_telegram(
                Telegram(
                    destination_address=IndividualAddress("1.1.9"),
                    tpci=tpci.TConnect(),
                )
            ),
        ).to_knx()
        assert service.send_cemi(point_to_point) is None
        assert "not 1.1.9" in (service.take_refusal() or "")

    def test_the_own_device_is_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _service()
        started: list[Any] = []

        def run_async(coro: Any) -> Future[Any]:
            started.append(coro)
            coro.close()
            future: Future[Any] = Future()
            future.set_result(None)
            return future

        monkeypatch.setattr(service, "run_async", run_async)
        assert service.restart_device(_ACTUATOR) is not None
        assert service.device_info(
            _ACTUATOR, group_communication=False, resolve_application=lambda _: None
        )
        assert len(started) == 2
        assert service.take_refusal() is None

    def test_the_reason_of_a_queued_operation(self) -> None:
        """An operation that waited for its connection fails with the reason."""
        service = _service()
        connector = SimpleNamespace(
            open=lambda: _done(None),
            release=lambda: None,
            opened_for_operations=lambda: True,
            submit=lambda fn: fn(),
        )
        service.operation_connector = cast(Any, connector)
        future = service.restart_device(_OTHER)
        assert future is not None
        with pytest.raises(Exception, match=r"not 1\.1\.9"):
            future.result(0)


def _done(value: Any) -> Future[Any]:
    future: Future[Any] = Future()
    future.set_result(value)
    return future


class TestIndividualAddress:
    """The individual address goes to the own device as the interface's own address."""

    def test_downloaded_through_the_writer(self) -> None:
        service = _service()
        written: list[str] = []
        pending: Future[None] = Future()

        def writer(address: str) -> Future[None]:
            written.append(address)
            return pending

        service.set_local_address_writer(writer)
        future = service.program_device(_OTHER, DownloadScope.INDIVIDUAL_ADDRESS)
        assert future is pending
        assert written == ["1.1.9"]
        # it holds the bus until written
        assert service.busy_operation == ("program", "1.1.9")
        pending.set_result(None)
        assert service.busy_operation is None
        # the overwrite and the assignment take the same way
        service.set_local_address_writer(lambda address: _done(written.append(address)))
        assert service.program_device(
            _ACTUATOR,
            DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS,
            current_address="1.0.2",
        )
        assert service.assign_individual_address("1.0.7") is not None
        assert written == ["1.1.9", "1.0.2", "1.0.7"]

    def test_a_writer_that_refuses(self) -> None:
        service = _service()

        def busy(address: str) -> Future[None]:
            raise ValueError(S.USB_ADDRESS_BUSY)

        service.set_local_address_writer(busy)
        assert service.program_device(_OTHER, DownloadScope.INDIVIDUAL_ADDRESS) is None
        assert service.take_refusal() == S.USB_ADDRESS_BUSY
        assert service.busy_operation is None
        # without a writer nothing starts
        service.set_local_address_writer(cast(Any, None))
        assert service.program_device(_OTHER, DownloadScope.INDIVIDUAL_ADDRESS) is None

    def test_on_the_bus_by_programming_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service()
        service.local_device = None
        written: list[str] = []
        downloaded: list[str] = []
        service.set_local_address_writer(lambda address: _done(written.append(address)))

        async def download(xknx: object, address: str, **_kwargs: object) -> None:
            downloaded.append(address)

        monkeypatch.setattr(commissioning, "download_individual_address", download)
        monkeypatch.setattr(service, "run_async", lambda coro: _done(asyncio.run(coro)))
        service.program_device(_OTHER, DownloadScope.INDIVIDUAL_ADDRESS)
        assert written == [] and downloaded == ["1.1.9"]


def _own_device(code: int, apdu: bytes) -> tuple[int, bytes] | None:
    """The interface's own device: answers a device descriptor read."""
    if code == 0x41 and apdu[:2] == b"\x03\x00":
        return 0x89, bytes.fromhex("034007b0")
    return None


class TestConnection:
    """Connected through the simulated interface with local device access."""

    async def test_connect_show_and_disconnect(self, fake_hid: FakeHIDModule) -> None:
        fake_hid.interface.own_device = _own_device
        plugin = _plugin()
        plugin._usb_local_device = True
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._ensure_async_loop = asyncio.get_running_loop
        await plugin._connect_async()
        try:
            assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
            assert plugin.local_device_connected and plugin.local_device_chosen
            assert _property_writes(fake_hid)[0] == "f600080134100106"
            assert plugin._api.connection.local_device == LocalDevice("1.0.2")
            assert plugin.shown_target == (
                "KNX-USB Interface (USB), local device access to 1.0.2"
            )
            link = plugin._link_info()
            assert link.local_device == "1.0.2"
            assert link.endpoint_name == "KNX-USB Interface (USB, local device 1.0.2)"
            # the own device is read through xknx's management
            xknx = plugin._xknx
            async with xknx.management.connection(IndividualAddress("1.0.2")) as device:
                response = await device.request(DeviceDescriptorRead(0))
            assert response.payload is not None
        finally:
            await plugin._disconnect_async()
        assert fake_hid.interface.properties[(8, 52)] == b"\xff"

    async def test_an_interface_without_it(self, fake_hid: FakeHIDModule) -> None:
        fake_hid.interface.transport_layer_refusal = (
            CEMIErrorCode.CEMI_ERROR_ILLEGAL_COMMAND
        )
        plugin = _plugin()
        plugin._report_failure = lambda error, startup: None
        plugin._usb_local_device = True
        plugin._usb_target = UsbTarget.of(_DEVICE)
        await plugin._connect_async()
        assert plugin._state is ConnectionState.ERROR
        assert "does not support the cEMI transport layer" in plugin._error_message

    async def test_switched_on_the_open_connection(
        self, fake_hid: FakeHIDModule
    ) -> None:
        plugin = await _connected(fake_hid)
        saved: list[bool] = []
        plugin._save_current_settings = lambda: saved.append(plugin._usb_local_device)
        try:
            assert not plugin.local_device_connected
            fake_hid.interface.written.clear()
            future = plugin.set_usb_local_device(True)
            assert plugin.usb_local_busy
            with pytest.raises(ValueError, match="being switched"):
                plugin.set_usb_interface_address("1.0.9")
            with pytest.raises(ValueError, match="being switched"):
                plugin.set_usb_local_device(False)
            await asyncio.wrap_future(future)
            assert not plugin.usb_local_busy
            assert plugin.local_device_connected
            assert _property_writes(fake_hid)[-1] == "f600080134100106"
            assert saved == [True]
            # the same again: nothing to do
            assert plugin.set_usb_local_device(True).done()
            await asyncio.wrap_future(plugin.set_usb_local_device(False))
            assert not plugin.local_device_connected
            assert saved == [True, False]
        finally:
            await plugin._disconnect_async()

    async def test_a_failed_switch_goes_back(self, fake_hid: FakeHIDModule) -> None:
        plugin = await _connected(fake_hid)
        reopened: list[str] = []
        plugin._run_async = lambda coro: (
            reopened.append(coro.cr_code.co_name),
            coro.close(),
        )
        fake_hid.interface.transport_layer_refusal = (
            CEMIErrorCode.CEMI_ERROR_TEMPORARILY_READ_ONLY
        )
        try:
            with pytest.raises(ConnectionError, match="could not be switched"):
                await asyncio.wrap_future(plugin.set_usb_local_device(True))
            assert "already has another transport layer connection" in (
                plugin._usb_local_error
            )
            assert not plugin._usb_local_device
            # opened again as it was
            assert reopened == ["_reopen"]
        finally:
            await plugin._disconnect_async()

    def test_chosen_while_disconnected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._save_current_settings = lambda: ConnectionPlugin._save_current_settings(
            plugin
        )
        assert plugin.set_usb_local_device(True).done()
        assert plugin.usb_local_device
        plugin._usb_local_device = False
        plugin._load_saved_settings()
        assert plugin.usb_local_device
        # the setting: read-only while a connection is open
        setting = next(s for s in plugin.settings() if s.key == "usb_local_device")
        assert setting.get() is True and setting.shown()
        setting.set(False)
        assert not plugin.usb_local_device
        plugin._state = ConnectionState.CONNECTED
        assert setting.read_only()
        setting.set(True)
        assert not plugin.usb_local_device

    def test_connect_to_usb_keeps_the_choice(self) -> None:
        plugin = _plugin()
        plugin._usb_devices = [_DEVICE]
        plugin._save_current_settings = lambda: None
        assert plugin.connect_to_usb_by_serial("", local_device=True)
        assert plugin._usb_local_device
        plugin._state = ConnectionState.DISCONNECTED
        plugin.connect_to_usb(_DEVICE)
        assert plugin._usb_local_device

    def test_no_bus_monitor(self) -> None:
        plugin = _plugin()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._usb_local_device = True
        states: list[tuple[BusmonitorState, str | None]] = []
        plugin._api.connection.busmonitor_active = False
        plugin._api.connection.set_busmonitor_state = lambda state, error=None: (
            states.append((state, error))
        )
        plugin.start_busmonitor()
        assert states == [(BusmonitorState.OFF, "local_device")]
        # the service tells why
        service = ConnectionService()
        service.set_busmonitor_controller(
            start=lambda: None,
            stop=lambda: None,
            supported=lambda: False,
            local_device=lambda: plugin.local_device_chosen,
        )
        assert service.busmonitor_unsupported_error() == "local_device"
        plugin._usb_local_device = False
        assert service.busmonitor_unsupported_error() == "routing"

    def test_link_info_on_the_bus(self) -> None:
        assert LinkInfo("X", "", 0, "Usb").endpoint_name == "X (USB)"
        assert (
            LinkInfo("X", "", 0, "Usb", local_device="").endpoint_name
            == "X (USB, local device)"
        )


def _imgui() -> Any:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    return ctx


class TestUi:
    async def test_the_dialog_switches_it(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = await _connected(fake_hid)
        switched: list[bool] = []
        plugin.set_usb_local_device = lambda on: switched.append(on)
        ctx = _imgui()
        real_checkbox = imgui.checkbox
        ticked: list[str] = []

        def checkbox(label: str, value: bool) -> tuple[bool, bool]:
            if label in ticked:
                ticked.remove(label)
                return True, not value
            return real_checkbox(label, value)

        monkeypatch.setattr(imgui, "checkbox", checkbox)
        texts: list[str] = []
        real_text = imgui.text_wrapped
        real_disabled = imgui.text_disabled
        monkeypatch.setattr(
            imgui,
            "text_wrapped",
            lambda text, *a: (texts.append(text), real_text(text)),
        )
        # the hint below the checkbox (control_hint)
        monkeypatch.setattr(
            imgui,
            "text_disabled",
            lambda text, *a: (texts.append(text), real_disabled(text)),
        )
        plugin._select_open = plugin._select_shown = plugin._progmode_open = False

        def frames(count: int = 2) -> None:
            for _ in range(count):
                imgui.new_frame()
                plugin.render_window()
                imgui.end_frame()

        try:
            plugin.open_usb_interface()
            frames()
            ticked.append("##usb_interface_local")
            frames()
            assert switched == [True]
            assert S.SETTING_USB_LOCAL_DEVICE_HINT in texts
            # switching and its failure are shown
            plugin._usb_local_busy = True
            frames()
            plugin._usb_local_busy = False
            plugin._usb_local_error = "The connection could not be switched: x"
            texts.clear()
            frames()
            assert "The connection could not be switched: x" in texts
            # a reason it can't start is shown too
            plugin.set_usb_local_device = ConnectionPlugin.set_usb_local_device.__get__(
                plugin
            )
            plugin._usb_address_checking = True
            ticked.append("##usb_interface_local")
            frames()
            assert plugin._usb_local_error == S.USB_LOCAL_DEVICE_BUSY
        finally:
            plugin._usb_address_checking = False
            imgui.destroy_context(ctx)
            await plugin._disconnect_async()

    async def test_no_address_check_with_local_device_access(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = _plugin()
        plugin._usb_local_device = True
        plugin._usb_target = UsbTarget.of(_DEVICE)
        plugin._ensure_async_loop = asyncio.get_running_loop
        await plugin._connect_async()
        ctx = _imgui()
        labels: list[str] = []
        texts: list[str] = []
        real_button, real_colored = imgui.button, imgui.text_colored

        def button(label: str, *args: Any) -> bool:
            labels.append(label)
            return bool(real_button(label, *args))

        def text_colored(color: Any, text: str) -> None:
            texts.append(text)
            real_colored(color, text)

        monkeypatch.setattr(imgui, "button", button)
        monkeypatch.setattr(imgui, "text_colored", text_colored)
        plugin._select_open = plugin._select_shown = plugin._progmode_open = False
        plugin.open_settings = None
        plugin.offered_transports = lambda: []
        try:
            plugin.open_usb_interface()
            for _ in range(2):
                imgui.new_frame()
                plugin.render_window()
                imgui.end_frame()
            assert "Apply##usb_address_apply" in labels
            assert "Address Free?##usb_address_free" not in labels
            # and the menu says so
            imgui.new_frame()
            imgui.begin("menu")
            plugin.render_menu()
            imgui.end()
            imgui.end_frame()
            assert S.USB_LOCAL_DEVICE_DETAILS in texts
        finally:
            imgui.destroy_context(ctx)
            await plugin._disconnect_async()

    def test_the_gateway_menu_does_not_offer_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The choice is in the connection settings and the USB Interface dialog; the menu's list
        of interfaces stays short."""
        plugin = _plugin()
        plugin._usb_devices = [_DEVICE]
        plugin._gateways = []
        plugin._scanning = True
        plugin._manual_probe_ip = plugin._controller_ip
        plugin._manual_tcp = None
        plugin._save_current_settings = lambda: None
        plugin.open_settings = None
        ctx = _imgui()
        real_checkbox = imgui.checkbox
        drawn: list[str] = []

        def checkbox(label: str, value: bool) -> tuple[bool, bool]:
            drawn.append(label)
            return real_checkbox(label, value)

        monkeypatch.setattr(imgui, "checkbox", checkbox)
        Clicks(monkeypatch, plugin_module)
        try:
            imgui.new_frame()
            imgui.begin("menu")
            plugin.render_menu()
            imgui.end()
            imgui.end_frame()
        finally:
            imgui.destroy_context(ctx)
        assert not any(S.USB_LOCAL_DEVICE_OPTION in label for label in drawn)
        assert not plugin.usb_local_device
