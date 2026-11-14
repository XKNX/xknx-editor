"""Deleting files of the browser UI's file folder: from the file dialogs, never the open
project's."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.web.dialogs import WebOpenFile, WebSaveFile
from editor_gui.web.files import FileFolder, FileInUseError, held_open
from editor_gui.web.server import WebHub


@pytest.fixture
def context() -> Iterator[imgui.IO]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield io
    imgui.destroy_context(ctx)


def _frame(draw: Callable[[], None]) -> None:
    imgui.get_io().delta_time = 1 / 60
    imgui.new_frame()
    draw()
    imgui.render()
    for texture in imgui.get_platform_io().textures:
        if texture.status != imgui.ImTextureStatus.ok:
            texture.set_tex_id(1)
            texture.set_status(imgui.ImTextureStatus.ok)


def test_a_file_of_the_folder_is_deleted(tmp_path: Path) -> None:
    folder = FileFolder(tmp_path)
    (tmp_path / "old.knxproj").write_bytes(b"x")
    folder.delete("old.knxproj")
    assert not (tmp_path / "old.knxproj").exists()


@pytest.mark.parametrize("name", ["missing.xknx", "../outside.xknx", ".hidden", "sub"])
def test_only_files_of_the_folder_by_their_plain_name(
    tmp_path: Path, name: str
) -> None:
    folder = FileFolder(tmp_path / "files")
    (tmp_path / "outside.xknx").write_bytes(b"x")
    (folder.root / ".hidden").write_bytes(b"x")
    (folder.root / "sub").mkdir()
    with pytest.raises(FileNotFoundError):
        folder.delete(name)
    assert (tmp_path / "outside.xknx").exists()
    assert (folder.root / ".hidden").exists() and (folder.root / "sub").is_dir()


def test_a_file_in_use_stays(tmp_path: Path) -> None:
    folder = FileFolder(tmp_path)
    (tmp_path / "Haus.xknx").write_bytes(b"x")
    with pytest.raises(FileInUseError):
        folder.delete("Haus.xknx", lambda path: path.name == "Haus.xknx")
    assert (tmp_path / "Haus.xknx").exists()


def test_the_open_project_and_its_database_files_are_held_open(tmp_path: Path) -> None:
    project = tmp_path / "Haus.xknx"
    for name in ("Haus.xknx", "Haus.xknx-wal", "Haus.xknx-shm", "Haus.xknx-journal"):
        assert held_open(tmp_path / name, [None, project])
    assert not held_open(tmp_path / "Haus.xknx.bak", [project])
    assert not held_open(tmp_path / "Garage.xknx", [project])
    assert not held_open(tmp_path / "other" / "Haus.xknx", [project])
    assert not held_open(project, [None])


def test_the_dialog_deletes_after_the_question(
    context: imgui.IO, tmp_path: Path
) -> None:
    hub = WebHub(tmp_path / "files")
    hub.file_in_use = lambda path: path.name == "open.xknx"
    for name in ("a.knxproj", "open.xknx"):
        (hub.files.root / name).write_bytes(b"x")
    dialog = WebOpenFile(hub, "Open")
    for _ in range(2):
        _frame(hub.host.draw)
    entry = next(e for e in hub.files.files() if e.name == "a.knxproj")
    dialog._selected = {"a.knxproj"}  # pyright: ignore[reportPrivateUsage]
    dialog._ask_delete(entry)  # pyright: ignore[reportPrivateUsage]
    # The question shows below the list; the file stays until it is confirmed.
    _frame(hub.host.draw)
    assert (hub.files.root / "a.knxproj").exists()
    dialog.delete("a.knxproj")
    assert not (hub.files.root / "a.knxproj").exists()
    assert dialog._selected == set()  # pyright: ignore[reportPrivateUsage]
    # The open project stays, with a note.
    dialog.delete("open.xknx")
    assert (hub.files.root / "open.xknx").exists()
    assert "open.xknx" in dialog._status  # pyright: ignore[reportPrivateUsage]
    _frame(hub.host.draw)


def test_the_save_dialog_deletes_too(context: imgui.IO, tmp_path: Path) -> None:
    hub = WebHub(tmp_path / "files")
    (hub.files.root / "export.knxproj").write_bytes(b"x")
    dialog = WebSaveFile(hub, "Export", "p.knxproj", ["Projects", "*.knxproj"])
    entry = hub.files.files()[0]
    dialog._ask_delete(entry)  # pyright: ignore[reportPrivateUsage]
    for _ in range(2):
        _frame(hub.host.draw)
    dialog.delete(entry.name)
    assert hub.files.files() == []
    # A file deleted meanwhile gives a note instead of an error.
    dialog.delete(entry.name)
    assert entry.name in dialog._status  # pyright: ignore[reportPrivateUsage]
