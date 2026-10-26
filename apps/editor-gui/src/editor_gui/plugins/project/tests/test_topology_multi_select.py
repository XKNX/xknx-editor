"""Several devices selected in the Topology tree: a press on one of them keeps them all for a
drag, their menu offers only what acts on all of them, and "Move to", "Delete" and the Delete
key take all of them, also from different lines."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import topology
from editor_gui.plugins.project.ui.topology import Area, Line, TopologyPanel

from ._clicks import Clicks
from .test_menu_separators import _SEPARATOR, _Menu


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Tree:
    """A Topology panel with two lines (1.1 and 1.2) and three devices, the first on 1.1 and
    the others on 1.2, recording what the panel asks for."""

    def __init__(self) -> None:
        self.devices = [
            Device(7, "Dimmer", None, "1.1.5"),
            Device(8, "Switch", None, "1.2.1"),
            Device(9, "Blinds", None, "1.2.2"),
        ]
        self.segment_of = {7: 10, 8: 20, 9: 20}
        self.selected: set[int] = set()
        self.moved: list[tuple[int, int]] = []
        self.deleted: list[int] = []
        self.undo_steps: list[str | None] = []
        lines = {
            0: [Line(2, 0, 0, "", medium_type="MT-5", main_segment_id=5)],
            1: [
                Line(3, 1, 0, "", main_segment_id=30),
                Line(1, 1, 1, "Office", main_segment_id=10),
                Line(4, 1, 2, "Garage", main_segment_id=20),
            ],
        }
        self.panel = TopologyPanel(
            get_devices=lambda: self.devices,
            get_areas=lambda: [Area(0, 0, ""), Area(1, 1, "Main")],
            get_lines=lambda area_id: lines[area_id],
            on_select_device=lambda d: self._select([d.node_id]),
            on_move_device=lambda _d, _a, _l: None,
            on_create_area=lambda _n, _t, _m: None,
            on_remove_area=lambda _a: None,
            on_create_line=lambda _a, _n, _t, _m: None,
            on_remove_line=lambda _l: None,
            on_select_devices=lambda _p, ids: self._select(ids),
            get_selected_node_ids=lambda: set(self.selected),
            get_device_segments=lambda: dict(self.segment_of),
            on_move_device_to_segment=lambda d, s: self.moved.append((d.node_id, s.id)),
            on_delete_device=lambda d: self.deleted.append(d.node_id),
            on_device_info=lambda _n: None,
            on_compare=lambda _n: None,
            on_park_device=lambda _n: None,
            on_unlink_device=lambda _n: None,
            one_undo_step=self._undo_step,
        )

    def _select(self, ids: list[int]) -> None:
        self.selected = set(ids)

    @contextmanager
    def _undo_step(self, label: str | None = None) -> Iterator[None]:
        self.undo_steps.append(label)
        yield

    def frames(self, count: int = 3) -> None:
        for _ in range(count):
            imgui.new_frame()
            imgui.set_next_window_pos(imgui.ImVec2(0, 0))
            imgui.set_next_window_size(imgui.ImVec2(900, 700))
            imgui.begin("Topology")
            self.panel.render()
            imgui.end()
            imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_a_press_on_one_of_several_selected_keeps_them_for_a_drag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _Tree()
    tree.selected = {7, 8}
    order = [7, 8, 9]
    tree.frames(1)
    # Pressed and dragged: all stay selected, and the drag takes them all.
    tree.panel._handle_device_click(tree.devices[1], order)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(imgui, "is_mouse_dragging", lambda *_a: True)
    tree.frames(1)
    assert tree.selected == {7, 8}
    dragged = tree.panel._dragged_devices(tree.devices[1])  # pyright: ignore[reportPrivateUsage]
    assert dragged.ids == (7, 8)
    # Pressed and released without a drag: only that one is selected then.
    monkeypatch.setattr(imgui, "is_mouse_dragging", lambda *_a: False)
    tree.panel._handle_device_click(tree.devices[1], order)  # pyright: ignore[reportPrivateUsage]
    tree.frames(1)
    assert tree.selected == {8}


@pytest.mark.usefixtures("context")
def test_the_menu_of_several_devices_acts_on_all_of_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _Tree()
    clicks = Clicks(monkeypatch, topology)
    menu = _Menu(monkeypatch)
    tree.selected = {7, 8}
    tree.frames()
    clicks.context = "##dev_ctx_7"
    tree.frames()
    menu.shown.clear()
    tree.frames(1)
    # Only what acts on all of them (no Duplicate, Copy Configuration, Copy Individual
    # Address, Read from Device or Compare with…).
    assert menu.shown == [
        S.CONTEXT_PASTE_CONFIG,
        _SEPARATOR,
        S.SPACES_MOVE_TO,
        _SEPARATOR,
        S.CONTEXT_PARK,
        S.CONTEXT_UNLINK_LINE,
        _SEPARATOR,
        S.CONTEXT_DELETE,
    ]
    # "Move to" takes both, from their different lines, in one undo step (the one already
    # on that line stays).
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add("1.1 Office##mvdev10")
    tree.frames(1)
    assert tree.moved == [(8, 10)] and tree.undo_steps == [None]


@pytest.mark.usefixtures("context")
def test_several_selected_devices_are_deleted_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.project.ui import paste_special

    tree = _Tree()
    clicks = Clicks(monkeypatch, topology, paste_special)
    tree.selected = {8, 9}
    tree.frames()
    # Their menu's "Delete" asks about all of them, by their number.
    clicks.context = "##dev_ctx_8"
    clicks.items.add(S.CONTEXT_DELETE)
    texts: list[str] = []
    real_text_wrapped = imgui.text_wrapped
    monkeypatch.setattr(
        imgui,
        "text_wrapped",
        lambda text, *a: (texts.append(text), real_text_wrapped(text, *a)),
    )
    tree.frames()
    assert S.DEVICE_DELETE_CONFIRM_SEVERAL.format(count=2) in texts
    clicks.button = S.CONTEXT_DELETE
    tree.frames()
    assert tree.deleted == [8, 9]
    assert tree.undo_steps[-1] == S.HISTORY_EV_REMOVE_DEVICES.format(count=2)
    # The Delete key does the same with the selected devices.
    tree.deleted.clear()
    asked: list[list[int]] = []
    monkeypatch.setattr(
        tree.panel,
        "_ask_delete_devices",
        lambda devices: asked.append([d.node_id for d in devices]),
    )
    tree.frames(1)
    row: Any = tree.panel._shortcut_row  # pyright: ignore[reportPrivateUsage]
    assert row is not None and row.on_delete is not None
    row.on_delete()
    assert asked == [[8, 9]]
