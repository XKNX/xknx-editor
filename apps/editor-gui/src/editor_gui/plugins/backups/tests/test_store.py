"""Backup files: written whole or not at all, read back, found by project, checked, pinned,
deleted and expired."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from editor_gui.plugins.backups.store import (
    FORMAT,
    INFO_NAME,
    KIND_CLOSE,
    KIND_INTERVAL,
    KIND_MANUAL,
    Backup,
    BackupError,
    BackupInfo,
    backups_of_file,
    check_backup,
    check_project_file,
    delete_backup,
    expired,
    extract_project,
    list_backups,
    move_aside,
    project_folder,
    read_backup,
    safe_name,
    set_pinned,
    snapshot,
    write_backup,
)
from xknxeditor.proj import ProjectService

_NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _project(path: Path, name: str = "Home") -> str:
    """A new project file at ``path``; returns its GUID."""
    svc = ProjectService()
    pid = svc.create(path, name=name)
    guid = svc.project(pid).guid
    svc.close(pid)
    return guid


def _info(guid: str, created: datetime = _NOW, **kwargs: str) -> BackupInfo:
    values: dict[str, str] = {"kind": KIND_MANUAL, "project_name": "Home"}
    values.update(kwargs)
    return BackupInfo(project_guid=guid, created=created, **values)


def test_a_backup_holds_the_project_and_its_info(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    guid = _project(source)
    info = _info(guid, comment="Before the new floor", source=str(source))
    backup = write_backup(source, tmp_path / "backups", info)

    assert backup.path.parent == tmp_path / "backups"
    assert backup.path.suffix == ".zip"
    assert not list(backup.path.parent.glob("*.tmp"))
    with zipfile.ZipFile(backup.path) as z:
        assert z.namelist() == ["Home.xknx", INFO_NAME]
    read = read_backup(backup.path)
    assert read is not None
    assert read.info == info
    assert not read.pinned

    restored = tmp_path / "restored.xknx"
    extract_project(backup.path, restored)
    assert restored.read_bytes() == source.read_bytes()
    assert check_backup(backup.path) is None


def test_two_backups_in_the_same_second_get_their_own_file(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    guid = _project(source)
    first = write_backup(source, tmp_path, _info(guid))
    second = write_backup(source, tmp_path, _info(guid))
    assert first.path != second.path
    assert second.path.stem.endswith("(2)")


def test_info_of_a_later_format_or_without_a_project_is_skipped() -> None:
    info = _info("g-1")
    assert BackupInfo.from_json(info.to_json()) == info
    later = info.to_json().replace(f'"format": {FORMAT}', f'"format": {FORMAT + 1}')
    assert BackupInfo.from_json(later) is None
    assert BackupInfo.from_json(_info("").to_json()) is None
    assert BackupInfo.from_json("not json") is None
    assert BackupInfo.from_json("[1, 2]") is None
    assert BackupInfo.from_json(info.to_json().replace("manual", "unknown")) is None


def test_info_without_a_time_zone_is_utc() -> None:
    text = _info("g-1").to_json().replace("+00:00", "")
    info = BackupInfo.from_json(text)
    assert info is not None
    assert info.created == _NOW


def test_backups_are_listed_by_project_the_newest_first(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    guid = _project(source)
    other = _project(tmp_path / "other.xknx", "Other")
    root = tmp_path / "root"
    older = write_backup(
        source,
        project_folder(root, guid, "Home"),
        _info(guid, _NOW - timedelta(days=1)),
    )
    newer = write_backup(source, project_folder(root, guid, "Home"), _info(guid))
    write_backup(source, project_folder(root, other, "Other"), _info(other))
    (project_folder(root, guid, "Home") / "notes.zip").write_bytes(b"not a zip")

    assert [b.path for b in list_backups(root, guid)] == [newer.path, older.path]
    assert list_backups(tmp_path / "missing", guid) == []


def test_a_renamed_project_keeps_its_folder(tmp_path: Path) -> None:
    folder = project_folder(tmp_path, "1A2B3C4D-0000", "Home")
    assert folder.name == "Home (1a2b3c4d)"
    folder.mkdir()
    assert project_folder(tmp_path, "1a2b3c4d-0000", "Renamed") == folder


def test_names_are_valid_on_every_system() -> None:
    assert safe_name('Haus: "Süd"/Nord?') == "Haus_ _Süd__Nord_"
    assert safe_name("  Home.  ") == "Home"
    assert safe_name("...") == "Project"
    assert len(safe_name("x" * 200)) == 80


def test_a_damaged_backup_is_found(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    guid = _project(source)
    backup = write_backup(source, tmp_path, _info(guid))
    data = bytearray(backup.path.read_bytes())
    # The project file's compressed data starts after its local header (30 bytes + name).
    middle = 30 + len("Home.xknx") + 200
    data[middle : middle + 16] = bytes(16)
    backup.path.write_bytes(bytes(data))

    assert check_backup(backup.path) is not None
    with pytest.raises(BackupError):
        extract_project(backup.path, tmp_path / "out.xknx")
    assert not (tmp_path / "out.xknx").exists()


def test_a_damaged_project_file_is_found(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    _project(source)
    assert check_project_file(source) is None
    broken = tmp_path / "broken.xknx"
    broken.write_bytes(b"SQLite format 3\x00" + bytes(200))
    assert check_project_file(broken) is not None
    empty = tmp_path / "empty.xknx"
    empty.write_bytes(b"")
    assert check_project_file(empty) == "no such table: projects"


def test_a_backup_is_pinned_and_deleted(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    guid = _project(source)
    backup = write_backup(source, tmp_path, _info(guid))

    pinned = set_pinned(backup, True)
    assert pinned.pinned
    read = read_backup(backup.path)
    assert read is not None
    assert read.pinned
    assert not set_pinned(pinned, False).pinned
    set_pinned(backup, True)

    delete_backup(backup)
    assert not backup.path.exists()
    assert list(tmp_path.glob("*.pinned")) == []
    assert read_backup(backup.path) is None


def _backup(days: int, kind: str = KIND_CLOSE, *, pinned: bool = False) -> Backup:
    info = _info("g", _NOW - timedelta(days=days), kind=kind)
    return Backup(Path(f"{days}-{kind}.zip"), info, 1, pinned)


def test_old_automatic_backups_expire_but_the_newest_stay() -> None:
    backups = [
        _backup(1),
        _backup(100),
        _backup(120, KIND_INTERVAL),
        _backup(130, pinned=True),
        _backup(140, KIND_MANUAL),
        _backup(150),
    ]
    due = expired(backups, days=90, keep_newest=2, now=_NOW)
    assert [b.path.name for b in due] == ["120-interval.zip", "150-close.zip"]
    # A project left alone keeps its newest automatic backups, however old they are.
    assert expired(backups[1:3], days=90, keep_newest=3, now=_NOW) == []
    assert [
        b.path.name for b in expired(backups, days=200, keep_newest=0, now=_NOW)
    ] == []


def test_the_backups_of_a_file_are_found(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    guid = _project(source)
    root = tmp_path / "root"
    write_backup(
        source, project_folder(root, guid, "Home"), _info(guid, source=str(source))
    )
    write_backup(
        source,
        project_folder(root, guid, "Home"),
        _info(guid, _NOW + timedelta(hours=1), source=str(tmp_path / "copy.xknx")),
    )
    found = backups_of_file(root, source)
    assert len(found) == 1
    assert found[0].info.source == str(source)
    assert backups_of_file(tmp_path / "missing", source) == []


def test_a_damaged_file_is_kept_under_another_name(tmp_path: Path) -> None:
    path = tmp_path / "home.xknx"
    path.write_bytes(b"damaged")
    kept = move_aside(path, _NOW)
    assert not path.exists()
    assert kept.read_bytes() == b"damaged"
    assert kept.name.startswith("home (damaged ")
    path.write_bytes(b"again")
    assert move_aside(path, _NOW) != kept


def test_a_snapshot_of_an_open_project_is_complete(tmp_path: Path) -> None:
    source = tmp_path / "home.xknx"
    svc = ProjectService()
    pid = svc.create(source, name="Home")
    svc.add_installation(pid, "Second")
    reports: list[tuple[int, int]] = []
    copy = tmp_path / "copy.xknx"
    snapshot(source, copy, lambda done, total: reports.append((done, total)))
    svc.close(pid)

    assert check_project_file(copy) is None
    assert reports
    assert reports[-1][0] == reports[-1][1]
    reopened = ProjectService()
    copy_pid = reopened.open(copy)
    assert len(reopened.installations(copy_pid)) == 2
    reopened.close(copy_pid)


def test_a_snapshot_of_no_project_fails(tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        snapshot(tmp_path / "missing.xknx", tmp_path / "copy.xknx")
