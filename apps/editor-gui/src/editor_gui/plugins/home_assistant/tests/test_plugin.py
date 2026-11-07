"""The Home Assistant page of the Settings window, and when the plugin shows at all."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui import host
from editor_gui.host import HostOptions
from editor_gui.plugins.home_assistant import service as service_module
from editor_gui.plugins.home_assistant.plugin import HomeAssistantPlugin


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """The settings files, kept here instead of the config folder."""
    files: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        service_module, "load_settings", lambda name: files.get(name, {})
    )
    monkeypatch.setattr(
        service_module,
        "save_settings",
        lambda name, data: files.__setitem__(name, data),
    )
    return files


@pytest.fixture(autouse=True)
def standalone() -> Iterator[None]:
    host.set_current(HostOptions())
    yield
    host.set_current(HostOptions())


def _plugin() -> HomeAssistantPlugin:
    api = SimpleNamespace(log=None)
    return HomeAssistantPlugin(cast("Any", api), cast("Any", SimpleNamespace()))


def test_automatic_updates_are_a_setting(stored: dict[str, dict[str, Any]]) -> None:
    plugin = _plugin()
    (setting,) = plugin.settings()
    assert setting.key == "home_assistant_auto_update"
    assert setting.kind == "bool"
    assert setting.get is not None and setting.set is not None
    assert setting.get() is True
    setting.set(False)
    assert plugin.service.auto_update is False
    assert stored["home_assistant"] == {"auto_update": False}
    # Kept for the next start.
    assert _plugin().service.auto_update is False


def test_only_inside_home_assistant(stored: dict[str, dict[str, Any]]) -> None:
    assert not _plugin().active
    host.set_current(HostOptions(home_assistant=True))
    assert _plugin().active
