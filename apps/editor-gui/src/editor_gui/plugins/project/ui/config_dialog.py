"""Shared options dialog for transferring a device configuration (parameters + group-address
links), used by both the Devices tree and the Buildings tree."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px_vec2


class DeviceConfigDialog:
    """Deferred-open modal offering Parameters / GA-links checkboxes for a paste or a duplicate.

    ``on_paste`` / ``on_duplicate`` receive (node_id, include_params, include_links). The dialog is
    embedded per panel (imgui popup state is panel-local) but shared so every device tree renders an
    identical dialog. ``popup_key`` keeps the two panels' modals from colliding on the same title."""

    def __init__(
        self,
        popup_key: str,
        on_paste: Callable[[int, bool, bool], None],
        on_duplicate: Callable[[int, bool, bool], None],
        count_paste_targets: Callable[[int], int] = lambda _n: 1,
    ) -> None:
        self._popup_id = f"{S.CONFIG_DIALOG_TITLE}##{popup_key}"
        self._on_paste = on_paste
        self._on_duplicate = on_duplicate
        self._count_paste_targets = count_paste_targets
        self._mode: str | None = None  # "paste" | "duplicate"
        self._node_id: int | None = None
        self._label = ""
        self._include_params = True
        self._include_links = True
        self._open = False

    def open(self, node_id: int, label: str, mode: str) -> None:
        self._node_id = node_id
        self._label = label
        self._mode = mode
        self._include_params = True
        self._include_links = True
        self._open = True

    def render(self) -> None:
        # Open deferred at the panel's top-level scope: open_popup issued inside a context menu
        # never matches begin_popup_modal here (different id seed).
        if self._open:
            imgui.open_popup(self._popup_id)
            self._open = False
        if not imgui.begin_popup_modal(
            self._popup_id, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        is_paste = self._mode == "paste"
        count = (
            self._count_paste_targets(self._node_id)
            if is_paste and self._node_id is not None
            else 1
        )
        if is_paste and count > 1:
            imgui.text_wrapped(S.CONFIG_PASTE_INTO_MULTI.format(count=count))
        else:
            header = S.CONFIG_PASTE_INTO if is_paste else S.CONFIG_DUPLICATE_OF
            imgui.text_wrapped(header.format(name=self._label))
        imgui.separator()
        _, self._include_params = imgui.checkbox(
            S.CONFIG_INCLUDE_PARAMS, self._include_params
        )
        _, self._include_links = imgui.checkbox(
            S.CONFIG_INCLUDE_LINKS, self._include_links
        )
        imgui.separator()
        imgui.begin_disabled(not (self._include_params or self._include_links))
        if imgui.button(
            S.CONFIG_PASTE_BTN if is_paste else S.CONFIG_DUPLICATE_BTN,
            px_vec2(110, 0),
        ):
            if self._node_id is not None:
                handler = self._on_paste if is_paste else self._on_duplicate
                handler(self._node_id, self._include_params, self._include_links)
            self._close()
            imgui.close_current_popup()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, px_vec2(90, 0)):
            self._close()
            imgui.close_current_popup()
        imgui.end_popup()

    def _close(self) -> None:
        self._node_id = None
        self._mode = None
