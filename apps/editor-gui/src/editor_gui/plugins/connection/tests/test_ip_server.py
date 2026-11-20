"""The KNXnet/IP server in front of a USB connection ("USB to IP bridge"): its settings kept and
checked, started and stopped with the connection, refused with local device access, a tunnel's
telegram on the bus and once in the Group Monitor, and its section of the USB Interface dialog."""

from __future__ import annotations

import asyncio
import re
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui
from xknx.cemi.const import CEMIMessageCode
from xknx.knxip import ConnectResponse, TunnellingAck, TunnellingRequest
from xknx.telegram import GroupAddress, IndividualAddress

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.ip_server import (
    IpServerSettings,
    OpenTunnel,
    check_tunnel_addresses,
    parse_tunnel_addresses,
    suggest_tunnel_addresses,
)
from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionState
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.connection.strings import S
from editor_gui.plugins.connection.usb import UsbTarget
from editor_gui.plugins.monitor.service import MonitorKind, MonitorService
from editor_gui.plugins.project.tests._clicks import Clicks

from .conftest import FakeHIDModule
from .server.conftest import RawClient, l_data
from .test_usb_connection import _DEVICE, _plugin

_INTERFACE = IndividualAddress("1.0.2")


def _nobody(_address: str) -> str | None:
    return None


def _free_port() -> int:
    """A port free for TCP and UDP on 127.0.0.1."""
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                try:
                    udp.bind(("127.0.0.1", port))
                except OSError:
                    continue
        return port
    raise OSError("no free port")


def _settings(**changes: Any) -> IpServerSettings:
    values: dict[str, Any] = {
        "enabled": True,
        "port": _free_port(),
        "listen": "localhost",
        "discovery": False,
        "tunnel_addresses": ("1.0.240", "1.0.241"),
    }
    values.update(changes)
    return IpServerSettings(**values)


class TestSettings:
    def test_off_by_default_and_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert not IpServerSettings().enabled
        settings = IpServerSettings(
            enabled=True,
            port=3700,
            listen="localhost",
            discovery=False,
            tcp=False,
            tunnel_addresses=("1.0.240",),
            name="Bridge",
        )
        assert IpServerSettings.from_settings(settings.to_settings()) == settings
        assert settings.local_ip == "127.0.0.1"
        assert IpServerSettings().local_ip == "0.0.0.0"
        # unreadable values get their defaults
        assert IpServerSettings.from_settings(None) == IpServerSettings()
        assert IpServerSettings.from_settings(
            {
                "enabled": "yes",
                "port": 70000,
                "listen": "x",
                "tunnel_addresses": "1.0.1",
            }
        ) == IpServerSettings(enabled=False)
        assert IpServerSettings.from_settings(
            {"tunnel_addresses": ["1.0.240", 7], "name": " "}
        ) == IpServerSettings(tunnel_addresses=("1.0.240",))
        # in the connection settings
        monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
        plugin = _plugin()
        plugin._ip_settings = settings
        ConnectionPlugin._save_current_settings(plugin)
        plugin._ip_settings = IpServerSettings()
        plugin._load_saved_settings()
        assert plugin.ip_server_settings == settings

    def test_tunnel_addresses_are_checked(self) -> None:
        def project(address: str) -> str | None:
            return "Actuator" if address == "1.0.9" else None

        assert parse_tunnel_addresses(" 1.0.240, 1.0.241  1.0.242") == [
            "1.0.240",
            "1.0.241",
            "1.0.242",
        ]
        assert check_tunnel_addresses(["1.0.240"], _INTERFACE, project) == [
            IndividualAddress("1.0.240")
        ]
        refused = {
            (): "At least one tunnel address is needed.",
            ("x",): "x is not an individual address",
            ("1.0.240", "1.0.240"): "is given twice",
            ("1.0.2",): "1.0.2 is the individual address of the interface itself.",
            ("1.1.240",): "1.1.240 is not on the line of the interface (1.0).",
            ("1.0.9",): "1.0.9 is the individual address of the device Actuator.",
            ("1.0.0",): "1.0.0 is not an individual address",
        }
        for addresses, reason in refused.items():
            with pytest.raises(ValueError, match=re.escape(reason)):
                check_tunnel_addresses(list(addresses), _INTERFACE, project)
        # the line is not checked while the interface's address is unknown
        assert check_tunnel_addresses(["2.3.4"], None, project)

    def test_suggested_addresses(self) -> None:
        def project(address: str) -> str | None:
            return "Actuator" if address == "1.0.250" else None

        assert suggest_tunnel_addresses(_INTERFACE, project) == ["1.0.249", "1.0.248"]
        assert suggest_tunnel_addresses(IndividualAddress("1.0.249"), project) == [
            "1.0.248",
            "1.0.247",
        ]
        assert suggest_tunnel_addresses(None, project) == []


