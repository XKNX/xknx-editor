"""Signing panel: view, edit, save and extract the .knxproj signing key.

Shows the active RSA signing key (modulus / private exponent / public exponent as hex), lets the
user edit it by hand, extract it from their own ETS ``Knx.Ets.XmlSigning.dll`` (via Mono/pythonnet),
save it (persisted per-user + applied to the signer), or revert to the built-in placeholder.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from imgui_bundle import hello_imgui, imgui

from editor_gui import file_dialogs, signing_key
from editor_gui.plugins.signing.strings import S
from editor_gui.widgets.flow import button_width, same_line_if_fits
from xknxeditor.proj import (
    DLL_NAME,
    KeyExtractionError,
    current_signing_key,
    default_dll_path,
    extract_converter_key,
    extraction_backend,
    signing_key_is_placeholder,
)

_RED = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)
_GREEN = imgui.ImVec4(0.4, 0.8, 0.4, 1.0)


class SigningPanel:
    def __init__(self, notify: Callable[[str], None] | None = None) -> None:
        self._notify = notify
        self._modulus = ""
        self._private_exponent = ""
        self._public_exponent = ""
        self._loaded = False
        self._dialog: file_dialogs.OpenFileDialog | None = None
        self._error: str | None = None
        self._status: str | None = None
        self._extracting = False
        # Written by the extraction thread, consumed on the UI thread.
        self._result: tuple[int, int, int] | None = None
        self._result_error: str | None = None

    def _load_from_signer(self) -> None:
        self._loaded = True
        # The hardcoded placeholder key is never shown: leave the fields empty until a genuine
        # key has been extracted or entered.
        if signing_key_is_placeholder():
            self._modulus = ""
            self._private_exponent = ""
            self._public_exponent = ""
            return
        n, d, e = current_signing_key()
        self._modulus = f"{n:x}"
        self._private_exponent = f"{d:x}"
        self._public_exponent = f"{e:x}"

    # --- extraction (background thread; the mono/pythonnet call can take a second) --------

    def begin_extract(self) -> None:
        # Point the picker at the detected ETS install and default the filter to the exact file.
        self._dialog = file_dialogs.open_file(
            S.EXTRACT,
            default_dll_path(),
            [DLL_NAME, DLL_NAME, S.DLL_FILTER, "*.dll", S.ALL_FILES, "*"],
        )

    def _poll_dialog(self) -> None:
        if self._dialog is not None and self._dialog.ready():
            result = self._dialog.result()
            self._dialog = None
            if result:
                self._start_extraction(result[0])

    def _start_extraction(self, dll_path: str) -> None:
        self._extracting = True
        self._error = None
        self._status = None
        self._result = None
        self._result_error = None

        def _work() -> None:
            try:
                self._result = extract_converter_key(dll_path)
            except (KeyExtractionError, Exception) as exc:
                self._result_error = f"{type(exc).__name__}: {exc}"

        threading.Thread(target=_work, daemon=True).start()

    def _apply_extraction_result(self) -> None:
        if not self._extracting:
            return
        if self._result is not None:
            n, d, e = self._result
            self._modulus = f"{n:x}"
            self._private_exponent = f"{d:x}"
            self._public_exponent = f"{e:x}"
            self._extracting = False
            self._result = None
            self._status = S.SAVED if self._save() else None
        elif self._result_error is not None:
            self._error = self._result_error
            self._extracting = False
            self._result_error = None

    # --- save / reset --------------------------------------------------------------------

    def _save(self) -> bool:
        try:
            n = int(self._modulus.strip().replace("\n", ""), 16)
            d = int(self._private_exponent.strip().replace("\n", ""), 16)
            e = int(self._public_exponent.strip().replace("\n", "") or "10001", 16)
        except ValueError as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return False
        signing_key.save_key(n, d, e)
        self._error = None
        if self._notify is not None:
            self._notify(S.SAVED)
        return True

    def _reset(self) -> None:
        signing_key.clear_key()
        self._load_from_signer()
        self._status = S.RESET_DONE
        self._error = None
        if self._notify is not None:
            self._notify(S.RESET_DONE)

    # --- rendering -----------------------------------------------------------------------

    def render_contents(self) -> None:
        if not self._loaded:
            self._load_from_signer()
        self._poll_dialog()
        self._apply_extraction_result()

        if signing_key_is_placeholder():
            imgui.text_colored(_RED, S.STATUS_PLACEHOLDER)
        else:
            imgui.text_colored(_GREEN, S.STATUS_GENUINE)
        imgui.text_wrapped(S.HINT)
        imgui.separator()

        field_h = hello_imgui.em_to_vec2(0.0, 2.4).y
        imgui.text_disabled(S.MODULUS)
        imgui.set_next_item_width(-1)
        _, self._modulus = imgui.input_text_multiline(
            "##modulus", self._modulus, imgui.ImVec2(-1, field_h)
        )
        imgui.text_disabled(S.PRIVATE_EXPONENT)
        imgui.set_next_item_width(-1)
        _, self._private_exponent = imgui.input_text_multiline(
            "##priv", self._private_exponent, imgui.ImVec2(-1, field_h)
        )
        imgui.text_disabled(S.PUBLIC_EXPONENT)
        imgui.set_next_item_width(-1)
        _, self._public_exponent = imgui.input_text("##pub", self._public_exponent)

        imgui.separator()
        imgui.text_wrapped(S.PICK_HINT)
        backend = extraction_backend()
        if backend is None:
            # text_colored does not wrap; push the colour so text_wrapped handles the line break.
            imgui.push_style_color(imgui.Col_.text, _RED)
            imgui.text_wrapped(S.NO_BACKEND)
            imgui.pop_style_color()
        if self._extracting:
            imgui.text_disabled(S.EXTRACTING)
        else:
            imgui.begin_disabled(backend is None)
            if imgui.button(S.EXTRACT):
                self.begin_extract()
            imgui.end_disabled()
        # The buttons go on in the next line where the dialog is narrow (a phone).
        same_line_if_fits(button_width(S.SAVE))
        if imgui.button(S.SAVE) and self._save():
            self._status = S.SAVED
        same_line_if_fits(button_width(S.RESET))
        if imgui.button(S.RESET):
            self._reset()

        if self._error:
            imgui.text_colored(_RED, self._error)
        elif self._status:
            imgui.text_colored(_GREEN, self._status)

        imgui.separator()
        # Wrapped, so a narrow dialog (a phone) shows it whole.
        imgui.push_style_color(
            imgui.Col_.text, imgui.get_style_color_vec4(imgui.Col_.text_disabled)
        )
        imgui.text_wrapped(S.CREDIT)
        imgui.pop_style_color()
