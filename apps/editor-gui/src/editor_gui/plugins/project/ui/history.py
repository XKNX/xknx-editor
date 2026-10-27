import datetime
from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px


@dataclass
class HistoryEntry:
    id: int
    display_text: str
    reverted: bool
    # When the step was made (UTC); None when unknown.
    timestamp: datetime.datetime | None = None


def history_time_text(
    moment: datetime.datetime | None, now: datetime.datetime | None = None
) -> str:
    """When a history step was made, in local time: the time of day for a step of today, else
    the date too ("2026-10-26 17:05:12"); "" when unknown."""
    if moment is None:
        return ""
    local = moment.astimezone()
    today = (now or datetime.datetime.now().astimezone()).date()
    pattern = "%H:%M:%S" if local.date() == today else "%Y-%m-%d %H:%M:%S"
    return local.strftime(pattern)


class HistoryPanel:
    def __init__(
        self,
        get_entries: Callable[[], list[HistoryEntry]],
        get_cursor: Callable[[], int],
        on_jump_to: Callable[[int], None],
    ) -> None:
        self._get_entries = get_entries
        self._get_cursor = get_cursor
        self._on_jump_to = on_jump_to
        # The shown time of each step, made once per day (the panel renders every frame).
        self._times: dict[datetime.datetime, str] = {}
        self._times_day: datetime.date | None = None

    def _time_text(self, moment: datetime.datetime | None) -> str:
        if moment is None:
            return ""
        today = datetime.date.today()
        if today != self._times_day:
            self._times, self._times_day = {}, today
        text = self._times.get(moment)
        if text is None:
            text = self._times[moment] = history_time_text(moment)
        return text

    def render(self) -> None:
        entries = self._get_entries()
        cursor = self._get_cursor()

        if not entries:
            imgui.text_disabled(S.HISTORY_NO_HISTORY)
            return

        style = imgui.get_style()
        btn_text_size = imgui.calc_text_size(S.HISTORY_REVERT)
        col_width = btn_text_size.x + style.frame_padding.x * 2

        for entry in entries:
            is_current = entry.id == cursor

            if entry.reverted:
                imgui.push_style_color(
                    imgui.Col_.text,
                    imgui.get_style_color_vec4(imgui.Col_.text_disabled),
                )

            row_min_y = imgui.get_cursor_screen_pos().y

            if is_current:
                draw_list = imgui.get_window_draw_list()
                cursor_pos = imgui.get_cursor_screen_pos()
                text_height = imgui.get_text_line_height()
                center = imgui.ImVec2(
                    cursor_pos.x + col_width / 2, cursor_pos.y + text_height / 2
                )
                draw_list.add_circle_filled(
                    center, px(4), imgui.get_color_u32(imgui.ImVec4(0.2, 0.8, 0.3, 1.0))
                )
                imgui.dummy(imgui.ImVec2(col_width, 0))
            else:
                mouse_pos = imgui.get_mouse_pos()
                window_pos = imgui.get_window_pos()
                row_height = imgui.get_text_line_height_with_spacing()
                row_hovered = (
                    row_min_y <= mouse_pos.y < row_min_y + row_height
                    and window_pos.x <= mouse_pos.x < window_pos.x + col_width + px(20)
                )
                if row_hovered:
                    if imgui.small_button(f"{S.HISTORY_REVERT}##{entry.id}"):
                        self._on_jump_to(entry.id)
                else:
                    imgui.dummy(imgui.ImVec2(col_width, 0))

            imgui.same_line()
            moment = self._time_text(entry.timestamp)
            if moment:
                imgui.text_disabled(moment)
                imgui.same_line()
            imgui.text(entry.display_text)

            if entry.reverted:
                imgui.pop_style_color()
