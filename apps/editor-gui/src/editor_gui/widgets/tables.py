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
from editor_gui.widgets.strings import S

_SETTINGS = "app"
_GENERATION_KEY = "table_generation"
_generation: list[int] = []


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
) -> bool:
    """``imgui.begin_table`` for a table of data, with the header menu of
    :func:`data_table_headers` instead of the built-in one."""
    opened = imgui.begin_table(
        data_table_id(str_id),
        columns,
        flags,
        outer_size if outer_size is not None else imgui.ImVec2(0.0, 0.0),
    )
    if opened:
        internal.get_current_table().disable_default_context_menu = True
    return opened


def data_table_headers() -> None:
    """The header row of a table begun with :func:`begin_data_table`, and its menu."""
    imgui.table_headers_row()
    table_header_menu()


def table_header_menu() -> None:
    """For a table begun with :func:`begin_data_table` whose header row is drawn otherwise (cell
    by cell), right after it: the menu a right click on the column headers opens: size the
    clicked or every column to its content, reset the order, the visibility or everything, show
    or hide columns."""
    table = internal.get_current_table()
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
