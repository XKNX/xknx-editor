"""Tables of data: a menu on their column headers to size and reset the columns, and a reset of
every table when the default layout is restored.

The column settings of a table (widths, order, visibility, sorting) are stored by its id. Each
table's id carries a generation, which restoring the default layout counts up: every table then
starts over with its default columns, also one not shown at the moment.
"""

from __future__ import annotations

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.settings import load_settings, save_settings
from editor_gui.widgets.dpi import px
from editor_gui.widgets.strings import S

_SETTINGS = "app"
# Narrower than this at 100 % scaling (a phone), a table is laid out this wide and scrolls
# sideways instead of squeezing its columns.
_NARROW_TABLE = 560.0
_GENERATION_KEY = "table_generation"
_generation: list[int] = []
# The width the header labels of a table need (by its imgui id), measured each frame its header
# row is drawn: below it the table scrolls sideways instead of cutting the labels off.
_header_widths: dict[int, float] = {}


def _current_generation() -> int:
    if not _generation:
        value = load_settings(_SETTINGS).get(_GENERATION_KEY, 0)
        _generation.append(value if isinstance(value, int) else 0)
    return _generation[0]


def reset_all_tables() -> None:
    """Every table back to its default columns: widths, order, visibility and sorting."""
    generation = _current_generation() + 1
    _generation[:] = [generation]
    data = load_settings(_SETTINGS)
    data[_GENERATION_KEY] = generation
    save_settings(_SETTINGS, data)


def data_table_id(str_id: str) -> str:
    """The id of the table ``str_id`` in the current generation (unchanged until the first
    reset, so stored column settings stay)."""
    generation = _current_generation()
    return f"{str_id}#{generation}" if generation else str_id


def begin_data_table(
    str_id: str,
    columns: int,
    flags: int = 0,
    outer_size: imgui.ImVec2 | None = None,
    *,
    scroll_when_narrow: bool = False,
    min_width: float = _NARROW_TABLE,
    needed_width: float = 0.0,
) -> bool:
    """``imgui.begin_table`` for a table of data, with the header menu of
    :func:`data_table_headers` instead of the built-in one.

    Where the space is narrower than the table needs, a table that scrolls keeps the width it
    needs and scrolls sideways; with ``scroll_when_narrow`` one that does not scroll does so too,
    filling the rest of the window then. It needs ``min_width`` (at 100 % scaling: a phone is
    narrower), ``needed_width`` (in pixels, e.g. its columns' :func:`columns_width`) and the
    width of its header labels, whichever is the most; where there is more, it fills it."""
    if not flags & imgui.TableFlags_.no_pad_outer_x:
        # The first header and cell keep the same distance to the left edge as between columns.
        flags |= imgui.TableFlags_.pad_outer_x
    inner_width = 0.0
    table_id = data_table_id(str_id)
    width = max(
        px(min_width), needed_width, _header_widths.get(imgui.get_id(table_id), 0.0)
    )
    scrolls = bool(flags & imgui.TableFlags_.scroll_y) or scroll_when_narrow
    # An explicitly horizontal table lets its columns determine the scroll range.
    if (
        scrolls
        and not flags & imgui.TableFlags_.scroll_x
        and imgui.get_content_region_avail().x < width
    ):
        flags |= imgui.TableFlags_.scroll_x | imgui.TableFlags_.scroll_y
        inner_width = width
    opened = imgui.begin_table(
        table_id,
        columns,
        flags,
        outer_size if outer_size is not None else imgui.ImVec2(0.0, 0.0),
        inner_width,
    )
    if opened:
        internal.get_current_table().disable_default_context_menu = True
    return opened


def columns_width(widths: list[float]) -> float:
    """The width a table needs for columns of ``widths`` (their content, in pixels): with the
    padding of each cell and the outer padding of a table begun with
    :func:`begin_data_table`."""
    padding = imgui.get_style().cell_padding.x
    return sum(widths) + 2 * padding * (len(widths) + 1)


