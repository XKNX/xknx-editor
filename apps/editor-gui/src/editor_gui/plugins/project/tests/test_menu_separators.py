"""The context menus of the trees are grouped the same way: the element's actions and "Move to",
then a separator and Copy / Cut / Paste / Paste Special together, then a separator and
"Delete"."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import group_addresses, paste_special, topology
from xknxeditor.proj.core.addressing import GroupAddressStyle

from ._clicks import Clicks
from .test_tree_keys_and_links import (
    _frames,  # pyright: ignore[reportPrivateUsage]
    _free_panel,  # pyright: ignore[reportPrivateUsage]
    _paster,  # pyright: ignore[reportPrivateUsage]
    _project,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = _project(tmp_path, GroupAddressStyle.THREE_LEVEL)
    yield proj
    proj.close()


@pytest.fixture
def free(tmp_path: Path) -> Iterator[ProjectService]:
    proj = _project(tmp_path, GroupAddressStyle.FREE)
    yield proj
    proj.close()


_CLIPBOARD = {S.CONTEXT_COPY, S.CONTEXT_CUT, S.CONTEXT_PASTE, S.CONTEXT_PASTE_SPECIAL}
_SEPARATOR = "---"


class _Menu:
    """Records what the open context menu shows, in order (``---`` for a separator)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.shown: list[str] = []
        real_separator = imgui.separator
        real_menu_item = imgui.menu_item
        real_begin_menu = imgui.begin_menu

        def in_popup() -> bool:
            return bool(internal.get_current_window().flags & imgui.WindowFlags_.popup)

        def separator() -> None:
            if in_popup():
                self.shown.append(_SEPARATOR)
            real_separator()

        def menu_item(label: str, *args: Any) -> Any:
            if in_popup():
                self.shown.append(label)
            return real_menu_item(label, *args)

        def begin_menu(label: str, *args: Any) -> bool:
            if in_popup():
                self.shown.append(label)
            return real_begin_menu(label, *args)

        monkeypatch.setattr(imgui, "separator", separator)
        monkeypatch.setattr(imgui, "menu_item", menu_item)
        monkeypatch.setattr(imgui, "begin_menu", begin_menu)


def _shown(clicks: Clicks, menu: _Menu, render: Any, context_id: str) -> list[str]:
    """What the context menu ``context_id`` shows once open."""
    clicks.context = context_id
    _frames(render)
    menu.shown.clear()
    _frames(render, 1)
    return list(menu.shown)


def _assert_grouped(shown: list[str]) -> None:
    groups: list[list[str]] = [[]]
    for entry in shown:
        if entry == _SEPARATOR:
            groups.append([])
        else:
            groups[-1].append(entry)
    assert all(groups), shown  # no separator first, last or twice
    clipboard = [g for g in groups if set(g) & _CLIPBOARD]
    assert len(clipboard) == 1 and set(clipboard[0]) <= _CLIPBOARD, shown
    assert groups[-1] == [S.CONTEXT_DELETE], shown


