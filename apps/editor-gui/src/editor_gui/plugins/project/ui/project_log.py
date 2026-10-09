"""Project log view: the project traces carried over on import.

Shows ProjectInformation/ProjectTraces as a filterable, sortable table. Date and User are plaintext;
the Comment is stored verbatim (it is encrypted on disk). A one-off key extraction from the user's
own ``Knx.Ets.Common.dll`` (top-of-panel button) unlocks the comments; until then they stay opaque.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd

from editor_gui import trace_key
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from xknxeditor.proj import (
    TRACE_DLL_NAME,
    KeyExtractionError,
    default_trace_dll_path,
    extract_trace_key,
    extraction_backend,
    trace_key_available,
)

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import _ProjectTrace

_RED = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)
_GREEN = imgui.ImVec4(0.4, 0.8, 0.4, 1.0)


class ProjectLogPanel:
    def __init__(self, get_traces: Callable[[], list[_ProjectTrace]]) -> None:
        self._get_traces = get_traces
        self._filter = ""
        # 0 = Date, 1 = User, 2 = Comment; default: newest first (Date descending).
        self._sort_key = 0
        self._sort_desc = True
        # Key extraction (background thread; the .NET call takes a moment).
        self._dialog: pfd.open_file | None = None
        self._extracting = False
        self._error: str | None = None
        self._result: tuple[bytes, bytes, str] | None = None
        self._result_error: str | None = None

    def render(self) -> None:
        self._poll_dialog()
        self._apply_extraction_result()
        self._render_key_bar()

        traces = self._get_traces()
        if not traces:
            imgui.text_disabled(S.PROJECT_LOG_EMPTY)
            return

        self._filter = filter_box(
            "##project_log_filter", S.PROJECT_LOG_FILTER_HINT, self._filter
        )

        needle = self._filter.strip().lower()
        rows = [t for t in traces if not needle or self._matches(t, needle)]
        rows.sort(key=self._sort_value, reverse=self._sort_desc)

        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.scroll_y
        )
        if not imgui.begin_table("##project_log", 3, flags):
            return
        imgui.table_setup_column(
            S.PROJECT_LOG_COL_DATE, imgui.TableColumnFlags_.width_fixed, px(150.0)
        )
        imgui.table_setup_column(
            S.PROJECT_LOG_COL_USER, imgui.TableColumnFlags_.width_fixed, px(120.0)
        )
        imgui.table_setup_column(
            S.PROJECT_LOG_COL_COMMENT, imgui.TableColumnFlags_.width_stretch, 1.0
        )
        self._sortable_header()

        for t in rows:
            comment = self._display(t)
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text_unformatted(t.date or "-")
            imgui.table_set_column_index(1)
            imgui.text_unformatted(t.user_name or "-")
            imgui.table_set_column_index(2)
            imgui.text_unformatted(comment or "-")
            if comment and imgui.is_item_hovered():
                imgui.set_tooltip(comment)
        imgui.end_table()

    # --- key extraction bar --------------------------------------------------------------

    def _render_key_bar(self) -> None:
        if trace_key_available():
            imgui.text_colored(_GREEN, S.PROJECT_LOG_DECRYPTED)
            return
        imgui.text_colored(_RED, S.PROJECT_LOG_ENCRYPTED)
        imgui.text_wrapped(S.PROJECT_LOG_DECRYPT_HINT)
        backend = extraction_backend()
        if backend is None:
            imgui.text_colored(_RED, S.PROJECT_LOG_NO_BACKEND)
        if self._extracting:
            imgui.text_disabled(S.PROJECT_LOG_EXTRACTING)
        else:
            imgui.begin_disabled(backend is None)
            if imgui.button(S.PROJECT_LOG_EXTRACT):
                self._begin_extract()
            imgui.end_disabled()
        if self._error:
            imgui.text_colored(_RED, self._error)
        imgui.text_disabled(S.PROJECT_LOG_CREDIT)
        imgui.separator()

    def _begin_extract(self) -> None:
        self._dialog = pfd.open_file(
            S.PROJECT_LOG_EXTRACT,
            default_trace_dll_path(),
            [
                TRACE_DLL_NAME,
                TRACE_DLL_NAME,
                S.PROJECT_LOG_DLL_FILTER,
                "*.dll",
                S.PROJECT_LOG_ALL_FILES,
                "*",
            ],
        )

    def _poll_dialog(self) -> None:
        if self._dialog is not None and self._dialog.ready():
            result = self._dialog.result()
            self._dialog = None
            if result:
                self._start_extraction(result[0])

    def _start_extraction(self, dll_path: str) -> None:
        self._extracting = True
        self._error = None
        self._result = None
        self._result_error = None

        def _work() -> None:
            try:
                self._result = extract_trace_key(dll_path)
            except (KeyExtractionError, Exception) as exc:
                self._result_error = f"{type(exc).__name__}: {exc}"

        threading.Thread(target=_work, daemon=True).start()

    def _apply_extraction_result(self) -> None:
        if not self._extracting:
            return
        if self._result is not None:
            trace_key.save_key(*self._result)
            self._extracting = False
            self._result = None
        elif self._result_error is not None:
            self._error = self._result_error
            self._extracting = False
            self._result_error = None

    # --- table helpers -------------------------------------------------------------------

    @staticmethod
    def _display(t: _ProjectTrace) -> str:
        return t.comment_plain or t.comment

    def _sortable_header(self) -> None:
        """Header row whose cells are clickable to sort by that column (no imgui sort-spec API)."""
        labels = (
            S.PROJECT_LOG_COL_DATE,
            S.PROJECT_LOG_COL_USER,
            S.PROJECT_LOG_COL_COMMENT,
        )
        imgui.table_next_row(imgui.TableRowFlags_.headers)
        for col, label in enumerate(labels):
            imgui.table_set_column_index(col)
            marker = ""
            if col == self._sort_key:
                marker = " v" if self._sort_desc else " ^"
            if imgui.selectable(f"{label}{marker}##hdr{col}", False):
                self._toggle_sort(col)

    def _toggle_sort(self, col: int) -> None:
        if self._sort_key == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = col
            self._sort_desc = False

    def _sort_value(self, t: _ProjectTrace) -> str:
        if self._sort_key == 1:
            return t.user_name.lower()
        if self._sort_key == 2:
            return self._display(t).lower()
        return t.date

    def _matches(self, t: _ProjectTrace, needle: str) -> bool:
        return (
            needle in t.date.lower()
            or needle in t.user_name.lower()
            or needle in self._display(t).lower()
        )
