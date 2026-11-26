"""Reading every group address of a group from the bus ("Read Values" in a group's menu),
and the recursive address collector behind it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import add_dialog, group_addresses
from editor_gui.plugins.project.ui.group_addresses import (
    GroupAddressesPanel,
    range_ga_texts,
)
from editor_gui.widgets.dpt_value import GroupValueBus
from xknxeditor.proj.core.addressing import GroupAddressStyle

from ._clicks import Clicks


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
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx", group_address_style=GroupAddressStyle.THREE_LEVEL)
    yield proj
    proj.close()


def _frames(render: Any, count: int = 3) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Tree")
        render()
        imgui.end()
        imgui.end_frame()


def _panel(
    project: ProjectService,
    bus: GroupValueBus,
    *,
    read_gap: float = 0.0,
    clock: Any = None,
) -> GroupAddressesPanel:
    extra: dict[str, Any] = {"read_gap": read_gap}
    if clock is not None:
        extra["clock"] = clock
    return GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga_in=project.create_group_address_in,
        on_remove_ga=project.remove_group_address,
        on_create_range=project.create_group_range,
        on_remove_range=project.remove_group_range,
        group_style=lambda: project.group_address_style,
        one_undo_step=project.one_undo_step,
        bus=bus,
        get_project_generation=lambda: project.generation,
        **extra,
    )


def _bus(reads: list[str], *, connected: bool = True) -> GroupValueBus:
    return GroupValueBus(
        connected=lambda: connected,
        read=lambda address: reads.append(address),
        write=lambda _a, _p: None,
        formats=dict,
    )


def _node(project: ProjectService, range_id: int) -> Any:
    stack = list(project.get_group_range_tree())
    while stack:
        node = stack.pop()
        if node.id == range_id:
            return node
        stack.extend(node.children)
    raise AssertionError(f"no range {range_id}")


def _two_addresses(project: ProjectService, middle: int) -> None:
    """Two group addresses at the start of the middle group ``middle``."""
    base = _node(project, middle).range_start
    project.create_group_address_in(middle, base, "A")
    project.create_group_address_in(middle, base + 1, "B")


def test_range_ga_texts_collects_own_and_descendant_addresses(
    project: ProjectService,
) -> None:
    main = project.create_group_range(None, "Lights", 1)
    middle = project.create_group_range(main, "Ground", 1)
    assert main is not None and middle is not None
    _two_addresses(project, middle)
    expected = [ga.text for ga in _node(project, middle).group_addresses]
    assert range_ga_texts(_node(project, main)) == expected
    assert len(expected) == 2


@pytest.mark.usefixtures("context")
def test_reading_a_group_reads_every_group_address_under_it(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    main = project.create_group_range(None, "Lights", 1)
    middle = project.create_group_range(main, "Ground", 1)
    assert main is not None and middle is not None
    _two_addresses(project, middle)
    expected = [ga.text for ga in _node(project, middle).group_addresses]
    reads: list[str] = []
    panel = _panel(project, _bus(reads))
    _frames(panel.render)
    # Read at the main group: its one middle group's addresses are read too.
    clicks.context = f"##gr_ctx_{main}"
    clicks.items.add(S.GA_READ_VALUES)
    _frames(panel.render, count=len(expected) + 2)
    assert reads == expected


@pytest.mark.usefixtures("context")
def test_group_read_is_paced_one_at_a_time(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    main = project.create_group_range(None, "Lights", 1)
    middle = project.create_group_range(main, "Ground", 1)
    assert main is not None and middle is not None
    base = _node(project, middle).range_start
    for offset in range(3):
        project.create_group_address_in(middle, base + offset, f"A{offset}")
    expected = [ga.text for ga in _node(project, middle).group_addresses]
    assert len(expected) == 3
    reads: list[str] = []
    now = [0.0]
    panel = _panel(project, _bus(reads), read_gap=0.1, clock=lambda: now[0])
    _frames(panel.render)
    clicks.context = f"##gr_ctx_{main}"
    clicks.items.add(S.GA_READ_VALUES)
    # The clock stands still: only the first read goes out, however many frames render.
    _frames(panel.render, count=6)
    assert reads == expected[:1]
    # An edit in the same project keeps the queued reads.
    assert project.create_group_range(None, "Heating", 2) is not None
    # Each time the gap elapses, exactly one more read is sent.
    now[0] = 0.1
    _frames(panel.render, count=6)
    assert reads == expected[:2]
    now[0] = 0.2
    _frames(panel.render, count=6)
    assert reads == expected


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("change", ("close", "open", "new"))
def test_group_read_drops_pending_reads_on_project_change(
    project: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: str,
) -> None:
    other_path = tmp_path / "other.xknx"
    if change == "open":
        original_path = project.path
        assert original_path is not None
        project.new(other_path)
        project.open(original_path)

    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    main = project.create_group_range(None, "Lights", 1)
    middle = project.create_group_range(main, "Ground", 1)
    assert main is not None and middle is not None
    _two_addresses(project, middle)
    first = _node(project, middle).group_addresses[0].text
    reads: list[str] = []
    now = [0.0]
    panel = _panel(project, _bus(reads), read_gap=0.1, clock=lambda: now[0])
    _frames(panel.render)
    clicks.context = f"##gr_ctx_{main}"
    clicks.items.add(S.GA_READ_VALUES)
    _frames(panel.render, count=6)
    assert reads == [first]

    if change == "close":
        project.close()
    elif change == "open":
        project.open(other_path)
    else:
        project.new(other_path)

    now[0] = 0.1
    _frames(panel.render, count=6)
    assert reads == [first]


@pytest.mark.usefixtures("context")
def test_read_values_sends_nothing_while_disconnected(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    clicks = Clicks(monkeypatch, group_addresses, add_dialog)
    main = project.create_group_range(None, "Lights", 1)
    middle = project.create_group_range(main, "Ground", 1)
    assert main is not None and middle is not None
    base = _node(project, middle).range_start
    project.create_group_address_in(middle, base, "A")
    reads: list[str] = []
    panel = _panel(project, _bus(reads, connected=False))
    _frames(panel.render)
    clicks.context = f"##gr_ctx_{middle}"
    clicks.open.add(f"Lights##gr{main}")
    clicks.items.add(S.GA_READ_VALUES)
    _frames(panel.render)
    assert reads == []
