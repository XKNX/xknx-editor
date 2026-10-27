"""The Bus Diagram drawn headless: its three views, the choice of what the Links view shows, Back
and Fit, the group objects of a device and its channels, and a window too narrow for the hint
beside the view buttons."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.bus_diagram import links_view
from editor_gui.plugins.bus_diagram import ui as ui_module
from editor_gui.plugins.bus_diagram.canvas import crisp_zoom
from editor_gui.plugins.bus_diagram.flow import GroupObject, Hub, Member, Scope
from editor_gui.plugins.bus_diagram.links_view import LinkView
from editor_gui.plugins.bus_diagram.strings import S
from editor_gui.plugins.bus_diagram.ui import BusDiagramPanel
from editor_gui.plugins.project.tests._clicks import Clicks
from editor_gui.widgets.dpi import px

_DEVICES = [
    Device(1, "Dimmer", None, "1.1.1", description="Hall"),
    Device(2, "Switch", None, "1.1.2"),
    Device(3, "Sensor", None, "1.2.1"),
    Device(4, "", None, ""),
]
_HUBS: list[Hub] = [
    Hub(
        10,
        "1/1/1",
        "Light",
        (
            Member(2, True, False, ((201, True, False),)),
            Member(1, False, True, ((101, False, True),)),
        ),
    ),
    Hub(
        11,
        "1/1/2",
        "Dim",
        (
            Member(3, True, False, ((301, True, False),)),
            Member(1, False, True, ((102, False, True),)),
            Member(2, False, True, ((202, False, True),)),
        ),
    ),
    Hub(12, "2/1/1", "Solo", (Member(2, True, True, ((203, True, True),)),)),
]
# The dimmer's two channels.
_OBJECTS = {
    1: {
        101: GroupObject(1, "Switch", "CH-A", "Channel A", 1),
        102: GroupObject(4, "Dim", "CH-B", "Channel B", 2),
    }
}


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


def _panel(
    opened: list[int],
    is_open: bool = True,
    requests: list[tuple[int, str]] | None = None,
) -> BusDiagramPanel:
    asked = requests if requests is not None else []
    return BusDiagramPanel(
        get_devices=lambda: _DEVICES,
        get_space_tree=lambda: _TREE,
        on_select=opened.append,
        is_open=lambda: is_open,
        get_link_graph=lambda: _HUBS,
        get_group_names=lambda: {"1": "Lights", "1/1": "Hall"},
        get_group_objects=lambda node_id: _OBJECTS.get(node_id, {}),
        take_request=lambda: asked.pop(0) if asked else None,
    )


class _Mouse:
    """Points the mouse at a place of the Links diagram, given in its unscaled coordinates, and
    clicks there."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, links: LinkView) -> None:
        self._links = links
        self._origin = imgui.ImVec2(0, 0)
        self._z = 1.0
        real = LinkView._draw  # pyright: ignore[reportPrivateUsage]

        def draw(view: LinkView, *args: Any) -> None:
            self._origin = imgui.get_cursor_screen_pos()
            self._z = crisp_zoom(view._zoom) * px(1.0)  # pyright: ignore[reportPrivateUsage]
            real(view, *args)

        monkeypatch.setattr(LinkView, "_draw", draw)

    def at(self, x: float, y: float) -> None:
        imgui.get_io().add_mouse_pos_event(
            self._origin.x + x * self._z,
            self._origin.y + (links_view._TITLE_H + y) * self._z,  # pyright: ignore[reportPrivateUsage]
        )

    def click(self, panel: BusDiagramPanel, x: float, y: float) -> None:
        """Hover the place for a frame, then click it; the diagram is laid out again in a frame
        after."""
        self.at(x, y)
        _frames(panel, width=1400, count=1)
        drag = self._links._drag  # pyright: ignore[reportPrivateUsage]
        self._links._drag = SimpleNamespace(update=lambda: True)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        _frames(panel, width=1400, count=1)
        self._links._drag = drag  # pyright: ignore[reportPrivateUsage]
        _frames(panel, width=1400, count=1)


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
    links.show(Scope("device", 2))
    _frames(panel)
    links.show(Scope("ga", 12))
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
    links.show(Scope("main", 9))
    _frames(panel)
    _frames(_panel([], is_open=False))


