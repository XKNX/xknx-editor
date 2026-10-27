"""Each monitor keeps at most its own number of telegrams (the Max field in its toolbar), 10,000
by default; the numbers set are kept, also the one from when all monitors shared one."""

from __future__ import annotations

from typing import Any

import pytest

from editor_gui.plugins.base import API_VERSION, PluginAPI
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.logger import LogService
from editor_gui.plugins.monitor import plugin as plugin_mod
from editor_gui.plugins.monitor.plugin import MonitorPlugin
from editor_gui.plugins.monitor.records import DEFAULT_MAX_RECORDS
from editor_gui.plugins.monitor.service import MonitorKind, MonitorService
from editor_gui.plugins.project.service import ProjectService


def test_each_monitor_has_its_own_cap() -> None:
    service = MonitorService(connection=object())  # type: ignore[arg-type]
    changed: list[tuple[MonitorKind, int]] = []
    service.on_max_records_changed = lambda kind, value: changed.append((kind, value))
    assert DEFAULT_MAX_RECORDS == 10_000
    assert all(service.max_records(kind) == 10_000 for kind in MonitorKind)
    service.set_max_records(MonitorKind.BUS, 500)
    assert service.max_records(MonitorKind.BUS) == 500
    assert service.max_records(MonitorKind.GROUP) == 10_000
    assert changed == [(MonitorKind.BUS, 500)]
    # The same value again is no change.
    service.set_max_records(MonitorKind.BUS, 500)
    assert len(changed) == 1


def _plugin(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, stored: dict[str, Any]
) -> MonitorPlugin:
    monkeypatch.setattr(plugin_mod, "load_settings", lambda _name: dict(stored))
    monkeypatch.setattr(
        plugin_mod, "save_settings", lambda _name, data: stored.update(data)
    )
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    api = PluginAPI(
        api_version=API_VERSION,
        project=ProjectService(catalog),
        catalog=catalog,
        connection=ConnectionService(),
        log=LogService(),
    )
    return MonitorPlugin(api)


def test_the_caps_are_kept(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, Any] = {"max_records": {"bus": 2000, "activity": 300}}
    plugin = _plugin(tmp_path, monkeypatch, stored)
    service = plugin._service  # pyright: ignore[reportPrivateUsage]
    assert service.max_records(MonitorKind.BUS) == 2000
    assert service.max_records(MonitorKind.ACTIVITY) == 300
    assert service.max_records(MonitorKind.GROUP) == 10_000
    service.set_max_records(MonitorKind.GROUP, 4000)
    assert stored["max_records"] == {"bus": 2000, "activity": 300, "group": 4000}


def test_the_shared_cap_of_before_is_the_group_monitors(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _plugin(tmp_path, monkeypatch, {"max_records": 50_000})
    service = plugin._service  # pyright: ignore[reportPrivateUsage]
    assert service.max_records(MonitorKind.GROUP) == 50_000
    assert service.max_records(MonitorKind.BUS) == 10_000
