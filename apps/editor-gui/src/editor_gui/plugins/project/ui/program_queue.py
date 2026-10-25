"""Docked "Operations" panel: running and waiting bus operations, and the ones that ended.

The Active tab lists the running operation and the queue behind it, each with a progress bar, its
current text and a cancel button. The History tab keeps ended operations until cleared. Every entry
has a collapsible status line whose details show the error, the connection and the start/end times.
"""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import hello_imgui, imgui

from editor_gui.plugins.project.device_info_view import Toned
from editor_gui.plugins.project.program_queue import OperationState, QueueItem
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.tabs import begin_tab_bar
from xknxeditor.download.scope import DownloadScope

# Seconds a script may ignore Cancel before Force stop is offered.
FORCE_STOP_AFTER = 10.0

_RED = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)
_WARNING = imgui.ImVec4(0.95, 0.75, 0.2, 1.0)
# Result values that match the project, and ones that differ from it.
_OK = imgui.ImVec4(0.45, 0.8, 0.45, 1.0)
_DIFFERS = imgui.ImVec4(0.95, 0.55, 0.35, 1.0)


class ProgramQueuePanel:
    def __init__(
        self,
        *,
        get_active: Callable[[], list[QueueItem]],
        get_history: Callable[[], list[QueueItem]],
        get_progress: Callable[[], tuple[int, int] | None],
        on_cancel: Callable[[QueueItem], None],
        on_cancel_all: Callable[[], None],
        on_clear_history: Callable[[], None],
        get_bus_operation: Callable[[], tuple[str, str] | None] = lambda: None,
    ) -> None:
        """``get_bus_operation`` is the running bus operation the queue has no entry for
        (kind and address, see ``bus_operation_text``): a device test, a restart, a scan …"""
        self._get_bus_operation = get_bus_operation
        self._get_active = get_active
        self._get_history = get_history
        self._get_progress = get_progress
        self._on_cancel = on_cancel
        self._on_cancel_all = on_cancel_all
        self._on_clear_history = on_clear_history
        self._select: bool | None = (
            None  # tab to bring to the front: True Active, False History
        )
        self._was_active = False

    def select_active(self) -> None:
        """Bring the Active tab to the front on the next frame."""
        self._select = True

    def render(self) -> None:
        # Active while operations run or wait, History once the last one has ended.
        active = bool(self._get_active()) or self._get_bus_operation() is not None
        if active != self._was_active:
            self._was_active = active
            self._select = active
        if not begin_tab_bar("##operations_tabs"):
            return
        select, self._select = self._select, None
        if imgui.begin_tab_item(
            f"{S.OPERATIONS_ACTIVE}###active", None, _tab_flags(select is True)
        )[0]:
            self._render_active()
            imgui.end_tab_item()
        if imgui.begin_tab_item(
            f"{S.OPERATIONS_HISTORY}###history", None, _tab_flags(select is False)
        )[0]:
            self._render_history()
            imgui.end_tab_item()
        imgui.end_tab_bar()

    def _render_active(self) -> None:
        items = self._get_active()
        bus_operation = self._get_bus_operation()
        imgui.begin_disabled(not items)
        if imgui.button(S.OPERATIONS_CANCEL_ALL):
            self._on_cancel_all()
        imgui.end_disabled()
        imgui.separator()
        if not items and bus_operation is None:
            imgui.text_disabled(S.OPERATIONS_NONE)
            return
        if imgui.begin_child("##active_list"):
            if bus_operation is not None:
                self._render_bus_operation(*bus_operation)
            for item in items:
                self._render_item(item, active=True)
        imgui.end_child()

    def _render_bus_operation(self, kind: str, address: str) -> None:
        """A running bus operation started elsewhere (a device test, a restart, a scan …): what it
        does and where, and how far it is."""
        imgui.text(bus_operation_text(kind, address))
        bar = imgui.ImVec2(-1.0, hello_imgui.em_size(1.0))
        progress = self._get_progress()
        if progress is not None and progress[1] > 0:
            imgui.progress_bar(
                progress[0] / progress[1], bar, f"{progress[0]}/{progress[1]}"
            )
        else:
            imgui.progress_bar(-1.0 * float(imgui.get_time()), bar, "")
        imgui.separator()

    def _render_history(self) -> None:
        items = self._get_history()
        imgui.begin_disabled(not items)
        if imgui.button(S.OPERATIONS_CLEAR_HISTORY):
            self._on_clear_history()
        imgui.end_disabled()
        imgui.separator()
        if not items:
            imgui.text_disabled(S.OPERATIONS_NONE)
            return
        if imgui.begin_child("##history_list"):
            for item in items:
                self._render_item(item, active=False)
        imgui.end_child()

    def _render_item(self, item: QueueItem, *, active: bool) -> None:
        imgui.push_id(str(id(item)))
        right = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x
        if item.state is OperationState.FAILED:
            _warning_icon()
            imgui.same_line()
        imgui.text(f"{item.address} {item.name}".strip())
        if item.origin is not None:
            imgui.same_line()
            imgui.text_disabled(item.origin)
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.OPERATION_STARTED_BY_MCP)
        if active:
            self._render_cancel(item, right)
            if item.state is OperationState.RUNNING:
                self._render_progress(item)
            _render_full_download_question(item)
        if item.script is not None and item.script.text:
            imgui.text_wrapped(item.script.text)
        failed = item.state is OperationState.FAILED
        if failed:
            imgui.push_style_color(imgui.Col_.text, _RED)
        is_open = imgui.tree_node_ex(
            f"{operation_text(item)}: {state_text(item)}###status",
            imgui.TreeNodeFlags_.span_avail_width,
        )
        if failed:
            imgui.pop_style_color()
        if is_open:
            _render_details(item)
            imgui.tree_pop()
        imgui.separator()
        imgui.pop_id()

    def _render_cancel(self, item: QueueItem, right: float) -> None:
        run = item.script
        size = imgui.get_frame_height()
        imgui.same_line(max(right - size, 0.0))
        imgui.begin_disabled(
            item.canceled_by_user or (run is not None and run.canceled)
        )
        if imgui.button("X##cancel", imgui.ImVec2(size, size)):
            self._on_cancel(item)
        imgui.end_disabled()
        if (
            run is not None
            and run.canceled
            and run.ignored_cancel_for() >= FORCE_STOP_AFTER
            and imgui.button(f"{S.SCRIPT_FORCE_STOP}##force_stop")
        ):
            run.abort.request()

    def _render_progress(self, item: QueueItem) -> None:
        bar = imgui.ImVec2(-1.0, hello_imgui.em_size(1.0))
        run = item.script
        if run is not None:
            if run.progress is not None:
                frac = min(max(run.progress / 100.0, 0.0), 1.0)
                imgui.progress_bar(frac, bar, "")
            else:
                imgui.progress_bar(-1.0 * float(imgui.get_time()), bar, "")
            return
        progress = self._get_progress()
        if progress is not None and progress[1] > 0:
            imgui.progress_bar(
                progress[0] / progress[1], bar, f"{progress[0]}/{progress[1]}"
            )
        else:
            imgui.progress_bar(-1.0 * float(imgui.get_time()), bar, "")


