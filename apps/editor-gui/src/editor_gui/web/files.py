"""The files the browser exchanges with the app.

In the browser UI the app's files live in one folder on the server (the *file folder*): uploads land
there, a save writes there, and the browser can download what the app wrote. Only names inside that
folder are accepted, and only files the app offered can be downloaded."""

from __future__ import annotations

import fnmatch
import itertools
import logging
import os
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_FORBIDDEN = set('<>:"/\\|?*')
# A written file counts as complete once its size and time stayed the same for this long.
_STABLE_SECONDS = 1.0
_WATCH_SECONDS = 3600.0


def safe_name(name: str) -> str:
    """``name`` as a plain file name inside the file folder."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        "_" if ch in _FORBIDDEN or ord(ch) < 32 else ch for ch in base
    ).strip()
    cleaned = cleaned.strip(". ")
    return cleaned or "file"


def matches(name: str, patterns: list[str]) -> bool:
    """Whether ``name`` matches one of the dialog filter ``patterns`` (``*`` matches all)."""
    if not patterns:
        return True
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, p.lower()) for p in patterns)


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    modified: float

    def as_json(self) -> dict[str, object]:
        return {"name": self.name, "size": self.size, "modified": self.modified}


class FileFolder:
    """The server folder the browser UI reads and writes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / safe_name(name)

    def files(self) -> list[Entry]:
        """The files in the folder, newest first."""
        return self._entries(lambda p: p.is_file())

    def folders(self) -> list[Entry]:
        return self._entries(lambda p: p.is_dir())

    def _entries(self, keep: Callable[[Path], bool]) -> list[Entry]:
        out: list[Entry] = []
        try:
            children = list(self.root.iterdir())
        except OSError:
            return out
        for child in children:
            if child.name.startswith(".") or not keep(child):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            out.append(Entry(child.name, stat.st_size, stat.st_mtime))
        out.sort(key=lambda e: e.modified, reverse=True)
        return out

    def unique_name(self, name: str) -> str:
        """``name``, or ``name (2)`` etc. when a file of that name exists."""
        name = safe_name(name)
        if not (self.root / name).exists():
            return name
        stem, dot, suffix = name.rpartition(".")
        if not dot or not stem:
            stem, suffix = name, ""
        for n in itertools.count(2):
            candidate = f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
            if not (self.root / candidate).exists():
                return candidate
        raise AssertionError("unreachable")


class Downloads:
    """Files the browser may fetch, by id. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._files: dict[str, Path] = {}

    def register(self, path: Path) -> str:
        with self._lock:
            key = f"{next(self._ids)}-{os.urandom(6).hex()}"
            self._files[key] = path
        return key

    def get(self, key: str) -> Path | None:
        with self._lock:
            return self._files.get(key)


def _signature(path: Path) -> tuple[int, int] | None:
    """Size and modification time of a file, or the sum over a folder's files; None if missing."""
    try:
        if path.is_dir():
            size = 0
            newest = 0
            for child in path.rglob("*"):
                if child.is_file():
                    stat = child.stat()
                    size += stat.st_size
                    newest = max(newest, stat.st_mtime_ns)
            return (size, newest) if size else None
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns) if stat.st_size else None


def watch_written(
    path: Path, ready: Callable[[Path], None], *, interval: float = 0.5
) -> threading.Thread:
    """Call ``ready(path)`` once the app has written ``path`` (a file or a folder).

    A file that exists already counts once it changed; any file counts once its size and time
    stayed the same for a moment, so a download never gets a half-written file."""
    before = _signature(path)

    def run() -> None:
        deadline = time.monotonic() + _WATCH_SECONDS
        last: tuple[int, int] | None = None
        since = 0.0
        while time.monotonic() < deadline:
            time.sleep(interval)
            current = _signature(path)
            if current is None or current == before:
                last = None
                continue
            if current != last:
                last, since = current, time.monotonic()
            elif time.monotonic() - since >= _STABLE_SECONDS:
                ready(path)
                return
        logger.info("gave up waiting for %s to be written", path)

    thread = threading.Thread(target=run, name="web-download-watch", daemon=True)
    thread.start()
    return thread


def zip_folder(folder: Path) -> Path:
    """A ZIP archive of ``folder`` in a temporary location."""
    target = Path(tempfile.mkdtemp(prefix="xknx-web-")) / f"{folder.name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for child in sorted(folder.rglob("*")):
            if child.is_file():
                archive.write(child, child.relative_to(folder.parent).as_posix())
    return target
