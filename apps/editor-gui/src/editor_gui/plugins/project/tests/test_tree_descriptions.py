"""Descriptions in the trees: without the line breaks around them, a short one inline after the
name, a longer one behind the note icon, on every kind of row; and an edit in the details is
stored for the element it was made in, also when another one is selected meanwhile."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project import plugin as plugin_module
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.ui import group_addresses
from editor_gui.plugins.project.ui.group_addresses import GroupAddressesPanel
from editor_gui.plugins.project.ui.topology import Area, Line, Segment, TopologyPanel
from editor_gui.widgets import description

from ._clicks import Clicks


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(render: Any, count: int = 2) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.begin("Tree")
        render()
        imgui.end()
        imgui.end_frame()


class _Shown:
    """The descriptions drawn: inline (dimmed text) and behind the icon."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.inline: list[str] = []
        self.icons: list[str] = []
        real_text = imgui.text_disabled
        real_icon = description.description_icon

        def text_disabled(text: str) -> None:
            self.inline.append(text)
            real_text(text)

        def description_icon(text: str, **kwargs: Any) -> bool:
            self.icons.append(text)
            return real_icon(text, **kwargs)

        monkeypatch.setattr(imgui, "text_disabled", text_disabled)
        monkeypatch.setattr(description, "description_icon", description_icon)
        monkeypatch.setattr(group_addresses, "description_icon", description_icon)


@pytest.mark.usefixtures("context")
def test_a_description_is_shown_without_the_line_breaks_around_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown = _Shown(monkeypatch)
    long = "\nA description that is much too long to show inline\n\n"
    _frames(
        lambda: (
            imgui.text("Row"),
            description.render_description("Kitchen\n\n"),
            description.render_description(long),
            description.render_description("\n \n"),
        ),
        1,
    )
    assert shown.inline == ["Kitchen"]
    assert shown.icons == [long.strip()]


@pytest.mark.usefixtures("context")
def test_group_addresses_and_their_groups_show_their_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "p.xknx")
    try:
        short = project.create_group_address("1/1/5", "Light")
        long = project.create_group_address("1/1/6", "Blind")
        assert short is not None and long is not None
        project.set_element_description("group_address", short, "Ceiling\n")
        long_text = "Opens and closes the blind of the big window in the kitchen"
        project.set_element_description("group_address", long, long_text)
        main = project.get_group_range_tree()[0]
        project.set_element_description("group_range", main.id, "Lights")
        clicks = Clicks(monkeypatch, group_addresses)
        main = project.get_group_range_tree()[0]
        middle = main.children[0]
        for node in (main, middle):
            clicks.open.add(f"{node.address}  {node.name}##gr{node.id}")
        panel = GroupAddressesPanel(
            get_range_tree=project.get_group_range_tree,
            get_assignments_for_ga=project.get_assignments_for_ga,
            get_devices=lambda: [],
            on_create_ga_in=lambda _g, _a, _n: None,
            on_remove_ga=lambda _i: None,
            on_create_range=project.create_group_range,
            group_style=lambda: project.group_address_style,
        )
        shown = _Shown(monkeypatch)
        _frames(panel.render)
        assert "Ceiling" in shown.inline and "Lights" in shown.inline
        assert long_text in shown.icons
    finally:
        project.close()


@pytest.mark.usefixtures("context")
def test_areas_lines_and_segments_show_their_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = Line(3, 1, 0, "", main_segment_id=30, description="Main\n")
    line = Line(
        1,
        1,
        1,
        "Office",
        [Segment(5, "Garden", description="Outside")],
        main_segment_id=10,
        description="First floor",
    )
    panel = TopologyPanel(
        get_devices=lambda: [],
        get_areas=lambda: [Area(1, 1, "House", description="\nWhole house\n")],
        get_lines=lambda _a: [main, line],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _t, _m: None,
        on_remove_area=lambda _a: None,
        on_create_line=lambda _a, _n, _t, _m: None,
        on_remove_line=lambda _l: None,
    )
    shown = _Shown(monkeypatch)
    _frames(panel.render)
    assert {"Whole house", "First floor", "Outside"} <= set(shown.inline)


class _Details:
    def __init__(self, name: str, log: list[str]) -> None:
        self._name = name
        self._log = log

    def render(self, target: Any) -> None:
        self._log.append(f"{self._name}:{getattr(target, 'id', target)}")


def test_an_edit_in_the_details_stays_with_its_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    project = SimpleNamespace(
        selected_space_id=1,
        selected_element=None,
        find_space=lambda space_id: SimpleNamespace(id=space_id),
        element_details=lambda kind, ident: SimpleNamespace(id=f"{kind}{ident}"),
    )
    plugin = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = cast(Any, SimpleNamespace(project=project))  # pyright: ignore[reportPrivateUsage]
    plugin._space_details = cast(Any, _Details("space", log))  # pyright: ignore[reportPrivateUsage]
    plugin._element_details = cast(Any, _Details("element", log))  # pyright: ignore[reportPrivateUsage]
    plugin._configure_panel = cast(  # pyright: ignore[reportPrivateUsage]
        Any, SimpleNamespace(render_device=lambda: log.append("device"))
    )
    plugin._get_selected_node_ids = None  # pyright: ignore[reportPrivateUsage]
    plugin._details_shown = None  # pyright: ignore[reportPrivateUsage]
    storing = [False]
    monkeypatch.setattr(plugin_module, "edit_being_stored", lambda: storing[0])
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    # Another space selected while the description field of space 1 stores its edit: space 1
    # stays shown for that frame, then space 2 is.
    project.selected_space_id = 2
    storing[0] = True
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    storing[0] = False
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    assert log == ["space:1", "space:1", "space:2"]
    # From a space to an element, and from there to the device, the same.
    project.selected_space_id = None
    project.selected_element = ("group_address", 7)
    storing[0] = True
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    storing[0] = False
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    project.selected_element = None
    storing[0] = True
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    storing[0] = False
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    assert log[3:] == [
        "space:2",
        "element:group_address7",
        "element:group_address7",
        "device",
    ]
    # A space that is gone meanwhile is not held.
    project.selected_space_id = 3
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    project.find_space = lambda _space_id: None
    project.selected_space_id = 4
    storing[0] = True
    plugin._render_device()  # pyright: ignore[reportPrivateUsage]
    assert log[-1] == "device"
