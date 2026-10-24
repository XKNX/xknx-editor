"""The Comment tab: each field below a row with its title and the formatting buttons; a device's
two fields share the height evenly, with as much room below the first as below the second."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.comment import CommentPanel
from editor_gui.widgets import rich_text_edit

_POS = imgui.ImVec2(0, 0)
_SIZE = imgui.ImVec2(400, 600)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[tuple[float, float]]]:
    """Where the fields (top, height) and the formatting buttons (top, height) were drawn."""
    found: dict[str, list[tuple[float, float]]] = {"fields": [], "buttons": []}
    begin_child, button = imgui.begin_child, imgui.button

    def child(name: str, size: imgui.ImVec2, *args: Any) -> bool:
        found["fields"].append((imgui.get_cursor_screen_pos().y, size.y))
        return begin_child(name, size, *args)

    def press(label: str, size: imgui.ImVec2) -> bool:
        found["buttons"].append((imgui.get_cursor_screen_pos().y, size.y))
        return button(label, size)

    monkeypatch.setattr(rich_text_edit.imgui, "begin_child", child)
    monkeypatch.setattr(rich_text_edit.imgui, "button", press)
    return found


def _render(kind: str) -> tuple[float, float]:
    """Render the panel for an element of ``kind`` in a window; the window's content top and
    bottom."""
    project = SimpleNamespace(
        is_open=True,
        comment_target=(kind, 7),
        comment=lambda kind, element_id, field="comment": "",
        set_comment=lambda *_: None,
    )
    panel = CommentPanel(project)  # type: ignore[arg-type]
    imgui.new_frame()
    imgui.set_next_window_pos(_POS)
    imgui.set_next_window_size(_SIZE)
    imgui.begin("Comment", None, imgui.WindowFlags_.no_decoration)
    top = imgui.get_cursor_screen_pos().y
    bottom = top + imgui.get_content_region_avail().y
    panel.render()
    imgui.end()
    imgui.end_frame()
    return top, bottom


@pytest.mark.usefixtures("context")
def test_a_device_has_two_fields_of_the_same_height(
    drawn: dict[str, list[tuple[float, float]]],
) -> None:
    top, bottom = _render("device")
    (hints_top, hints_height), (comment_top, comment_height) = drawn["fields"]
    assert hints_height == pytest.approx(comment_height)
    # The buttons sit above their field, in its title row.
    buttons = drawn["buttons"]
    assert len(buttons) == 6
    assert all(y < hints_top for y, _ in buttons[:3])
    assert all(hints_top + hints_height < y < comment_top for y, _ in buttons[3:])
    # As much room below the first field as below the second one (with the window padding).
    style = imgui.get_style()
    below_first = buttons[3][0] - (hints_top + hints_height)
    below_second = bottom + style.window_padding.y - (comment_top + comment_height)
    assert below_first == pytest.approx(below_second)
    assert buttons[0][0] == pytest.approx(top)


@pytest.mark.usefixtures("context")
def test_another_element_has_one_field_filling_the_tab(
    drawn: dict[str, list[tuple[float, float]]],
) -> None:
    _, bottom = _render("group_address")
    [(field_top, field_height)] = drawn["fields"]
    assert len(drawn["buttons"]) == 3
    gap = imgui.get_style().item_spacing.y
    assert field_top + field_height + gap == pytest.approx(bottom)