def _remember_header_width(table: internal.Table) -> None:
    """Note the width the shown header labels of ``table`` need (see :func:`begin_data_table`)."""
    style = imgui.get_style()
    arrow = imgui.get_font_size() * SORT_ARROW_SCALE + style.frame_padding.x
    widths: list[float] = []
    for column in range(table.columns_count):
        flags = imgui.table_get_column_flags(column)
        if not flags & imgui.TableColumnFlags_.is_enabled:
            continue
        label = (internal.table_get_column_name(table, column) or "").split("##", 1)[0]
        width = imgui.calc_text_size(label).x
        if table.flags & imgui.TableFlags_.sortable and not (
            flags & imgui.TableColumnFlags_.no_sort
        ):
            width += arrow
        widths.append(width)
    _header_widths[table.id_] = columns_width(widths)


def data_table_headers() -> None:
    """The header row of a table begun with :func:`begin_data_table`, and its menu. In a table
    that scrolls vertically, the header row stays at the top (columns the caller froze stay
    frozen)."""
    freeze_header()
    headers_row()
    table_header_menu()


# imgui draws a header's sort arrow this much smaller than the text.
SORT_ARROW_SCALE = 0.65


def headers_row() -> None:
    """``imgui.table_headers_row`` with the headers of :func:`table_header`."""
    imgui.table_next_row(
        imgui.TableRowFlags_.headers, internal.table_get_header_row_height()
    )
    for column in range(imgui.table_get_column_count()):
        if not imgui.table_set_column_index(column):
            continue
        hidden = (
            imgui.table_get_column_flags(column)
            & imgui.TableColumnFlags_.no_header_label
        )
        name = "" if hidden else imgui.table_get_column_name(column) or ""
        imgui.push_id(column)
        table_header(name)
        imgui.pop_id()
        _header_cell_lines(column)


def row_selectable(
    label: str,
    selected: bool = False,
    flags: int = 0,
    size: imgui.ImVec2 | None = None,
) -> tuple[bool, bool]:
    """``imgui.selectable`` that selects the whole row of the current table: its highlight
    (selected, hovered) fills the row from line to line. imgui reaches half the item spacing
    past the text, so the spacing becomes the row's padding above and below; where the table
    draws lines between its rows, the line on the row's first pixel stays uncovered."""
    padding = imgui.get_style().cell_padding.y
    spacing = imgui.get_style().item_spacing
    reach = padding * 2
    if internal.get_current_table().flags & imgui.TableFlags_.borders_inner_h:
        # imgui reaches the lower half of the spacing (rounded down) above the text.
        reach -= 1.0
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, reach))
    try:
        return imgui.selectable(
            label,
            selected,
            flags | imgui.SelectableFlags_.span_all_columns,
            size or imgui.ImVec2(0.0, 0.0),
        )
    finally:
        imgui.pop_style_var()


def _header_cell_lines(column: int) -> None:
    """Lines around a header cell, as in Windows lists: along its bottom and at its right edge
    (where the table draws no column line of its own), in the color of the table's lines."""
    table = internal.get_current_table()
    cell = internal.table_get_cell_bg_rect(table, column)
    color = imgui.get_color_u32(imgui.Col_.table_border_light)
    draw = imgui.get_window_draw_list()
    # In the cell's own channel and clip rectangle, on top of the header's background.
    bottom = cell.max.y - 0.5
    draw.add_line(
        imgui.ImVec2(cell.min.x, bottom), imgui.ImVec2(cell.max.x, bottom), color
    )
    if not table.flags & imgui.TableFlags_.borders_inner_v:
        right = cell.max.x - 0.5
        draw.add_line(
            imgui.ImVec2(right, cell.min.y), imgui.ImVec2(right, cell.max.y), color
        )


def _sort_direction(column: int) -> imgui.SortDirection | None:
    """The direction the current table is sorted by ``column`` in, None when not by it."""
    if not imgui.table_get_column_flags(column) & imgui.TableColumnFlags_.is_sorted:
        return None
    # A sorted column: the table sorts, so it has sort specs.
    specs = imgui.table_get_sort_specs()
    for index in range(specs.specs_count):
        spec = specs.get_specs(index)
        if spec.column_index == column:
            return spec.sort_direction
    return None


