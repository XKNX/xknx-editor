"""The Delete key and F2 on the element selected in a tree, the group addresses of a building
function (selectable rows with a menu, "Link with…", "Add Group Addresses…"), "Add → Devices…"
in the Topology tree, and Free style group ranges moved and copied like the other groups."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.paste import PasteError, PasteTarget, paste_error_text
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import (
    add_dialog,
    group_addresses,
    name_focus,
    paste_special,
    spaces,
)
from editor_gui.plugins.project.ui.group_addresses import GroupAddressesPanel
from editor_gui.plugins.project.ui.paste_special import (
    ShortcutRow,
    handle_row_keys,
    no_paste,
)
from editor_gui.plugins.project.ui.spaces import SpacesPanel
from editor_gui.plugins.project.ui.tree_drag import Dragged
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import PasteOptions

from ._clicks import Clicks


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _project(tmp_path: Path, style: GroupAddressStyle) -> ProjectService:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / f"{style.value}.xknx", group_address_style=style)
    return project


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


def _frames(render: Any, count: int = 3, keys: tuple[imgui.Key, ...] = ()) -> None:
    """Frames of a window rendering ``render``; with ``keys`` it has the keyboard and the last
    frame has them down (a shortcut takes its keys from the frame after the one it was first
    asked in). Without, nothing takes the focus (that would close an open dialog)."""
    io = imgui.get_io()
    for index in range(count):
        if keys and index == count - 1:
            for key in keys:
                io.add_key_event(key, True)
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        if keys:
            imgui.set_next_window_focus()
        imgui.begin("Tree")
        render()
        imgui.end()
        imgui.end_frame()
    if keys:
        for key in keys:
            io.add_key_event(key, False)
        imgui.new_frame()
        imgui.end_frame()


def _focus_tree(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    """The tree area of ``module``'s panel takes the keyboard, as after a click into it."""
    real = module.begin_tree_area

    def begin_tree_area(*args: Any) -> bool:
        imgui.set_next_window_focus()
        return real(*args)

    monkeypatch.setattr(module, "begin_tree_area", begin_tree_area)


@pytest.fixture(autouse=True)
def _no_f2() -> None:
    name_focus._Pending.frame = None  # pyright: ignore[reportPrivateUsage]
    name_focus._Pending.focused = None  # pyright: ignore[reportPrivateUsage]
    name_focus._Pending.tab = False  # pyright: ignore[reportPrivateUsage]


# -- the Delete key and F2 ------------------------------------------------------------------


@pytest.mark.usefixtures("context")
def test_the_delete_key_and_f2_act_on_the_selected_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[int] = []
    row = ShortcutRow(None, no_paste, on_delete=lambda: deleted.append(1))
    _frames(lambda: handle_row_keys(row), keys=(imgui.Key.delete,))
    assert deleted == [1]
    # Backspace deletes only on a Mac, whose Delete key sends it.
    _frames(lambda: handle_row_keys(row), keys=(imgui.Key.backspace,))
    assert deleted == [1]
    monkeypatch.setattr(paste_special, "mac_keys", lambda: True)
    _frames(lambda: handle_row_keys(row), keys=(imgui.Key.backspace,))
    assert deleted == [1, 1]
    # A row without "Delete" deletes nothing; nothing without a row.
    _frames(
        lambda: handle_row_keys(ShortcutRow(None, no_paste)), keys=(imgui.Key.delete,)
    )
    _frames(lambda: handle_row_keys(None), keys=(imgui.Key.delete,))
    assert deleted == [1, 1]
    # F2: the Device tab is asked for, once.
    name_focus.take_tab_request()
    _frames(lambda: handle_row_keys(row), keys=(imgui.Key.f2,))
    assert name_focus.take_tab_request()
    assert not name_focus.take_tab_request()