def _render_full_download_question(item: QueueItem) -> None:
    question = item.full_download_question
    if question is None or question.done():
        return
    imgui.text_wrapped(S.FULL_DOWNLOAD_DIFFERENT)
    imgui.text_wrapped(S.FULL_DOWNLOAD_QUESTION)
    if imgui.button(f"{S.BTN_YES}##full_download_yes"):
        question.set_result(True)
    imgui.same_line()
    if imgui.button(f"{S.BTN_NO}##full_download_no"):
        question.set_result(False)


def _render_details(item: QueueItem) -> None:
    if item.message:
        imgui.push_style_color(imgui.Col_.text, _RED)
        imgui.text_wrapped(item.message)
        imgui.pop_style_color()
    elif item.details:
        _render_result(item.details)
    else:
        imgui.text_disabled(S.OPERATION_NO_DETAILS)
    if item.origin is not None:
        imgui.text(S.OPERATION_STARTED_BY_MCP)
    if item.connection:
        imgui.text(S.OPERATION_CONNECTION.format(connection=item.connection))
    if item.started_at is not None:
        imgui.text(S.OPERATION_START.format(time=item.started_at.strftime("%H:%M:%S")))
    if item.ended_at is not None:
        imgui.text(S.OPERATION_END.format(time=item.ended_at.strftime("%H:%M:%S")))


def _render_result(sections: list[tuple[str, list[tuple[str, str]]]]) -> None:
    """Result sections, each a heading over ``- label: value`` rows."""
    flags = imgui.TableFlags_.sizing_fixed_fit | imgui.TableFlags_.no_saved_settings
    if not imgui.begin_table("##result", 2, flags):
        return
    for title, rows in sections:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.text(title)
        for label, value in rows:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(f"- {label}:")
            imgui.table_next_column()
            tone = value.tone if isinstance(value, Toned) else None
            if tone is None:
                imgui.text(value)
            else:
                imgui.text_colored(_OK if tone == "ok" else _DIFFERS, value)
    imgui.end_table()
    imgui.spacing()


