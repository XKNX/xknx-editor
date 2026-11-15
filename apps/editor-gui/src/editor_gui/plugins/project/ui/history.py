"""The History panel: the project's steps, newest first, and the way back to any of them.

A step is selected with a click (or a tap); Restore above the list (a double click, or Restore in
the step's context menu) brings the project back to it. The current state is marked with an
arrow in its own column; undone steps after it are grey until a new change drops them.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.icon_button import ICON_CURRENT


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
        # The step selected in the list (None: none).
        self.selected: int | None = None
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

    def restorable(self, cursor: int) -> bool:
        """Whether Restore can bring the project to the selected step (not the current one)."""
        return self.selected is not None and self.selected != cursor

    def restore(self, entry_id: int) -> None:
        """Bring the project back (or forward) to the step ``entry_id``."""
        self._on_jump_to(entry_id)
        self.selected = None

    def render(self) -> None:
        entries = self._get_entries()
        cursor = self._get_cursor()

        if not entries:
            imgui.text_disabled(S.HISTORY_NO_HISTORY)
            return
        if self.selected is not None and all(e.id != self.selected for e in entries):
            self.selected = None

        self._render_toolbar(cursor)
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.sizing_fixed_fit
            | imgui.TableFlags_.no_saved_settings
        )
        if not imgui.begin_table("##history", 3, flags, imgui.ImVec2(0.0, 0.0)):
            return
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_stretch)
        for entry in entries:
            self._render_row(entry, entry.id == cursor)
        imgui.end_table()

    def _render_toolbar(self, cursor: int) -> None:
        enabled = self.restorable(cursor)
        imgui.begin_disabled(not enabled)
        clicked = imgui.button(S.HISTORY_REVERT)
        imgui.end_disabled()
        if not enabled and imgui.is_item_hovered(
            imgui.HoveredFlags_.allow_when_disabled
        ):
            imgui.set_tooltip(S.HISTORY_RESTORE_HINT)
        if clicked and enabled and self.selected is not None:
            self.restore(self.selected)

    def _render_row(self, entry: HistoryEntry, is_current: bool) -> None:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        flags = (
            imgui.SelectableFlags_.span_all_columns
            | imgui.SelectableFlags_.allow_double_click
        )
        if imgui.selectable(f"##history_{entry.id}", self.selected == entry.id, flags)[
            0
        ]:
            self.selected = entry.id
            if (
                imgui.is_mouse_double_clicked(imgui.MouseButton_.left)
                and not is_current
            ):
                self.restore(entry.id)
        if imgui.begin_popup_context_item(f"##history_menu_{entry.id}"):
            self.selected = entry.id
            if imgui.menu_item(S.HISTORY_REVERT, "", False, not is_current)[0]:
                self.restore(entry.id)
            imgui.end_popup()
        imgui.same_line(0.0, 0.0)
        if is_current:
            imgui.text_colored(status_color("success"), ICON_CURRENT)
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.HISTORY_CURRENT)
        else:
            # The column as wide as the arrow on every row.
            imgui.dummy(imgui.ImVec2(imgui.calc_text_size(ICON_CURRENT).x, 0.0))
        if entry.reverted:
            imgui.push_style_color(
                imgui.Col_.text, imgui.get_style_color_vec4(imgui.Col_.text_disabled)
            )
        imgui.table_set_column_index(1)
        imgui.text_disabled(self._time_text(entry.timestamp))
        imgui.table_set_column_index(2)
        imgui.text(entry.display_text)
        if entry.reverted:
            imgui.pop_style_color()