async def _connected(
    fake_hid: FakeHIDModule, settings: IpServerSettings, *, monitor: bool = False
) -> tuple[Any, MonitorService | None]:
    """A plugin connected through the simulated interface with the KNXnet/IP server
    ``settings``; with ``monitor`` a real connection service with the monitor's."""
    plugin = _plugin()
    service: MonitorService | None = None
    if monitor:
        connection = ConnectionService()
        connection.set_logger(MagicMock())
        service = MonitorService(connection)
        connection.add_raw_cemi_listener(service.on_raw_cemi)
        connection.add_sent_cemi_listener(service.on_sent_cemi)
        plugin._api.connection = connection
    plugin._ip_settings = settings
    plugin._usb_target = UsbTarget.of(_DEVICE)
    plugin._ensure_async_loop = asyncio.get_running_loop
    plugin._save_current_settings = lambda: None
    await plugin._connect_async()
    assert plugin._state is ConnectionState.CONNECTED, plugin._error_message
    return plugin, service


async def _tunnel(plugin: Any) -> RawClient:
    client = await RawClient.open(SimpleNamespace(port=plugin.ip_server_port))  # type: ignore[arg-type]
    response = await client.connect()
    assert isinstance(response, ConnectResponse)
    assert response.status_code.value == 0
    return client


