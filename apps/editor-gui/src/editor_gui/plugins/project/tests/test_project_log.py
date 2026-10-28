"""The Project Log filters and sorts its (possibly thousands of) rows only when the log, the filter or
the sorting changes, not every frame."""

from __future__ import annotations

import pytest

from editor_gui.plugins.project.service import ProjectTrace
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import project_log
from editor_gui.plugins.project.ui.project_log import ProjectLogPanel, _trace_assembly
from xknxeditor.proj import KeyExtractionError


def _trace(date: str, user: str, comment: str) -> ProjectTrace:
    return ProjectTrace(date=date, user_name=user, comment=comment, comment_plain=None)


def _traces() -> list[ProjectTrace]:
    return [
        _trace("2026-01-02", "anna", "Programmed"),
        _trace("2026-01-03", "ben", "Exported"),
        _trace("2026-01-01", "anna", "Created"),
    ]


def test_rows_newest_first_and_filtered() -> None:
    panel = ProjectLogPanel(_traces)
    traces = _traces()
    assert [t.date for t in panel._rows(traces, "")] == [
        "2026-01-03",
        "2026-01-02",
        "2026-01-01",
    ]
    assert [t.comment for t in panel._rows(traces, "anna")] == ["Programmed", "Created"]


def test_rows_worked_out_again_only_on_a_change() -> None:
    panel = ProjectLogPanel(_traces)
    traces = _traces()
    rows = panel._rows(traces, "")
    assert panel._rows(traces, "") is rows
    panel._sort_desc = False
    assert [t.date for t in panel._rows(traces, "")] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]
    # Another log (read again after a change in the project) is shown.
    again = [*traces, _trace("2026-01-04", "carl", "Renamed")]
    assert len(panel._rows(again, "")) == 4


def test_the_assembly_among_the_files_chosen() -> None:
    # In the browser, the assembly comes with log4net.dll, in either order.
    assert (
        _trace_assembly(["/files/log4net.dll", "/files/Knx.Ets.Common.dll"])
        == "/files/Knx.Ets.Common.dll"
    )
    assert _trace_assembly(["/files/knx.ets.common.dll"]) == "/files/knx.ets.common.dll"
    assert _trace_assembly(["/files/other.dll"]) == "/files/other.dll"


def test_a_missing_log4net_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def extract(path: str) -> tuple[bytes, bytes, str]:
        raise KeyExtractionError(
            "extractor failed: System.IO.FileNotFoundException: Could not load file or "
            "assembly 'log4net, Version=3.0.3.0'"
        )

    monkeypatch.setattr(project_log, "extract_trace_key", extract)
    monkeypatch.setattr(project_log.threading, "Thread", _Inline)
    panel = ProjectLogPanel(_traces)
    panel._start_extraction("/files/Knx.Ets.Common.dll")
    assert panel._result_error == S.PROJECT_LOG_NEEDS_LOG4NET


class _Inline:
    """A thread that runs its work at once."""

    def __init__(self, target, daemon: bool) -> None:
        self._target = target

    def start(self) -> None:
        self._target()