@pytest.mark.usefixtures("context")
def test_f2_gives_the_name_field_the_keyboard_with_its_text_selected() -> None:
    text = ["Kitchen"]
    states: list[tuple[bool, int, int]] = []

    def field() -> None:
        name_focus.focus_name_field()
        _, text[0] = imgui.input_text("##name", text[0])
        name_focus.select_name()
        state = imgui.internal.get_input_text_state(imgui.get_item_id())
        active = imgui.is_item_active()
        if active and state is not None:  # pyright: ignore[reportUnnecessaryComparison]
            states.append(
                (active, state.get_selection_start(), state.get_selection_end())
            )

    _frames(field, 2)
    assert states == []
    name_focus.request()
    _frames(field, 4)
    # Active, with all of "Kitchen" selected (from either end).
    assert states and states[-1][0]
    assert sorted(states[-1][1:]) == [0, len("Kitchen")]
    # A late request (the field did not show up in time) does nothing.
    states.clear()
    name_focus.request()
    for _ in range(8):
        imgui.new_frame()
        imgui.end_frame()
    _frames(field, 2)
    assert states == [] or states[-1][1] == states[-1][2]


# -- a building function's group addresses --------------------------------------------------


def _spaces_panel(
    project: ProjectService, notes: list[str], selected: list[tuple[str, int]]
) -> SpacesPanel:
    def select(kind: str, ident: int) -> None:
        selected.append((kind, ident))
        project.select_element(kind, ident)

    return SpacesPanel(
        get_space_tree=project.get_space_tree,
        on_select_device_id=lambda _n: None,
        on_create_function=project.create_function,
        on_remove_function=project.remove_function,
        on_move_function=project.move_function,
        on_add_function_ga=project.add_function_group_address,
        on_remove_function_ga=project.remove_function_group_address,
        get_ga_range_tree=project.get_group_range_tree,
        on_create_space=project.create_space,
        get_selected_space_id=lambda: project.selected_space_id,
        on_select_element=select,
        get_trade_tree=project.get_trade_tree,
        on_create_trade=project.create_trade,
        on_remove_trade=project.remove_trade,
        notify=notes.append,
        one_undo_step=project.one_undo_step,
        group_style=lambda: project.group_address_style,
        on_create_ga_in=project.create_group_address_in,
        get_selected_element=lambda: project.selected_element,
    )


def _function(project: ProjectService, function_id: int) -> Any:
    stack = list(project.get_space_tree())
    while stack:
        space = stack.pop()
        for function in space.functions:
            if function.id == function_id:
                return function
        stack.extend(space.children)
    return None


@pytest.mark.usefixtures("context")
def test_a_functions_group_addresses_are_rows_with_a_menu(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, spaces, add_dialog)
    notes: list[str] = []
    selected: list[tuple[str, int]] = []
    building = project.get_space_tree()[0].id
    function_id = project.create_function(building, "Custom", "Light")
    first = project.create_group_address("1/1/5", "Ceiling")
    second = project.create_group_address("1/1/6", "Wall")
    assert function_id is not None and first is not None and second is not None
    panel = _spaces_panel(project, notes, selected)
    clicks.open.add(f"ƒ Light  [{S.SPACES_FN_TYPE_CUSTOM}]##fn{function_id}")
    _frames(panel.render)
    # "Link with…": a group address picked from the groups, without a role.
    clicks.context = f"##fnctx{function_id}"
    clicks.items.add(S.SPACES_FN_LINK)
    _frames(panel.render)
    panel._link_filter = "ceiling"  # pyright: ignore[reportPrivateUsage]
    clicks.items.add(f"1/1/5  Ceiling##fngapick{function_id}_{first}")
    _frames(panel.render)
    (link,) = _function(project, function_id).group_addresses
    assert (link.group_address_id, link.name, link.role) == (first, "Ceiling", "")
    # A role from an imported project shows after the name.
    project.remove_function_group_address(link.id)
    project.add_function_group_address(function_id, first, "Switch")
    (link,) = _function(project, function_id).group_addresses
    # A row: a click selects the group address, Delete in its menu unlinks it.
    clicks.items.add(f"1/1/5  Ceiling  (Switch)##fnga{link.id}")
    _frames(panel.render)
    assert selected[-1] == ("group_address", first)
    clicks.context = f"##fnga_ctx{link.id}"
    clicks.items.add(S.CONTEXT_DELETE)
    _frames(panel.render)
    assert _function(project, function_id).group_addresses == []
    # The Delete key on the selected row unlinks too.
    project.add_function_group_address(function_id, second, "")
    (link,) = _function(project, function_id).group_addresses
    clicks.items.add(f"1/1/6  Wall##fnga{link.id}")
    _frames(panel.render)
    _focus_tree(monkeypatch, spaces)
    _frames(panel.render, keys=(imgui.Key.delete,))
    assert _function(project, function_id).group_addresses == []
    # No small buttons are left in the rows.
    assert notes == []


