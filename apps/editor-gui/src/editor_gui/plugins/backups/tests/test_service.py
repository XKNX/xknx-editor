"""Backups of the open project: made on request, on close and at an interval only when the
project changed, restored (the state before kept as a backup), exported, and expired."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from editor_gui.plugins.backups import service as service_mod
from editor_gui.plugins.backups.service import BackupService
from editor_gui.plugins.backups.settings import BackupSettings
from editor_gui.plugins.backups.store import (
    KIND_CLOSE,
    KIND_INTERVAL,
    KIND_MANUAL,
    KIND_RESTORE,
    Backup,
    BackupError,
    list_backups,
)
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.proj import InvalidProjectPassword, ProjectPasswordRequired

_START = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@dataclass
class Env:
    project: ProjectService
    backups: BackupService
    path: Path
    root: Path
    now: list[datetime]
    clock: list[float]
    guid: str

    def later(self, **delta: float) -> None:
        self.now[0] += timedelta(**delta)

    def made(self) -> list[Backup]:
        """The project's backups once the ones asked for are written."""
        self.backups.wait_idle(30)
        return list_backups(self.root, self.guid)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    log = LogService()
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    project = ProjectService(catalog)
    project.set_logger(Logger(log, "project"))
    now, clock = [_START], [0.0]
    backups = BackupService(
        project,
        catalog.io_lock,
        Logger(log, "backups"),
        now=lambda: now[0],
        clock=lambda: clock[0],
    )
    root = tmp_path / "backups"
    backups.set_settings(BackupSettings(folder=str(root)))
    path = tmp_path / "home.xknx"
    project.new(path, name="Home")
    yield Env(project, backups, path, root, now, clock, project.ensure_guid())
    if project.is_open:
        project.close()
    backups.wait_idle(30)


def _wait(job: service_mod.BackupJob) -> Backup:
    backup = job.future.result(timeout=30)
    assert backup is not None
    return backup


def _name(env: Env) -> str:
    info = env.project.get_project_metadata()
    assert info is not None
    return info.name


def test_a_backup_is_made_on_request(env: Env) -> None:
    backup = _wait(env.backups.create("Before the new floor"))
    info = backup.info
    assert info.kind == KIND_MANUAL
    assert info.comment == "Before the new floor"
    assert info.project_name == "Home"
    assert info.source == str(env.path)
    assert info.state == env.project.state_key()
    assert info.created == _START
    assert backup.path.parent.name.startswith("Home (")
    assert [b.path for b in env.made()] == [backup.path]
    finished = env.backups.take_finished()
    assert [job.kind for job in finished] == [KIND_MANUAL]
    assert env.backups.take_finished() == []


def test_the_window_list_is_read_in_the_background(env: Env) -> None:
    assert env.backups.backups() == []
    backup = _wait(env.backups.create())
    env.backups.wait_idle(30)
    env.backups.backups()
    env.backups.wait_idle(30)
    assert [b.path for b in env.backups.backups()] == [backup.path]
    assert env.backups.folder() == backup.path.parent


def test_no_backup_without_a_project(env: Env) -> None:
    env.project.close()
    with pytest.raises(BackupError):
        env.backups.create()
    assert env.backups.backups() == []
    assert env.backups.folder() == env.root


def test_a_damaged_project_gets_no_backup(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_mod, "check_project_file", lambda _path: "damaged")
    job = env.backups.create()
    with pytest.raises(BackupError, match="damaged"):
        job.future.result(timeout=30)
    assert env.made() == []


def test_on_close_only_when_turned_on_and_changed(env: Env) -> None:
    env.project.close()
    assert env.made() == []

    env.backups.set_settings(BackupSettings(folder=str(env.root), on_close=True))
    env.project.open(env.path)
    env.project.close()
    first = env.made()
    assert [b.info.kind for b in first] == [KIND_CLOSE]

    # Nothing changed since: no second backup.
    env.later(hours=1)
    env.project.open(env.path)
    env.project.close()
    assert len(env.made()) == 1

    env.later(hours=1)
    env.project.open(env.path)
    env.project.set_project_name("Renamed")
    env.project.close()
    made = env.made()
    assert [b.info.kind for b in made] == [KIND_CLOSE, KIND_CLOSE]
    assert made[0].info.project_name == "Renamed"
    # The renamed project's backups stay in its folder.
    assert made[0].path.parent == made[1].path.parent


def test_interval_backups_when_due_and_changed(env: Env) -> None:
    env.backups.tick()
    assert env.made() == []

    env.backups.set_settings(
        BackupSettings(folder=str(env.root), interval=True, interval_minutes=60)
    )
    env.backups.tick()
    # The interval counts from when the project was opened.
    assert env.made() == []
    env.clock[0] += 61
    env.later(minutes=61)
    env.backups.tick()
    assert [b.info.kind for b in env.made()] == [KIND_INTERVAL]

    # Changed, but the newest backup is younger than the interval.
    env.project.set_project_name("Renamed")
    env.later(minutes=10)
    env.clock[0] += 61
    env.backups.tick()
    assert len(env.made()) == 1
    env.clock[0] += 61
    env.later(minutes=55)
    env.backups.tick()
    env.backups.wait_idle(30)
    made = env.made()
    assert len(made) == 2
    assert made[0].info.project_name == "Renamed"

    # Looked at again only after a minute; unchanged, none is made.
    env.clock[0] += 61
    env.later(minutes=70)
    env.backups.tick()
    assert len(env.made()) == 2