def _links(panel: BusDiagramPanel) -> LinkView:
    links = panel._links  # pyright: ignore[reportPrivateUsage]
    assert links is not None
    return links


def test_the_back_keys_go_back(clicks: Clicks) -> None:
    """Backspace and the Back key while the diagram has the focus, and the mouse's back button
    over it, do what Back does."""
    panel = _panel([], requests=[(1, "")])
    _frames(panel, width=1400)
    links = _links(panel)
    for scope in (Scope("ga", 10), Scope("ga", 11), Scope("ga", 12)):
        links.show(scope)
    _frames(panel, width=1400)
    io = imgui.get_io()
    io.add_mouse_pos_event(500, 400)
    _frames(panel, width=1400, count=1)
    shown: list[Scope] = []
    for press, release in (
        (
            lambda: io.add_key_event(imgui.Key.backspace, True),
            lambda: io.add_key_event(imgui.Key.backspace, False),
        ),
        (
            lambda: io.add_key_event(imgui.Key.app_back, True),
            lambda: io.add_key_event(imgui.Key.app_back, False),
        ),
        (
            lambda: io.add_mouse_button_event(3, True),
            lambda: io.add_mouse_button_event(3, False),
        ),
    ):
        press()
        _frames(panel, width=1400, count=1)
        release()
        _frames(panel, width=1400, count=1)
        shown.append(links._scope)  # pyright: ignore[reportPrivateUsage]
    assert shown == [Scope("ga", 11), Scope("ga", 10), Scope("device", 1)]
    # Back to the start, then nothing to go back to: the keys do nothing.
    for _ in range(2):
        io.add_key_event(imgui.Key.backspace, True)
        _frames(panel, width=1400, count=1)
        io.add_key_event(imgui.Key.backspace, False)
        _frames(panel, width=1400, count=1)
    assert links._scope == Scope("all")  # pyright: ignore[reportPrivateUsage]
    links.back()
    assert links._scope == Scope("all")  # pyright: ignore[reportPrivateUsage]


def test_a_device_shown_lists_its_group_objects_and_its_arrow_closes_them(
    clicks: Clicks,
) -> None:
    panel = _panel([], requests=[(1, "")])
    _frames(panel, width=1400)
    # "Show in Bus Diagram" from a device: the Links view with that device.
    assert panel._mode == "links"  # pyright: ignore[reportPrivateUsage]
    links = _links(panel)
    assert links._scope == Scope("device", 1)  # pyright: ignore[reportPrivateUsage]
    layout = links._layout  # pyright: ignore[reportPrivateUsage]
    assert layout is not None
    assert {n.key for n in layout.nodes if n.owner == 1} == {("ro", 101), ("ro", 102)}
    assert [c.label for c in layout.channels] == ["Channel A", "Channel B"]


