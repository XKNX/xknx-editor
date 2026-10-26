"""The Bus Diagram drawn headless: its three views, the choice of what the Links view shows, Back
and Fit, and a window too narrow for the hint beside the view buttons."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.bus_diagram import links_view
from editor_gui.plugins.bus_diagram import ui as ui_module
from editor_gui.plugins.bus_diagram.flow import Hub, Scope
from editor_gui.plugins.bus_diagram.strings import S
from editor_gui.plugins.bus_diagram.ui import BusDiagramPanel
from editor_gui.plugins.project.tests._clicks import Clicks

_DEVICES = [
    Device(1, "Dimmer", None, "1.1.1", description="Hall"),
    Device(2, "Switch", None, "1.1.2"),
    Device(3, "Sensor", None, "1.2.1"),
    Device(4, "", None, ""),
]
_HUBS: list[Hub] = [
    (10, "1/1/1", "Light", ((2, True, False), (1, False, True))),
    (11, "1/1/2", "Dim", ((3, True, False), (1, False, True), (2, False, True))),
    (12, "2/1/1", "Solo", ((2, True, True),)),
]


def _space(name: str, devices: list[int], *children: Any) -> Any:
    return SimpleNamespace(
        name=name,
        space_type="Room",
        devices=[
            SimpleNamespace(
                id=d.node_id,
                name=d.name,
                individual_address=d.individual_address,
                description=d.description,
            )
            for d in _DEVICES
            if d.node_id in devices
        ],
        children=list(children),
    )


_TREE = [_space("House", [], _space("Kitchen", [1, 3]), _space("Hall", [2]))]


@pytest.fixture
def clicks(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clicks]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1200, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield Clicks(monkeypatch, ui_module, links_view)
    imgui.destroy_context(ctx)


def _panel(opened: list[int], is_open: bool = True) -> BusDiagramPanel:
    return BusDiagramPanel(
        get_devices=lambda: _DEVICES,
        get_space_tree=lambda: _TREE,
        on_select=opened.append,
        is_open=lambda: is_open,
        get_link_graph=lambda: _HUBS,
        get_group_names=lambda: {"1": "Lights", "1/1": "Hall"},
    )


def _frames(panel: BusDiagramPanel, width: float = 1000, count: int = 2) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(width, 700))
        imgui.begin("Bus Diagram")
        panel.render()
        imgui.end()
        imgui.end_frame()
        imgui.render()


def _choose(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Click the view button ``label`` in the next frame."""
    real = ui_module.radio_button

    def radio_button(text: str, active: bool) -> bool:
        return text == label or real(text, active)

    monkeypatch.setattr(ui_module, "radio_button", radio_button)


def test_every_view_is_drawn(clicks: Clicks, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _panel([])
    _frames(panel)
    _choose(monkeypatch, S.BUS_DIAGRAM_MODE_BUILDING)
    _frames(panel)
    assert panel._mode == "building"  # pyright: ignore[reportPrivateUsage]
    _choose(monkeypatch, S.BUS_DIAGRAM_MODE_LINKS)
    _frames(panel)
    assert panel._mode == "links"  # pyright: ignore[reportPrivateUsage]
    # A phone-wide window: the hint goes on a line of its own.
    _frames(panel, width=300)


def test_the_links_view_shows_a_room_a_main_group_and_goes_back(
    clicks: Clicks, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel([])
    _choose(monkeypatch, S.BUS_DIAGRAM_MODE_LINKS)
    _frames(panel)
    links = panel._links  # pyright: ignore[reportPrivateUsage]
    assert links is not None
    clicks.combos.add("##topo_scope")
    clicks.items.add("House / Kitchen")
    _frames(panel, count=1)
    assert links._scope == Scope("room", "House / Kitchen")  # pyright: ignore[reportPrivateUsage]
    clicks.combos.add("##topo_scope")
    clicks.items.add("1/*  Lights")
    _frames(panel, count=1)
    assert links._scope == Scope("main", 1)  # pyright: ignore[reportPrivateUsage]
    # One device and one group address, as a click on them shows them.
    links._show(Scope("device", 2))  # pyright: ignore[reportPrivateUsage]
    _frames(panel)
    links._show(Scope("ga", 12))  # pyright: ignore[reportPrivateUsage]
    _frames(panel)
    real_button = imgui.button
    pressed = {S.BUS_DIAGRAM_BACK, S.BUS_DIAGRAM_FIT}

    def button(label: str, *args: Any) -> bool:
        clicked = real_button(label, *args)
        if label in pressed:
            pressed.discard(label)
            return True
        return clicked

    monkeypatch.setattr(imgui, "button", button)
    _frames(panel, count=1)
    assert links._scope == Scope("device", 2)  # pyright: ignore[reportPrivateUsage]
    clicks.combos.add("##topo_scope")
    clicks.items.add(S.BUS_DIAGRAM_SCOPE_ALL)
    _frames(panel, count=1)
    assert links._scope == Scope("all")  # pyright: ignore[reportPrivateUsage]


def test_nothing_linked_and_no_project(
    clicks: Clicks, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel([])
    _choose(monkeypatch, S.BUS_DIAGRAM_MODE_LINKS)
    links = panel._links  # pyright: ignore[reportPrivateUsage]
    assert links is not None
    links._show(Scope("main", 9))  # pyright: ignore[reportPrivateUsage]
    _frames(panel)
    _frames(_panel([], is_open=False))
