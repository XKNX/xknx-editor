"""The Project Log filters and sorts its (possibly thousands of) rows only when the log, the filter or
the sorting changes, not every frame."""

from __future__ import annotations

from editor_gui.plugins.project.service import ProjectTrace
from editor_gui.plugins.project.ui.project_log import ProjectLogPanel


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
