"""The limit of the "Add" dialog: with no room left it does not open, and the counts of its rows
stay within the room there is."""

from __future__ import annotations

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.add_dialog import (
    AddChoice,
    AddDialog,
    AddRow,
    one_row,
)


def test_no_room_opens_no_dialog() -> None:
    dialog = AddDialog("test")
    assert not dialog.open("Add Lines", "1.0", lambda _r: None, limit=0)
    assert dialog.open("Add Lines", "1.0", lambda _r: None, limit=3)


def test_the_counts_stay_within_the_limit() -> None:
    dialog = AddDialog("test")
    dialog.open("Add Lines", "1.0", lambda _r: None, limit=3)
    rows = dialog.request.rows
    rows[0].count = 2
    rows.append(AddRow(count=5))
    dialog._clip(1)  # pyright: ignore[reportPrivateUsage]
    assert [row.count for row in rows] == [2, 1]
    assert dialog._error == S.ADD_LIMIT_REACHED  # pyright: ignore[reportPrivateUsage]


def test_one_row_has_the_defaults() -> None:
    choice = AddChoice("Medium", [("MT-0", "TP"), ("MT-2", "RF")], "MT-2")
    request = one_row(choice, 4)
    assert [(r.count, r.name, r.choice) for r in request.rows] == [(1, "", "MT-2")]
    assert request.start_with == 4
