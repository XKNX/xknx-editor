"""Full backups of a project: one self-contained ZIP file per backup.

A backup holds the project file (``<name>.xknx``) as it was and ``backup.json`` with when, where
and why it was made. It needs nothing else to be restored: any ZIP tool gets the project file out.

The backups of a project are kept in a folder of their own in the backup folder, named after the
project and the start of its GUID (``Home (1a2b3c4d)``). They are found by the GUID, so a renamed
project keeps its backups. A ZIP is never changed once written: a backup is pinned by an empty
``<file>.pinned`` next to it.
"""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

# Version of the layout of ``backup.json``; a reader skips backups of a later one.
FORMAT = 1
INFO_NAME = "backup.json"
PIN_SUFFIX = ".pinned"

# Why a backup was made.
KIND_MANUAL = "manual"
KIND_CLOSE = "close"
KIND_INTERVAL = "interval"
KIND_RESTORE = "restore"  # the project as it was before a restore
KINDS = (KIND_MANUAL, KIND_CLOSE, KIND_INTERVAL, KIND_RESTORE)

# Progress of a long step: (bytes or pages done, total).
Progress = Callable[[int, int], None]

_CHUNK = 1 << 20
_SNAPSHOT_PAGES = 1024
# Characters a file name cannot hold on one of the systems.
_UNSAFE = set('<>:"/\\|?*')
_NAME_LIMIT = 80