def table_header(label: str) -> None:
    """``imgui.table_header`` of the current column, with its sort arrow in the middle of the
    text's line: imgui puts the smaller arrow at the top of the line."""
    direction = _sort_direction(imgui.table_get_column_index())
    if direction is None:
        imgui.table_header(label)
        return
    start = imgui.get_cursor_screen_pos()
    # imgui's header as it is (its clicks sort), its text and arrow drawn here instead.
    imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.0, 0.0, 0.0, 0.0))
    imgui.table_header(label)
    imgui.pop_style_color()
    if not imgui.is_item_visible():
        return
    style = imgui.get_style()
    font = imgui.get_font_size()
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    arrow_w = int(font * SORT_ARROW_SCALE + style.frame_padding.x)
    text = label.split("##", 1)[0]
    size = imgui.calc_text_size(text)
    end = max(high.x - arrow_w, start.x)
    draw = imgui.get_window_draw_list()
    internal.render_text_ellipsis(
        draw,
        start,
        imgui.ImVec2(end, start.y + size.y + style.frame_padding.y),
        end,
        text,
        size,
    )
    internal.render_arrow(
        draw,
        imgui.ImVec2(
            max(low.x, high.x - arrow_w),
            start.y + font * 0.5 * (1.0 - SORT_ARROW_SCALE),
        ),
        imgui.get_color_u32(imgui.Col_.text),
        imgui.Dir.up if direction == imgui.SortDirection.ascending else imgui.Dir.down,
        SORT_ARROW_SCALE,
    )


def freeze_header() -> None:
    """Keep the header row of the current table at the top while its rows scroll vertically;
    call it after the columns are set up, before the header row."""
    table = internal.get_current_table()
    if table.flags & imgui.TableFlags_.scroll_y and table.freeze_rows_request == 0:
        imgui.table_setup_scroll_freeze(table.freeze_columns_request, 1)


def text_table_height(rows: int, min_width: float = _NARROW_TABLE) -> float:
    """The height of a scrolling table showing its header and ``rows`` rows of one text line, no
    more, and the sideways scrollbar where :func:`begin_data_table` makes it scroll sideways
    (with the same ``min_width``)."""
    style = imgui.get_style()
    height = (rows + 1) * (imgui.get_text_line_height() + 2 * style.cell_padding.y)
    if imgui.get_content_region_avail().x < px(min_width):
        height += style.scrollbar_size
    return height


def table_header_menu() -> None:
    """For a table begun with :func:`begin_data_table` whose header row is drawn otherwise (cell
    by cell), right after it: the menu a right click on the column headers opens: size the
    clicked or every column to its content, reset the order, the visibility or everything, show
    or hide columns."""
    table = internal.get_current_table()
    _remember_header_width(table)
    # Read and changed while the table is current: in its menu (a popup) it is not.
    columns = [
        (internal.table_get_column_name(table, n), imgui.table_get_column_flags(n))
        for n in range(table.columns_count)
    ]
    if not internal.table_begin_context_menu_popup(table):
        return
    enable: dict[int, bool] = {}
    column = table.context_popup_column
    if column >= 0 and imgui.menu_item(S.TABLE_SIZE_COLUMN, "", False)[0]:
        internal.table_set_column_width_auto_single(table, column)
    if imgui.menu_item(S.TABLE_SIZE_ALL, "", False)[0]:
        internal.table_set_column_width_auto_all(table)
    imgui.separator()
    if imgui.menu_item(S.TABLE_RESET_ORDER, "", False)[0]:
        table.is_reset_display_order_request = True
    hideable = bool(table.flags & imgui.TableFlags_.hideable)
    if hideable and imgui.menu_item(S.TABLE_RESET_VISIBILITY, "", False)[0]:
        for n, (_name, flags) in enumerate(columns):
            enable[n] = not flags & imgui.TableColumnFlags_.default_hide
    if imgui.menu_item(S.TABLE_RESET_ALL, "", False)[0]:
        table.is_reset_all_request = True
    if hideable:
        imgui.separator()
        for n, (name, flags) in enumerate(columns):
            enabled = bool(flags & imgui.TableColumnFlags_.is_enabled)
            can_hide = not flags & imgui.TableColumnFlags_.no_hide
            # The id apart from the label: a label ending in "#" (the "#" column) would run
            # into an id suffix and hide itself.
            imgui.push_id(n)
            if imgui.menu_item(name.split("##")[0], "", enabled, can_hide)[0]:
                enable[n] = not enabled
            imgui.pop_id()
    imgui.end_popup()
    for n, enabled in enable.items():
        imgui.table_set_column_enabled(n, enabled)
