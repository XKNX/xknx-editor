"""The room between the items of a parameter page where it differs by kind: a grid or an
information box keeps less room to a separator line above it than a headline or a text does, and
a grid less to the top of the page and to a grid of lines above it; lines in grid cells end where
the cells' fields end."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.widgets import backgrounds, parameter_widgets, text_layout
from editor_gui.widgets.parameter_widgets import ParameterPage
from xknxeditor.prod.parser_v2.ui import (
    NumberWidget,
    UiButton,
    UiParameter,
    UiParameterBlock,
    UiSeparator,
)
from xknxeditor.prod.parser_v2.ui.parameter_block import ParameterBlockLayout

_DEVICE: Any = SimpleNamespace(node_id="D-1", param_errors={})


@pytest.fixture(autouse=True)
def frame(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(1400, 900)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    # The editor's spacing between items.
    imgui.get_style().item_spacing = imgui.ImVec2(8, 6)
    monkeypatch.setattr(
        parameter_widgets, "bold", lambda _scale=1.0: contextlib.nullcontext()
    )
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(1400, 900))
    imgui.begin("Parameters")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _line() -> UiSeparator:
    return UiSeparator(id="PS-L", text="", hint="HorizontalRuler")


def _grid(
    *cells: UiSeparator, widths: tuple[str, ...] = ("25%", "51%")
) -> UiParameterBlock:
    return UiParameterBlock(
        id=f"PB-{len(cells)}-{cells[0].id}",
        inline=True,
        layout=ParameterBlockLayout.GRID,
        row_labels=("",),
        column_widths=widths,
        children=cells,
    )


def _label_grid() -> UiParameterBlock:
    return _grid(UiSeparator(id="PS-T", text="Testen", cell="1,1"))


def _lines_grid() -> UiParameterBlock:
    return _grid(
        UiSeparator(id="PS-1", text="", hint="HorizontalRuler", cell="1,1"),
        UiSeparator(id="PS-2", text="", hint="HorizontalRuler", cell="1,2"),
        widths=("25%", "96%"),
    )


def _record(
    monkeypatch: pytest.MonkeyPatch, name: str, tops: list[float]
) -> Callable[..., Any]:
    """Note the screen y at which ``parameter_widgets.<name>`` is called."""
    original = getattr(parameter_widgets, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tops.append(imgui.get_cursor_screen_pos().y)
        return original(*args, **kwargs)

    monkeypatch.setattr(parameter_widgets, name, wrapper)
    return original


def _page(*content: Any) -> None:
    parameter_widgets._render_page(  # pyright: ignore[reportPrivateUsage]
        _DEVICE,
        ParameterPage(key="page", label="Page", icon=None, content=content),
        lambda *_: None,
        False,
        "",
        frozenset(),
        None,
    )


def test_a_box_and_a_grid_after_a_line_keep_less_room_than_a_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines: list[float] = []
    boxes: list[float] = []
    grids: list[float] = []
    texts: list[float] = []
    _record(monkeypatch, "_render_ruler", lines)
    _record(monkeypatch, "_render_hint", boxes)
    _record(monkeypatch, "_render_grid_block", grids)
    _record(monkeypatch, "_aligned_text", texts)
    info = UiSeparator(id="PS-I", text="Falls diese Applikation …", hint="Information")
    headline = UiSeparator(id="PS-H", text="Hilfsfunktionen", hint="Headline")
    _page(_line(), info, _line(), _label_grid(), _line(), headline)
    # The box border 16 px below the line's top, the grid's row 11 px; a headline keeps the
    # 17 px of a separator's room (and its own 1 px above).
    assert boxes[0] - lines[0] == 16
    assert grids[0] - lines[1] == 11
    assert texts[-1] - lines[2] == 18


def test_a_grid_keeps_less_room_to_the_top_of_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grids: list[float] = []
    texts: list[float] = []
    _record(monkeypatch, "_render_grid_block", grids)
    _record(monkeypatch, "_aligned_text", texts)
    top = imgui.get_cursor_screen_pos().y
    _page(_label_grid())
    imgui.set_cursor_screen_pos(imgui.ImVec2(imgui.get_cursor_screen_pos().x, top))
    _page(UiSeparator(id="PS-H", text="HF-Sensor", hint="Headline"))
    assert grids[0] == top - 6
    assert texts[0] == top + 1


def test_a_grid_keeps_less_room_to_a_grid_of_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[float, float]] = []
    original = parameter_widgets._render_grid_block  # pyright: ignore[reportPrivateUsage]

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        before = imgui.get_cursor_screen_pos().y
        result = original(*args, **kwargs)
        calls.append((before, imgui.get_cursor_screen_pos().y))
        return result

    monkeypatch.setattr(parameter_widgets, "_render_grid_block", wrapper)
    _page(_label_grid(), _lines_grid(), _label_grid(), _label_grid())
    # After a grid the next one joins it row against row (one spacing up); after one of lines
    # it comes 6 px closer still.
    (_, end1), (start2, end2), (start3, end3), (start4, _) = calls
    assert start2 - end1 == -6
    assert start3 - end2 == -6 - 6
    assert start4 - end3 == -6


def test_lines_in_cells_end_where_the_fields_of_their_cells_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans: list[tuple[float, float]] = []
    draw = imgui.ImDrawList.add_rect_filled

    def record(
        self: imgui.ImDrawList, low: imgui.ImVec2, high: imgui.ImVec2, *args: Any
    ) -> None:
        if high.y - low.y == 1.0:
            spans.append((low.x, high.x))
        draw(self, low, high, *args)

    monkeypatch.setattr(imgui.ImDrawList, "add_rect_filled", record)
    left = imgui.get_cursor_screen_pos().x
    _page(_lines_grid())
    # The cells start at 0 and 25 % of 532 px; each line ends 6 px before its cell does.
    assert [x - left for span in spans for x in span] == pytest.approx(
        [0, 133 - 6, 133, 133 + 510.72 - 6], abs=1.0
    )


def test_a_field_with_a_unit_in_a_cell_keeps_room_after_the_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widths: list[float] = []
    original = imgui.set_next_item_width

    def record(width: float) -> None:
        widths.append(width)
        original(width)

    monkeypatch.setattr(imgui, "set_next_item_width", record)
    field = UiParameter(
        ref_id="P-1",
        label="",
        value="5",
        default_value="5",
        widget=NumberWidget(min=0, max=255),
        cell="1,2",
        suffix="Sekunden",
    )
    grid = UiParameterBlock(
        id="PB-F",
        inline=True,
        layout=ParameterBlockLayout.GRID,
        row_labels=("",),
        column_widths=("25%", "22%"),
        children=(field,),
    )
    _page(grid)
    unit = text_layout.text_width("Sekunden")
    # The cell (22 % of 532 px, 117 px), less its gap, and the unit (as wide as WPF lays it
    # out) with 4 px before and 5 px after it, on the nearest whole pixel.
    assert widths[0] == round(117 - (6 + 4 + unit + 5))


@pytest.mark.parametrize(("page", "face"), [(1.0, 0.95), (0.1, 0.145)])
def test_a_button_is_light_grey_over_the_page(
    monkeypatch: pytest.MonkeyPatch, page: float, face: float
) -> None:
    # Black at 5 % over a light page, white at 5 % over a dark one.
    color = imgui.ImVec4(page, page, page, 1.0)
    monkeypatch.setattr(backgrounds, "content_background", lambda: color)
    drawn = backgrounds.page_button()
    assert (drawn.x, drawn.y, drawn.z) == pytest.approx((face, face, face))


def test_a_button_has_a_border_and_its_icon_is_16_px(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    borders: list[float] = []
    icons: list[float] = []
    original = imgui.button

    def record(label: str, size: imgui.ImVec2) -> bool:
        borders.append(imgui.get_style().frame_border_size)
        return original(label, size)

    monkeypatch.setattr(imgui, "button", record)
    monkeypatch.setattr(parameter_widgets, "_get_icon", lambda _name: object())
    monkeypatch.setattr(
        parameter_widgets, "draw_icon", lambda _icon, _pos, side: icons.append(side)
    )
    actions = parameter_widgets.ButtonActions(
        on_click=lambda *_: None,
        state=lambda *_: (True, None),
        error=lambda *_: None,
    )
    for button in (
        UiButton(id="B-1", text="Ermitteln (sofort)"),
        UiButton(id="B-2", text="", icon="content-copy"),
    ):
        parameter_widgets._render_button(  # pyright: ignore[reportPrivateUsage]
            _DEVICE, button, "page", actions
        )
    assert borders == [1.0, 1.0]
    assert icons == [16.0]


def test_a_number_field_with_spin_arrows_as_the_reference_draws_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels: list[tuple[float, float, float]] = []
    draw = imgui.ImDrawList.add_rect_filled

    def record(
        self: imgui.ImDrawList,
        low: imgui.ImVec2,
        high: imgui.ImVec2,
        col: int,
        *args: Any,
    ) -> None:
        if (high.x - low.x, high.y - low.y) == (1.0, 1.0):
            pixels.append((low.x, low.y, ((col >> 24) & 0xFF) / 255))
        draw(self, low, high, col, *args)

    monkeypatch.setattr(imgui.ImDrawList, "add_rect_filled", record)
    top = imgui.get_cursor_screen_pos().y
    field = imgui.get_frame_height()
    imgui.set_next_item_width(60)
    with parameter_widgets._spin_field() as frame:  # pyright: ignore[reportPrivateUsage]
        imgui.input_text("##n", "4477")
    parameter_widgets._spin_buttons("n", enabled=True, frame=frame)  # pyright: ignore[reportPrivateUsage]
    # The frame a pixel above and below where a field is; the row keeps a field's height.
    low, high = frame
    assert (low.y, high.y) == (top - 1, top + field + 1)
    assert internal.get_current_window().dc.cursor_max_pos.y == top + field
    # Arrows 6 px wide and 4 px high, centred 11 px from the right edge, their bases 9 px below
    # the top and 8 px above the bottom; the up arrow is the down arrow mirrored.
    up_base, down_base = low.y + 9, high.y - 8
    up = {(x, y): a for x, y, a in pixels if y < up_base}
    down = {(x, y): a for x, y, a in pixels if y >= down_base}
    assert len(up) + len(down) == len(pixels)
    assert {x for x, _ in up} == {x for x, _ in down}
    assert min(x for x, _ in up) == high.x - 11 - 3
    assert max(x for x, _ in up) == high.x - 11 + 2
    assert {y for _, y in up} == {up_base - 4, up_base - 3, up_base - 2, up_base - 1}
    # Mirrored: each row as far from its base, the same pixels.
    mirrored = {(x, down_base + (up_base - 1 - y)): a for (x, y), a in up.items()}
    assert down == pytest.approx(mirrored)


def test_an_information_box_places_its_icon_and_text_as_the_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    circles: list[imgui.ImVec2] = []
    rects: list[tuple[float, float, float, float]] = []
    texts: list[float] = []
    circle = imgui.ImDrawList.add_circle_filled
    rect = imgui.ImDrawList.add_rect_filled

    def record_circle(self: imgui.ImDrawList, centre: imgui.ImVec2, *args: Any) -> None:
        circles.append(centre)
        circle(self, centre, *args)

    def record_rect(
        self: imgui.ImDrawList, low: imgui.ImVec2, high: imgui.ImVec2, *args: Any
    ) -> None:
        rects.append((low.x, low.y, high.x, high.y))
        rect(self, low, high, *args)

    monkeypatch.setattr(imgui.ImDrawList, "add_circle_filled", record_circle)
    monkeypatch.setattr(imgui.ImDrawList, "add_rect_filled", record_rect)
    monkeypatch.setattr(
        parameter_widgets,
        "wrapped_text",
        lambda _text, _width: texts.append(imgui.get_cursor_screen_pos().y),
    )
    top = imgui.get_cursor_screen_pos()
    parameter_widgets._render_hint(  # pyright: ignore[reportPrivateUsage]
        "Falls diese Applikation …",
        imgui.ImVec4(0, 0, 1, 1),
        imgui.ImVec4(1, 1, 1, 1),
        imgui.ImVec4(0, 0, 1, 1),
        "i",
    )
    # The 16 px icon 11 px from the box's left and top edges, the text 7 px below the top.
    [centre] = circles
    assert (centre.x - top.x, centre.y - top.y) == (19, 19)
    assert texts == [top.y + 7]
    # Its "i": a dot of 2 x 2 px 3 px below the icon's top, a stem of 2 x 6 px 7 px below it.
    dot, stem = rects[-2:]
    icon_top = top.y + 11
    assert dot == (centre.x - 1, icon_top + 3, centre.x + 1, icon_top + 5)
    assert stem == (centre.x - 1, icon_top + 7, centre.x + 1, icon_top + 13)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ((0.2, 0.2, 0.2), (0.0, 0.0, 0.0)),
        ((0.9, 0.9, 0.9), (1.0, 1.0, 1.0)),
        # A changed value keeps its tint.
        ((0.36, 0.71, 1.0), (0.36, 0.71, 1.0)),
    ],
)
def test_a_number_field_shows_its_text_black_on_a_light_theme(
    text: tuple[float, float, float], shown: tuple[float, float, float]
) -> None:
    color = parameter_widgets._field_text(  # pyright: ignore[reportPrivateUsage]
        imgui.ImVec4(*text, 1.0)
    )
    assert (color.x, color.y, color.z) == pytest.approx(shown)


def test_a_button_label_is_centred_on_whole_pixels_as_wpf_centres_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Its width rounded (86.31 -> 86), then the room beside it halved and rounded: in a button
    # 114 px wide the label starts 14 px in, where the reference puts "Ermitteln (sofort)".
    starts: list[float] = []
    monkeypatch.setattr(text_layout, "text_width", lambda _text: 86.31)
    monkeypatch.setattr(
        text_layout, "draw_text_at", lambda _text, origin: starts.append(origin.x)
    )
    left = imgui.get_cursor_screen_pos().x
    parameter_widgets._page_button(  # pyright: ignore[reportPrivateUsage]
        None, "Ermitteln (sofort)", "###b", imgui.ImVec2(114, 24)
    )
    assert starts == [left + 14]