@dataclass(frozen=True)
class BackupInfo:
    """What ``backup.json`` says about a backup. ``created`` is in UTC; ``source`` is the project
    file the backup was made from and ``state`` the project's state key then (see
    ``ProjectService.state_key``)."""

    project_guid: str
    project_name: str
    created: datetime
    kind: str
    comment: str = ""
    user: str = ""
    machine: str = ""
    source: str = ""
    state: str = ""
    app_version: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        data["created"] = self.created.astimezone(UTC).isoformat()
        return json.dumps({"format": FORMAT, **data}, indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(text: str | bytes) -> BackupInfo | None:
        """The info, or None for one that is not a backup's of a format this version reads."""
        try:
            raw = json.loads(text)
        except ValueError:
            return None
        if not isinstance(raw, dict):
            return None
        data = cast(dict[str, Any], raw)
        if not isinstance(data.get("format"), int) or data["format"] > FORMAT:
            return None
        try:
            created = datetime.fromisoformat(str(data["created"]))
        except (KeyError, ValueError):
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        def text_of(key: str) -> str:
            value = data.get(key, "")
            return value if isinstance(value, str) else ""

        guid, kind = text_of("project_guid"), text_of("kind")
        if not guid or kind not in KINDS:
            return None
        return BackupInfo(
            project_guid=guid,
            project_name=text_of("project_name"),
            created=created,
            kind=kind,
            comment=text_of("comment"),
            user=text_of("user"),
            machine=text_of("machine"),
            source=text_of("source"),
            state=text_of("state"),
            app_version=text_of("app_version"),
        )


@dataclass(frozen=True)
class Backup:
    """A backup file with its info, its size in bytes and whether it is pinned."""

    path: Path
    info: BackupInfo
    size: int
    pinned: bool = False

    @property
    def automatic(self) -> bool:
        """Made by the app, not by the user: deleted after a while (see :func:`expired`)."""
        return self.info.kind != KIND_MANUAL


class BackupError(Exception):
    """A backup could not be made, read or restored; the message says why."""


def user_name() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return ""


def machine_name() -> str:
    return socket.gethostname()


def safe_name(name: str) -> str:
    """``name`` as a file name valid on every system: without the characters one of them
    refuses, control characters and trailing dots and spaces; "Project" for an empty one."""
    cleaned = "".join("_" if c in _UNSAFE or ord(c) < 32 else c for c in name.strip())
    cleaned = cleaned[:_NAME_LIMIT].rstrip(". ")
    return cleaned or "Project"


def _guid_tag(guid: str) -> str:
    return f"({guid.replace('-', '')[:8].lower()})"


def project_folder(root: Path, guid: str, name: str) -> Path:
    """The folder of the project's backups in ``root``: the one there already (also after the
    project was renamed), else a new one named after the project."""
    tag = _guid_tag(guid)
    with contextlib.suppress(OSError):
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and entry.name.endswith(tag):
                return entry
    return root / f"{safe_name(name)} {tag}"


def _folders(root: Path, guid: str) -> list[Path]:
    tag = _guid_tag(guid)
    try:
        return [e for e in root.iterdir() if e.is_dir() and e.name.endswith(tag)]
    except OSError:
        return []


def snapshot(source: Path, dest: Path, progress: Progress | None = None) -> None:
    """Copy the project file ``source`` to ``dest`` as one consistent state, also while the
    project is open and being changed (SQLite's online backup: a change meanwhile starts the
    copy over)."""

    def report(_status: int, remaining: int, total: int) -> None:
        if progress is not None:
            progress(total - remaining, total)

    dest.unlink(missing_ok=True)
    try:
        with (
            closing(
                sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
            ) as src,
            closing(sqlite3.connect(dest)) as dst,
        ):
            src.backup(dst, pages=_SNAPSHOT_PAGES, progress=report, sleep=0.001)
    except sqlite3.Error as e:
        raise BackupError(str(e)) from e


def check_project_file(path: Path) -> str | None:
    """Why the project file at ``path`` is damaged, or None when SQLite finds it intact and it
    holds a project."""
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as db:
            rows = [str(r[0]) for r in db.execute("PRAGMA integrity_check")]
            if rows != ["ok"]:
                return "; ".join(rows[:5])
            if db.execute("SELECT count(*) FROM projects").fetchone()[0] != 1:
                return "no project in the file"
    except sqlite3.Error as e:
        return str(e)
    return None


def _unique(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def write_backup(
    project_file: Path,
    folder: Path,
    info: BackupInfo,
    progress: Progress | None = None,
) -> Backup:
    """Write a backup of ``project_file`` (a snapshot nothing writes to) into ``folder``: to a
    temporary file first, checked by reading it back, then renamed, so the folder never holds a
    half-written backup (a cloud folder never uploads one)."""
    folder.mkdir(parents=True, exist_ok=True)
    local = info.created.astimezone()
    name = safe_name(info.project_name)
    path = _unique(folder / f"{name} {local:%Y-%m-%d %H-%M-%S}.zip")
    tmp = folder / f".{path.name}.{os.getpid()}.tmp"
    total = project_file.stat().st_size
    member = zipfile.ZipInfo(f"{name}.xknx", date_time=local.timetuple()[:6])
    member.compress_type = zipfile.ZIP_DEFLATED
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            with (
                project_file.open("rb") as src,
                z.open(member, "w", force_zip64=True) as out,
            ):
                done = 0
                while chunk := src.read(_CHUNK):
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
            z.writestr(INFO_NAME, info.to_json())
        problem = _check_zip(tmp)
        if problem is not None:
            raise BackupError(problem)
        os.replace(tmp, path)
    except (OSError, zipfile.BadZipFile) as e:
        raise BackupError(str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)
    return Backup(path, info, path.stat().st_size)


def _check_zip(path: Path) -> str | None:
    """Why the ZIP at ``path`` is not a readable backup (a CRC that does not match, no project
    file, no info), or None."""
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad is not None:
                return f"{bad} is damaged"
            if _project_member(z) is None:
                return "no project file in the backup"
            if BackupInfo.from_json(z.read(INFO_NAME)) is None:
                return "no backup information in the backup"
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        return str(e)
    return None


def _project_member(z: zipfile.ZipFile) -> zipfile.ZipInfo | None:
    return next((i for i in z.infolist() if i.filename.lower().endswith(".xknx")), None)


# Backups read before, by path: (modification time, size) and what was read.
_read_cache: dict[Path, tuple[tuple[int, int], BackupInfo | None]] = {}


def read_backup(path: Path) -> Backup | None:
    """The backup at ``path``, or None for a file that is not one."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    hit = _read_cache.get(path)
    if hit is not None and hit[0] == key:
        info = hit[1]
    else:
        try:
            with zipfile.ZipFile(path) as z:
                info = BackupInfo.from_json(z.read(INFO_NAME))
        except (OSError, KeyError, zipfile.BadZipFile):
            info = None
        _read_cache[path] = (key, info)
    if info is None:
        return None
    pinned = path.with_name(path.name + PIN_SUFFIX).exists()
    return Backup(path, info, stat.st_size, pinned)


def list_backups(root: Path, guid: str) -> list[Backup]:
    """The backups of the project ``guid`` in ``root``, the newest first."""
    found: list[Backup] = []
    for folder in _folders(root, guid):
        with contextlib.suppress(OSError):
            for entry in folder.glob("*.zip"):
                backup = read_backup(entry)
                if backup is not None and backup.info.project_guid == guid:
                    found.append(backup)
    return sorted(found, key=lambda b: b.info.created, reverse=True)


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def backups_of_file(root: Path, source: Path) -> list[Backup]:
    """The backups in ``root`` made from the project file ``source``, the newest first: those of
    a file that can no longer be opened."""
    found: list[Backup] = []
    try:
        folders = [e for e in root.iterdir() if e.is_dir()]
    except OSError:
        return []
    for folder in folders:
        with contextlib.suppress(OSError):
            for entry in folder.glob("*.zip"):
                backup = read_backup(entry)
                if backup is not None and _same_path(backup.info.source, str(source)):
                    found.append(backup)
    return sorted(found, key=lambda b: b.info.created, reverse=True)


def extract_project(backup: Path, dest: Path, progress: Progress | None = None) -> None:
    """Write the project file of the backup ``backup`` to ``dest``, through a temporary file
    beside it; a damaged backup raises :class:`BackupError` (its CRC is checked while reading)."""
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(backup) as z:
            member = _project_member(z)
            if member is None:
                raise BackupError("no project file in the backup")
            with z.open(member) as src, tmp.open("wb") as out:
                done = 0
                while chunk := src.read(_CHUNK):
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, member.file_size)
        os.replace(tmp, dest)
    except (OSError, zipfile.BadZipFile) as e:
        raise BackupError(str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)


def check_backup(backup: Path) -> str | None:
    """Why the backup at ``backup`` cannot be restored (a damaged ZIP or project file), or None."""
    problem = _check_zip(backup)
    if problem is not None:
        return problem
    with tempfile.TemporaryDirectory(prefix="xknx-backup-") as tmp:
        project = Path(tmp) / "check.xknx"
        try:
            extract_project(backup, project)
        except BackupError as e:
            return str(e)
        return check_project_file(project)


def set_pinned(backup: Backup, pinned: bool) -> Backup:
    marker = backup.path.with_name(backup.path.name + PIN_SUFFIX)
    try:
        if pinned:
            marker.touch()
        else:
            marker.unlink(missing_ok=True)
    except OSError as e:
        raise BackupError(str(e)) from e
    return replace(backup, pinned=pinned)


def delete_backup(backup: Backup) -> None:
    try:
        backup.path.unlink(missing_ok=True)
        backup.path.with_name(backup.path.name + PIN_SUFFIX).unlink(missing_ok=True)
    except OSError as e:
        raise BackupError(str(e)) from e
    _read_cache.pop(backup.path, None)


def expired(
    backups: Iterable[Backup], *, days: int, keep_newest: int, now: datetime
) -> list[Backup]:
    """The automatic, unpinned backups older than ``days`` days, but never one of the newest
    ``keep_newest`` automatic backups (a project left alone keeps those)."""
    automatic = sorted(
        (b for b in backups if b.automatic),
        key=lambda b: b.info.created,
        reverse=True,
    )
    limit = now - timedelta(days=days)
    return [
        b for b in automatic[keep_newest:] if not b.pinned and b.info.created < limit
    ]


def move_aside(path: Path, now: datetime) -> Path:
    """Rename the damaged project file ``path`` to a name beside it that says so, and return
    that name; nothing is deleted."""
    target = _unique(
        path.with_name(
            f"{path.stem} (damaged {now.astimezone():%Y-%m-%d %H-%M-%S}){path.suffix}"
        )
    )
    shutil.move(path, target)
    return target
