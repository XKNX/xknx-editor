"""Connect at Startup (Settings > Connection) and ``--no-connect``: turned off, the start searches
for no interface and connects to none, so the editor can be opened safely next to a live
installation and connected only when the user chooses."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.io.connection import ConnectionType

from editor_gui import main
from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import (
    _DEFAULT_CONTROLLER_IP,  # pyright: ignore[reportPrivateUsage]
    ConnectionPlugin,
    ConnectionState,
)


class _Logger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.infos.append((event, kwargs))


def _plugin() -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._usb_target = None
    plugin._usb_addresses = {}
    plugin._controller_ip = _DEFAULT_CONTROLLER_IP
    plugin._multicast_group = "224.0.23.12"
    plugin._connection_type = ConnectionType.TUNNELING
    plugin._preferred_gateway_ip = None
    plugin._connect_at_startup = True
    plugin.skip_startup_connect = False
    plugin._state = ConnectionState.DISCONNECTED
    plugin._log = _Logger()
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(use_serial_number=True, busmonitor_active=False)
    )
    plugin.scans = []
    plugin.scan = lambda auto_connect=False: plugin.scans.append(auto_connect)
    return plugin


def test_the_start_connects_by_default() -> None:
    plugin = _plugin()
    plugin.autostart()
    assert plugin.scans == [True]


def test_turned_off_the_start_searches_and_connects_nothing() -> None:
    plugin = _plugin()
    plugin._connect_at_startup = False
    plugin.autostart()
    assert plugin.scans == []
    assert plugin._log.infos == [("no connection at startup", {"reason": "setting"})]


def test_no_connect_on_the_command_line_wins_over_the_setting() -> None:
    plugin = _plugin()
    plugin.skip_startup_connect = True
    plugin.autostart()
    assert plugin.scans == []
    assert plugin._log.infos == [
        ("no connection at startup", {"reason": "command line"})
    ]


def test_the_setting_is_changed_kept_and_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _plugin()
    setting = {s.key: s for s in plugin.settings()}["connect_at_startup"]
    assert setting.kind == "bool"
    assert setting.get() is True
    setting.set(False)
    assert setting.get() is False
    assert plugin_module.load_settings("connection")["connect_at_startup"] is False
    # A later start reads it back.
    later = _plugin()
    ConnectionPlugin._load_saved_settings(later)  # pyright: ignore[reportPrivateUsage]
    assert later._connect_at_startup is False


def test_settings_saved_before_the_setting_existed_connect_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin_module.save_settings("connection", {"controller_ip": "192.168.0.5"})
    plugin = _plugin()
    plugin._connect_at_startup = False
    ConnectionPlugin._load_saved_settings(plugin)  # pyright: ignore[reportPrivateUsage]
    assert plugin._connect_at_startup is True


def test_no_connect_is_read_from_the_command_line() -> None:
    assert main.no_connect(["editor", "--no-connect", "Haus.xknx"])
    assert not main.no_connect(["editor", "Haus.xknx"])
    # The program's own name is not an option.
    assert not main.no_connect(["--no-connect"])


def test_the_start_hands_no_connect_to_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "load_settings", lambda _name: {})
    seen: list[bool] = []
    connection = SimpleNamespace(skip_startup_connect=False)
    connection.autostart = lambda: seen.append(connection.skip_startup_connect)
    stub = SimpleNamespace(
        _connection_plugin=connection, no_connect=True, startup_file=None
    )
    main.KnxGuiApp._start_session(stub)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert seen == [True]
