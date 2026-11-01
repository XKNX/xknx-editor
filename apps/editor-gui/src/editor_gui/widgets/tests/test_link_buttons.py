"""The group addresses cell of the group objects table: its "x" and "+" buttons are one size with
the sign in their middle, as far from each other as from the cell's edges (below the line above
the row), and the texts beside them start at their top."""

from __future__ import annotations

import itertools
from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import group_objects_widgets as widgets
from editor_gui.widgets import text_layout

_Rect = tuple[float, float, float, float]
# FreeType's LightHinting flag, as the editor loads its fonts.
_HINTING = 1 << 3


@pytest.fixture
def table() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    config = imgui.ImFontConfig()
    config.font_loader_flags = _HINTING
    path = text_layout._ASSETS / "fonts/XKNXSans-Regular.ttf"  # pyright: ignore[reportPrivateUsage]
    io.fonts.add_font_from_file_ttf(str(path), 14.0, config)
    style = imgui.get_style()
    # The editor's spacing (see theme.apply_style).
    style.frame_padding = imgui.ImVec2(8.0, 4.0)
    style.item_spacing = imgui.ImVec2(8.0, 6.0)
    style.cell_padding = imgui.ImVec2(6.0, 4.0)
    imgui.new_frame()
    imgui.begin("Group Objects")
    assert imgui.begin_table("##objects", 2, imgui.TableFlags_.borders_inner)
    yield
    imgui.end_table()
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _rect() -> _Rect:
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    return low.x, low.y, high.x, high.y


def _row(links: int) -> tuple[_Rect, list[_Rect], list[_Rect]]:
    """A row with a field and the cell of ``links`` group addresses; the cell's room (from below
    the line above the row to the line above the next row), its buttons and its texts."""
    imgui.table_next_row()
    imgui.table_set_column_index(0)
    imgui.button("Field")
    imgui.table_set_column_index(1)
    padding = imgui.get_style().cell_padding
    start = imgui.get_cursor_screen_pos()
    buttons: list[_Rect] = []
    texts: list[_Rect] = []
    widgets._begin_link_buttons()  # pyright: ignore[reportPrivateUsage]
    for n in range(links):
        widgets._link_button(f"unlink{n}", "x")  # pyright: ignore[reportPrivateUsage]
        buttons.append(_rect())
        widgets._text_beside()  # pyright: ignore[reportPrivateUsage]
        imgui.same_line()
        imgui.text(f"0/0/{n}  Temperatur")
        texts.append(_rect())
    widgets._link_button("add", "+")  # pyright: ignore[reportPrivateUsage]
    buttons.append(_rect())
    widgets._end_link_buttons()  # pyright: ignore[reportPrivateUsage]
    imgui.table_next_row()
    imgui.table_set_column_index(1)
    end = imgui.get_cursor_screen_pos()
    line = widgets._ROW_LINE  # pyright: ignore[reportPrivateUsage]
    cell = (start.x - padding.x, start.y - padding.y + line, 0.0, end.y - padding.y)
    return cell, buttons, texts


@pytest.mark.usefixtures("table")
@pytest.mark.parametrize("links", [0, 1, 2])
def test_buttons_are_as_far_apart_as_from_the_cell_edges(links: int) -> None:
    cell, buttons, _texts = _row(links)
    left, top, _, bottom = cell
    gaps = [buttons[0][1] - top, buttons[0][0] - left]
    gaps += [b[1] - a[3] for a, b in itertools.pairwise(buttons)]
    gaps.append(bottom - buttons[-1][3])
    assert len(set(gaps)) == 1, gaps
    # One size for "x" and "+".
    assert len({(b[2] - b[0], b[3] - b[1]) for b in buttons}) == 1


@pytest.mark.usefixtures("table")
def test_a_lone_plus_keeps_the_height_of_a_row_of_fields() -> None:
    cell, _buttons, _texts = _row(0)
    line = widgets._ROW_LINE  # pyright: ignore[reportPrivateUsage]
    assert cell[3] - cell[1] + line == pytest.approx(
        imgui.get_frame_height() + 2 * imgui.get_style().cell_padding.y
    )


@pytest.mark.usefixtures("table")
def test_the_signs_have_a_middle_pixel() -> None:
    size = widgets._link_button_size()  # pyright: ignore[reportPrivateUsage]
    side, stroke = widgets._sign_size()  # pyright: ignore[reportPrivateUsage]
    assert (side, stroke) == (5, 1)
    assert (size.x - side) % 2 == 0
    assert (size.y - side) % 2 == 0
    assert (side - stroke) % 2 == 0


@pytest.mark.usefixtures("table")
def test_texts_start_at_the_top_of_their_button() -> None:
    _cell, buttons, texts = _row(2)
    for button, text in zip(buttons, texts, strict=False):
        assert text[1] == button[1]
