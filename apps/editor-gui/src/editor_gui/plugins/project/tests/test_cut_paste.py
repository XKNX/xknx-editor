""" "Cut" and the keyboard in the trees: what was cut moves where it is pasted (once), Paste
Special is for copies only, Ctrl+C/X/V act on the selected element, and the moves themselves,
one undo step for all."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.paste import PasteKind, PasteTarget
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import paste_special
from editor_gui.plugins.project.ui.paste_special import (
    PasteSpecialDialog,
    ShortcutRow,
    copied,
    is_cut,
)
from editor_gui.plugins.project.ui.tree_drag import Dragged
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import PasteOptions

from ._clicks import Clicks
from .test_buildings_actions import _device


@pytest.fixture(autouse=True)
def _empty_clipboard() -> Iterator[None]:
    paste_special._Clipboard.copied = None  # pyright: ignore[reportPrivateUsage]
    yield
    paste_special._Clipboard.copied = None  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Calls:
    def __init__(self) -> None:
        self.pasted: list[tuple[PasteKind, list[int], PasteTarget]] = []
        self.moved: list[tuple[PasteKind, list[int], PasteTarget]] = []

    def paste(
        self, kind: PasteKind, ids: list[int], target: PasteTarget, _o: PasteOptions
    ) -> str | None:
        self.pasted.append((kind, ids, target))
        return None

    def move(self, kind: PasteKind, ids: list[int], target: PasteTarget) -> str | None:
        self.moved.append((kind, ids, target))
        return None


def _dialog(calls: _Calls) -> PasteSpecialDialog:
    return PasteSpecialDialog(
        "test",
        group_style=lambda: GroupAddressStyle.THREE_LEVEL,
        group_ranges=list,
        names=lambda _k, ids: [str(i) for i in ids],
        on_paste=calls.paste,
        on_move=calls.move,
    )


_TARGET = PasteTarget("group_range", 9)
_ROW = ShortcutRow(Dragged("group_address", (4,)), lambda _c: _TARGET)


def _frame(render: object, keys: tuple[imgui.Key, ...] = ()) -> None:
    """A frame of a focused window rendering ``render``, then one with ``keys`` (and Ctrl)
    down: a shortcut takes its keys from the frame after the one it was first asked in."""
    io = imgui.get_io()
    for down in (False, True) if keys else (False,):
        for key in keys if down else ():
            io.add_key_event(imgui.Key.reserved_for_mod_ctrl, True)
            io.add_key_event(key, True)
        imgui.new_frame()
        imgui.set_next_window_focus()
        imgui.begin("Tree")
        render()  # type: ignore[operator]
        imgui.end()
        imgui.end_frame()
    for key in keys:
        io.add_key_event(key, False)
        io.add_key_event(imgui.Key.reserved_for_mod_ctrl, False)
    imgui.new_frame()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_cut_moves_once_and_copy_copies(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _Calls()
    dialog = _dialog(calls)
    clicks = Clicks(monkeypatch, paste_special)
    shown: list[tuple[str, bool]] = []
    real_item = imgui.menu_item

    def menu_item(label: str, *args: object) -> object:
        shown.append((label, bool(args[2]) if len(args) > 2 else True))
        return real_item(label, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(imgui, "menu_item", menu_item)

    def menu() -> None:
        if imgui.begin_popup_context_window("##menu"):
            paste_special.render_copy_item(Dragged("group_address", (4,)))
            dialog.render_paste_items(lambda _c: _TARGET)
            imgui.end_popup()

    clicks.items.add(S.CONTEXT_CUT)
    _frame(lambda: (imgui.open_popup("##menu"), menu()))
    assert is_cut()
    # Paste Special is for copies: disabled while something is cut.
    shown.clear()
    _frame(lambda: (imgui.open_popup("##menu"), menu()))
    assert (S.CONTEXT_PASTE_SPECIAL, False) in shown
    clicks.items.add(S.CONTEXT_PASTE)
    _frame(lambda: (imgui.open_popup("##menu"), menu()))
    assert calls.moved == [("group_address", [4], _TARGET)] and calls.pasted == []
    # A cut element is pasted once.
    assert copied() is None
    clicks.items.add(S.CONTEXT_COPY)
    _frame(lambda: (imgui.open_popup("##menu"), menu()))
    clicks.items.add(S.CONTEXT_PASTE)
    _frame(lambda: (imgui.open_popup("##menu"), menu()))
    clicks.items.add(S.CONTEXT_PASTE)
    _frame(lambda: (imgui.open_popup("##menu"), menu()))
    assert len(calls.pasted) == 2 and len(calls.moved) == 1


@pytest.mark.usefixtures("context")
def test_the_keyboard_copies_cuts_and_pastes_the_selection() -> None:
    calls = _Calls()
    dialog = _dialog(calls)
    _frame(lambda: dialog.handle_shortcuts(_ROW), (imgui.Key.c,))
    assert copied() == Dragged("group_address", (4,)) and not is_cut()
    _frame(lambda: dialog.handle_shortcuts(_ROW), (imgui.Key.v,))
    assert calls.pasted == [("group_address", [4], _TARGET)]
    _frame(lambda: dialog.handle_shortcuts(_ROW), (imgui.Key.x,))
    assert is_cut()
    _frame(lambda: dialog.handle_shortcuts(_ROW), (imgui.Key.v,))
    assert calls.moved == [("group_address", [4], _TARGET)] and copied() is None
    # A main group is not cut; nothing selected takes nothing.
    fixed = ShortcutRow(Dragged("group_range", (1,)), lambda _c: None, movable=False)
    _frame(lambda: dialog.handle_shortcuts(fixed), (imgui.Key.x,))
    assert copied() is None
    _frame(lambda: dialog.handle_shortcuts(None), (imgui.Key.c,))
    assert copied() is None


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


def test_what_was_cut_moves_where_it_is_pasted(project: ProjectService) -> None:
    first = project.create_group_address("1/1/1", "A")
    second = project.create_group_address("1/1/2", "B")
    project.create_group_address("1/2/1", "C")
    assert first is not None and second is not None
    target = next(
        child.id
        for main in project.get_group_range_tree()
        for child in main.children
        if child.address == "1/2"
    )
    entries = len(project.history())
    assert (
        project.move_pasted(
            "group_address", [first, second], PasteTarget("group_range", target)
        )
        is None
    )
    moved = project.get_group_address(first)
    assert moved is not None and moved.address.startswith("1/2/")
    # Both in one undo step.
    assert len(project.history()) == entries + 1
    # A building part into another one, a device onto a line's segment.
    building = project.get_space_tree()[0].id
    floor = project.create_space(building, "Floor", "EG")
    room = project.create_space(building, "Room", "Kitchen")
    assert floor is not None and room is not None
    assert project.move_pasted("space", [room], PasteTarget("space", floor)) is None
    assert [c.name for c in project.get_space_tree()[0].children[0].children] == [
        "Kitchen"
    ]
    device = _device(project, 7)
    project.unlink_device(device)
    line = next(
        ln
        for area in project.get_areas()
        if area.area_number == 1
        for ln in project.get_lines(area.id)
        if ln.line_number == 1
    )
    assert line.main_segment_id is not None
    assert (
        project.move_pasted(
            "device", [device], PasteTarget("segment", line.main_segment_id)
        )
        is None
    )
    assert project.device_segments()[device] == line.main_segment_id
    # A device from one trade into another.
    lights = project.create_trade(None, "Lights")
    blinds = project.create_trade(None, "Blinds")
    assert lights is not None and blinds is not None
    project.add_trade_device(lights, device)
    assert project.move_pasted("device", [device], PasteTarget("trade", blinds)) is None
    devices = {
        t.name: [d.device.id for d in t.devices] for t in project.get_trade_tree()
    }
    assert devices == {"Lights": [], "Blinds": [device]}
    # What cannot go there is told.
    assert (
        project.move_pasted("space", [floor], PasteTarget("trade", lights))
        == S.PASTE_CANNOT_MOVE
    )
