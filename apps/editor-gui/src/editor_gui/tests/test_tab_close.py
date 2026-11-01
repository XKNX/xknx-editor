"""The close button of a docked window's tab: as far from the tab's right edge as from its top and
bottom (the tab narrower by what imgui leaves right of its own button), on the tab in front and on
one under the mouse; it closes the window, a section's through hello_imgui, a tool window when it
begins next."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui import dock_menus
from editor_gui.widgets.dialog import closed_from_tab

_WINDOWS = ("One", "Two")


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.config_flags |= imgui.ConfigFlags_.docking_enable
    # As the theme sets it: imgui's own close buttons never show.
    style = imgui.get_style()
    style.tab_close_button_min_width_selected = 1e30
    style.tab_close_button_min_width_unselected = 1e30
    yield
    imgui.destroy_context(ctx)


class _Frames:
    """Frames of a dock space with two docked windows, "Two" in front; ``buttons`` gets the close
    buttons drawn in the last one (window, box, shown)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.buttons: list[tuple[str, tuple[float, float, float, float], bool]] = []
        self.dockable: dict[str, Any] = {}
        self.node: Any = None
        draw = dock_menus._close_button  # pyright: ignore[reportPrivateUsage]

        def record(
            box: imgui.internal.ImRect, ident: int, *, shown: bool = True
        ) -> bool:
            name = next(
                n for n in _WINDOWS if imgui.get_id(f"##tab_close_{n}") == ident
            )
            self.buttons.append(
                (name, (box.min.x, box.min.y, box.max.x, box.max.y), shown)
            )
            return draw(box, ident, shown=shown)

        monkeypatch.setattr(dock_menus, "_close_button", record)

    def run(
        self, mouse: tuple[float, float], count: int = 3, *, down: bool = False
    ) -> None:
        io = imgui.get_io()
        io.add_mouse_pos_event(*mouse)
        for _ in range(count):
            io.add_mouse_button_event(0, down)
            imgui.new_frame()
            imgui.set_next_window_pos(imgui.ImVec2(0, 0))
            imgui.set_next_window_size(imgui.ImVec2(800, 600))
            imgui.begin("Host", None, imgui.WindowFlags_.no_docking)
            space = imgui.get_id("Space")
            if imgui.internal.dock_builder_get_node(space) is None:  # pyright: ignore[reportUnnecessaryComparison]
                imgui.internal.dock_builder_add_node(
                    space, imgui.internal.DockNodeFlagsPrivate_.dock_space
                )
                imgui.internal.dock_builder_set_node_size(space, imgui.ImVec2(800, 600))
                for name in _WINDOWS:
                    imgui.internal.dock_builder_dock_window(name, space)
                imgui.internal.dock_builder_finish(space)
            imgui.dock_space(space)
            imgui.end()
            for name in _WINDOWS:
                imgui.begin(name, True)
                imgui.end()
            self.node = imgui.internal.dock_builder_get_node(space)
            self.buttons.clear()
            if imgui.internal.dock_node_begin_amend_tab_bar(self.node):
                dock_menus._render_tab_closes(self.node, self.dockable)  # pyright: ignore[reportPrivateUsage]
                imgui.internal.dock_node_end_amend_tab_bar()
            imgui.end_frame()
            imgui.render()

    def tab(self, name: str) -> Any:
        return next(t for t in self.node.tab_bar.tabs if t.window.name == name)

    def tab_rect(self, name: str) -> tuple[float, float, float, float]:
        tab = self.tab(name)
        bar = self.node.tab_bar.bar_rect
        left = bar.min.x + tab.offset
        return left, bar.min.y, left + tab.width, bar.max.y


@pytest.mark.usefixtures("context")
def test_close_buttons_on_the_front_tab_and_under_the_mouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = _Frames(monkeypatch)
    frames.run((-1, -1))
    assert [(name, shown) for name, _box, shown in frames.buttons] == [
        ("One", False),
        ("Two", True),
    ]
    left, top, right, _bottom = frames.tab_rect("One")
    frames.run(((left + right) / 2 - 10, top + 10))
    assert [(name, shown) for name, _box, shown in frames.buttons] == [
        ("One", True),
        ("Two", True),
    ]


@pytest.mark.usefixtures("context")
def test_close_button_as_far_from_the_right_edge_as_from_top_and_bottom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = _Frames(monkeypatch)
    frames.run((-1, -1))
    _name, (x0, y0, x1, y1), _shown = frames.buttons[1]
    _left, top, right, _bottom = frames.tab_rect("Two")
    style = imgui.get_style()
    # The tab starts 1 px below the bar and reaches its bottom (a frame high).
    tab_top, tab_bottom = top + 1, top + int(imgui.get_frame_height())
    gap = right - x1
    assert gap == y0 - tab_top == pytest.approx(tab_bottom - y1)
    # The button as far from the label as imgui's: the tab narrower by what is left over.
    font = imgui.get_font_size()
    label = imgui.calc_text_size("Two").x
    imgui_width = label + 2 * style.frame_padding.x + style.item_inner_spacing.x + font
    side = x1 - x0
    assert frames.tab("Two").width == pytest.approx(
        imgui_width - (style.frame_padding.x + font - gap - side)
    )


@pytest.mark.usefixtures("context")
def test_close_button_closes_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = _Frames(monkeypatch)
    frames.run((-1, -1))
    _name, (x0, y0, x1, y1), _shown = frames.buttons[1]
    centre = ((x0 + x1) / 2, (y0 + y1) / 2)
    # A tool window: closed when it begins next.
    frames.run(centre, 2)
    frames.run(centre, 1, down=True)
    frames.run(centre, 1)
    assert closed_from_tab("Two")
    assert not closed_from_tab("Two")
    # A window of a section: hidden through hello_imgui.
    section = SimpleNamespace(is_visible=True)
    frames.dockable["Two"] = section
    frames.run(centre, 2)
    frames.run(centre, 1, down=True)
    frames.run(centre, 1)
    assert section.is_visible is False
    assert not closed_from_tab("Two")