def _warning_icon() -> None:
    size = imgui.get_text_line_height()
    pos = imgui.get_cursor_screen_pos()
    draw = imgui.get_window_draw_list()
    color = imgui.get_color_u32(_WARNING)
    draw.add_triangle_filled(
        imgui.ImVec2(pos.x + size * 0.5, pos.y),
        imgui.ImVec2(pos.x + size, pos.y + size),
        imgui.ImVec2(pos.x, pos.y + size),
        color,
    )
    mark = imgui.calc_text_size("!")
    draw.add_text(
        imgui.ImVec2(pos.x + (size - mark.x) * 0.5, pos.y + size * 0.15),
        imgui.get_color_u32(imgui.ImVec4(0.0, 0.0, 0.0, 1.0)),
        "!",
    )
    imgui.dummy(imgui.ImVec2(size, size))


# The bus operations the queue has no entry for, by kind (see ConnectionService.begin_operation).
def _bus_operation_names() -> dict[str, str]:
    return {
        "test": S.BTN_EVAL_DEVICE,
        "restart": S.BTN_RESTART_DEVICE,
        "reset": S.RESET_POPUP_TITLE,
        "dali": S.EDITOR_TAB_DALI,
        "siemens_dali": S.EDITOR_TAB_SIEMENS_DALI,
        "siemens_dca": S.PLUGIN_PARAMS_PROGRAM_DCA,
        "recover": S.OPERATION_RECOVER,
        "scan_progmode": S.OPERATION_PROGRAMMING_MODE,
    }


# Addresses that only name a step of an operation, not where it runs.
_STEPS = frozenset({"broadcast", "read-back", "verify"})


def bus_operation_text(kind: str, address: str) -> str:
    """A running bus operation as the Operations panel and the status bar name it: what it does
    and where ("Recover Project from Bus 1.0.1..1.0.20")."""
    name = _bus_operation_names().get(kind, kind)
    return f"{name} {address}".strip() if address not in _STEPS else name


def _tab_flags(selected: bool) -> int:
    return imgui.TabItemFlags_.set_selected if selected else imgui.TabItemFlags_.none


def operation_text(item: QueueItem) -> str:
    """What an operation of the queue does, as its entry names it ("Download (Parameters)")."""
    if item.script is not None:
        return S.SCRIPT_OPERATION
    if item.device_info is not None:
        return S.DEVICE_INFO_GROUPS if item.device_info else S.DEVICE_INFO
    if item.scope is DownloadScope.INDIVIDUAL_ADDRESS and item.serial_number:
        return f"{S.OPERATION_DOWNLOAD}({S.SCOPE_INDIVIDUAL_ADDRESS_SERIAL})"
    return f"{S.OPERATION_DOWNLOAD}({_scope_label(item.scope)})"


def state_text(item: QueueItem) -> str:
    """How far an operation of the queue is, as its entry shows it ("Waiting", "Downloading" …)."""
    match item.state:
        case OperationState.WAITING:
            return S.OPERATION_WAITING
        case OperationState.RUNNING:
            if item.canceled_by_user:
                return S.SCRIPT_CANCELING
            if item.device_info is not None:
                return S.OPERATION_READING
            if item.script is None:
                if item.waiting_for_button:
                    return S.OPERATION_PRESS_PROGRAMMING_BUTTON
                if item.full_download_question is not None:
                    return S.OPERATION_CONFIRM
                return S.OPERATION_DOWNLOADING
            return S.SCRIPT_CANCELING if item.script.canceled else S.SCRIPT_RUNNING
        case OperationState.FINISHED:
            return S.OPERATION_FINISHED
        case OperationState.CANCELED:
            return S.OPERATION_CANCELED
        case OperationState.FAILED:
            return S.OPERATION_FAILED


def _scope_label(scope: DownloadScope | None) -> str:
    match scope:
        case DownloadScope.PARAMETERS:
            return S.SCOPE_PARAMETERS
        case DownloadScope.GROUP_COMMUNICATION:
            return S.SCOPE_GROUP_COMMUNICATION
        case DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION:
            return f"{S.SCOPE_PARAMETERS}, {S.SCOPE_GROUP_COMMUNICATION}"
        case DownloadScope.APPLICATION:
            return S.SCOPE_APPLICATION
        case DownloadScope.UNLOAD:
            return S.SCOPE_UNLOAD
        case DownloadScope.UNLOAD_ALL:
            return S.SCOPE_UNLOAD_ALL
        case DownloadScope.INDIVIDUAL_ADDRESS:
            return S.SCOPE_INDIVIDUAL_ADDRESS
        case DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS:
            return S.SCOPE_OVERWRITE_INDIVIDUAL_ADDRESS
        case _:
            return S.SCOPE_FULL
