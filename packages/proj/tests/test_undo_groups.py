"""Several edits made one undo step after the fact, as one drop of several elements is: undone and
redone together, one history entry, kept across a reopen."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.proj import ProjectService


def _area_names(svc: ProjectService, pid: str) -> list[str]:
    return [a.name for a in svc.topology(pid, 0).areas]


def test_edits_since_a_cursor_become_one_undo_step(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-0C01")
    before = _area_names(svc, pid)
    start = svc.cursor(pid)
    svc.create_area(pid, 0, 2, "Two")
    svc.create_area(pid, 0, 3, "Three")
    entries = len(svc.history(pid))
    svc.group_since(pid, start, "Two areas")
    assert len(svc.history(pid)) == entries - 1
    newest = svc.history(pid)[0]
    assert (newest.event_type, newest.data["label"]) == ("Composite", "Two areas")
    assert svc.undo(pid)
    assert _area_names(svc, pid) == before
    assert svc.redo(pid)
    assert _area_names(svc, pid)[-2:] == ["Two", "Three"]
    # Kept across a reopen: one undo still takes both.
    svc.close(pid)
    pid = svc.open(path)
    assert svc.undo(pid)
    assert _area_names(svc, pid) == before


def test_one_edit_or_an_undone_one_is_left_as_it_is(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    start = svc.cursor(pid)
    svc.create_area(pid, 0, 2, "Two")
    entries = len(svc.history(pid))
    svc.group_since(pid, start)
    assert len(svc.history(pid)) == entries
    svc.create_area(pid, 0, 3, "Three")
    svc.undo(pid)
    svc.group_since(pid, start)
    assert len(svc.history(pid)) == entries + 1
