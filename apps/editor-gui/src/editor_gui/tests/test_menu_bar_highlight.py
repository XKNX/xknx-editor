"""A menu of the menu bar is highlighted down to the bar's lower edge; the menu it opens keeps
the usual spacing."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui import main


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.set_ini_filename("")
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_a_bar_menu_reaches_the_bar_edge(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    spacing: list[float] = []
    real = imgui.begin_menu

    def begin_menu(label: str, *args: Any) -> bool:
        spacing.append(imgui.get_style().item_spacing.y)
        return real(label, *args)

    monkeypatch.setattr(imgui, "begin_menu", begin_menu)
    style = imgui.get_style()
    usual = style.item_spacing.y
    imgui.new_frame()
    if imgui.begin_main_menu_bar():
        if main._bar_menu("File"):  # pyright: ignore[reportPrivateUsage]
            imgui.end_menu()
        imgui.end_main_menu_bar()
    imgui.end_frame()
    # Half the spacing (rounded down) above the text, the rest below: down to the bar's edge,
    # which is the frame padding below the text.
    assert spacing == [style.frame_padding.y * 2 - 1.0]
    assert style.item_spacing.y == usual
