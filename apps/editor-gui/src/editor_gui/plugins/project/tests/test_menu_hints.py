"""Hints that point to a menu name it with the menu's own label, in every UI language."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.languages import TRANSLATED
from editor_gui.plugins.connection.strings import S as CONNECTION_S
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.topology import TopologyPanel
from editor_gui.strings import S as APP_S
from editor_gui.strings import set_locale


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def english_afterwards() -> Iterator[None]:
    yield
    set_locale("en")


@pytest.mark.usefixtures("english_afterwards")
@pytest.mark.parametrize("language", ["en", *TRANSLATED])
def test_the_hints_name_the_menus_label(language: str) -> None:
    set_locale(language)
    gateway = S.PREFLIGHT_NO_CONNECTION.format(menu=CONNECTION_S.MENU_GATEWAY)
    assert CONNECTION_S.MENU_GATEWAY in gateway
    file = S.DEVICE_EMPTY_HINT.format(menu=APP_S.MENU_FILE)
    assert APP_S.MENU_FILE in file


def _empty_topology() -> TopologyPanel:
    return TopologyPanel(
        get_devices=list,
        get_areas=list,
        get_lines=lambda _area_id: [],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _s, _m: None,
        on_remove_area=lambda _a: None,
        on_create_line=lambda _a, _n, _s, _m: None,
        on_remove_line=lambda _l: None,
    )


@pytest.mark.usefixtures("context", "english_afterwards")
@pytest.mark.parametrize(
    ("language", "expected"), [("en", "(File menu)"), ("de", "(Menü Datei)")]
)
def test_an_empty_topology_points_to_the_file_menu(
    monkeypatch: pytest.MonkeyPatch, language: str, expected: str
) -> None:
    set_locale(language)
    shown: list[str] = []
    monkeypatch.setattr(imgui, "text_disabled", shown.append)
    panel = _empty_topology()
    imgui.new_frame()
    imgui.begin("Topology")
    panel.render()
    imgui.end()
    imgui.end_frame()
    assert shown[0] == S.DEVICE_EMPTY_HINT.format(menu=APP_S.MENU_FILE)
    assert expected in shown[0]
