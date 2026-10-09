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
            imgui.text(entry.display_text)

            if entry.reverted:
                imgui.pop_style_color()
