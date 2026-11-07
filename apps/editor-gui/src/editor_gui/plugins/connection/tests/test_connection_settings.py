"""The connection's settings in the Settings window: downloads by serial number, kept with the other
connection settings."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.io.connection import ConnectionType

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import (
    _DEFAULT_CONTROLLER_IP,  # pyright: ignore[reportPrivateUsage]
    ConnectionPlugin,
)


def _plugin() -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._usb_target = None
    plugin._usb_info = None
    plugin._usb_devices = []
    plugin._controller_ip = _DEFAULT_CONTROLLER_IP
    plugin._multicast_group = "224.0.23.12"
    plugin._connection_type = ConnectionType.TUNNELING
    plugin._preferred_gateway_ip = None
    plugin._api = SimpleNamespace(connection=SimpleNamespace(use_serial_number=True))
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
