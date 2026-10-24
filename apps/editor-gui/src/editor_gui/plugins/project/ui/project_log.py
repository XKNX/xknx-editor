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

from editor_gui import file_dialogs, trace_key
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.proj import (
    TRACE_DLL_NAME,
    KeyExtractionError,
    default_trace_dll_path,
    extract_trace_key,
    extraction_backend,
    trace_key_available,
)

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectTrace

_RED = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)
_GREEN = imgui.ImVec4(0.4, 0.8, 0.4, 1.0)
_UNKNOWN = "?"


class ProjectLogPanel:
    def __init__(self, get_traces: Callable[[], list[ProjectTrace]]) -> None:
        self._get_traces = get_traces
        self._filter = ""
        # 0 = Date, 1 = User, 2 = Comment; default: newest first (Date descending).
        self._sort_key = 0
        self._sort_desc = True
        # Key extraction (background thread; the .NET call takes a moment).
        self._dialog: file_dialogs.OpenFileDialog | None = None
        self._extracting = False
        self._error: str | None = None
        self._result: tuple[bytes, bytes, str] | None = None
        self._result_error: str | None = None
        # The tool that extracts the key (see extraction_backend), once looked up.
        self._backend: str | None = _UNKNOWN
        # The rows shown: the log, filter and sorting they were made for, and the rows.
        self._shown: (
            tuple[list[ProjectTrace], tuple[str, int, bool], list[ProjectTrace]] | None
        ) = None

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

        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.reorderable
            | imgui.TableFlags_.sortable
        )
        if not begin_data_table("##project_log", 3, flags):
            return
        imgui.table_setup_column(
            S.PROJECT_LOG_COL_DATE,
            imgui.TableColumnFlags_.width_fixed
            | imgui.TableColumnFlags_.default_sort
            | imgui.TableColumnFlags_.prefer_sort_descending,
            px(150.0),
        )
        imgui.table_setup_column(
            S.PROJECT_LOG_COL_USER, imgui.TableColumnFlags_.width_fixed, px(120.0)
        )
        imgui.table_setup_column(
            S.PROJECT_LOG_COL_COMMENT, imgui.TableColumnFlags_.width_stretch, 1.0
        )
        data_table_headers()
        self._read_sort()
        rows = self._rows(traces, self._filter.strip().lower())

        # Only the rows in view are drawn: a project log can have thousands of entries.
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        while clipper.step():
            for t in rows[clipper.display_start : clipper.display_end]:
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
        # Looked up once: finding the tools searches the PATH, too slow for every frame.
        if self._backend is _UNKNOWN:
            self._backend = extraction_backend()
        backend = self._backend
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
        self._dialog = file_dialogs.open_file(
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

    def _rows(self, traces: list[ProjectTrace], needle: str) -> list[ProjectTrace]:
        """The rows shown, filtered by ``needle`` and sorted; worked out again only when the log,
        the filter or the sorting changed."""
        state = (needle, self._sort_key, self._sort_desc)
        if (
            self._shown is None
            or self._shown[0] is not traces
            or self._shown[1] != state
        ):
            rows = [t for t in traces if not needle or self._matches(t, needle)]
            rows.sort(key=self._sort_value, reverse=self._sort_desc)
            self._shown = (traces, state, rows)
        return self._shown[2]

    @staticmethod
    def _display(t: ProjectTrace) -> str:
        return t.comment_plain or t.comment

    def _read_sort(self) -> None:
        """The column and direction the user sorts by, from the header row."""
        specs = imgui.table_get_sort_specs()
        # The bindings type the specs as non-optional, but they are None outside a sortable table.
        if specs is None or not specs.specs_count:  # pyright: ignore[reportUnnecessaryComparison]
            return
        column = specs.get_specs(0)
        self._sort_key = column.column_index
        self._sort_desc = column.get_sort_direction() == imgui.SortDirection.descending

    def _sort_value(self, t: ProjectTrace) -> str:
        if self._sort_key == 1:
            return t.user_name.lower()
        if self._sort_key == 2:
            return self._display(t).lower()
        return t.date

    def _matches(self, t: ProjectTrace, needle: str) -> bool:
        return (
            needle in t.date.lower()
            or needle in t.user_name.lower()
            or needle in self._display(t).lower()
        )
