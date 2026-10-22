"""The details of an area or a line selected in the Devices tree, shown in place of the device's:
its name, address and description, each saved when its field is left."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import label_column_width


class TopologyElement(Protocol):
    """An area or a line as the project service lists it."""

    id: int
    name: str
    description: str


class TopologyDetailsPanel:
    def __init__(
        self,
        on_rename: Callable[[str, TopologyElement, str], None],
        on_set_description: Callable[[str, int, str], None],
        on_set_address: Callable[[str, int, str], str | None],
    ) -> None:
        self._on_rename = on_rename
        self._on_set_description = on_set_description
        self._on_set_address = on_set_address
        self._key: tuple[str, int] | None = None
        self._name = ""
        self._description = ""
        self._address = ""
        self._error = ""

    def render(
        self, kind: str, element: TopologyElement, prefix: str, address: int
    ) -> None:
        """``prefix`` is the area's address before a line's ("1."), "" for an area."""
        if self._key != (kind, element.id):
            self._key = (kind, element.id)
            self._name = element.name
            self._description = element.description
            self._address = str(address)
            self._error = ""
        label_width = label_column_width(
            S.CONFIGURE_NAME, S.CONFIGURE_DESCRIPTION, S.GA_ADDRESS
        )
        if not imgui.begin_table("##topology_details", 2):
            return
        imgui.table_setup_column(
            "##label", imgui.TableColumnFlags_.width_fixed, label_width
        )
        imgui.table_setup_column("##field", imgui.TableColumnFlags_.width_stretch)
        self._row(S.CONFIGURE_NAME)
        self._name_field(kind, element)
        self._row(S.GA_ADDRESS)
        self._address_field(kind, element, prefix, address)
        self._row(S.CONFIGURE_DESCRIPTION)
        self._description_field(kind, element)
        imgui.end_table()

    @staticmethod
    def _row(label: str) -> None:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.align_text_to_frame_padding()
        imgui.text(label)
        imgui.table_next_column()

    def _name_field(self, kind: str, element: TopologyElement) -> None:
        imgui.set_next_item_width(-1)
        _, self._name = imgui.input_text("##topology_name", self._name)
        if imgui.is_item_deactivated_after_edit() and self._name != element.name:
            self._on_rename(kind, element, self._name.strip())
        if not imgui.is_item_active() and self._name != element.name:
            self._name = element.name

    def _address_field(
        self, kind: str, element: TopologyElement, prefix: str, address: int
    ) -> None:
        if prefix:
            imgui.align_text_to_frame_padding()
            imgui.text_unformatted(prefix)
            imgui.same_line(0.0, 0.0)
        imgui.set_next_item_width(-1)
        _, self._address = imgui.input_text(
            "##topology_address", self._address, imgui.InputTextFlags_.chars_decimal
        )
        if imgui.is_item_deactivated_after_edit():
            self._error = self._on_set_address(kind, element.id, self._address) or ""
        if not imgui.is_item_active() and self._address != str(address):
            self._address = str(address)
        if self._error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._error)
            imgui.pop_text_wrap_pos()

    def _description_field(self, kind: str, element: TopologyElement) -> None:
        """Two lines high, like a device's description."""
        height = (
            imgui.get_text_line_height() * 2 + imgui.get_style().frame_padding.y * 2
        )
        _, self._description = imgui.input_text_multiline(
            "##topology_description", self._description, imgui.ImVec2(-1, height)
        )
        if (
            imgui.is_item_deactivated_after_edit()
            and self._description != element.description
        ):
            self._on_set_description(kind, element.id, self._description)
        if not imgui.is_item_active() and self._description != element.description:
            self._description = element.description
