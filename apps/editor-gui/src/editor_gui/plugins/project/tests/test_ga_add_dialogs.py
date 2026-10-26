"""The "Add" dialogs of the Group Addresses tree: main groups, middle groups and group addresses,
several at once, numbered as "Generate Addresses" says, one undo step per row."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import add_dialog, group_addresses
from editor_gui.plugins.project.ui.group_addresses import (
    TOOLBAR_ID,
    GroupAddressesPanel,
)
from xknxeditor.proj.core.paste import Allocation

from ._clicks import Clicks
from .test_move_to_menus import _frames

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import GroupRangeInfo


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel(project: ProjectService, notes: list[str]) -> GroupAddressesPanel:
    return GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga_in=project.create_group_address_in,
        on_remove_ga=lambda _i: None,
        on_create_range=project.create_group_range,
        on_remove_range=project.remove_group_range,
        group_style=lambda: project.group_address_style,
        notify=notes.append,
        one_undo_step=project.one_undo_step,
    )


def _main(project: ProjectService) -> dict[str, str]:
    return {node.address: node.name for node in project.get_group_range_tree()}


@pytest.mark.usefixtures("context")
def test_main_groups_middle_groups_and_addresses_are_added(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    notes: list[str] = []
    panel = _panel(project, notes)
    _frames(panel)
    before = set(_main(project))
    # Two main groups from 5 on, with the default name, in one undo step.
    clicks.context = "##ga_context"
    clicks.items.add(S.GA_ADD_MAIN_GROUPS)
    _frames(panel)
    request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
    request.rows[0].count = 2
    request.allocation = Allocation.START_WITH
    request.start_with = 5
    clicks.button = S.BTN_OK
    _frames(panel)
    added = {k: v for k, v in _main(project).items() if k not in before}
    assert added == {"5": S.GA_DEFAULT_MAIN_GROUP, "6": S.GA_DEFAULT_MAIN_GROUP}
    assert project.history()[0].display_text == (
        S.HISTORY_EV_ADDED_MAIN_GROUPS.format(count=2, target=S.PANEL_GROUP_ADDRESSES)
    )
    main = next(n for n in project.get_group_range_tree() if n.address == "5")
    # Middle groups below it: the free numbers from 0.
    clicks.context = f"##gr_ctx_{main.id}"
    clicks.items.add(S.GA_ADD_MIDDLE_GROUPS)
    _frames(panel)
    panel._adder.request.rows[0].name = "Lights"  # pyright: ignore[reportPrivateUsage]
    clicks.button = S.BTN_OK
    _frames(panel)
    main = next(n for n in project.get_group_range_tree() if n.address == "5")
    (middle,) = main.children
    assert (middle.address, middle.name) == ("5/0", "Lights")
    # Group addresses in it, from the address 10 within the group on.
    clicks.open.add(f"5  {S.GA_DEFAULT_MAIN_GROUP}##gr{main.id}")
    _frames(panel)
    clicks.context = f"##gr_ctx_{middle.id}"
    clicks.items.add(S.GA_ADD_GROUP_ADDRESSES)
    _frames(panel)
    request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
    request.rows[0].count = 3
    request.allocation = Allocation.START_WITH
    request.start_with = 10
    clicks.button = S.BTN_OK
    _frames(panel)
    main = next(n for n in project.get_group_range_tree() if n.address == "5")
    gas = main.children[0].group_addresses
    assert sorted(ga.address & 0xFF for ga in gas) == [10, 11, 12]
    assert {ga.name for ga in gas} == {S.GA_DEFAULT_GROUP_ADDRESS}
    assert project.undo()
    main = next(n for n in project.get_group_range_tree() if n.address == "5")
    assert main.children[0].group_addresses == []
    assert notes == []


@pytest.mark.usefixtures("context")
def test_free_style_ranges_and_their_addresses_are_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xknxeditor.proj.core.addressing import GroupAddressStyle

    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "free.xknx", group_address_style=GroupAddressStyle.FREE)
    try:
        clicks = Clicks(monkeypatch, group_addresses, add_dialog)
        notes: list[str] = []
        panel = GroupAddressesPanel(
            get_range_tree=project.get_group_range_tree,
            get_assignments_for_ga=project.get_assignments_for_ga,
            get_devices=lambda: [],
            on_remove_ga=lambda _i: None,
            on_create_range=project.create_group_range,
            on_remove_range=project.remove_group_range,
            group_style=lambda: project.group_address_style,
            notify=notes.append,
            one_undo_step=project.one_undo_step,
            on_create_free_range=project.create_free_group_range,
            on_create_ga_in=project.create_group_address_in,
        )
        _frames(panel)
        before = {(n.range_start, n.range_end) for n in project.get_group_range_tree()}
        # Two ranges of 100 addresses from 5000 on.
        clicks.context = "##ga_context"
        clicks.items.add(S.GA_ADD_GROUP_RANGES)
        _frames(panel)
        request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
        assert request.rows[0].capacity == 1024
        request.rows[0].count = 2
        request.rows[0].capacity = 100
        request.allocation = Allocation.START_WITH
        request.start_with = 5000
        clicks.button = S.BTN_OK
        _frames(panel)
        added = [
            n
            for n in project.get_group_range_tree()
            if (n.range_start, n.range_end) not in before
        ]
        assert [(n.range_start, n.range_end, n.name) for n in added] == [
            (5000, 5099, S.GA_DEFAULT_GROUP_RANGE),
            (5100, 5199, S.GA_DEFAULT_GROUP_RANGE),
        ]
        # Group addresses in the first one: "Start with" is the address itself.
        clicks.context = f"##gr_ctx_{added[0].id}"
        clicks.items.add(S.GA_ADD_GROUP_ADDRESSES)
        _frames(panel)
        request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
        assert request.start_with == 5000
        request.rows[0].count = 2
        request.allocation = Allocation.START_WITH
        request.start_with = 5010
        clicks.button = S.BTN_OK
        _frames(panel)
        first = next(n for n in project.get_group_range_tree() if n.id == added[0].id)
        assert sorted(ga.address for ga in first.group_addresses) == [5010, 5011]
        assert notes == []
    finally:
        project.close()


def _group(project: ProjectService, address: str) -> GroupRangeInfo:
    stack = list(project.get_group_range_tree())
    while stack:
        node = stack.pop()
        if node.address == address:
            return node
        stack.extend(node.children)
    raise AssertionError(address)


@pytest.mark.usefixtures("context")
def test_group_addresses_are_added_from_the_panel_into_the_chosen_group(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    project.create_group_address("1/2/3", "Light")
    dimmer = project.create_group_address("1/3/1", "Dimmer")
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    notes: list[str] = []
    panel = _panel(project, notes)
    panel._selected_ga_id = dimmer  # pyright: ignore[reportPrivateUsage]
    _frames(panel)
    # The toolbar button: the group of the selected group address is chosen at first.
    clicks.items.add(f"{S.GA_ADD_GROUP_ADDRESSES}{TOOLBAR_ID}")
    _frames(panel)
    request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
    assert request.rows[0].choice == str(_group(project, "1/3").id)
    request.rows[0].count = 2
    clicks.button = S.BTN_OK
    _frames(panel)
    assert len(_group(project, "1/3").group_addresses) == 3
    assert project.history()[0].display_text == (
        S.HISTORY_EV_ADDED_GROUP_ADDRESSES.format(
            count=2, target=S.PANEL_GROUP_ADDRESSES
        )
    )
    # The same from the menu of the empty space, into another group.
    clicks.context = "##ga_tree_ctx"
    clicks.items.add(S.GA_ADD_GROUP_ADDRESSES)
    _frames(panel)
    request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
    request.rows[0].choice = str(_group(project, "1/2").id)
    clicks.button = S.BTN_OK
    _frames(panel)
    names = {ga.name for ga in _group(project, "1/2").group_addresses}
    assert names == {"Light", S.GA_DEFAULT_GROUP_ADDRESS}
    assert notes == []


@pytest.mark.usefixtures("context")
def test_groups_without_room_gray_out_their_add_items(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    used = {node.range_start >> 11 for node in project.get_group_range_tree()}
    for number in range(32):
        if number not in used:
            project.create_group_range(None, f"Main {number}", number)
    full = _group(project, "1")
    taken = {(child.range_start >> 8) & 7 for child in full.children}
    for number in range(8):
        if number not in taken:
            project.create_group_range(full.id, f"Middle {number}", number)
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    enabled: dict[str, list[bool]] = {}
    clicked_item = imgui.menu_item

    def menu_item(label: str, *args: Any) -> Any:
        if len(args) > 2:
            enabled.setdefault(label, []).append(args[2])
        return clicked_item(label, *args)

    monkeypatch.setattr(imgui, "menu_item", menu_item)
    notes: list[str] = []
    panel = _panel(project, notes)
    _frames(panel)
    # All 32 main groups exist: the toolbar button and the item do nothing.
    clicks.items.add(f"{S.GA_ADD_MAIN_GROUPS}{TOOLBAR_ID}")
    _frames(panel)
    clicks.context = "##ga_tree_ctx"
    clicks.items.add(S.GA_ADD_MAIN_GROUPS)
    _frames(panel)
    assert enabled[S.GA_ADD_MAIN_GROUPS] == [False]
    assert not imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)
    # A main group with all 8 middle groups takes no more; one with room does.
    clicks.context = f"##gr_ctx_{full.id}"
    clicks.items.add(S.GA_ADD_MIDDLE_GROUPS)
    _frames(panel)
    assert enabled[S.GA_ADD_MIDDLE_GROUPS] == [False]
    assert not imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)
    clicks.context = f"##gr_ctx_{_group(project, '2').id}"
    clicks.items.add(S.GA_ADD_MIDDLE_GROUPS)
    _frames(panel)
    assert enabled[S.GA_ADD_MIDDLE_GROUPS] == [False, True]
    assert imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)
    assert notes == []
