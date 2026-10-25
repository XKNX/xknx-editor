"""Choose items of menus and dialogs in a headless imgui context, as a user would click them.

A test names what it wants: the context menu to open (by its popup id), the submenus to enter,
the menu items, selectables and small buttons to click and the dialog button to press (by their
labels). Each is used once; everything else renders and behaves as without it.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
from imgui_bundle import imgui


class Clicks:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *modules: ModuleType) -> None:
        self.context: str | None = None
        self.menus: set[str] = set()
        self.items: set[str] = set()
        self.button: str | None = None
        # Tree nodes shown open (by label), as after a click on their arrow; they stay open.
        self.open: set[str] = set()
        # Drop-downs opened once (by label), their entries listed as in the open popup.
        self.combos: set[str] = set()
        self._entered: list[bool] = []
        self._combos_entered: list[bool] = []
        real_end_combo = imgui.end_combo

        def end_combo() -> None:
            if not self._combos_entered.pop():
                real_end_combo()

        monkeypatch.setattr(imgui, "end_combo", end_combo)
        real_context = imgui.begin_popup_context_item
        real_begin_menu = imgui.begin_menu
        real_end_menu = imgui.end_menu
        real_menu_item = imgui.menu_item
        real_selectable = imgui.selectable
        real_small_button = imgui.small_button

        def begin_popup_context_item(str_id: str | None = None, *args: Any) -> bool:
            if str_id is not None and str_id == self.context:
                self.context = None
                imgui.open_popup(str_id)
            return real_context(str_id, *args)

        def begin_menu(label: str, *args: Any) -> bool:
            if label in self.menus:
                self.menus.discard(label)
                self._entered.append(True)
                return True
            opened = real_begin_menu(label, *args)
            if opened:
                self._entered.append(False)
            return opened

        def end_menu() -> None:
            if not self._entered.pop():
                real_end_menu()

        # A click on a menu item, or on a selectable in a popup, closes the popup.
        def menu_item(label: str, *args: Any) -> Any:
            result = real_menu_item(label, *args)
            if label in self.items:
                self.items.discard(label)
                imgui.close_current_popup()
                return True, result[1]
            return result

        def selectable(label: str, *args: Any, **kwargs: Any) -> Any:
            result = real_selectable(label, *args, **kwargs)
            if label in self.items:
                self.items.discard(label)
                if not (self._combos_entered and self._combos_entered[-1]):
                    imgui.close_current_popup()
                return True, result[1]
            return result

        def small_button(label: str) -> bool:
            clicked = real_small_button(label)
            if label in self.items:
                self.items.discard(label)
                return True
            return clicked

        for name, fake in (
            ("begin_popup_context_item", begin_popup_context_item),
            ("begin_menu", begin_menu),
            ("end_menu", end_menu),
            ("menu_item", menu_item),
            ("selectable", selectable),
            ("small_button", small_button),
        ):
            monkeypatch.setattr(imgui, name, fake)
        for module in modules:
            if hasattr(module, "tree_node"):

                def tree_node(
                    label: str, *args: Any, _real: Any = module.tree_node, **kwargs: Any
                ) -> bool:
                    if label in self.open:
                        imgui.set_next_item_open(True)
                    return _real(label, *args, **kwargs)

                monkeypatch.setattr(module, "tree_node", tree_node)
            if hasattr(module, "begin_combo"):

                def begin_combo(
                    label: str,
                    *args: Any,
                    _real: Any = module.begin_combo,
                    **kwargs: Any,
                ) -> bool:
                    if label in self.combos:
                        self.combos.discard(label)
                        self._combos_entered.append(True)
                        return True
                    opened = _real(label, *args, **kwargs)
                    if opened:
                        self._combos_entered.append(False)
                    return opened

                monkeypatch.setattr(module, "begin_combo", begin_combo)
            if not hasattr(module, "dialog_buttons"):
                continue
            real_buttons = module.dialog_buttons

            def dialog_buttons(
                *labels: str, _real: Any = real_buttons, **kwargs: Any
            ) -> int | None:
                clicked = _real(*labels, **kwargs)
                if self.button in labels:
                    index = labels.index(self.button)
                    self.button = None
                    return index
                return clicked

            monkeypatch.setattr(module, "dialog_buttons", dialog_buttons)