@pytest.mark.usefixtures("context")
def test_a_function_adds_group_addresses_it_is_linked_with(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, spaces, add_dialog)
    notes: list[str] = []
    building = project.get_space_tree()[0].id
    function_id = project.create_function(building, "Custom", "Light")
    first = project.create_group_address("1/2/5", "Ceiling")
    project.create_group_address("3/0/1", "Other")
    assert function_id is not None and first is not None
    project.add_function_group_address(function_id, first, "")
    panel = _spaces_panel(project, notes, [])
    _frames(panel.render)
    clicks.context = f"##fnctx{function_id}"
    clicks.items.add(S.GA_ADD_GROUP_ADDRESSES)
    _frames(panel.render)
    request = panel._adder.request  # pyright: ignore[reportPrivateUsage]
    # At first into the group of the function's first group address.
    middle = next(
        m.id
        for main in project.get_group_range_tree()
        for m in main.children
        if m.address == "1/2"
    )
    assert request.rows[0].choice == str(middle)
    request.rows[0].count = 2
    clicks.button = S.BTN_OK
    _frames(panel.render)
    texts = sorted(ref.text for ref in _function(project, function_id).group_addresses)
    assert texts == ["1/2/0", "1/2/1", "1/2/5"]
    # One undo step for the row: the addresses and their links.
    assert project.history()[
        0
    ].display_text == S.HISTORY_EV_ADDED_GROUP_ADDRESSES.format(count=2, target="Light")
    assert project.undo()
    assert [r.text for r in _function(project, function_id).group_addresses] == [
        "1/2/5"
    ]
    assert notes == []


@pytest.mark.usefixtures("context")
def test_a_function_without_a_group_tells_so_and_subtrades_have_their_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, GroupAddressStyle.TWO_LEVEL)
    try:
        for main in project.get_group_range_tree():
            project.remove_group_range(main.id)
        clicks = Clicks(monkeypatch, spaces, add_dialog)
        notes: list[str] = []
        building = project.get_space_tree()[0].id
        function_id = project.create_function(building, "Custom", "Light")
        trade = project.create_trade(None, "Lights")
        panel = _spaces_panel(project, notes, [])
        _frames(panel.render)
        clicks.context = f"##fnctx{function_id}"
        clicks.items.add(S.GA_ADD_GROUP_ADDRESSES)
        _frames(panel.render)
        assert notes == [S.SPACES_FN_NO_GROUP]
        # The "Add" dialog below a trade adds subtrades.
        clicks.context = f"##tradectx{trade}"
        clicks.menus.add(S.SPACES_ADD_MENU)
        clicks.items.add(S.TRADES_ADD_TRADES)
        _frames(panel.render)
        assert panel._adder._heading == S.TRADES_TITLE_SUBTRADES  # pyright: ignore[reportPrivateUsage]
        # The Delete key deletes the selected trade, as its menu's "Delete" does.
        clicks.button = S.BTN_CANCEL
        _frames(panel.render)
        panel._selected_trade = trade  # pyright: ignore[reportPrivateUsage]
        _focus_tree(monkeypatch, spaces)
        _frames(panel.render, keys=(imgui.Key.delete,))
        assert project.get_trade_tree() == []
    finally:
        project.close()


