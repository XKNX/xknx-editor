"""Each run logs to its own file in the log folder; only the newest runs' files are kept."""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from editor_gui import log_file
from editor_gui.log_file import PlainTextFile, session_log, set_session_log


@pytest.fixture
def folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    logs = tmp_path / "Logs"
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(log_file, "log_dir", lambda: logs)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    previous = session_log()
    set_session_log(None)
    yield logs
    session_log().close()
    set_session_log(previous)


def test_a_run_logs_to_its_own_file(folder: Path) -> None:
    log = session_log()
    assert session_log() is log
    log.write("started\n")
    (path,) = folder.glob("xknx-editor_*.log")
    assert path.name.endswith(f"_{os.getpid()}.log")
    assert path.read_text(encoding="utf-8") == "started\n"


def test_only_the_newest_runs_are_kept(folder: Path) -> None:
    folder.mkdir()
    old = [
        folder / f"xknx-editor_2026-10-{day:02d}_10-00-00_1.log" for day in range(1, 13)
    ]
    for path in old:
        path.write_text("x", encoding="utf-8")
    legacy = Path(tempfile.gettempdir()) / "xknx-editor.log"
    legacy.write_text("x", encoding="utf-8")

    session_log()

    kept = sorted(folder.glob("xknx-editor_*.log"))
    assert len(kept) == 10
    assert kept[:9] == old[3:]
    assert not legacy.exists()


def test_the_file_gets_no_colour_codes() -> None:
    sink = io.StringIO()
    PlainTextFile(sink).write(
        "\x1b[2m14:56:26\x1b[0m [\x1b[32m\x1b[1mdebug    \x1b[0m] started\n"
    )
    assert sink.getvalue() == "14:56:26 [debug    ] started\n"
