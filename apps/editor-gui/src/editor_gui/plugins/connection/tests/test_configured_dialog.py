"""The Configured Connections dialog's draft and the plugin around it (#351): new, delete, edit,
the project's connection, import, OK and Cancel; connecting through one, its NAT and protocol, and
the connections kept with the settings."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.io.connection import ConnectionType

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.configured import ConfiguredConnection, export_xml
from editor_gui.plugins.connection.configured_ui import (
    ConfiguredConnectionsDialog,
    connection_label,
)
from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionState

_HOME = ConfiguredConnection("Home", "192.168.0.221")
_OTHER = ConfiguredConnection("Brücktal", "192.168.97.24", 3700, "Udp", use_nat=True)


def _dialog() -> tuple[ConfiguredConnectionsDialog, list[Any]]:
    applied: list[Any] = []
    dialog = ConfiguredConnectionsDialog(lambda *args: applied.append(args))
    return dialog, applied


def test_new_edit_and_delete() -> None:
    dialog, applied = _dialog()
    dialog.open([_HOME], None, project_open=False)
    assert dialog.selected == 0
    dialog.new()
    assert dialog.selected == 1
    assert dialog.connections[1] == ConfiguredConnection("IP Tunneling", "")
    # no address yet: OK stays off
    assert not dialog.valid()
    dialog.update(host="192.168.0.30", port=3672, protocol="Tcp")
    dialog.new()
    assert dialog.connections[2].name == "IP Tunneling (1)"
    dialog.delete()
    assert dialog.selected == 1
    assert dialog.valid()
    dialog.apply()
    assert not dialog.is_open
    ((connections, project, changed),) = applied
    assert connections == [
        _HOME,
        ConfiguredConnection("IP Tunneling", "192.168.0.30", 3672, "Tcp"),
    ]
    assert (project, changed) == (None, False)


def test_cancel_takes_nothing_over() -> None:
    dialog, applied = _dialog()
    dialog.open([_HOME], None, project_open=True)
    dialog.delete()
    dialog.cancel()
    assert applied == []
    assert not dialog.is_open


def test_the_project_connection_is_marked_and_toggled() -> None:
    dialog, applied = _dialog()
    # The project's connection is the same interface as the first (protocol aside).
    dialog.open(
        [_HOME, _OTHER],
        ConfiguredConnection("", "192.168.0.221", protocol="Tcp"),
        project_open=True,
    )
    assert dialog.project_connection == _HOME
    dialog.select(1)
    dialog.toggle_project()
    assert dialog.project_connection == _OTHER
    # again: none
    dialog.toggle_project()
    assert dialog.project_connection is None
    dialog.toggle_project()
    dialog.apply()
    ((_connections, project, changed),) = applied
    assert (project, changed) == (_OTHER, True)


def test_a_project_connection_none_of_them_is_listed() -> None:
    dialog, applied = _dialog()
    own = ConfiguredConnection("Project", "10.0.0.5")
    dialog.open([_HOME], own, project_open=True)
    assert dialog.connections == [_HOME, own]
    assert dialog.project_connection == own
    dialog.apply()
    ((connections, project, changed),) = applied
    assert (connections, project, changed) == ([_HOME, own], own, False)


def test_without_a_project_nothing_is_the_project_connection() -> None:
    dialog, _applied = _dialog()
    dialog.open([_HOME], _HOME, project_open=False)
    assert dialog.project_connection is None
    assert not dialog.can_be_project_connection()
    dialog.toggle_project()
    assert dialog.project_connection is None


def test_import_keeps_the_marking() -> None:
    dialog, _applied = _dialog()
    dialog.open([_HOME], _HOME, project_open=True)
    assert dialog.import_data(export_xml([_HOME, _OTHER])) == 1
    assert dialog.connections == [_HOME, _OTHER]
    assert dialog.project_connection == _HOME
    with pytest.raises(ValueError):
        dialog.import_data(b"<broken")


def test_labels() -> None:
    assert connection_label(_HOME) == "Home (192.168.0.221)"
    assert connection_label(_OTHER) == "Brücktal (192.168.97.24:3700 UDP)"
    assert connection_label(ConfiguredConnection("", "10.0.0.1")) == "10.0.0.1"


class _Project:
    def __init__(self, bus: str | None = None) -> None:
        self.is_open = True
        self.bus = bus
        self.set: list[str] = []

    def backbone_bus_connection(self) -> tuple[str, str] | None:
        return (self.bus, "") if self.bus is not None else None

    def set_backbone_bus_connection(self, parameter: str) -> None:
        self.set.append(parameter)
        self.bus = parameter or None


def _plugin(project: _Project | None = None) -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._api = SimpleNamespace(project=project or _Project())
    plugin._configured = ()
    plugin._gateways = []
    plugin._state = ConnectionState.DISCONNECTED
    plugin._log = SimpleNamespace(info=lambda *_a, **_k: None)
    plugin.saved = 0

    def save() -> None:
        plugin.saved += 1

    plugin._save_current_settings = save
    return plugin


def test_the_dialog_sets_the_project_connection_on_ok() -> None:
    project = _Project(_HOME.to_parameter())
    plugin = _plugin(project)
    plugin._configured = (_HOME, _OTHER)
    dialog = ConfiguredConnectionsDialog(plugin._apply_configured)
    plugin._configured_dialog = dialog
    plugin.open_configured_connections()
    assert dialog.project_connection == _HOME
    dialog.select(1)
    dialog.toggle_project()
    dialog.apply()
    assert project.set == [_OTHER.to_parameter()]
    assert plugin.saved == 1
    # Unchanged: the project's Bus Connection is left as it is.
    plugin.open_configured_connections()
    dialog.apply()
    assert project.set == [_OTHER.to_parameter()]


def test_nat_and_protocol_of_a_configured_connection() -> None:
    plugin = _plugin(_Project(None))
    plugin._configured = (_HOME, _OTHER)
    assert plugin._uses_nat("192.168.97.24", 3700)
    assert not plugin._uses_nat("192.168.97.24", 3671)
    assert not plugin._uses_nat("192.168.0.221", 3671)
    assert plugin.tunnel_type_of(_OTHER) is ConnectionType.TUNNELING
    assert plugin.tunnel_type_of(ConfiguredConnection("", "h", protocol="Tcp")) is (
        ConnectionType.TUNNELING_TCP
    )
    # Automatic: TCP when a scanned gateway there offers it.
    plugin._gateways = [
        SimpleNamespace(
            ip_addr="192.168.0.221", port=3671, supports_tunnelling_tcp=True
        )
    ]
    assert plugin.tunnel_type_of(_HOME) is ConnectionType.TUNNELING_TCP
    assert plugin.tunnel_type_of(ConfiguredConnection("", "10.0.0.9")) is (
        ConnectionType.TUNNELING
    )
    # NAT of the project's connection too.
    plugin._configured = ()
    plugin._api.project.bus = _OTHER.to_parameter()
    assert plugin._uses_nat("192.168.97.24", 3700)


def test_connect_through_a_configured_connection() -> None:
    plugin = _plugin()
    chosen: list[Any] = []
    plugin.connect = lambda **kwargs: chosen.append((plugin._controller_ip, kwargs))
    plugin.connect_to_configured(_OTHER)
    assert chosen == [
        ("192.168.97.24:3700", {"connection_type": ConnectionType.TUNNELING})
    ]
    plugin._state = ConnectionState.CONNECTED
    plugin.connect_to_configured(_HOME)
    assert len(chosen) == 1


def test_add_needs_an_address() -> None:
    plugin = _plugin()
    assert plugin.add_configured_connection(
        ConfiguredConnection("", "10.0.0.1")
    ).name == ("IP Tunneling")
    with pytest.raises(ValueError):
        plugin.add_configured_connection(ConfiguredConnection("", "255.255.255.255"))


def test_the_connections_are_kept_with_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.connection.tests.test_connection_settings import (
        _plugin as settings_plugin,  # pyright: ignore[reportPrivateUsage]
    )

    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = settings_plugin()
    plugin._configured = (_HOME, _OTHER)
    plugin._save_current_settings()
    stored = plugin_module.load_settings("connection")
    assert stored["configured_connections"] == [
        _HOME.to_parameter(),
        _OTHER.to_parameter(),
    ]
    again = settings_plugin()
    ConnectionPlugin._load_saved_settings(again)  # pyright: ignore[reportPrivateUsage]
    assert again._configured == (_HOME, _OTHER)
    settings = {s.key: s for s in plugin.settings()}
    assert settings["configured_connections"].get() == stored["configured_connections"]
