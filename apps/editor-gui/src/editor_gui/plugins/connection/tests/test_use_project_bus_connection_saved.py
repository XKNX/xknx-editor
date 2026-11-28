"""Use the Project's Bus Connection (Settings > Connection) survives a restart: the plugin made at
start reads it back from the saved settings instead of resetting it afterwards."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin


def _started(api: Any) -> Any:
    return ConnectionPlugin(api)


@pytest.mark.parametrize("saved", [True, False])
def test_a_started_plugin_keeps_the_saved_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved: bool
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin_module.save_settings("connection", {"use_project_bus_connection": saved})
    plugin = _started(MagicMock())
    setting = {s.key: s for s in plugin.settings()}["use_project_bus_connection"]
    assert setting.get() is saved


def test_without_a_saved_value_it_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _started(MagicMock())
    assert {s.key: s for s in plugin.settings()}[
        "use_project_bus_connection"
    ].get() is False


def test_a_started_plugin_keeps_the_configured_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    stored = "Type=IpTunneling;HostAddress=100.64.0.1;ProtocolType=Tcp;Name=Site"
    plugin_module.save_settings("connection", {"configured_connections": [stored]})
    plugin = _started(MagicMock())
    assert [c.to_parameter() for c in plugin.configured_connections] == [stored]
    # Saving any setting keeps them in the file.
    {s.key: s for s in plugin.settings()}["use_project_bus_connection"].set(True)
    assert plugin_module.load_settings("connection")["configured_connections"] == [
        stored
    ]
