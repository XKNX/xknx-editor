"""The coupler filter-table dialog: the group addresses a coupler routes, with export.

Mirrors ETS's "Preview Filter Table": the computed routing set (which group addresses cross the
coupler), read-only, for inspection and export. The table itself is programmed into the coupler by
a download; this only shows and exports it."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.dialog import begin_dialog, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.dpt_value import dpt_label
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.text_util import text_clipped_tooltip

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import CouplerFilterTable


class FilterTableDialog:
    """A modal listing a coupler's routed group addresses, with "Export…" to CSV.

    ``get_table`` computes the table for a device node id (None for a device that is no coupler)."""

    def __init__(self, get_table: Callable[[int], CouplerFilterTable | None]) -> None:
        self._get_table = get_table
        self._pending: int | None = None
        self._table: CouplerFilterTable | None = None
        self._save: file_dialogs.SaveFileDialog | None = None
        self._error = ""

    def open(self, node_id: int) -> None:
        """Open the dialog for the coupler ``node_id`` (ignored when it is no coupler)."""
        table = self._get_table(node_id)
        if table is None:
            return
        self._table = table
        self._pending = node_id
        self._error = ""

    def render(self) -> None:
        """Draw the dialog; call once per frame in the window that opens it."""
        self._poll_save()
        if self._pending is not None:
            imgui.open_popup(S.FILTER_TABLE_TITLE)
            self._pending = None
        table = self._table
        if table is None:
            return
        if not begin_dialog(S.FILTER_TABLE_TITLE, min_width=460.0):
            return
        heading = f"{table.device_address}  {table.device_name}".strip()
        imgui.text_unformatted(heading)
        imgui.spacing()
        if not table.entries:
            imgui.text_disabled(S.FILTER_TABLE_EMPTY)
        else:
            imgui.text_disabled(
                f"{S.FILTER_TABLE_GROUP_ADDRESSES} ({len(table.entries)})"
            )
            self._render_table(table)
        imgui.spacing()
        if imgui.button(S.FILTER_TABLE_EXPORT, imgui.ImVec2(px(120.0), 0.0)):
            self._save = file_dialogs.save_file(
                S.FILTER_TABLE_EXPORT,
                default_path=f"filter-table-{table.device_address}.csv",
                filters=["CSV", "*.csv"],
            )
        imgui.same_line()
        if imgui.button(S.BTN_CLOSE, imgui.ImVec2(px(120.0), 0.0)):
            imgui.close_current_popup()
        if self._error:
            imgui.text_colored(status_color("error"), self._error)
        end_dialog()

    def _render_table(self, table: CouplerFilterTable) -> None:
        flags = (
            imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.scroll_y
        )
        if not begin_data_table("filter_table", 3, flags, imgui.ImVec2(0.0, px(320.0))):
            return
        stretch = imgui.TableColumnFlags_.width_stretch
        imgui.table_setup_column(S.GA_WRITE_ADDRESS, stretch, 0.3)
        imgui.table_setup_column(S.FILTER_TABLE_NAME, stretch, 0.45)
        imgui.table_setup_column(S.GA_WRITE_DPT, stretch, 0.25)
        data_table_headers()
        clipper = imgui.ListClipper()
        clipper.begin(len(table.entries))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                entry = table.entries[i]
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text_unformatted(entry.address)
                imgui.table_set_column_index(1)
                text_clipped_tooltip(entry.name)
                imgui.table_set_column_index(2)
                text_clipped_tooltip(dpt_label(entry.datapoint_type))
        imgui.end_table()

    def _poll_save(self) -> None:
        if self._save is None or not self._save.ready():
            return
        path = self._save.result()
        self._save = None
        if path and self._table is not None:
            from pathlib import Path

            try:
                Path(path).write_text(_csv(self._table), encoding="utf-8", newline="")
            except OSError as exc:
                self._error = str(exc)


def _safe_cell(text: str) -> str:
    """A CSV cell that a spreadsheet will not run as a formula: a leading =, +, -, @, tab or
    carriage return (through which formula injection works) is prefixed with an apostrophe."""
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _csv(table: CouplerFilterTable) -> str:
    """The filter table as CSV: a header row, then one row per routed group address."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([S.GA_WRITE_ADDRESS, S.FILTER_TABLE_NAME, S.GA_WRITE_DPT])
    for entry in table.entries:
        writer.writerow(
            [
                _safe_cell(entry.address),
                _safe_cell(entry.name),
                _safe_cell(dpt_label(entry.datapoint_type)),
            ]
        )
    return buffer.getvalue()
