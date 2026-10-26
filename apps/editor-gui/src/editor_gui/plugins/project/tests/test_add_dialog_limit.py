"""The limit of the "Add" dialog: with no room left it does not open and its "Add …" item is
grayed out, telling why, and the counts of its rows stay within the room there is."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.add_dialog import (
    AddChoice,
    AddDialog,
    AddRow,
    add_button,
    add_menu_item,
    no_room,
    one_row,
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_no_room_tells_why() -> None:
    assert no_room(0) == no_room(-1) == S.ADD_LIMIT_REACHED
    assert no_room(1) == ""


@pytest.mark.usefixtures("context")
def test_items_without_room_are_grayed_out_and_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enabled: dict[str, bool] = {}
    tips: list[str] = []
    real_menu_item = imgui.menu_item

    # Every item "clicked", as when the user clicks it; only the ones with room take it.
    def menu_item(label: str, *args: Any) -> Any:
        enabled[label] = args[2]
        return True, real_menu_item(label, *args)[1]

    monkeypatch.setattr(imgui, "menu_item", menu_item)
    monkeypatch.setattr(imgui, "small_button", lambda _label: True)
    monkeypatch.setattr(imgui, "is_item_hovered", lambda *_a: True)
    monkeypatch.setattr(imgui, "set_tooltip", tips.append)
    imgui.new_frame()
    imgui.begin("w")
    assert add_menu_item("Add Lines", no_room(2))
    assert not add_menu_item("Add Areas", no_room(0))
    assert add_button("Add Main Groups", "")
    assert not add_button("Add Group Addresses", S.SPACES_FN_NO_GROUP)
    imgui.end()
    imgui.end_frame()
    assert enabled == {"Add Lines": True, "Add Areas": False}
    assert tips == [S.ADD_LIMIT_REACHED, S.SPACES_FN_NO_GROUP]


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
