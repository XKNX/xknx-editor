"""A drop-down of an element's details grays out the choices the rules refuse for it (a segment
on a TP line cannot be IoT), telling why when hovered; they cannot be chosen."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui import status_field
from editor_gui.plugins.project.ui.status_field import choice_combo

_OPTIONS = [("MT-0", "TP"), ("MT-2", "RF"), ("MT-6", "IoT")]


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
def test_a_refused_choice_is_grayed_out_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled: list[bool] = []
    tips: list[str] = []
    real_begin_disabled = imgui.begin_disabled
    monkeypatch.setattr(status_field, "begin_combo", lambda *_a: True)
    monkeypatch.setattr(imgui, "end_combo", lambda: None)

    def begin_disabled(on: bool = True) -> None:
        disabled.append(on)
        real_begin_disabled(on)

    monkeypatch.setattr(imgui, "begin_disabled", begin_disabled)
    monkeypatch.setattr(imgui, "is_item_hovered", lambda *_a: True)
    monkeypatch.setattr(imgui, "set_tooltip", tips.append)
    # Every entry "clicked": only the ones not refused are chosen.
    real_selectable = imgui.selectable

    def selectable(label: str, *args: Any) -> Any:
        return True, real_selectable(label, *args)[1]

    monkeypatch.setattr(imgui, "selectable", selectable)
    imgui.new_frame()
    imgui.begin("Details")
    chosen = choice_combo("##medium", _OPTIONS, "MT-0", {"MT-6": "Its line is not IP."})
    imgui.end()
    imgui.end_frame()
    assert disabled == [False, False, True]
    assert tips == ["Its line is not IP."]
    assert chosen == "MT-2"
