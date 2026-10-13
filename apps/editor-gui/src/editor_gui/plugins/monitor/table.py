"""One monitor tab: toolbar, status, filter and the telegram table with multi-selection."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd

from editor_gui.os_open import open_path
from editor_gui.plugins.connection.service import BusmonitorState
from editor_gui.plugins.monitor.formatting import (
    COLUMN_INDEX,
    COLUMNS,
    PRINT_COLUMNS,
    PRINT_WEIGHTS,
    ProjectIndex,
    format_cells,
    record_cells,
)
from editor_gui.plugins.monitor.pdf import table_pdf
from editor_gui.plugins.monitor.records import MonitorRecord, RecordKind
from editor_gui.plugins.monitor.service import MonitorKind
from editor_gui.plugins.monitor.strings import S
from editor_gui.plugins.monitor.xmlio import export_xml, import_xml
from editor_gui.strings import get_locale
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.filter_box import filter_box

if TYPE_CHECKING:
    from editor_gui.plugins.connection.service import ConnectionService
    from editor_gui.plugins.monitor.service import MonitorService

# FontAwesome 4 glyphs (the icon font the app loads).
ICON_START = ""
ICON_STOP = ""
ICON_CLEAR = ""
ICON_IMPORT = ""
ICON_EXPORT = ""
ICON_PRINT = ""
ICON_REPLAY = ""
ICON_STOP_REPLAY = ""
ICON_COPY = ""

# Seconds of a frame spent filtering a large log before showing what is done.
_FILTER_BUDGET = 0.008
_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_WARN_COLOR = imgui.ImVec4(0.95, 0.6, 0.15, 1.0)
_EVENT_COLOR = imgui.ImVec4(0.55, 0.7, 0.95, 1.0)
_FILE_NAMES = {
    MonitorKind.GROUP: "GroupMonitor",
    MonitorKind.BUS: "BusMonitor",
    MonitorKind.ACTIVITY: "BusActivity",
}
_WRAPPED_COLUMNS = {
    COLUMN_INDEX["source_name"],
    COLUMN_INDEX["destination_name"],
    COLUMN_INDEX["info"],
    COLUMN_INDEX["raw_data"],
    COLUMN_INDEX["building_part"],
    COLUMN_INDEX["building_function"],
}

# The compact column set of each monitor. "Show extended information" reveals every column
# (including date, the full type, the raw hex value and all the optional columns).
_SIMPLE_COLUMNS = {
    kind: frozenset(COLUMN_INDEX[key] for key in keys)
    for kind, keys in (
        (
            MonitorKind.GROUP,
            (
                "time",
                "type_short",
                "source_address",
                "destination_address",
                "destination_name",
                "info",
            ),
        ),
        (
            MonitorKind.BUS,
            (
                "time",
                "type_short",
                "flags",
                "source_address",
                "destination_address",
                "info",
            ),
        ),
        (
            MonitorKind.ACTIVITY,
            (
                "time",
                "service",
                "type_short",
                "source_address",
                "destination_address",
                "info",
            ),
        ),
    )
}


@dataclass
class _Question:
    title: str
    text: str
    on_yes: Callable[[], None]
    opened: bool = False


class TelegramView:
    def __init__(
        self,
        kind: MonitorKind,
        service: MonitorService,
        connection: ConnectionService,
        notify: Callable[[str], None] | None = None,
        on_select_device: Callable[[int], None] | None = None,
    ) -> None:
        self.kind = kind
        self._service = service
        self._connection = connection
        self._notify = notify
        self._on_select_device = on_select_device
        self._channel = service.channel(kind)
        self.filter = ""
        # The rows shown: the log's records that match the filter, oldest first.
        self._source: list[MonitorRecord] = []
        self._view: list[MonitorRecord] = []
        self._pos = 0
        self._view_gen: int = -1
        self._view_filter = ""
        self._view_key: object = None
        self._last_rows = 0
        # Selected rows by record number, and the row clicked last.
        self.selected: set[int] = set()
        self.focused: MonitorRecord | None = None
        self._question: _Question | None = None
        self._scope_action: str | None = None
        self._scope_choice = 0
        self._scope_opened = False
        self._save_dialog: pfd.save_file | None = None
        self._open_dialog: pfd.open_file | None = None
        self._pending_export: list[MonitorRecord] = []
        self._pending_export_all = False
        self._message: tuple[str, bool] | None = None
        self._replay_reported: tuple[int, bool] | None = None
        self._last_index = ProjectIndex()
        # Columns the user has not hidden, as of the last frame.
        self._enabled_columns: list[int] = list(PRINT_COLUMNS)
        # Start in the compact column set; the "Show extended information" toggle next to the
        # filter reveals the full detail table.
        self._extended = False
        # Set by the panel: flips the Compact/Extend bottom-bar layout and the column set together.
        self._toggle_layout: Callable[[], None] | None = None

    @property
    def extended(self) -> bool:
        return self._extended

    @extended.setter
    def extended(self, value: bool) -> None:
        self._extended = value

    def set_layout_toggler(self, toggle: Callable[[], None]) -> None:
        """Wire the Compact/Extend toggle button shown next to the filter."""
        self._toggle_layout = toggle

    # --- view ------------------------------------------------------------

    @property
    def view(self) -> list[MonitorRecord]:
        return self._view

    @property
    def filtering(self) -> bool:
        return self._pos < len(self._source)

    def update_view(self, index: ProjectIndex) -> None:
        """Bring the shown rows up to date with the log, filtering in small steps."""
        log = self._channel.log
        key = (index.revision, get_locale())
        needle = self.filter.strip().lower()
        rebuild: bool = (
            needle != self._view_filter
            or log.generation != self._view_gen
            or (bool(needle) and key != self._view_key)
        )
        if not rebuild:
            new = log.since(self._view_gen, len(self._source))
            if new is None:
                rebuild = True
            else:
                self._source.extend(new)
        if rebuild:
            self._view_gen = log.generation
            self._source = log.snapshot()
            self._view = []
            self._pos = 0
            self._view_filter = needle
            self._view_key = key
            known = {record.number for record in self._source}
            self.selected &= known
        if not needle:
            self._view.extend(self._source[self._pos :])
            self._pos = len(self._source)
            return
        deadline = time.perf_counter() + _FILTER_BUDGET
        source = self._source
        while self._pos < len(source):
            record = source[self._pos]
            record_cells(record, index, key)
            if needle in record.haystack:
                self._view.append(record)
            self._pos += 1
            if self._pos % 256 == 0 and time.perf_counter() > deadline:
                break

    def selected_records(self) -> list[MonitorRecord]:
        return [record for record in self._source if record.number in self.selected]

    # --- render ----------------------------------------------------------

    def render(
        self, index: ProjectIndex, command_bar: Callable[[], None] | None
    ) -> None:
        self._last_index = index
        self.update_view(index)
        self._render_toolbar()
        self._render_status()
        if command_bar is not None:
            command_bar()
        self._render_filter_row()
        self._render_table(index)
        self._handle_copy_shortcut()
        self._render_question()
        self._render_scope()
        self._poll_dialogs()

    def _render_filter_row(self) -> None:
        """The filter box, with the Compact/Extend toggle to the right of it."""
        toggle = self._toggle_layout
        reserve = 0.0
        label = ""
        if toggle is not None:
            label = (
                S.MONITOR_HIDE_EXTENDED if self._extended else S.MONITOR_SHOW_EXTENDED
            )
            reserve = (
                imgui.calc_text_size(label).x
                + imgui.get_style().frame_padding.x * 2.0
                + px(4.0)
            )
        self.filter = filter_box(
            f"##mon_filter_{self.kind.value}",
            S.MONITOR_FILTER_HINT,
            self.filter,
            reserve,
        )
        if toggle is not None:
            imgui.same_line(0.0, px(4.0))
            if imgui.button(f"{label}###mon_extend_toggle"):
                toggle()

    def _icon_button(self, icon: str, name: str, hint: str, enabled: bool) -> bool:
        imgui.begin_disabled(not enabled)
        clicked = imgui.button(f"{icon}##{name}_{self.kind.value}")
        imgui.end_disabled()
        imgui.set_item_tooltip(f"{name}\n{hint}" if hint else name)
        imgui.same_line()
        return clicked and enabled

    def _render_toolbar(self) -> None:
        channel = self._channel
        has_records = len(channel.log) > 0
        busmon = self._connection.busmonitor_state
        idle_dialogs = self._save_dialog is None and self._open_dialog is None
        if self.kind is MonitorKind.BUS:
            can_start = busmon is BusmonitorState.OFF and not self._service.replaying
            can_stop = busmon is BusmonitorState.RUNNING
            start_hint, stop_hint = S.TOOL_START_BUS_HINT, S.TOOL_STOP_BUS_HINT
        else:
            can_start = not channel.recording
            can_stop = channel.recording
            start_hint = (
                S.TOOL_START_GROUP_HINT
                if self.kind is MonitorKind.GROUP
                else S.TOOL_START_ACTIVITY_HINT
            )
            stop_hint = S.TOOL_STOP_HINT
        if self._icon_button(ICON_START, S.TOOL_START, start_hint, can_start):
            self._start()
        if self._icon_button(ICON_STOP, S.TOOL_STOP, stop_hint, can_stop):
            self._service.stop(self.kind)
        if self._icon_button(
            ICON_CLEAR, S.MONITOR_CLEAR, S.TOOL_CLEAR_HINT, has_records
        ):
            self._ask_unsaved(S.QUESTION_CLEAR_UNSAVED, self._clear)
        busy = busmon in (BusmonitorState.STARTING, BusmonitorState.STOPPING)
        if self._icon_button(
            ICON_IMPORT, S.TOOL_IMPORT, S.TOOL_IMPORT_HINT, idle_dialogs and not busy
        ):
            self._ask_import()
        if self._icon_button(
            ICON_EXPORT, S.TOOL_EXPORT, S.TOOL_EXPORT_HINT, has_records and idle_dialogs
        ):
            self._open_scope("export")
        if self._icon_button(ICON_PRINT, S.TOOL_PRINT, S.TOOL_PRINT_HINT, has_records):
            self._open_scope("print")
        if self._icon_button(
            ICON_COPY, S.TOOL_COPY, S.TOOL_COPY_HINT, bool(self._view)
        ):
            self._copy()
        if self.kind is MonitorKind.GROUP:
            replaying = self._service.replaying
            can_replay = not replaying and self._service.is_connected() and has_records
            if self._icon_button(
                ICON_REPLAY, S.TOOL_REPLAY, S.TOOL_REPLAY_HINT, can_replay
            ):
                self._replay()
            if self._icon_button(
                ICON_STOP_REPLAY, S.TOOL_STOP_REPLAY, S.TOOL_STOP_REPLAY_HINT, replaying
            ):
                self._service.stop_replay()
            self._report_replay()
        self._render_state()
        imgui.same_line()
        imgui.text_disabled(
            S.MONITOR_BUS_LOAD.format(rate=f"{self._service.bus_load():.1f}")
        )
        imgui.same_line()
        count = len(self._view)
        text = S.MONITOR_TELEGRAM_COUNT_ONE if count == 1 else S.MONITOR_TELEGRAM_COUNT
        imgui.text_disabled(text.format(count=count))
        if self.filtering:
            imgui.same_line()
            done = self._pos * 100 // max(len(self._source), 1)
            imgui.text_disabled(f"({done}%)")

    def _render_state(self) -> None:
        """The recording state in the toolbar row."""
        recording = imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
        if self.kind is MonitorKind.BUS:
            state = self._connection.busmonitor_state
            if state is BusmonitorState.STARTING:
                imgui.text_colored(_WARN_COLOR, S.STATUS_BUSMON_STARTING)
            elif state is BusmonitorState.STOPPING:
                imgui.text_colored(_WARN_COLOR, S.STATUS_BUSMON_STOPPING)
            elif state is BusmonitorState.RUNNING:
                imgui.text_colored(recording, S.STATUS_RECORDING)
            else:
                imgui.text_disabled(S.STATUS_STOPPED)
        elif self._channel.session:
            imgui.text_colored(recording, S.STATUS_RECORDING)
        elif self._channel.recording:
            imgui.text_disabled(S.STATUS_WAITING)
        else:
            imgui.text_disabled(S.STATUS_STOPPED)
        if self._service.replaying and self.kind is MonitorKind.GROUP:
            progress = self._service.replay_progress()
            if progress is not None:
                imgui.same_line()
                imgui.text_colored(
                    _WARN_COLOR,
                    S.STATUS_REPLAYING.format(done=progress[0], total=progress[1]),
                )

    def _render_status(self) -> None:
        """Notes below the toolbar: the bus monitor mode, its errors and other messages."""
        if self.kind is MonitorKind.BUS:
            state = self._connection.busmonitor_state
            if state is BusmonitorState.RUNNING:
                imgui.push_text_wrap_pos(0.0)
                imgui.text_colored(_WARN_COLOR, S.STATUS_BUSMON_RUNNING)
                imgui.pop_text_wrap_pos()
            elif state is BusmonitorState.OFF and self._connection.busmonitor_error:
                imgui.push_text_wrap_pos(0.0)
                imgui.text_colored(_ERROR_COLOR, self._busmon_error_text())
                imgui.pop_text_wrap_pos()
                detail = self._connection.busmonitor_error_detail
                if detail and imgui.is_item_hovered():
                    imgui.set_tooltip(detail)
        if self._message is not None:
            text, error = self._message
            imgui.push_text_wrap_pos(0.0)
            if error:
                imgui.text_colored(_ERROR_COLOR, text)
            else:
                imgui.text_disabled(text)
            imgui.pop_text_wrap_pos()

    def _busmon_error_text(self) -> str:
        error = self._connection.busmonitor_error
        if error == "routing":
            return S.BUSMON_UNSUPPORTED_CONNECTION
        text = {
            "unsupported": S.BUSMON_UNSUPPORTED,
            "busy": S.BUSMON_BUSY,
        }.get(error or "", S.BUSMON_FAILED)
        return f"{text} {S.BUSMON_RESTORED}"

    # --- table -----------------------------------------------------------

    def _render_table(self, index: ProjectIndex) -> None:
        extended = self._extended
        flags = (
            imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable
            | imgui.TableFlags_.hideable
            | imgui.TableFlags_.scroll_x
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.borders_inner_v
            | imgui.TableFlags_.sizing_fixed_fit
        )
        # Separate table id per mode so simple/extended keep their own column layout and switching
        # resets cleanly to the mode's defaults. Column settings are stored by index, so the id
        # changes whenever columns are inserted.
        table_id = f"##monitor_{self.kind.value}_{'full' if extended else 'simple'}"
        if not imgui.begin_table(table_id, len(COLUMNS), flags):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        for i, column in enumerate(COLUMNS):
            column_flags = imgui.TableColumnFlags_.width_fixed
            # Extended shows every column; compact shows only the simple set.
            hide = not extended and i not in _SIMPLE_COLUMNS[self.kind]
            if hide:
                column_flags |= imgui.TableColumnFlags_.default_hide
            imgui.table_setup_column(column.label, column_flags, px(column.width))
        imgui.table_headers_row()
        self._enabled_columns = [
            column
            for column in range(len(COLUMNS))
            if imgui.table_get_column_flags(column) & imgui.TableColumnFlags_.is_enabled
        ]
        at_bottom = imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 2.0
        key = (index.revision, get_locale())
        rows = self._view
        ms_flags = (
            imgui.MultiSelectFlags_.clear_on_escape
            | imgui.MultiSelectFlags_.box_select1d
        )
        ms_io = imgui.begin_multi_select(ms_flags, len(self.selected), len(rows))
        self._apply_requests(ms_io)
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        if ms_io.range_src_item != -1:
            clipper.include_item_by_index(int(ms_io.range_src_item))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                self._render_row(i, rows[i], record_cells(rows[i], index, key))
        ms_io = imgui.end_multi_select()
        self._apply_requests(ms_io)
        if at_bottom and len(rows) > self._last_rows:
            imgui.set_scroll_here_y(1.0)
        self._last_rows = len(rows)
        imgui.end_table()

    def _render_row(
        self, i: int, record: MonitorRecord, cells: tuple[str, ...]
    ) -> None:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.set_next_item_selection_user_data(i)
        selected = record.number in self.selected
        if imgui.selectable(
            f"{cells[0]}##row{record.number}",
            selected,
            imgui.SelectableFlags_.span_all_columns
            | imgui.SelectableFlags_.allow_overlap,
        )[0]:
            self.focused = record
        if (
            self._on_select_device is not None
            and imgui.is_item_hovered()
            and imgui.is_mouse_double_clicked(imgui.MouseButton_.left)
        ):
            frame = record.frame if record.is_telegram else None
            if frame is not None and frame.is_data and frame.source is not None:
                self._on_select_device(frame.source)
        color: imgui.ImVec4 | None = None
        if not record.is_telegram:
            color = _EVENT_COLOR
        elif cells[COLUMN_INDEX["flags"]]:
            color = _WARN_COLOR
        for column in range(1, len(COLUMNS)):
            if not imgui.table_set_column_index(column):
                continue
            text = cells[column]
            if not text:
                continue
            width = imgui.get_content_region_avail().x
            if color is not None:
                imgui.text_colored(color, text)
            else:
                imgui.text_unformatted(text)
            if (
                column in _WRAPPED_COLUMNS
                and imgui.is_item_hovered()
                and imgui.calc_text_size(text).x > width
            ):
                imgui.set_tooltip(text)

    def _apply_requests(self, ms_io: imgui.MultiSelectIO) -> None:
        rows = self._view
        for request in ms_io.requests:
            if request.type == imgui.SelectionRequestType.set_all:
                if request.selected:
                    self.selected = {record.number for record in rows}
                else:
                    self.selected = set()
            elif request.type == imgui.SelectionRequestType.set_range:
                first = max(int(request.range_first_item), 0)
                last = min(int(request.range_last_item), len(rows) - 1)
                for i in range(first, last + 1):
                    if request.selected:
                        self.selected.add(rows[i].number)
                    else:
                        self.selected.discard(rows[i].number)

    # --- actions ---------------------------------------------------------

    def _handle_copy_shortcut(self) -> None:
        """Ctrl+C (Cmd+C) copies the rows while the monitor has focus and no text field does."""
        io = imgui.get_io()
        if (
            imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows)
            and not io.want_text_input
            and (io.key_ctrl or io.key_super)
            and imgui.is_key_pressed(imgui.Key.c)
        ):
            self._copy()

    def _copy(self) -> None:
        """Copy the selected rows (all shown rows if none is selected) as tab-separated text."""
        imgui.set_clipboard_text(self.copy_text())

    def copy_text(self) -> str:
        """The selected rows (all shown rows if none is selected) with a header line, one row
        per line, the shown columns separated by tabs."""
        records = [r for r in self._view if r.number in self.selected] or self._view
        columns = self._enabled_columns
        key = (self._last_index.revision, get_locale())
        lines = ["\t".join(COLUMNS[column].label for column in columns)]
        for record in records:
            cells = record_cells(record, self._last_index, key)
            lines.append("\t".join(cells[column] for column in columns))
        return "\n".join(lines)

    def _start(self) -> None:
        if self.kind is not MonitorKind.BUS:
            self._service.start(self.kind)
            return
        if not self._connection.busmonitor_supported():
            self._connection.busmonitor_error = "routing"
            return
        self._question = _Question(
            S.BUSMON_CONFIRM_TITLE,
            S.BUSMON_CONFIRM_TEXT,
            lambda: self._service.start(MonitorKind.BUS),
        )

    def _ask_unsaved(self, text: str, action: Callable[[], None]) -> None:
        if self._channel.log.has_unsaved:
            self._question = _Question(S.QUESTION_TITLE, text, action)
        else:
            action()

    def _clear(self) -> None:
        self._service.clear_log(self.kind)
        self.selected = set()
        self.focused = None
        self._message = None

    def _ask_import(self) -> None:
        if self._channel.log.has_unsaved:
            self._question = _Question(
                S.QUESTION_TITLE, S.QUESTION_IMPORT_UNSAVED, self._open_import
            )
        elif self._channel.session or self._channel.recording:
            self._question = _Question(
                S.TOOL_IMPORT, S.QUESTION_IMPORT, self._open_import
            )
        else:
            self._open_import()

    def _open_import(self) -> None:
        self._open_dialog = pfd.open_file(
            S.TOOL_IMPORT, "", [S.FILE_FILTER, "*.xml", S.FILE_ALL, "*"]
        )

    def _open_scope(self, action: str) -> None:
        self._scope_action = action
        self._scope_choice = 0
        self._scope_opened = False

    def _scope_records(self) -> list[MonitorRecord]:
        if self._scope_choice == 1:
            return self.selected_records()
        if self._scope_choice == 2:
            return list(self._view)
        return self._channel.log.snapshot()

    def _export(self, records: list[MonitorRecord], everything: bool) -> None:
        self._pending_export = records
        self._pending_export_all = everything
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._save_dialog = pfd.save_file(
            S.TOOL_EXPORT,
            f"{_FILE_NAMES[self.kind]}-{stamp}.xml",
            [S.FILE_FILTER, "*.xml", S.FILE_ALL, "*"],
        )

    def _print(self, records: list[MonitorRecord], index: ProjectIndex) -> None:
        headers = [COLUMNS[i].label for i in PRINT_COLUMNS]
        title = {
            MonitorKind.GROUP: S.MONITOR_TAB_GROUP,
            MonitorKind.BUS: S.MONITOR_TAB_BUS,
            MonitorKind.ACTIVITY: S.MONITOR_TAB_ACTIVITY,
        }[self.kind]
        page_text = S.PRINT_PAGE

        def work() -> None:
            try:
                rows = [
                    [cells[i] for i in PRINT_COLUMNS]
                    for cells in (format_cells(record, index) for record in records)
                ]
                data = table_pdf(
                    headers,
                    rows,
                    PRINT_WEIGHTS,
                    page_label=lambda page: page_text.format(page=page),
                    title=title,
                )
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                path = Path(tempfile.gettempdir()) / (
                    f"xknx-editor-{_FILE_NAMES[self.kind]}-{stamp}-{os.getpid()}.pdf"
                )
                path.write_bytes(data)
                open_path(str(path))
            except Exception as err:
                self._message = (S.PRINT_FAILED.format(error=err), True)

        threading.Thread(target=work, name="Monitor-Print", daemon=True).start()

    def _replay(self) -> None:
        records = self.selected_records() or self._channel.log.snapshot()
        plan = self._service.start_replay(records)
        messages: list[str] = []
        if plan.skipped_secure:
            messages.append(S.REPLAY_SECURE_SKIPPED.format(count=plan.skipped_secure))
        if not plan.items:
            messages.append(S.REPLAY_NOTHING)
        self._message = (". ".join(messages), True) if messages else None
        if plan.skipped_secure and self._notify is not None:
            self._notify(S.REPLAY_SECURE_SKIPPED.format(count=plan.skipped_secure))
        self._replay_reported = self._service.replay_result

    def _report_replay(self) -> None:
        result = self._service.replay_result
        if result is None or result is self._replay_reported:
            return
        self._replay_reported = result
        sent, cancelled = result
        text = (S.REPLAY_CANCELLED if cancelled else S.REPLAY_FINISHED).format(
            count=sent
        )
        self._message = (text, False)

    # --- dialogs ---------------------------------------------------------

    def _render_question(self) -> None:
        question = self._question
        if question is None:
            return
        popup = f"{question.title}###mon_question_{self.kind.value}"
        if not question.opened:
            imgui.open_popup(popup)
            question.opened = True
        imgui.set_next_window_size(px_vec2(460.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(popup, None)[0]:
            self._question = None
            return
        imgui.push_text_wrap_pos(0.0)
        imgui.text_unformatted(question.text)
        imgui.pop_text_wrap_pos()
        imgui.spacing()
        yes = imgui.button(S.BTN_YES, px_vec2(80.0, 0.0))
        imgui.same_line()
        no = imgui.button(S.BTN_NO, px_vec2(80.0, 0.0))
        if yes or no or imgui.is_key_pressed(imgui.Key.escape):
            self._question = None
            imgui.close_current_popup()
            if yes:
                question.on_yes()
        imgui.end_popup()

    def _render_scope(self) -> None:
        action = self._scope_action
        if action is None:
            return
        title = S.SCOPE_EXPORT_TITLE if action == "export" else S.SCOPE_PRINT_TITLE
        popup = f"{title}###mon_scope_{self.kind.value}"
        if not self._scope_opened:
            imgui.open_popup(popup)
            self._scope_opened = True
        imgui.set_next_window_size(px_vec2(360.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(popup, None)[0]:
            self._scope_action = None
            return
        imgui.text_unformatted(S.SCOPE_LABEL)
        if imgui.radio_button(
            S.SCOPE_ALL.format(count=len(self._channel.log)), self._scope_choice == 0
        ):
            self._scope_choice = 0
        selected = len(self.selected)
        imgui.begin_disabled(selected == 0)
        if imgui.radio_button(
            S.SCOPE_SELECTED.format(count=selected), self._scope_choice == 1
        ):
            self._scope_choice = 1
        imgui.end_disabled()
        if self.filter.strip() and imgui.radio_button(
            S.SCOPE_FILTERED.format(count=len(self._view)), self._scope_choice == 2
        ):
            self._scope_choice = 2
        imgui.spacing()
        ok = imgui.button(S.BTN_OK, px_vec2(80.0, 0.0))
        imgui.same_line()
        cancel = imgui.button(S.BTN_CANCEL, px_vec2(80.0, 0.0))
        if ok or cancel or imgui.is_key_pressed(imgui.Key.escape):
            self._scope_action = None
            imgui.close_current_popup()
            if ok:
                records = self._scope_records()
                if action == "export":
                    self._export(records, self._scope_choice == 0)
                else:
                    self._print(records, self._last_index)
        imgui.end_popup()

    def _poll_dialogs(self) -> None:
        if self._save_dialog is not None and self._save_dialog.ready():
            path = self._save_dialog.result()
            self._save_dialog = None
            if path:
                self._write_export(path)
        if self._open_dialog is not None and self._open_dialog.ready():
            paths = self._open_dialog.result()
            self._open_dialog = None
            if paths:
                self._read_import(paths[0])

    def _write_export(self, path: str) -> None:
        if not path.lower().endswith(".xml"):
            path += ".xml"
        records = self._pending_export
        try:
            Path(path).write_bytes(export_xml(records))
        except OSError as err:
            self._message = (S.EXPORT_FAILED.format(error=err), True)
            return
        if self._pending_export_all:
            self._channel.log.mark_saved()
        count = sum(1 for record in records if record.kind is RecordKind.TELEGRAM)
        self._message = (S.EXPORTED.format(count=count, path=path), False)
        self._pending_export = []

    def _read_import(self, path: str) -> None:
        try:
            result = import_xml(Path(path).read_bytes())
        except (OSError, ValueError) as err:
            self._message = (S.IMPORT_FAILED.format(error=err), True)
            return
        self._service.load(self.kind, result.records)
        self.selected = set()
        self.focused = None
        text = S.IMPORTED.format(count=len(result.records), path=path)
        if result.skipped:
            text += ". " + S.IMPORT_SKIPPED.format(count=result.skipped)
        self._message = (text, False)


def selected_payload_text(value: Any) -> str:
    """A decoded value as the value field accepts it (``on``/``off``, point decimals)."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)
