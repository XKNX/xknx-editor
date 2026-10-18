"""The session log file: one file per run in the per-user log folder (Help > Open Log Folder).

Each run writes ``xknx-editor_<date>_<time>_<pid>.log``, so runs side by side never share a file
and the newest one is the run to report. Only the newest runs' files are kept. The frozen app's
entry point opens it first (for native and Python crash output); the log service writes to the same
file object.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import IO

import platformdirs

_APP_NAME = "xknx-editor"
# Session log files kept, the current one included.
_KEEP = 10
_LEGACY_LOG = "xknx-editor.log"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_session: IO[str] | None = None


def log_dir() -> Path:
    """The per-user log folder (e.g. ``%LOCALAPPDATA%\\xknx-editor\\xknx-editor\\Logs``)."""
    return Path(platformdirs.user_log_dir(_APP_NAME))


def _session_logs(folder: Path) -> list[Path]:
    """The session log files in ``folder``, newest first."""
    return sorted(folder.glob(f"{_APP_NAME}_*.log"), reverse=True)


def _prune(folder: Path) -> None:
    """Delete all but the newest session logs, and the single log older versions appended to in the
    temp folder. A file still open in a running instance stays."""
    for old in [
        *_session_logs(folder)[_KEEP - 1 :],
        Path(tempfile.gettempdir()) / _LEGACY_LOG,
    ]:
        with contextlib.suppress(OSError):
            old.unlink()


def session_log() -> IO[str]:
    """This run's log file, opened (line-buffered, appending) on the first call."""
    global _session
    if _session is None:
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        _prune(folder)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        path = folder / f"{_APP_NAME}_{stamp}_{os.getpid()}.log"
        _session = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - kept for the run
    return _session


def set_session_log(file: IO[str] | None) -> None:
    """Write this run's log to ``file`` instead (tests, which must not touch the user's log folder);
    None opens the session log file again on the next call of :func:`session_log`."""
    global _session
    _session = file


class PlainTextFile:
    """Writes to a log file without the console's colour codes."""

    def __init__(self, file: IO[str]) -> None:
        self._file = file

    def write(self, data: str) -> int:
        self._file.write(_ANSI.sub("", data))
        return len(data)

    def flush(self) -> None:
        self._file.flush()