def test_the_arrow_opens_and_closes_a_device(
    clicks: Clicks, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = _panel([], requests=[(1, "")])
    _frames(panel, width=1400)
    links = _links(panel)
    mouse = _Mouse(monkeypatch, links)
    _frames(panel, width=1400)

    def node(key: tuple[str, int]) -> Any:
        layout = links._layout  # pyright: ignore[reportPrivateUsage]
        assert layout is not None
        return next(n for n in layout.nodes if n.key == key)

    # The arrow of the device shown closes its group objects, a second click opens them again.
    mouse.click(panel, links_view._X[2] + 6, node(("r", 1)).y + 10)  # pyright: ignore[reportPrivateUsage]
    assert not node(("r", 1)).opened
    mouse.click(panel, links_view._X[2] + 6, node(("r", 1)).y + 10)  # pyright: ignore[reportPrivateUsage]
    assert node(("r", 1)).opened
    # Another device opens with its arrow, and stays open in another view.
    switch = node(("s", 2))
    mouse.click(panel, links_view._X[0] + 6, switch.y + 10)  # pyright: ignore[reportPrivateUsage]
    assert node(("s", 2)).opened
    assert {n.key for n in links._layout.nodes if n.owner == 2} >= {("so", 201)}  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    links.show(Scope("all"))
    _frames(panel, width=1400)
    assert node(("s", 2)).opened
    # The device shown is open only while it is shown, unless it was opened with its arrow.
    assert node(("r", 1)).opened
    links.show(Scope("device", 3))
    _frames(panel, width=1400)
    mouse.click(panel, links_view._X[0] + 6, node(("s", 3)).y + 10)  # pyright: ignore[reportPrivateUsage]
    links.show(Scope("all"))
    _frames(panel, width=1400)
    assert not node(("s", 3)).opened


def test_a_click_on_a_channel_or_a_group_object_shows_its_channel(
    clicks: Clicks, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[int] = []
    panel = _panel(opened, requests=[(1, "")])
    _frames(panel, width=1400)
    links = _links(panel)
    mouse = _Mouse(monkeypatch, links)
    _frames(panel, width=1400)
    layout = links._layout  # pyright: ignore[reportPrivateUsage]
    assert layout is not None
    heading = next(c for c in layout.channels if c.channel == "CH-B")
    mouse.click(panel, links_view._X[2] + 40, heading.y + 3)  # pyright: ignore[reportPrivateUsage]
    assert links._scope == Scope("channel", 1, "CH-B")  # pyright: ignore[reportPrivateUsage]
    # Only the channel's group address; its device still lists the group object.
    layout = links._layout  # pyright: ignore[reportPrivateUsage]
    assert layout is not None
    assert [n.key for n in layout.nodes if n.column == 1] == [("g", 11)]
    links.show(Scope("device", 1))
    _frames(panel, width=1400)
    layout = links._layout  # pyright: ignore[reportPrivateUsage]
    assert layout is not None
    switch = next(n for n in layout.nodes if n.key == ("ro", 101))
    mouse.click(panel, links_view._X[2] + 60, switch.y + 10)  # pyright: ignore[reportPrivateUsage]
    assert links._scope == Scope("channel", 1, "CH-A")  # pyright: ignore[reportPrivateUsage]
    # Hovering a group object, a device and a group address shows what they are.
    layout = links._layout  # pyright: ignore[reportPrivateUsage]
    assert layout is not None
    for key in (("ro", 101), ("s", 2), ("g", 10)):
        n = next(n for n in layout.nodes if n.key == key)
        mouse.at(links_view._X[n.column] + 60, n.y + 10)  # pyright: ignore[reportPrivateUsage]
        _frames(panel, width=1400, count=1)
    assert opened == []


def test_the_channels_of_the_device_shown_can_be_chosen(clicks: Clicks) -> None:
    panel = _panel([], requests=[(1, "CH-A")])
    _frames(panel, width=1400)
    links = _links(panel)
    # "Show in Bus Diagram" from a channel page: the Links view with that channel.
    assert links._scope == Scope("channel", 1, "CH-A")  # pyright: ignore[reportPrivateUsage]
    assert links._scope_label(links._scope, _HUBS) == "Channel: Channel A (1.1.1)"  # pyright: ignore[reportPrivateUsage]
    clicks.combos.add("##topo_scope")
    clicks.items.add("Channel B##CH-B")
    _frames(panel, width=1400, count=1)
    assert links._scope == Scope("channel", 1, "CH-B")  # pyright: ignore[reportPrivateUsage]
    # A channel the device does not list is named by its id.
    assert links._channel_label(1, "CH-X") == "CH-X"  # pyright: ignore[reportPrivateUsage]
