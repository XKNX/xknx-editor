"""The Paste Special dialog, and the drops with Ctrl held that open it: where the trees take
copies, what the dialog offers and what OK pastes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.paste import PasteKind, PasteTarget
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import group_addresses, paste_special, spaces
from editor_gui.plugins.project.ui.group_addresses import GroupAddressesPanel
from editor_gui.plugins.project.ui.paste_special import PasteSpecialDialog
from editor_gui.plugins.project.ui.tree_drag import Dragged
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import Allocation, GroupAddressCopy, PasteOptions

from ._clicks import Clicks
from ._drops import drop_on
from .test_buildings_actions import _device
from .test_buildings_view import _panel as _buildings

Pasted = tuple[PasteKind, list[int], PasteTarget, PasteOptions]


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


def _frames(panel: Any, count: int = 1) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Tree")
        panel.render()
        imgui.end()
        imgui.end_frame()


class _Opened(PasteSpecialDialog):
    """The dialog of a panel, recording what it was opened for."""

    def __init__(self, project: ProjectService) -> None:
        self.pasted: list[Pasted] = []

        def on_paste(
            kind: PasteKind, ids: list[int], target: PasteTarget, options: PasteOptions
        ) -> str | None:
            self.pasted.append((kind, ids, target, options))
            return None

        super().__init__(
            "test",
            group_style=lambda: project.group_address_style,
            group_ranges=project.get_group_range_tree,
            names=project.paste_names,
            on_paste=on_paste,
        )
        self.asked: list[tuple[PasteKind, list[int], PasteTarget]] = []

    def open(self, kind: PasteKind, ids: list[int], target: PasteTarget) -> None:
        self.asked.append((kind, ids, target))


def _dialog(
    project: ProjectService,
    pasted: list[Pasted],
    style: GroupAddressStyle | None = None,
) -> PasteSpecialDialog:
    def on_paste(
        kind: PasteKind, ids: list[int], target: PasteTarget, options: PasteOptions
    ) -> str | None:
        pasted.append((kind, ids, target, options))
        return None

    return PasteSpecialDialog(
        "test",
        group_style=(lambda: style) if style else lambda: project.group_address_style,
        group_ranges=project.get_group_range_tree,
        names=project.paste_names,
        on_paste=on_paste,
    )


@pytest.mark.usefixtures("context")
def test_ok_pastes_with_the_defaults(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _device(project, 7)
    pasted: list[Pasted] = []
    dialog = _dialog(project, pasted)
    target = PasteTarget("segment", project.device_segments()[device])
    dialog.open("device", [device], target)
    clicks = Clicks(monkeypatch, paste_special)
    _frames(dialog, 2)
    clicks.button = S.BTN_OK
    _frames(dialog)
    assert pasted == [
        ("device", [device], target, PasteOptions(target_range="0/0")),
    ]
    # Closed: nothing more is pasted.
    _frames(dialog, 2)
    assert len(pasted) == 1


@pytest.mark.usefixtures("context")
def test_the_choices_reach_the_paste(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _device(project, 7)
    pasted: list[Pasted] = []
    dialog = _dialog(project, pasted)
    target = PasteTarget("segment", project.device_segments()[device])
    dialog.open("device", [device], target)
    chosen = {S.PASTE_GA_NEW_MIDDLE, S.PASTE_APPEND}
    shown: list[str] = []
    real_radio = paste_special.radio_button

    def radio_button(label: str, active: bool, **kwargs: Any) -> bool:
        shown.append(label.split("##")[0])
        real_radio(label, active, **kwargs)
        return label.split("##")[0] in chosen

    monkeypatch.setattr(paste_special, "radio_button", radio_button)
    clicks = Clicks(monkeypatch, paste_special)
    _frames(dialog, 2)
    assert shown[:3] == [S.PASTE_GA_DONT_COPY, S.PASTE_GA_KEEP, S.PASTE_GA_NEW_MIDDLE]
    clicks.button = S.BTN_OK
    _frames(dialog)
    (_kind, _ids, _target, options) = pasted[0]
    assert options.group_addresses is GroupAddressCopy.CREATE_NEW
    assert options.allocation is Allocation.APPEND


@pytest.mark.usefixtures("context")
def test_group_addresses_need_no_choice_about_group_addresses(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    light = project.create_group_address("1/1/5", "Light")
    assert light is not None
    dialog = _dialog(project, [])
    dialog.open("group_address", [light], PasteTarget("group_range", 1))
    shown: list[str] = []
    real_radio = paste_special.radio_button

    def radio_button(label: str, active: bool, **kwargs: Any) -> bool:
        shown.append(label.split("##")[0])
        return real_radio(label, active, **kwargs)

    monkeypatch.setattr(paste_special, "radio_button", radio_button)
    _frames(dialog, 2)
    assert S.PASTE_GA_KEEP not in shown
    assert S.PASTE_FILL_UP in shown


@pytest.mark.usefixtures("context")
def test_ok_waits_for_a_group_that_exists_in_the_style(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _device(project, 7)
    dialog = _dialog(project, [], GroupAddressStyle.TWO_LEVEL)
    dialog.open("device", [device], PasteTarget("segment", 1))
    # Two-level style: a main group, "0" at first.
    assert dialog.options().target_range == "0"
    enabled: list[Any] = []
    real_buttons = paste_special.dialog_buttons

    def dialog_buttons(*labels: str, **kwargs: Any) -> int | None:
        enabled.append(kwargs.get("enabled"))
        return real_buttons(*labels, **kwargs)

    monkeypatch.setattr(paste_special, "dialog_buttons", dialog_buttons)
    _frames(dialog, 2)
    assert enabled[-1] == (True, True)
    dialog._range_text = "1/2"  # pyright: ignore[reportPrivateUsage]
    _frames(dialog)
    assert enabled[-1] == (False, True)


@pytest.mark.usefixtures("context")
def test_buildings_take_copies_where_they_may_go(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    building = project.get_space_tree()[0].id
    ground = project.create_space(building, "Floor", "Ground")
    assert ground is not None
    room = project.create_space(ground, "Room", "Kitchen")
    assert room is not None
    light = project.create_function(room, "FT-1", "Light")
    assert isinstance(light, int)
    trade = project.create_trade(None, "Lighting")
    assert trade is not None
    device = _device(project, 7)
    panel = _buildings(project, [])
    dialog = _Opened(project)
    panel._paste_dialog = dialog  # pyright: ignore[reportPrivateUsage]
    _frames(panel, 2)
    for row, dragged, target in (
        # A room next to itself (onto the floor it is on), a function into its own room, a
        # device into a room, a trade to the top.
        (f"##sp{ground}", Dragged("space", (room,)), PasteTarget("space", ground)),
        (f"##sp{room}", Dragged("function", (light,)), PasteTarget("space", room)),
        (f"##sp{room}", Dragged("device", (device,)), PasteTarget("space", room)),
        ("##trades_root", Dragged("trade", (trade,)), PasteTarget("trade", None)),
        (f"##trade{trade}", Dragged("device", (device,)), PasteTarget("trade", trade)),
        # Not: a room to the top, a floor into its own room, a trade into itself.
        ("##spaces_root", Dragged("space", (room,)), None),
        (f"##sp{room}", Dragged("space", (ground,)), None),
        (f"##trade{trade}", Dragged("trade", (trade,)), None),
    ):
        offered = drop_on(monkeypatch, spaces, row, dragged, copy=True)
        _frames(panel)
        assert offered == [target is not None], row
        if target is not None:
            assert dialog.asked.pop() == (dragged.kind, list(dragged.ids), target)
        monkeypatch.undo()
    assert dialog.asked == []


@pytest.mark.usefixtures("context")
def test_group_addresses_take_copies_also_in_their_own_group(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    light = project.create_group_address("1/1/5", "Light")
    project.create_group_address("2/1/5", "Other")
    assert light is not None
    panel = GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga=lambda _a, _n: None,
        on_rename_ga=lambda _i, _n: None,
        on_remove_ga=lambda _i: None,
        on_create_range=project.create_group_range,
        group_style=lambda: project.group_address_style,
        on_move_ga=project.move_group_address,
        on_move_range=project.move_group_range,
    )
    dialog = _Opened(project)
    panel._paste_dialog = dialog  # pyright: ignore[reportPrivateUsage]
    clicks = Clicks(monkeypatch, group_addresses)
    main_1, main_2 = project.get_group_range_tree()
    middle_1 = main_1.children[0]
    for node in (main_1, middle_1, main_2):
        clicks.open.add(f"{node.address}  {node.name or f'[{node.id}]'}##gr{node.id}")
    _frames(panel, 2)
    for row, dragged, target in (
        (
            f"##gr{middle_1.id}",
            Dragged("group_address", (light,)),
            PasteTarget("group_range", middle_1.id),
        ),
        (
            f"##gr{main_1.id}",
            Dragged("group_range", (middle_1.id,)),
            PasteTarget("group_range", main_1.id),
        ),
        # A main group's copy goes to the top, whichever main group it is dropped on.
        (
            f"##gr{main_2.id}",
            Dragged("group_range", (main_1.id,)),
            PasteTarget("group_range", None),
        ),
        (f"##gr{main_2.id}", Dragged("group_address", (light,)), None),
        (f"##gr{middle_1.id}", Dragged("group_range", (middle_1.id,)), None),
    ):
        offered = drop_on(monkeypatch, group_addresses, row, dragged, copy=True)
        _frames(panel)
        assert offered == [target is not None], row
        if target is not None:
            assert dialog.asked.pop() == (dragged.kind, list(dragged.ids), target)
        monkeypatch.undo()
    # Without Ctrl a main group goes nowhere.
    offered = drop_on(
        monkeypatch,
        group_addresses,
        f"##gr{main_2.id}",
        Dragged("group_range", (main_1.id,)),
    )
    _frames(panel)
    assert offered == [False]


@pytest.mark.usefixtures("context")
def test_topology_takes_copies_of_devices_segments_and_sub_lines(
    monkeypatch: pytest.MonkeyPatch, project: ProjectService
) -> None:
    from editor_gui.plugins.project.ui import topology as topology_ui
    from editor_gui.plugins.project.ui.topology import (
        Area,
        Line,
        Segment,
        TopologyPanel,
    )

    lines = {
        1: [
            Line(10, 1, 0, "", main_segment_id=100),
            Line(11, 1, 1, "Office", [Segment(5, "Garden")], main_segment_id=110),
        ],
    }
    dialog = _Opened(project)
    panel = TopologyPanel(
        get_devices=list,
        get_areas=lambda: [Area(1, 1, "A")],
        get_lines=lambda area_id: lines[area_id],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _s: None,
        on_remove_area=lambda _a: None,
        on_rename_area=lambda _a, _n: None,
        on_create_line=lambda _a, _n, _s: None,
        on_remove_line=lambda _l: None,
        on_rename_line=lambda _l, _n: None,
        paste_dialog=dialog,
    )
    _frames(panel, 2)
    for row, dragged, target in (
        ("##area_1", Dragged("line", (11,)), PasteTarget("area", 1)),
        ("##line_11", Dragged("segment", (5,)), PasteTarget("line", 11)),
        ("##segment_5", Dragged("device", (42,)), PasteTarget("segment", 5)),
        ("##line_11", Dragged("device", (42,)), PasteTarget("segment", 110)),
        # A main line is not copied.
        ("##area_1", Dragged("line", (10,)), None),
    ):
        offered = drop_on(monkeypatch, topology_ui, row, dragged, copy=True)
        _frames(panel)
        assert offered == [target is not None], row
        if target is not None:
            assert dialog.asked.pop() == (dragged.kind, list(dragged.ids), target)
        monkeypatch.undo()


@pytest.mark.usefixtures("context")
def test_copy_and_paste_by_the_context_menus(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    building = project.get_space_tree()[0].id
    ground = project.create_space(building, "Floor", "Ground")
    assert ground is not None
    room = project.create_space(ground, "Room", "Kitchen")
    assert room is not None
    panel = _buildings(project, [])
    panel._on_move_space = project.move_space  # pyright: ignore[reportPrivateUsage]
    dialog = _Opened(project)
    panel._paste_dialog = dialog  # pyright: ignore[reportPrivateUsage]
    clicks = Clicks(monkeypatch, spaces)
    _frames(panel, 2)
    # Nothing copied yet: the room's Paste does nothing.
    clicks.context = f"##spctx{room}"
    clicks.items.add(S.CONTEXT_PASTE)
    _frames(panel, 2)
    assert dialog.pasted == []
    clicks.items.clear()
    clicks.context = f"##spctx{room}"
    clicks.items.add(S.CONTEXT_COPY)
    _frames(panel, 2)
    # Pasted on its floor: a copy with the defaults; Paste Special… asks first.
    clicks.context = f"##spctx{ground}"
    clicks.items.add(S.CONTEXT_PASTE)
    _frames(panel, 2)
    assert dialog.pasted == [
        ("space", [room], PasteTarget("space", ground), PasteOptions())
    ]
    clicks.context = f"##spctx{ground}"
    clicks.items.add(S.CONTEXT_PASTE_SPECIAL)
    _frames(panel, 2)
    assert dialog.asked == [("space", [room], PasteTarget("space", ground))]
    # A room does not go to the top: the root's Paste is off.
    clicks.context = "##spaces_root_ctx"
    clicks.items.add(S.CONTEXT_PASTE)
    _frames(panel, 2)
    assert len(dialog.pasted) == 1
