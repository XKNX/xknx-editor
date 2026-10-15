"""Dialog "Add Device Certificate": the FDSK of a device that is commissioned securely."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog

_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


class DeviceCertificateDialog:
    def __init__(self, on_add: Callable[[str], str | None]) -> None:
        """``on_add`` stores the entered code and returns an error message, or ``None``."""
        self._on_add = on_add
        self._device_label = ""
        self._code = ""
        self._error = ""
        self._then: Callable[[], None] | None = None
        self._plain: Callable[[], None] | None = None
        self._open_requested = False

    def open(
        self,
        device_label: str,
        then: Callable[[], None] | None = None,
        plain: Callable[[], None] | None = None,
    ) -> None:
        """Ask for the certificate of ``device_label``; ``then`` runs once it is added. With
        ``plain``, a third button commissions the device in the clear instead."""
        self._device_label = device_label
        self._code = ""
        self._error = ""
        self._then = then
        self._plain = plain
        self._open_requested = True

    def render(self) -> None:
        popup = f"{S.DEVICE_CERTIFICATE_TITLE}###device_certificate"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        if not begin_dialog(popup, width=520.0):
            return
        imgui.text(self._device_label)
        imgui.spacing()
        imgui.text_wrapped(S.DEVICE_CERTIFICATE_PROMPT)
        imgui.set_next_item_width(-1)
        submitted, self._code = imgui.input_text(
            "##fdsk", self._code, imgui.InputTextFlags_.enter_returns_true
        )
        if self._error:
            imgui.text_colored(_ERROR_COLOR, self._error)
        if self._plain is not None:
            clicked = dialog_buttons(
                S.BTN_OK,
                S.DEVICE_CERTIFICATE_PLAIN,
                S.BTN_CANCEL,
                enabled=[bool(self._code.strip()), True, True],
            )
            if clicked == 1:
                plain, self._plain, self._then = self._plain, None, None
                imgui.close_current_popup()
                plain()
                end_dialog()
                return
            if clicked == 2:
                clicked = 1
        else:
            clicked = dialog_buttons(
                S.BTN_OK, S.BTN_CANCEL, enabled=[bool(self._code.strip()), True]
            )
        if (clicked == 0 or submitted) and self._code.strip():
            error = self._on_add(self._code)
            if error is None:
                then, self._then = self._then, None
                imgui.close_current_popup()
                if then is not None:
                    then()
            else:
                self._error = error
        elif clicked == 1:
            self._then = self._plain = None
            imgui.close_current_popup()
        end_dialog()