@pytest.mark.usefixtures("context")
def test_topology_menus_are_grouped(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project.ui.topology import Area, Line, TopologyPanel

    def areas() -> list[Area]:
        return [
            Area(id=a.id, number=a.area_number, name=a.name)
            for a in project.get_areas()
        ]

    def lines_of(area_id: int) -> list[Line]:
        return [
            Line(
                id=ln.id,
                area_id=ln.area_id,
                number=ln.line_number,
                name=ln.name,
                medium_type=ln.medium_type,
                main_segment_id=ln.main_segment_id,
            )
            for ln in project.get_lines(area_id)
        ]

    dialog = paste_special.PasteSpecialDialog(
        "test",
        group_style=lambda: project.group_address_style,
        group_ranges=project.get_group_range_tree,
        names=lambda _k, ids: [str(i) for i in ids],
        on_paste=_paster(project),
        on_move=project.move_pasted,
    )
    clicks = Clicks(monkeypatch, topology)
    menu = _Menu(monkeypatch)
    panel = TopologyPanel(
        get_devices=lambda: [],
        get_areas=areas,
        get_lines=lines_of,
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _t, _m: None,
        on_remove_area=lambda _a: None,
        on_create_line=lambda _a, _n, _t, _m: None,
        on_remove_line=lambda _l: None,
        paste_dialog=dialog,
    )
    area = next(a for a in project.get_areas() if a.area_number == 1)
    line = next(ln for ln in project.get_lines(area.id) if ln.line_number == 1)
    _frames(panel.render)
    shown = _shown(clicks, menu, panel.render, f"##line_ctx_{line.id}")
    # The separator above "Copy", not between "Cut" and "Paste".
    assert shown[shown.index(S.CONTEXT_COPY) - 1] == _SEPARATOR
    assert shown[shown.index(S.CONTEXT_PASTE) - 1] == S.CONTEXT_CUT
    _assert_grouped(shown)
    _assert_grouped(_shown(clicks, menu, panel.render, f"##area_ctx_{area.id}"))
    # The empty space below the elements only adds areas.
    assert _shown(clicks, menu, panel.render, "##topology_context") == [
        S.TOPOLOGY_ADD_AREAS_ITEM
    ]


@pytest.mark.usefixtures("context")
def test_a_device_menu_is_grouped(monkeypatch: pytest.MonkeyPatch) -> None:
    from editor_gui.device import Device
    from editor_gui.plugins.project.ui.topology import Area, Line, TopologyPanel

    dialog = paste_special.PasteSpecialDialog(
        "test",
        group_style=lambda: GroupAddressStyle.THREE_LEVEL,
        group_ranges=list,
        names=lambda _k, ids: [str(i) for i in ids],
        on_paste=lambda *_a: None,
        on_move=lambda *_a: None,
    )
    clicks = Clicks(monkeypatch, topology)
    menu = _Menu(monkeypatch)
    lines = {
        0: [Line(2, 0, 0, "", medium_type="MT-5", main_segment_id=20)],
        1: [
            Line(3, 1, 0, "", main_segment_id=30),
            Line(1, 1, 1, "L", main_segment_id=10),
        ],
    }
    panel = TopologyPanel(
        get_devices=lambda: [Device(7, "Dimmer", None, "1.1.5")],
        get_areas=lambda: [Area(0, 0, ""), Area(1, 1, "Main")],
        get_lines=lambda area_id: lines[area_id],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _t, _m: None,
        on_remove_area=lambda _a: None,
        on_create_line=lambda _a, _n, _t, _m: None,
        on_remove_line=lambda _l: None,
        on_device_info=lambda _n: None,
        on_compare=lambda _n: None,
        on_park_device=lambda _n: None,
        on_unlink_device=lambda _n: None,
        get_device_segments=lambda: {7: 10},
        paste_dialog=dialog,
    )
    _frames(panel.render)
    shown = _shown(clicks, menu, panel.render, "##dev_ctx_7")
    # Its configuration; "Move to" with its address; reading, comparing, unlinking; then
    # Copy / Cut / Paste and "Delete" ("Read from Device" brings no separator of its own).
    assert shown == [
        S.CONTEXT_DUPLICATE,
        S.CONTEXT_COPY_CONFIG,
        S.CONTEXT_PASTE_CONFIG,
        _SEPARATOR,
        S.SPACES_MOVE_TO,
        S.CONTEXT_COPY_DEVICE_ADDRESS,
        _SEPARATOR,
        S.BTN_READ_DEVICE_INFO,
        S.CONTEXT_COMPARE_DEVICE,
        S.CONTEXT_PARK,
        S.CONTEXT_UNLINK_LINE,
        _SEPARATOR,
        S.CONTEXT_COPY,
        S.CONTEXT_CUT,
        S.CONTEXT_PASTE,
        S.CONTEXT_PASTE_SPECIAL,
        _SEPARATOR,
        S.CONTEXT_DELETE,
    ]


@pytest.mark.usefixtures("context")
def test_group_address_menus_are_grouped(
    free: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = free.create_free_group_range(None, "Lights", 1, 1024)
    assert top is not None
    free.create_free_group_range(None, "Blinds", 2001, 3000)
    ga = free.create_group_address_in(top, 5, "Kitchen")
    clicks = Clicks(monkeypatch, group_addresses)
    menu = _Menu(monkeypatch)
    panel = _free_panel(free, [])
    _frames(panel.render)
    _assert_grouped(_shown(clicks, menu, panel.render, f"##gr_ctx_{top}"))
    clicks.open.add(f"Lights##gr{top}")
    shown = _shown(clicks, menu, panel.render, f"##ga_ctx_{ga}")
    _assert_grouped(shown)
    # "Copy Group Address" right below "Move to".
    assert shown[:2] == [S.SPACES_MOVE_TO, S.CONTEXT_COPY_GROUP_ADDRESS]


def test_menu_groups_separate_only_groups_that_show(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.project.ui.paste_special import MenuGroups

    drawn: list[str] = []
    monkeypatch.setattr(imgui, "separator", lambda: drawn.append(_SEPARATOR))
    groups = MenuGroups()
    groups.next()  # nothing shown yet: no separator at the top
    groups.show()
    drawn.append("Duplicate")
    groups.next()
    groups.next()  # an empty group: one separator only
    groups.show()
    drawn.append("Move to")
    groups.show()
    drawn.append("Copy Individual Address")
    groups.next()  # the last group shows nothing: no separator at the end
    assert drawn == ["Duplicate", _SEPARATOR, "Move to", "Copy Individual Address"]