def test_restore_keeps_the_state_before(env: Env) -> None:
    backup = _wait(env.backups.create())
    env.project.set_project_name("Changed")
    changed = env.project.state_key()
    env.later(minutes=5)

    env.backups.restore(backup)
    assert env.project.is_open
    assert env.project.path == env.path
    assert _name(env) == "Home"
    made = env.made()
    before = next(b for b in made if b.info.kind == KIND_RESTORE)
    assert before.info.state == changed

    # The restore is taken back by restoring the state before it.
    env.later(minutes=5)
    env.backups.restore(before)
    assert _name(env) == "Changed"


def test_restore_without_a_change_saves_nothing_before(env: Env) -> None:
    backup = _wait(env.backups.create())
    env.backups.restore(backup)
    assert [b.info.kind for b in env.made()] == [KIND_MANUAL]


def test_restore_of_a_backup_with_another_password(env: Env) -> None:
    env.project.set_project_password("old")
    backup = _wait(env.backups.create())
    env.project.set_project_password("new")
    env.later(minutes=5)

    with pytest.raises(InvalidProjectPassword):
        env.backups.restore(backup)
    with pytest.raises(InvalidProjectPassword):
        env.backups.restore(backup, "wrong")
    assert env.project.project_password == "new"

    env.backups.restore(backup, "old")
    assert env.project.project_password == "old"


def test_restore_of_a_protected_backup_into_an_open_project(env: Env) -> None:
    env.project.set_project_password("secret")
    backup = _wait(env.backups.create())
    env.project.set_project_password(None)
    env.later(minutes=5)
    with pytest.raises(ProjectPasswordRequired):
        env.backups.restore(backup)
    env.backups.restore(backup, "secret")
    assert env.project.project_password == "secret"


def test_a_damaged_project_file_is_restored(env: Env) -> None:
    backup = _wait(env.backups.create())
    env.project.close()
    env.path.write_bytes(b"damaged")

    assert env.backups.backups_of_file(env.path)[0].path == backup.path
    kept = env.backups.restore_file(backup, env.path)
    assert kept is not None
    assert kept.read_bytes() == b"damaged"
    env.project.open(env.path)
    assert _name(env) == "Home"


def test_a_missing_project_file_is_restored(env: Env) -> None:
    backup = _wait(env.backups.create())
    env.project.close()
    env.path.unlink()
    assert env.backups.restore_file(backup, env.path) is None
    assert env.path.exists()


def test_export_writes_a_project_file_but_not_over_the_open_one(
    env: Env, tmp_path: Path
) -> None:
    backup = _wait(env.backups.create())
    with pytest.raises(BackupError):
        env.backups.export(backup, env.path)
    dest = tmp_path / "exported.xknx"
    env.backups.export(backup, dest)
    with closing(sqlite3.connect(dest)) as db:
        assert db.execute("SELECT name FROM projects").fetchone()[0] == "Home"
    assert env.backups.check(backup) is None


def test_pin_and_delete(env: Env) -> None:
    backup = _wait(env.backups.create())
    assert env.backups.set_pinned(backup, True).pinned
    assert env.backups.find(str(backup.path)).pinned
    env.backups.delete(backup)
    assert env.made() == []
    with pytest.raises(BackupError):
        env.backups.find(str(backup.path))


def test_old_automatic_backups_are_deleted_after_a_new_one(env: Env) -> None:
    env.backups.set_settings(
        BackupSettings(
            folder=str(env.root), on_close=True, expire_days=10, keep_newest=1
        )
    )
    manual = _wait(env.backups.create())
    for name in ("One", "Two", "Three"):
        env.later(days=20)
        env.project.set_project_name(name)
        env.project.close()
        env.backups.wait_idle(30)
        env.project.open(env.path)
    made = env.made()
    # The manual backup stays; of the automatic ones, the newest (and none younger than ten
    # days) is kept.
    assert [b.info.kind for b in made] == [KIND_CLOSE, KIND_MANUAL]
    assert made[0].info.project_name == "Three"
    assert made[1].path == manual.path


def test_expired_backups_stay_with_expiry_turned_off(env: Env) -> None:
    env.backups.set_settings(
        BackupSettings(folder=str(env.root), on_close=True, expire=False, keep_newest=0)
    )
    for name in ("One", "Two"):
        env.later(days=400)
        env.project.set_project_name(name)
        env.project.close()
        env.backups.wait_idle(30)
        env.project.open(env.path)
    assert len(env.made()) == 2


def test_the_state_key_follows_edits_and_undo(env: Env) -> None:
    first = env.project.state_key()
    assert first == env.project.state_key()
    env.project.set_project_name("Renamed")
    renamed = env.project.state_key()
    assert renamed != first
    env.project.undo()
    assert env.project.state_key() == first
    env.project.set_project_password("secret")
    assert env.project.state_key() != first