class TestServer:
    async def test_started_and_stopped_with_the_connection(
        self, fake_hid: FakeHIDModule
    ) -> None:
        plugin, _ = await _connected(fake_hid, _settings())
        try:
            assert plugin.ip_server_running and plugin.ip_server_error is None
            assert plugin.ip_server_status_text() == (
                f"KNXnet/IP server on port {plugin.ip_server_port}, 0 tunnels"
            )
            client = await _tunnel(plugin)
            try:
                assert plugin.ip_server_tunnels() == [
                    OpenTunnel(
                        channel=plugin.ip_server_tunnels()[0].channel,
                        client=f"127.0.0.1:{client.hpai.port}",
                        protocol="UDP",
                        address="1.0.240",
                    )
                ]
                assert plugin.ip_server_status_text().endswith(", 1 tunnel")
            finally:
                client.close()
        finally:
            await plugin._disconnect_async()
        assert not plugin.ip_server_running and plugin.ip_server_port is None

    async def test_a_tunnels_telegram_is_in_the_group_monitor_once(
        self, fake_hid: FakeHIDModule
    ) -> None:
        plugin, monitor = await _connected(fake_hid, _settings(), monitor=True)
        assert monitor is not None
        try:
            client = await _tunnel(plugin)
            try:
                channel = plugin.ip_server_tunnels()[0].channel
                client.send(
                    TunnellingRequest(
                        communication_channel_id=channel,
                        sequence_counter=0,
                        raw_cemi=l_data(
                            CEMIMessageCode.L_DATA_REQ,
                            "0.0.0",
                            GroupAddress("1/2/3"),
                        ),
                    )
                )
                # the interface sends it to the bus with the tunnel's address
                sent = bytes.fromhex("1100bce010f00a03010081")
                async with asyncio.timeout(3):
                    while sent not in fake_hid.interface.written_cemi():
                        await asyncio.sleep(0.01)
                # acknowledged, and the confirmation goes back to the tunnel
                await client.receive(TunnellingAck)
                await client.receive_cemi()
                await asyncio.sleep(0.2)
            finally:
                client.close()
        finally:
            await plugin._disconnect_async()
        group = [
            record
            for record in monitor.channels[MonitorKind.GROUP].log.snapshot()
            if record.is_telegram
        ]
        assert [record.raw[0] for record in group] == [0x2E]
        assert group[0].raw[4:6] == bytes.fromhex("10f0")

    async def test_not_with_local_device_access(self, fake_hid: FakeHIDModule) -> None:
        plugin = _plugin()
        plugin._usb_local_device = True
        plugin._ip_settings = _settings()
        plugin._usb_target = UsbTarget.of(_DEVICE)
        await plugin._connect_async()
        try:
            assert not plugin.ip_server_running
            assert plugin.ip_server_error == S.IP_SERVER_NOT_LOCAL
            assert plugin.ip_server_refusal() == S.IP_SERVER_NOT_LOCAL
        finally:
            await plugin._disconnect_async()
        plugin._usb_target = None
        assert plugin.ip_server_refusal() == S.IP_SERVER_NEEDS_USB

    async def test_stopped_for_the_bus_monitor(self, fake_hid: FakeHIDModule) -> None:
        plugin, _ = await _connected(fake_hid, _settings())
        states: list[Any] = []
        plugin._api.connection.set_busmonitor_state = lambda state, error=None: (
            states.append(state)
        )
        plugin._api.connection.dispatch_busmonitor_cemi = lambda raw: None
        plugin._api.connection.busmonitor_error_detail = None
        plugin._api.connection.busmonitor_link = None
        assert plugin.ip_server_running
        await plugin._start_busmonitor_async()
        try:
            # the connection is closed for the bus monitor, and the server with it
            assert not plugin.ip_server_running
        finally:
            if plugin._busmonitor is not None:
                await plugin._busmonitor.disconnect()

    async def test_bad_addresses_and_a_port_in_use(
        self, fake_hid: FakeHIDModule
    ) -> None:
        plugin, _ = await _connected(fake_hid, _settings(tunnel_addresses=("1.0.2",)))
        try:
            assert not plugin.ip_server_running
            assert plugin.ip_server_error == (
                "1.0.2 is the individual address of the interface itself."
            )
        finally:
            await plugin._disconnect_async()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        try:
            plugin, _ = await _connected(
                fake_hid, _settings(port=blocker.getsockname()[1])
            )
            try:
                assert not plugin.ip_server_running
                assert "could not start" in (plugin.ip_server_error or "")
            finally:
                await plugin._disconnect_async()
        finally:
            blocker.close()

    async def test_configured_while_connected(self, fake_hid: FakeHIDModule) -> None:
        plugin, _ = await _connected(fake_hid, IpServerSettings())
        try:
            assert not plugin.ip_server_running and plugin.ip_server_error is None
            with pytest.raises(ValueError, match="At least one tunnel address"):
                plugin.configure_ip_server(_settings(tunnel_addresses=()))
            with pytest.raises(ValueError, match="70000 is not a port"):
                plugin.configure_ip_server(_settings(port=70000))
            assert not plugin.ip_server_settings.enabled
            settings = _settings()
            future = plugin.configure_ip_server(settings)
            assert plugin.ip_server_busy
            with pytest.raises(ValueError, match="being started or stopped"):
                plugin.configure_ip_server(settings)
            await asyncio.wrap_future(future)
            assert plugin.ip_server_running and plugin.ip_server_settings == settings
            # the same again: nothing to do
            assert plugin.configure_ip_server(settings).done()
            await asyncio.wrap_future(
                plugin.configure_ip_server(IpServerSettings(port=settings.port))
            )
            assert not plugin.ip_server_running and plugin.ip_server_error is None
        finally:
            await plugin._disconnect_async()
        # without a connection it is only kept
        assert plugin.configure_ip_server(_settings()).done()
        assert plugin.ip_server_settings.enabled and not plugin.ip_server_running


