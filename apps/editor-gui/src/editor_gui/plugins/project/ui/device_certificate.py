"""Dialog "Add Device Certificate" / "Device Certificate": the FDSK of a device that is
commissioned securely, with the certificate the project already holds for it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.device_info import serial_text
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px

_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)
_WARNING_COLOR = imgui.ImVec4(0.95, 0.55, 0.35, 1.0)


@dataclass(frozen=True, slots=True)
class CertificateState:
    """The project's certificate of a device: the serial number it is stored for (``None``
    without one) and whether the device uses its own Tool Key, which makes the certificate
    needed again only after a factory reset."""

    serial: bytes | None
    key_in_use: bool


def certificate_prompt(state: CertificateState | None) -> list[str]:
    """The texts above the code field: the device's key state, then what to enter."""
    texts: list[str] = []
    if state is not None:
        texts.append(
            S.DEVICE_CERTIFICATE_IN_USE
            if state.key_in_use
            else S.DEVICE_CERTIFICATE_NEEDED
        )
    stored = state is not None and state.serial is not None
    texts.append(S.DEVICE_CERTIFICATE_REPLACE if stored else S.DEVICE_CERTIFICATE_ENTER)
    return texts


class DeviceCertificateDialog:
    def __init__(
        self,
        on_add: Callable[[str], str | None],
        on_remove: Callable[[bytes], None] | None = None,
    ) -> None:
        """``on_add`` stores the entered code and returns an error message, or ``None``;
        ``on_remove`` removes the stored certificate of a serial number."""
        self._on_add = on_add
        self._on_remove = on_remove
        self._device_label = ""
        self._state: CertificateState | None = None
        self._confirm_remove = False
        self._code = ""
        self._error = ""
        self._then: Callable[[], None] | None = None
        self._plain: Callable[[], None] | None = None
        self._on_dont_ask: Callable[[bool], None] | None = None
        self._dont_ask = False
        self._open_requested = False

    def open(
        self,
        device_label: str,
        then: Callable[[], None] | None = None,
        plain: Callable[[], None] | None = None,
        on_dont_ask: Callable[[bool], None] | None = None,
        state: CertificateState | None = None,
    ) -> None:
        """Ask for the certificate of ``device_label``; ``then`` runs once it is added. With
        ``plain``, a third button commissions the device in the clear instead. With
        ``on_dont_ask`` (a device just added) the dialog can be postponed ("Later") and offers not
        to ask again; ``on_dont_ask`` gets that choice when it closes. ``state`` is the device's
        certificate in the project (``None`` when no device is chosen)."""
        self._device_label = device_label
        self._state = state
        self._confirm_remove = False
        self._code = ""
        self._error = ""
        self._then = then
        self._plain = plain
        self._on_dont_ask = on_dont_ask
        self._dont_ask = False
        self._open_requested = True

    def render(self) -> None:
        stored = self._state.serial if self._state is not None else None
        title = (
            S.DEVICE_CERTIFICATE_TITLE_STORED
            if stored is not None
            else S.DEVICE_CERTIFICATE_TITLE
        )
        popup = f"{title}###device_certificate"
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(popup)
        if not begin_dialog(popup, width=520.0):
            return
        imgui.text(self._device_label)
        imgui.spacing()
        if stored is not None:
            self._render_stored(stored)
        if self._confirm_remove:
            self._render_confirm_remove(stored)
            end_dialog()
            return
        for text in certificate_prompt(self._state):
            imgui.text_wrapped(text)
        imgui.set_next_item_width(-1)
        submitted, self._code = imgui.input_text(
            "##fdsk", self._code, imgui.InputTextFlags_.enter_returns_true
        )
        if self._error:
            imgui.text_colored(_ERROR_COLOR, self._error)
        if self._on_dont_ask is not None:
            imgui.spacing()
            _, self._dont_ask = checkbox(S.DEVICE_CERTIFICATE_DONT_ASK, self._dont_ask)
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
                S.BTN_OK,
                S.DEVICE_CERTIFICATE_LATER
                if self._on_dont_ask is not None
                else S.BTN_CANCEL,
                enabled=[bool(self._code.strip()), True],
            )
        if (clicked == 0 or submitted) and self._code.strip():
            error = self._on_add(self._code)
            if error is None:
                then, self._then = self._then, None
                self._closed()
                imgui.close_current_popup()
                if then is not None:
                    then()
            else:
                self._error = error
        elif clicked == 1:
            self._then = self._plain = None
            self._closed()
            imgui.close_current_popup()
        end_dialog()

    def _render_stored(self, serial: bytes) -> None:
        """The stored certificate, with the button that removes it."""
        imgui.align_text_to_frame_padding()
        imgui.text(S.DEVICE_CERTIFICATE_STORED.format(serial=serial_text(serial) or ""))
        if self._on_remove is not None and not self._confirm_remove:
            imgui.same_line()
            if imgui.button(S.PROJECT_CERTIFICATES_REMOVE):
                self._confirm_remove = True
        imgui.spacing()

    def _render_confirm_remove(self, serial: bytes | None) -> None:
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + px(500.0))
        imgui.text_colored(_WARNING_COLOR, S.DEVICE_CERTIFICATE_REMOVE_CONFIRM)
        imgui.pop_text_wrap_pos()
        clicked = dialog_buttons(S.PROJECT_CERTIFICATES_REMOVE, S.BTN_CANCEL)
        if clicked == 0:
            if serial is not None and self._on_remove is not None:
                self._on_remove(serial)
            if self._state is not None:
                self._state = CertificateState(None, self._state.key_in_use)
            self._confirm_remove = False
        elif clicked == 1:
            self._confirm_remove = False

    def _closed(self) -> None:
        on_dont_ask, self._on_dont_ask = self._on_dont_ask, None
        if on_dont_ask is not None:
            on_dont_ask(self._dont_ask)
