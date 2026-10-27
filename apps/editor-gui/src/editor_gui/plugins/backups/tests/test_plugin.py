"""The Backups dialog and its dialogs, driven headless: a backup is created,
restored (asking for an older password), deleted, and offered for a project that could not be
opened; the settings are kept."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.backups import service as service_mod
from editor_gui.plugins.backups.plugin import BackupsPlugin
from editor_gui.plugins.backups.settings import BackupSettings
from editor_gui.plugins.backups.store import Backup
from editor_gui.plugins.backups.strings import S
from editor_gui.plugins.base import API_VERSION, Logger, PluginAPI
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.logger import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.widgets import dialog


@dataclass
class Env:
    plugin: BackupsPlugin
    project: ProjectService
    path: Path
    notes: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])
    opened: list[str] = field(default_factory=list[str])
    compared: list[tuple[str, str, str | None]] = field(
        default_factory=list[tuple[str, str, str | None]]
    )

    def frames(self, count: int = 4, key: imgui.Key | None = None) -> None:
        """Render ``count`` frames, ``key`` pressed in the third."""
        io = imgui.get_io()
        for frame in range(count):
            if key is not None and frame == 2:
                io.add_key_event(key, True)
            if key is not None and frame == 3:
                io.add_key_event(key, False)
            imgui.new_frame()
            self.plugin.render_window()
            imgui.end_frame()
            imgui.render()

    def backup(self, comment: str = "") -> Backup:
        result = self.plugin.service.create(comment).future.result(timeout=30)
        assert result is not None
        self.plugin.service.wait_idle(30)
        return result

    def name(self) -> str:
        info = self.project.get_project_metadata()
        assert info is not None
        return info.name

    def list_shown(self) -> bool:
        """Whether the list of backups is shown, after one more frame."""
        imgui.new_frame()
        self.plugin.render_window()
        shown = self.plugin.list_shown
        imgui.end_frame()
        imgui.render()
        return shown


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    # Each test counts its frames from 0: a key press of an earlier test is not this one's.
    monkeypatch.setattr(dialog, "_enter_frame", [-1])
    monkeypatch.setattr(dialog, "_escape_frame", [-1])
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 700)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    log = LogService()
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    project = ProjectService(catalog)
    project.set_logger(Logger(log, "project"))
    path = tmp_path / "home.xknx"
    holder: list[Env] = []
    api = PluginAPI(
        api_version=API_VERSION,
        project=project,
        catalog=catalog,
        connection=ConnectionService(),
        log=log,
        notify=lambda text: holder[0].notes.append(text),
        warn=lambda text: holder[0].warnings.append(text),
    )
    plugin = BackupsPlugin(
        api,
        open_project=lambda p: holder[0].opened.append(p),
        compare=lambda p, n, pw: holder[0].compared.append((p, n, pw)),
    )
    plugin.service.set_settings(BackupSettings(folder=str(tmp_path / "backups")))
    project.new(path, name="Home")
    holder.append(Env(plugin, project, path))
    yield holder[0]
    if project.is_open:
        project.close()
    plugin.shutdown(30)
    imgui.destroy_context(ctx)


def _until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_the_window_lists_the_backups(env: Env) -> None:
    env.plugin.show_window()
    env.frames()
    backup = env.backup("Before the new floor")
    env.frames()
    env.plugin.service.wait_idle(30)
    env.frames()
    assert [b.path for b in env.plugin.service.backups()] == [backup.path]
    # Only a backup made by hand is reported when it is done.
    assert env.notes == [S.CREATED.format(name=backup.path.name)]


def test_create_asks_for_a_comment(env: Env) -> None:
    env.plugin.ask_create()
    # The comment field has the keyboard focus once the dialog is shown.
    env.frames(3)
    env.frames(key=imgui.Key.enter)
    env.plugin.service.wait_idle(30)
    env.frames()
    made = env.plugin.service.list_now()
    assert [b.info.kind for b in made] == ["manual"]
    assert len(env.notes) == 1


def test_a_failed_backup_is_reported(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_mod, "check_project_file", lambda _path: "damaged")
    env.plugin.service.create()
    env.plugin.service.wait_idle(30)
    env.frames(1)
    assert env.warnings == [S.FAILED.format(error="damaged")]


def test_restore_after_confirming(env: Env) -> None:
    backup = env.backup()
    env.project.set_project_name("Changed")
    env.plugin._ask_restore(backup)  # pyright: ignore[reportPrivateUsage]
    env.frames(key=imgui.Key.enter)
    assert env.name() == "Home"
    assert len(env.notes) == 2


def test_restore_asks_for_the_password_the_project_had(env: Env) -> None:
    env.project.set_project_password("old")
    backup = env.backup()
    env.project.set_project_password("new")
    env.plugin._ask_restore(backup)  # pyright: ignore[reportPrivateUsage]
    env.frames(key=imgui.Key.enter)
    assert env.project.project_password == "new"

    env.plugin._password = "old"  # pyright: ignore[reportPrivateUsage]
    env.frames(6, key=imgui.Key.enter)
    assert env.project.project_password == "old"


def test_delete_after_confirming(env: Env) -> None:
    backup = env.backup()
    env.plugin._target = backup  # pyright: ignore[reportPrivateUsage]
    env.plugin._request = "delete"  # pyright: ignore[reportPrivateUsage]
    env.frames(key=imgui.Key.enter)
    assert not backup.path.exists()


def test_compare_shows_project_diff(env: Env) -> None:
    env.project.set_project_password("secret")
    backup = env.backup()
    env.plugin.show_window()
    env.frames(2)
    assert env.list_shown()
    env.plugin._compare_with(backup)  # pyright: ignore[reportPrivateUsage]
    assert env.compared[0][0] == str(backup.path)
    assert env.compared[0][2] == "secret"
    # Project Diff shows in the Tools panel: the list (a dialog) closes for it.
    env.frames(1)
    assert not env.list_shown()


def test_the_list_is_a_dialog_closed_with_escape(env: Env) -> None:
    env.backup()
    env.plugin.show_window()
    env.frames(2)
    assert env.list_shown()
    env.frames(key=imgui.Key.escape)
    assert not env.list_shown()


def test_a_restore_asked_in_the_list_opens_on_top_of_it(env: Env) -> None:
    backup = env.backup("Before the new floor")
    env.project.set_project_name("Changed")
    env.plugin.show_window()
    env.frames(2)
    env.plugin._ask_restore(backup)  # pyright: ignore[reportPrivateUsage]
    env.frames(key=imgui.Key.enter)
    assert env.name() == "Home"
    # The list stays, now with the state before the restore kept too.
    env.plugin.service.wait_idle(30)
    assert env.list_shown()
    kinds = sorted(b.info.kind for b in env.plugin.service.list_now())
    assert kinds == ["manual", "restore"]


def test_the_settings_are_kept_on_ok(env: Env) -> None:
    env.plugin.ask_settings()
    env.plugin._draft = replace(env.plugin._draft, on_close=True, expire_days=30)  # pyright: ignore[reportPrivateUsage]
    env.frames(key=imgui.Key.enter)
    assert env.plugin.service.settings.on_close
    assert env.plugin.service.settings.expire_days == 30

    env.plugin.ask_settings()
    env.plugin._draft = replace(env.plugin._draft, on_close=False)  # pyright: ignore[reportPrivateUsage]
    env.frames(key=imgui.Key.escape)
    assert env.plugin.service.settings.on_close


def test_a_project_that_cannot_be_opened_is_restored(env: Env) -> None:
    env.backup()
    env.project.close()
    env.path.write_bytes(b"damaged")
    env.plugin.offer_restore(
        str(env.path), "(sqlite3.DatabaseError) file is not a database [SQL: x]\nmore"
    )
    _until(lambda: env.plugin._damaged is not None)  # pyright: ignore[reportPrivateUsage]
    damaged = env.plugin._damaged  # pyright: ignore[reportPrivateUsage]
    assert damaged is not None
    assert damaged[1] == "(sqlite3.DatabaseError) file is not a database"
    env.frames(key=imgui.Key.enter)
    assert env.opened == [str(env.path)]
    assert len(env.notes) == 2
    kept = [p for p in env.path.parent.glob("home (damaged *).xknx")]
    assert len(kept) == 1


def test_no_offer_without_a_backup(env: Env) -> None:
    env.plugin.offer_restore(str(env.path.with_name("other.xknx")), "")
    time.sleep(0.2)
    env.frames(2)
    assert env.plugin._damaged is None  # pyright: ignore[reportPrivateUsage]
