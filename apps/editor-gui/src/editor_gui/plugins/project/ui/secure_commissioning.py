"""Dialog "Deactivate Secure Commissioning": what a device commissioned in the clear loses."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog


class SecureCommissioningDialog:
    """Confirms deactivating a device's secure commissioning. It lists the group addresses that
    then communicate in the clear and those the device's group objects are removed from (they
    are secured and only take devices commissioned securely)."""

    def __init__(self) -> None:
        self._device_label = ""
        self._unsecured: list[str] = []
        self._removed: list[str] = []
        self._confirm: Callable[[], None] | None = None
        self._open_requested = False

    def open(
        self,
        device_label: str,
        unsecured: list[str],
        removed: list[str],
        confirm: Callable[[], None],
    ) -> None:
        self._device_label = device_label
        self._unsecured = unsecured
        self._removed = removed
        self._confirm = confirm
        self._open_requested = True

    def render(self) -> None:
        popup = f"{S.SECURE_COMMISSIONING_OFF_TITLE}###secure_commissioning_off"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        if not begin_dialog(popup, width=520.0):
            return
        imgui.text(self._device_label)
        imgui.spacing()
        for heading, names in (
            (S.SECURE_COMMISSIONING_OFF_UNSECURED, self._unsecured),
            (S.SECURE_COMMISSIONING_OFF_REMOVED, self._removed),
        ):
            if not names:
                continue
            imgui.text_wrapped(heading)
            for name in names:
                imgui.bullet_text(name)
            imgui.spacing()
        clicked = dialog_buttons(S.SECURE_COMMISSIONING_OFF_CONFIRM, S.BTN_CANCEL)
        if clicked == 0:
            confirm, self._confirm = self._confirm, None
            imgui.close_current_popup()
            if confirm is not None:
                confirm()
        elif clicked == 1:
            self._confirm = None
            imgui.close_current_popup()
        end_dialog()
