"""The monitors' limits and the Group Monitor's persistence are settings (Settings > Monitor, and
MCP settings_get/set), not fields in the monitors' toolbars."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.monitor.plugin import MonitorPlugin
from editor_gui.plugins.monitor.service import MonitorKind


class _Service:
    def __init__(self) -> None:
        self.limits = {kind: 10_000 for kind in MonitorKind}

    def max_records(self, kind: MonitorKind) -> int:
        return self.limits[kind]

    def set_max_records(self, kind: MonitorKind, value: int) -> None:
        self.limits[kind] = value


def _settings() -> tuple[dict[str, Any], _Service, SimpleNamespace]:
    service = _Service()
    plugin = SimpleNamespace(
        _service=service, _persist=False, _max_file_mb=0, persisted=[]
    )
    plugin.set_persist = plugin.persisted.append
    plugin.set_max_file_mb = lambda _value: None
    plugin._render_open_folder = MonitorPlugin._render_open_folder  # pyright: ignore[reportPrivateUsage]
    settings = MonitorPlugin.settings(cast("Any", plugin), lambda _folder: None)
    return {s.key: s for s in settings}, service, plugin


def test_every_monitor_has_its_limit() -> None:
    settings, service, _plugin = _settings()
    for key, kind in (
        ("max_telegrams", MonitorKind.GROUP),
        ("max_bus_telegrams", MonitorKind.BUS),
        ("max_activity_telegrams", MonitorKind.ACTIVITY),
    ):
        setting = settings[key]
        setting.set(500)
        assert service.limits[kind] == 500
        assert setting.get() == 500
        # 0 is unlimited; any other limit keeps at least 10 telegrams.
        setting.set(0)
        assert service.limits[kind] == 0
        setting.set(3)
        assert service.limits[kind] == 10


def test_persisting_the_group_monitor_is_a_setting() -> None:
    settings, _service, plugin = _settings()
    settings["persist_telegrams"].set(True)
    assert plugin.persisted == [True]
