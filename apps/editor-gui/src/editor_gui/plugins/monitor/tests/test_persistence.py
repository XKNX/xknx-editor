"""Persisting the Group Monitor: the unlimited count cap, and the per-project XML file."""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugins.monitor import persistence
from editor_gui.plugins.monitor.records import (
    MonitorRecord,
    RecordKind,
    TelegramLog,
)

_RAW = bytes.fromhex("2900BCE0102F1820010081")


def _telegrams(count: int) -> list[MonitorRecord]:
    return [
        MonitorRecord(kind=RecordKind.TELEGRAM, ticks=i * 10_000_000, raw=_RAW)
        for i in range(count)
    ]


def test_count_cap_zero_is_unlimited() -> None:
    log = TelegramLog(max_records=0)
    for record in _telegrams(50):
        log.append(record)
    assert len(log) == 50  # nothing trimmed
    # Disabling a positive cap later also stops trimming.
    capped = TelegramLog(max_records=10)
    capped.set_max_records(0)
    for record in _telegrams(30):
        capped.append(record)
    assert len(capped) == 30


def test_positive_cap_still_trims() -> None:
    log = TelegramLog(max_records=10)
    for record in _telegrams(40):
        log.append(record)
    assert len(log) <= 10


def test_flush_and_load_round_trip(tmp_path: Path) -> None:
    log = TelegramLog(max_records=0)
    for record in _telegrams(20):
        log.append(record)
    path = tmp_path / "group.xml"
    persistence.flush(log, path, max_file_mb=0)
    assert not log.has_unsaved  # marked saved
    loaded = persistence.load(path)
    assert len(loaded) == 20
    assert all(r.raw == _RAW for r in loaded)


def test_flush_trims_to_max_mb(tmp_path: Path, monkeypatch) -> None:
    # Shrink the "MB" unit so a modest log exceeds a 1-unit limit and gets trimmed.
    monkeypatch.setattr(persistence, "_MB", 200)
    log = TelegramLog(max_records=0)
    for record in _telegrams(200):
        log.append(record)
    path = tmp_path / "group.xml"
    assert persistence.flush(log, path, max_file_mb=1)
    loaded = persistence.load(path)
    assert 1 <= len(loaded) < 200  # oldest dropped to fit (floor is 1, not 10)


def test_flush_failure_keeps_previous_file(tmp_path: Path) -> None:
    # A failed write (here: the temp path is blocked by a directory) returns False and leaves the
    # previously saved file intact, rather than truncating it to an unreadable stub.
    log = TelegramLog(max_records=0)
    for record in _telegrams(3):
        log.append(record)
    path = tmp_path / "group.xml"
    assert persistence.flush(log, path, max_file_mb=0)
    (tmp_path / "group.xml.tmp").mkdir()  # block the atomic temp write
    for record in _telegrams(2):
        log.append(record)
    assert persistence.flush(log, path, max_file_mb=0) is False
    assert len(persistence.load(path)) == 3  # old save preserved


def test_mark_saved_token_guards_concurrent_append() -> None:
    log = TelegramLog(max_records=0)
    for record in _telegrams(3):
        log.append(record)
    token = log.sequence
    # A telegram arriving between the snapshot and mark_saved keeps the log dirty.
    log.append(_telegrams(1)[0])
    log.mark_saved(token)
    assert log.has_unsaved
    # With a matching token (nothing appended since) it clears.
    log.mark_saved(log.sequence)
    assert not log.has_unsaved


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    assert persistence.load(tmp_path / "nope.xml") == []


def test_group_file_is_per_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(persistence, "config_dir", lambda: tmp_path)
    one = persistence.group_file("11111111-aaaa", "Haus A")
    two = persistence.group_file("22222222-bbbb", "Haus B")
    assert one.name == "group.xml" and two.name == "group.xml"
    assert one.parent != two.parent  # different projects, different folders
    assert "Haus A" in str(one.parent)  # readable name as the prefix


def _fake_plugin(tmp_path, monkeypatch, *, guid: str, name: str):
    from types import SimpleNamespace
    from typing import Any, cast

    from editor_gui.plugins.monitor.plugin import MonitorPlugin
    from editor_gui.plugins.monitor.service import MonitorKind

    monkeypatch.setattr(persistence, "config_dir", lambda: tmp_path)
    plugin = cast(Any, object.__new__(MonitorPlugin))
    plugin._persist = True
    plugin._max_file_mb = 0
    plugin._owner_guid = ""
    plugin._owner_name = ""
    plugin._flushed_generation = -1
    plugin._last_autosave = 0.0
    channel = SimpleNamespace(log=TelegramLog(0))
    plugin._service = SimpleNamespace(channels={MonitorKind.GROUP: channel})
    plugin._api = SimpleNamespace(
        project=SimpleNamespace(
            is_open=True,
            loading=False,
            get_project_metadata=lambda: SimpleNamespace(name=name),
            ensure_guid=lambda: guid,
        )
    )
    return plugin, channel.log


def test_autosave_tick_loads_the_open_projects_file(tmp_path, monkeypatch) -> None:
    plugin, log = _fake_plugin(tmp_path, monkeypatch, guid="guid-a", name="Haus A")
    # Seed a persisted file for this project.
    seed = TelegramLog(0)
    for record in _telegrams(7):
        seed.append(record)
    persistence.flush(seed, persistence.group_file("guid-a", "Haus A"), 0)

    plugin.autosave_tick()  # first tick sees the project and loads its file
    assert len(log) == 7
    assert plugin._owner_guid == "guid-a"


def test_autosave_tick_skips_while_loading(tmp_path, monkeypatch) -> None:
    # A transient load (e.g. a catalog import) must not reset tracking or reload over the live log.
    plugin, log = _fake_plugin(tmp_path, monkeypatch, guid="guid-a", name="Haus A")
    plugin._owner_guid, plugin._owner_name = "guid-a", "Haus A"
    for record in _telegrams(5):
        log.append(record)
    plugin._api.project.loading = True
    plugin.autosave_tick()
    assert len(log) == 5  # not reloaded/cleared
    assert plugin._owner_guid == "guid-a"  # tracking preserved


def test_set_persist_off_flushes_once(tmp_path, monkeypatch) -> None:
    plugin, log = _fake_plugin(tmp_path, monkeypatch, guid="guid-b", name="Haus B")
    monkeypatch.setattr(plugin, "_save_persist_settings", lambda: None)
    for record in _telegrams(5):
        log.append(record)
    plugin.set_persist(False)
    assert not plugin._persist
    assert len(persistence.load(persistence.group_file("guid-b", "Haus B"))) == 5


def test_enabling_persist_keeps_current_recording(tmp_path, monkeypatch) -> None:
    # Switching persistence on mid-session must persist the live telegrams, not wipe them.
    plugin, log = _fake_plugin(tmp_path, monkeypatch, guid="guid-c", name="Haus C")
    monkeypatch.setattr(plugin, "_save_persist_settings", lambda: None)
    plugin._persist = False  # start disabled
    for record in _telegrams(4):
        log.append(record)
    plugin.set_persist(True)
    assert plugin._persist and len(log) == 4  # kept
    # ...and written to this project's file.
    assert len(persistence.load(persistence.group_file("guid-c", "Haus C"))) == 4
