"""Dialog of "Read from device": whether to include the group communication tables."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog


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
        if not begin_dialog(popup):
            return
        imgui.text(self._device_label)
        imgui.spacing()
        imgui.text(S.READ_INCLUDE_GROUP_COMMUNICATION)
        clicked = dialog_buttons(S.BTN_YES, S.BTN_NO, S.BTN_CANCEL)
        if clicked in (0, 1):
            if self._node_id is not None:
                self._on_read(self._node_id, clicked == 0)
            imgui.close_current_popup()
        elif clicked == 2:
            imgui.close_current_popup()
        end_dialog()