# -- "Add → Devices…" in the Topology tree, and where the catalog adds devices ---------------


def test_the_catalog_adds_devices_where_the_topology_asked(
    project: ProjectService,
) -> None:
    choices = project.segment_choices()
    lines = [text for _s, text in choices]
    assert lines[0].startswith("0.0") and any(t.startswith("1.1") for t in lines)
    segment = next(s for s, text in choices if text.startswith("1.1"))
    assert project.device_target_segment is None
    project.add_devices_to(segment)
    assert project.take_focus_catalog() and not project.take_focus_catalog()
    assert project.device_target_segment == segment
    # A segment that is gone is no target any more.
    project.device_target_segment = 99999
    assert project.device_target_segment is None


@pytest.mark.usefixtures("context")
def test_the_topology_add_menus_open_the_catalog(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project.ui import topology
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

    clicks = Clicks(monkeypatch, topology, add_dialog)
    asked: list[int | None] = []
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
        on_add_devices=asked.append,
    )
    area = next(a for a in project.get_areas() if a.area_number == 1)
    lines = {ln.line_number: ln for ln in project.get_lines(area.id)}
    _frames(panel.render)
    for context, expected in (
        (f"##area_ctx_{area.id}", lines[0].main_segment_id),
        (f"##line_ctx_{lines[1].id}", lines[1].main_segment_id),
    ):
        clicks.context = context
        clicks.menus.add(S.SPACES_ADD_MENU)
        clicks.items.add(S.SPACES_ADD_DEVICES)
        _frames(panel.render)
        assert asked[-1] == expected
    assert len(asked) == 2


# -- Free style group ranges ----------------------------------------------------------------


def _paster(project: ProjectService) -> Any:
    """Paste as the plugin does: the reason it cannot, or None."""

    def paste(
        kind: Any, ids: list[int], target: PasteTarget, options: PasteOptions
    ) -> str | None:
        try:
            project.paste(kind, ids, target, options)
        except PasteError as error:
            return paste_error_text(error)
        return None

    return paste


def _free_panel(project: ProjectService, notes: list[str]) -> GroupAddressesPanel:
    dialog = paste_special.PasteSpecialDialog(
        "test",
        group_style=lambda: project.group_address_style,
        group_ranges=project.get_group_range_tree,
        names=lambda _k, ids: [str(i) for i in ids],
        on_paste=_paster(project),
        on_move=project.move_pasted,
        notify=notes.append,
    )
    return GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga=lambda a, n: project.create_group_address(a, n),
        on_remove_ga=project.remove_group_address,
        on_create_range=project.create_group_range,
        on_remove_range=project.remove_group_range,
        group_style=lambda: project.group_address_style,
        notify=notes.append,
        one_undo_step=project.one_undo_step,
        on_create_free_range=project.create_free_group_range,
        on_create_ga_in=project.create_group_address_in,
        on_move_ga=project.move_group_address,
        on_move_range=project.move_group_range,
        paste_dialog=dialog,
    )


def _range(project: ProjectService, range_id: int) -> Any:
    stack = list(project.get_group_range_tree())
    while stack:
        node = stack.pop()
        if node.id == range_id:
            return node
        stack.extend(node.children)
    return None