def _imgui() -> Any:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 900)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    return ctx


class TestDialog:
    async def test_the_section_applies_and_shows_the_tunnels(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin, _ = await _connected(fake_hid, IpServerSettings())
        ctx = _imgui()
        clicks = Clicks(monkeypatch, plugin_module)
        monkeypatch.setattr(plugin_module, "collapsing_header", lambda label: True)
        texts: list[str] = []
        for name in ("text", "text_wrapped", "text_disabled"):
            real = getattr(imgui, name)

            def recorded(text: str, *args: Any, _real: Any = real) -> None:
                texts.append(text)
                _real(text, *args)

            monkeypatch.setattr(imgui, name, recorded)
        real_colored = imgui.text_colored
        monkeypatch.setattr(
            imgui,
            "text_colored",
            lambda color, text: (texts.append(text), real_colored(color, text)),
        )
        real_checkbox = imgui.checkbox
        ticked: list[str] = []

        def checkbox(label: str, value: bool) -> tuple[bool, bool]:
            if label in ticked:
                ticked.remove(label)
                return True, not value
            return real_checkbox(label, value)

        monkeypatch.setattr(imgui, "checkbox", checkbox)
        plugin._select_open = plugin._select_shown = plugin._progmode_open = False

        def frames(count: int = 2) -> None:
            for _ in range(count):
                imgui.new_frame()
                plugin.render_window()
                imgui.end_frame()

        try:
            plugin.open_usb_interface()
            # free addresses of the interface's line are suggested
            assert plugin._ip_addresses_text == "1.0.250 1.0.249"
            frames()
            assert S.IP_SERVER_STOPPED in texts
            # a port that is no number
            plugin._ip_port_text = "x"
            texts.clear()
            frames()
            assert "x is not a port (1 to 65535)." in texts
            plugin._ip_port_text = str(_free_port())
            plugin._ip_addresses_text = "1.0.2"
            ticked.append("##ip_enabled")
            frames()
            ticked.append(f"{S.IP_SERVER_DISCOVERY}##ip_discovery")
            frames()
            clicks.items.add(f"{S.IP_SERVER_APPLY}##ip_apply")
            frames()
            assert plugin._ip_error == (
                "1.0.2 is the individual address of the interface itself."
            )
            clicks.items.add(f"{S.IP_SERVER_SUGGEST}##ip_suggest")
            frames()
            assert plugin._ip_addresses_text == "1.0.250 1.0.249"
            clicks.combos.add("##ip_listen")
            clicks.items.add(f"{S.IP_SERVER_LISTEN_LOCAL}##ip_listen_localhost")
            frames()
            clicks.items.add(f"{S.IP_SERVER_APPLY}##ip_apply")
            frames()
            async with asyncio.timeout(5):
                while not plugin.ip_server_running or plugin.ip_server_busy:
                    await asyncio.sleep(0.01)
            settings = plugin.ip_server_settings
            assert settings.enabled and settings.listen == "localhost"
            assert not settings.discovery
            assert settings.tunnel_addresses == ("1.0.250", "1.0.249")
            client = await _tunnel(plugin)
            try:
                texts.clear()
                frames()
                assert plugin.ip_server_status_text() in texts
                assert "1.0.250" in texts and "UDP" in texts
                # the Gateway menu tells it too
                plugin.open_settings = None
                plugin.offered_transports = lambda: []
                texts.clear()
                imgui.new_frame()
                imgui.begin("menu")
                plugin.render_menu()
                imgui.end()
                imgui.end_frame()
                assert plugin.ip_server_status_text() in texts
            finally:
                client.close()
        finally:
            imgui.destroy_context(ctx)
            await plugin._disconnect_async()
