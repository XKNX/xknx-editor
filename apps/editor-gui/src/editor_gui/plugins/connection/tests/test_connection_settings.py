"""The connection's settings in the Settings window: downloads by serial number, kept with the other
connection settings."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.io.connection import ConnectionType

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.ip_server import IpServer, IpServerSettings
from editor_gui.plugins.connection.plugin import (
    _DEFAULT_CONTROLLER_IP,  # pyright: ignore[reportPrivateUsage]
    ConnectionPlugin,
    ConnectionState,
)
from xknxeditor.download.timeouts import BusTimeouts, bus_timeouts, set_bus_timeouts


def _plugin() -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    # not connected: changed settings are kept for the next connect
    plugin._state = ConnectionState.DISCONNECTED
    plugin._usb_target = None
    plugin._usb_addresses = {}
    plugin._usb_local_device = False
    plugin._ip_settings = IpServerSettings()
    plugin._ip_server = IpServer()
    plugin._ip_busy = False
    plugin._ip_port_text = ""
    plugin._ip_addresses_text = ""
    plugin._ip_name_text = ""
    plugin._ip_draft = None
    plugin._ip_error = None
    plugin._usb_info = None
    plugin._usb_devices = []
    plugin._controller_ip = _DEFAULT_CONTROLLER_IP
    plugin._multicast_group = "224.0.23.12"
    plugin._connection_type = ConnectionType.TUNNELING
    plugin._preferred_gateway_ip = None
    plugin._connect_at_startup = True
    plugin._block_bus_writes_setting = False
    plugin._block_bus_writes_forced = False
    plugin._log = SimpleNamespace(info=lambda *_a, **_k: None)
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(use_serial_number=True, block_bus_writes=False)
    )
    return plugin


def test_the_serial_number_setting_is_changed_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _plugin()
    settings = {s.key: s for s in plugin.settings()}
    setting = settings["use_serial_number"]
    assert setting.kind == "bool"
    assert setting.get() is True
    setting.set(False)
    assert plugin._api.connection.use_serial_number is False
    stored = plugin_module.load_settings("connection")
    assert stored["serial_number_downloads"] is False
    assert setting.get() is False


def test_connection_type_and_gateway_ip_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _plugin()
    settings = {s.key: s for s in plugin.settings()}
    ctype = settings["connection_type"]
    assert ctype.kind == "choice"
    assert ctype.get() == "tunneling"
    assert {key for key, _ in ctype.choices()} == {
        "tunneling",
        "tunneling_tcp",
        "routing",
    }
    ctype.set("tunneling_tcp")
    assert plugin._connection_type == ConnectionType.TUNNELING_TCP
    assert ctype.get() == "tunneling_tcp"
    gateway_ip = settings["gateway_ip"]
    assert gateway_ip.kind == "text"
    gateway_ip.set("192.168.0.5")
    assert plugin._controller_ip == "192.168.0.5"
    assert plugin_module.load_settings("connection")["connection_type"] == (
        ConnectionType.TUNNELING_TCP.value
    )


@pytest.fixture
def default_timeouts() -> Iterator[None]:
    yield
    set_bus_timeouts(BusTimeouts())


@pytest.mark.usefixtures("default_timeouts")
def test_the_timeouts_are_changed_kept_and_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _plugin()
    settings = {s.key: s for s in plugin.settings()}
    timeouts = [
        settings[f"{name}_timeout"]
        for name in ("confirmation", "acknowledgement", "response")
    ]
    assert [(s.kind, s.bounds, s.get()) for s in timeouts] == [
        ("int", (1, 60), 3),
        ("int", (1, 60), 3),
        ("int", (1, 60), 6),
    ]
    timeouts[0].set(10)
    timeouts[1].set(5)
    # Beyond the bounds (an MCP client) it is the nearest bound.
    timeouts[2].set(600)
    assert bus_timeouts() == BusTimeouts(10, 5, 60)
    stored = plugin_module.load_settings("connection")
    assert (
        stored["confirmation_timeout"],
        stored["acknowledgement_timeout"],
        stored["response_timeout"],
    ) == (10, 5, 60)
    # A later start uses them again.
    set_bus_timeouts(BusTimeouts())
    ConnectionPlugin._load_saved_settings(_plugin())  # pyright: ignore[reportPrivateUsage]
    assert bus_timeouts() == BusTimeouts(10, 5, 60)


@pytest.mark.usefixtures("default_timeouts")
def test_missing_or_broken_timeouts_are_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin_module.save_settings(
        "connection", {"confirmation_timeout": "slow", "acknowledgement_timeout": 0}
    )
    set_bus_timeouts(BusTimeouts(9, 9, 9))
    ConnectionPlugin._load_saved_settings(_plugin())  # pyright: ignore[reportPrivateUsage]
    assert bus_timeouts() == BusTimeouts(confirmation=3, acknowledgement=1, response=6)
