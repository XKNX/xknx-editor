"""Enumeration entries with an icon: the icon before the text in the drop-down and its list, an
entry without text that repeats or stretches its picture (a colour) across the row."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.tests._clicks import Clicks
from editor_gui.widgets import parameter_widgets
from editor_gui.widgets.icons import Icon
from editor_gui.widgets.parameter_widgets import (
    choice_text,
    device_images,
    render_param_widget,
)
from xknxeditor.namespaces.intermediate.horizontal_alignment_t import (
    HorizontalAlignment,
)
from xknxeditor.prod.parser_v2.ui.parameter import (
    EnumChoice,
    EnumWidget,
    ParameterTypeTypeRestrictionBase,
    UiParameter,
)

_CHOICES = (
    EnumChoice(0, "No Icon", "e0", picture_alignment=HorizontalAlignment.STRETCH),
    EnumChoice(73, "Accept", "e73", icon="icon_03_accept"),
    EnumChoice(
        1, "", "e1", icon="color_red", picture_alignment=HorizontalAlignment.REPEAT
    ),
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _param(value: str) -> UiParameter:
    widget = EnumWidget(_CHOICES, ParameterTypeTypeRestrictionBase.VALUE)
    return UiParameter(ref_id="r", label="Icon", value=value, widget=widget)


class _Drawn:
    """What was drawn: icons (by texture id) and full-row pictures."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.icons: list[int] = []
        self.rows: list[int] = []
        monkeypatch.setattr(
            parameter_widgets,
            "draw_icon",
            lambda icon, _pos, _size: self.icons.append(icon.texture.get_tex_id()),
        )
        real = imgui.ImDrawList.add_image

        def add_image(draw: Any, texture: Any, *args: Any) -> None:
            self.rows.append(texture.get_tex_id())
            real(draw, texture, *args)

        monkeypatch.setattr(imgui.ImDrawList, "add_image", add_image)


_TEXTURES = {"icon_03_accept": 7, "color_red": 9}


def _icon(name: str) -> Icon | None:
    texture = _TEXTURES.get(name)
    return Icon(imgui.ImTextureRef(texture), tinted=False) if texture else None


def _frame(param: UiParameter, changes: list[str]) -> None:
    imgui.new_frame()
    imgui.begin("Page")
    with device_images(_icon):
        render_param_widget(param, "w", changes.append)
    imgui.end()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_the_chosen_entry_and_the_list_show_the_icons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn = _Drawn(monkeypatch)
    changes: list[str] = []
    # The chosen entry: its icon before its text.
    _frame(_param("73"), changes)
    assert drawn.icons == [7] and drawn.rows == []
    # A colour fills the field.
    drawn.icons.clear()
    _frame(_param("1"), changes)
    assert drawn.icons == [] and drawn.rows == [9]
    # The open list: text only, the icon before the text, the colour across its row.
    clicks = Clicks(monkeypatch, parameter_widgets)
    clicks.combos.add("##w")
    clicks.items.add("##2")
    drawn.rows.clear()
    _frame(_param("0"), changes)
    assert drawn.icons == [7] and drawn.rows == [9]
    assert changes == ["1"]


@pytest.mark.usefixtures("context")
def test_without_icons_entries_show_text_or_the_icon_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn = _Drawn(monkeypatch)
    imgui.new_frame()
    imgui.begin("Page")
    render_param_widget(_param("1"), "w", lambda _v: None)
    imgui.end()
    imgui.end_frame()
    assert drawn.icons == [] and drawn.rows == []
    assert [choice_text(c) for c in _CHOICES] == ["No Icon", "Accept", "color_red"]


def test_device_images_are_set_only_inside() -> None:
    assert parameter_widgets._get_icon is None  # pyright: ignore[reportPrivateUsage]
    with device_images(_icon):
        assert parameter_widgets._get_icon is _icon  # pyright: ignore[reportPrivateUsage]
    assert parameter_widgets._get_icon is None  # pyright: ignore[reportPrivateUsage]
