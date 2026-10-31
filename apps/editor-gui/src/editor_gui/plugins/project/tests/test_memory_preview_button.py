"""The memory preview opens from a small icon button beside the programming buttons."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import configure
from editor_gui.plugins.project.ui.configure import (
    MEMORY_PREVIEW_BUTTON,
    ConfigurePanel,
)


def _panel(device: Any, opened: list[Any]) -> ConfigurePanel:
    def unused(*_args: Any) -> None:
        raise AssertionError("not expected")

    return ConfigurePanel(
        get_devices=lambda: [device],
        get_selected_device=lambda: device,
        set_selected_device=unused,
        on_param_change=unused,
        on_individual_address_change=unused,
        on_name_change=unused,
        on_description_change=unused,
        set_flag=unused,
        get_links_for_com_object=cast(Any, unused),
        get_all_group_addresses=cast(Any, unused),
        on_link_com_object=unused,
        on_unlink_com_object=unused,
        on_auto_create_gas=unused,
        get_device_info=lambda _node_id: None,
        open_memory_preview=opened.append,
    )


class _Imgui:
    """The imgui calls of the programming row, recorded; ``clicked`` presses that button."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, clicked: str | None) -> None:
        self.buttons: list[tuple[str, bool]] = []
        self.tooltips: list[str] = []
        self._disabled = False
        imgui = configure.imgui
        monkeypatch.setattr(imgui, "button", self._button)
        monkeypatch.setattr(imgui, "begin_disabled", self._begin_disabled)
        monkeypatch.setattr(imgui, "end_disabled", lambda: None)
        monkeypatch.setattr(imgui, "set_item_tooltip", self.tooltips.append)
        monkeypatch.setattr(imgui, "set_tooltip", self.tooltips.append)
        monkeypatch.setattr(imgui, "is_item_hovered", lambda *_args: True)
        monkeypatch.setattr(configure, "_same_line_if_fits", lambda _label: None)
        self._clicked = clicked

    def _begin_disabled(self, disabled: bool = True) -> None:
        self._disabled = disabled

    def _button(self, label: str, *_args: Any) -> bool:
        self.buttons.append((label, self._disabled))
        return label == self._clicked


def _device(*, has_application: bool) -> Any:
    return SimpleNamespace(
        node_id=1,
        name="",
        individual_address="1.1.1",
        has_application=has_application,
    )


def test_the_button_shows_an_icon_and_names_itself_in_its_tooltip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _device(has_application=True)
    opened: list[Any] = []
    panel = _panel(device, opened)
    monkeypatch.setattr(panel, "_render_scope_selector", lambda: None)
    recorded = _Imgui(monkeypatch, clicked=MEMORY_PREVIEW_BUTTON)

    panel._render_programming(device)  # pyright: ignore[reportPrivateUsage]

    assert recorded.buttons == [(MEMORY_PREVIEW_BUTTON, False)]
    # Only the icon is drawn: the text after "###" is the button's id.
    assert MEMORY_PREVIEW_BUTTON.split("###")[0] == ""
    assert recorded.tooltips == [f"{S.BTN_PREVIEW_MEMORY}\n{S.PREVIEW_MEMORY_HINT}"]
    assert opened == [device]


def test_without_an_application_the_tooltip_says_why_it_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _device(has_application=False)
    opened: list[Any] = []
    panel = _panel(device, opened)
    monkeypatch.setattr(panel, "_render_scope_selector", lambda: None)
    recorded = _Imgui(monkeypatch, clicked=None)

    panel._render_programming(device)  # pyright: ignore[reportPrivateUsage]

    assert recorded.buttons == [(MEMORY_PREVIEW_BUTTON, True)]
    assert recorded.tooltips[-1] == S.CONFIGURE_NO_APPLICATION
    assert opened == []


def test_the_title_row_makes_room_for_the_icon_only() -> None:
    panel = _panel(_device(has_application=True), [])
    assert panel._programming_labels() == [MEMORY_PREVIEW_BUTTON]  # pyright: ignore[reportPrivateUsage]