@pytest.mark.usefixtures("context")
def test_free_style_ranges_move_by_their_menu(
    free: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog, paste_special)
    notes: list[str] = []
    lights = free.create_free_group_range(None, "Lights", 1, 1024)
    hall = free.create_free_group_range(lights, "Hall", 1, 256)
    other = free.create_free_group_range(None, "Other", 2000, 3000)
    assert lights is not None and hall is not None and other is not None
    ga = free.create_group_address_in(hall, 10, "Hall light")
    assert ga is not None
    panel = _free_panel(free, notes)
    _frames(panel.render)
    # "Move to" of a range: another range, or the top.
    clicks.context = f"##gr_ctx_{hall}"
    clicks.open.add(f"Lights##gr{lights}")
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(f"2000-3000  Other##mvgr{other}")
    _frames(panel.render)
    assert any(c.id == hall for c in _range(free, other).children)
    assert _range(free, hall).range_start == 2000
    clicks.context = f"##gr_ctx_{hall}"
    clicks.open.add(f"Other##gr{other}")
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(S.SPACES_MOVE_ROOT)
    _frames(panel.render)
    assert hall in [n.id for n in free.get_group_range_tree()]
    # "Move to" of a group address: any range, where it keeps its address when it can.
    clicks.context = f"##ga_ctx_{ga}"
    clicks.open.add(f"Hall##gr{hall}")
    clicks.menus.add(S.SPACES_MOVE_TO)
    clicks.items.add(f"2000-3000  Other##mvgr{other}")
    _frames(panel.render)
    moved = free.get_group_address(ga)
    assert moved is not None and moved.address == "2000"
    assert notes == []
    # Copies go into a range, or to the top from the tree's own menu.
    assert panel._range_copy_target(  # pyright: ignore[reportPrivateUsage]
        _range(free, other), True, Dragged("group_range", (hall,))
    ) == PasteTarget("group_range", other)
    assert (
        panel._range_copy_target(  # pyright: ignore[reportPrivateUsage]
            _range(free, hall), True, Dragged("group_range", (hall,))
        )
        is None
    )
    assert panel._top_copy_target(  # pyright: ignore[reportPrivateUsage]
        Dragged("group_range", (hall,))
    ) == PasteTarget("group_range", None)
    assert panel._range_copy_target(  # pyright: ignore[reportPrivateUsage]
        _range(free, hall), True, Dragged("group_address", (ga,))
    ) == PasteTarget("group_range", hall)


def test_free_style_ranges_are_copied_and_cut(free: ProjectService) -> None:
    lights = free.create_free_group_range(None, "Lights", 1, 100)
    hall = free.create_free_group_range(lights, "Hall", 1, 50)
    assert lights is not None and hall is not None
    free.create_group_address_in(hall, 10, "Hall light")
    free.create_group_address_in(lights, 60, "Light")
    entries = len(free.history())
    paste = _paster(free)
    # A copy at the top, beside the range: as large, with its range and group addresses at the
    # same place in it.
    assert (
        paste("group_range", [lights], PasteTarget("group_range", None), PasteOptions())
        is None
    )
    assert len(free.history()) == entries + 1
    copy = next(n for n in free.get_group_range_tree() if n.id != lights)
    assert (copy.range_start, copy.range_end) == (101, 200)
    assert [ga.address for ga in copy.group_addresses] == [160]
    (child,) = copy.children
    assert (child.range_start, child.range_end, child.name) == (101, 150, "Hall")
    assert [ga.address for ga in child.group_addresses] == [110]
    assert free.history()[0].display_text == S.HISTORY_EV_COPIED.format(
        name="Lights", target=S.PANEL_GROUP_ADDRESSES
    )
    # Not into itself or a range in it.
    assert (
        paste("group_range", [lights], PasteTarget("group_range", hall), PasteOptions())
        == S.PASTE_NO_RANGE_ROOM
    )
    # Cut and pasted at the top: moved there.
    assert (
        free.move_pasted("group_range", [hall], PasteTarget("group_range", None))
        is None
    )
    assert hall in [n.id for n in free.get_group_range_tree()]
    # A range too large for the place is told.
    assert free.move_group_range(lights, copy.id) == S.GROUP_MOVE_NO_ROOM
    assert free.move_group_range(lights, lights) == S.GROUP_MOVE_INTO_ITSELF
