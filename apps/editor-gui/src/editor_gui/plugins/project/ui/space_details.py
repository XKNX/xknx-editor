"""The details of a space selected in the Buildings tree (a building, floor, room, …), shown in
place of the device's: its name, a room's use, its description, number, status and current line,
each saved when its field is left or its choice is made."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.status_field import (
    line_combo,
    status_combo,
    usage_combo,
)
from editor_gui.widgets.dpi import label_column_width

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import SpaceInfo


class SpaceDetailsPanel:
    def __init__(
        self,
        on_rename: Callable[[int, str], None],
        on_set_description: Callable[[int, str], None],
        on_set_number: Callable[[int, str], None],
        on_set_usage: Callable[[int, str], None] = lambda _i, _u: None,
        get_status: Callable[[int], str | None] = lambda _i: None,
        on_set_status: Callable[[int, str], None] = lambda _i, _s: None,
        line_choices: Callable[[], list[tuple[int, str]]] = list,
        get_current_line: Callable[[int], tuple[int, SpaceInfo] | None] = lambda _i: (
            None
        ),
        on_set_current_line: Callable[[int, int | None], None] = lambda _i, _l: None,
    ) -> None:
        self._on_rename = on_rename
        self._on_set_description = on_set_description
        self._on_set_number = on_set_number
        self._on_set_usage = on_set_usage
        self._get_status = get_status
        self._on_set_status = on_set_status
        self._line_choices = line_choices
        self._get_current_line = get_current_line
        self._on_set_current_line = on_set_current_line
        self._space_id: int | None = None
        self._name = ""
        self._description = ""
        self._number = ""

    def render(self, space: SpaceInfo) -> None:
        if self._space_id != space.id:
            self._space_id = space.id
            self._name = space.name
            self._description = space.description
            self._number = space.number
        label_width = label_column_width(
            S.CONFIGURE_NAME,
            S.SPACE_USE,
            S.CONFIGURE_DESCRIPTION,
            S.SPACE_NUMBER,
            S.STATUS,
            S.CURRENT_LINE,
        )
        if not imgui.begin_table("##space_details", 2):
            return
        imgui.table_setup_column(
            "##label", imgui.TableColumnFlags_.width_fixed, label_width
        )
        imgui.table_setup_column("##field", imgui.TableColumnFlags_.width_stretch)
        self._row(S.CONFIGURE_NAME)
        self._name_field(space)
        if space.space_type == "Room":
            self._row(S.SPACE_USE)
            usage = usage_combo("##space_usage", space.usage, space.usage_text)
            if usage is not None:
                self._on_set_usage(space.id, usage)
        self._row(S.CONFIGURE_DESCRIPTION)
        self._description_field(space)
        self._row(S.SPACE_NUMBER)
        self._number_field(space)
        status = self._get_status(space.id)
        if status is not None:
            self._row(S.STATUS)
            chosen = status_combo("##space_status", status)
            if chosen is not None:
                self._on_set_status(space.id, chosen)
        self._row(S.CURRENT_LINE)
        self._current_line_field(space)
        imgui.end_table()

    def _current_line_field(self, space: SpaceInfo) -> None:
        """The space's own line; without one, the line it inherits and the space that sets it."""
        choices = self._line_choices()
        texts = dict(choices)
        current = self._get_current_line(space.id)
        if space.default_line_id is not None:
            preview = texts.get(space.default_line_id, S.CURRENT_LINE_NONE)
        elif current is not None:
            line_id, setter = current
            preview = S.CURRENT_LINE_INHERITED.format(
                line=texts.get(line_id, ""), space=setter.name
            )
        else:
            preview = S.CURRENT_LINE_NONE
        changed, line_id = line_combo(
            "##space_current_line",
            space.default_line_id,
            preview,
            choices,
            S.CURRENT_LINE_NONE,
        )
        if changed:
            self._on_set_current_line(space.id, line_id)

    @staticmethod
    def _row(label: str) -> None:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.align_text_to_frame_padding()
        imgui.text(label)
        imgui.table_next_column()

    def _name_field(self, space: SpaceInfo) -> None:
        imgui.set_next_item_width(-1)
        _, self._name = imgui.input_text("##space_name", self._name)
        if imgui.is_item_deactivated_after_edit():
            name = self._name.strip()
            # A space keeps a name: an emptied field goes back to the current one.
            if name and name != space.name:
                self._on_rename(space.id, name)
        if not imgui.is_item_active() and self._name != space.name:
            self._name = space.name

    def _description_field(self, space: SpaceInfo) -> None:
        """Two lines high, like a device's description."""
        height = (
            imgui.get_text_line_height() * 2 + imgui.get_style().frame_padding.y * 2
        )
        _, self._description = imgui.input_text_multiline(
            "##space_description", self._description, imgui.ImVec2(-1, height)
        )
        if (
            imgui.is_item_deactivated_after_edit()
            and self._description != space.description
        ):
            self._on_set_description(space.id, self._description)
        if not imgui.is_item_active() and self._description != space.description:
            self._description = space.description

    def _number_field(self, space: SpaceInfo) -> None:
        imgui.set_next_item_width(-1)
        _, self._number = imgui.input_text("##space_number", self._number)
        if imgui.is_item_deactivated_after_edit() and self._number != space.number:
            self._on_set_number(space.id, self._number.strip())
        if not imgui.is_item_active() and self._number != space.number:
            self._number = space.number
