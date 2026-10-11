"""Dialog of "Read from device": whether to include the group communication tables."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px_vec2


class ReadDeviceDialog:
    def __init__(self, on_read: Callable[[int, bool], None]) -> None:
        self._on_read = on_read
        self._node_id: int | None = None
        self._device_label = ""
        self._open_requested = False

    def open(self, node_id: int, device_label: str) -> None:
        self._node_id = node_id
        self._device_label = device_label
        self._open_requested = True

    def render(self) -> None:
        popup = f"{S.BTN_READ_DEVICE_INFO}###read_device"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        if not imgui.begin_popup_modal(
            popup, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text(self._device_label)
        imgui.spacing()
        imgui.text(S.READ_INCLUDE_GROUP_COMMUNICATION)
        imgui.separator()
        size = px_vec2(90, 0)
        for label, group_communication in ((S.BTN_YES, True), (S.BTN_NO, False)):
            if imgui.button(label, size):
                if self._node_id is not None:
                    self._on_read(self._node_id, group_communication)
                imgui.close_current_popup()
            imgui.same_line()
        if imgui.button(S.BTN_CANCEL, size):
            imgui.close_current_popup()
        imgui.end_popup()
