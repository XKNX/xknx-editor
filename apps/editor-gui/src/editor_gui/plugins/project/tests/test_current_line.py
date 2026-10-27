"""A space's "Current Line" as the editor shows and changes it: the line choices, the line a space
inherits, and the drop-down in the space's details."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.ui import space_details
from editor_gui.plugins.project.ui.space_details import SpaceDetailsPanel


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
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def test_a_room_inherits_the_building_s_line_until_it_has_its_own(
    project: ProjectService,
) -> None:
    lines = {text: line_id for line_id, text in project.line_choices()}
    # A line without a name is called after its medium.
    assert list(lines) == ["0.0 IP backbone line", "1.0 IP main line", "1.1 TP line"]
    lines["1.1"] = lines["1.1 TP line"]
    lines["1.0"] = lines["1.0 IP main line"]
    building = project.get_space_tree()[0].id
    room = project.create_space(building, "Room", "Living")
    assert room is not None
    current = project.current_line(room)
    assert current is not None
    assert (current[0], current[1].id) == (lines["1.1"], building)

    project.set_space_default_line(room, lines["1.0"])
    current = project.current_line(room)
    assert current is not None
    assert (current[0], current[1].id) == (lines["1.0"], room)
    assert project.history()[0].display_text == "Set current line"
    steps = len(project.history())
    project.set_space_default_line(room, lines["1.0"])
    assert len(project.history()) == steps

    project.set_space_default_line(building, None)
    project.set_space_default_line(room, None)
    assert project.current_line(room) is None
    assert project.current_line(-1) is None


def _render(panel: SpaceDetailsPanel, project: ProjectService, space_id: int) -> None:
    space = project.find_space(space_id)
    assert space is not None
    imgui.new_frame()
    imgui.begin("details")
    panel.render(space)
    imgui.end()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_the_details_render_with_the_drop_down(project: ProjectService) -> None:
    building = project.get_space_tree()[0].id
    panel = SpaceDetailsPanel(
        on_rename=project.rename_space,
        on_set_description=project.set_space_description,
        on_set_number=project.set_space_number,
        line_choices=project.line_choices,
        get_current_line=project.current_line,
        on_set_current_line=project.set_space_default_line,
    )
    for _frame in range(2):
        _render(panel, project, building)
    space = project.find_space(building)
    assert space is not None and space.default_line_id is not None


@pytest.mark.usefixtures("context")
def test_the_details_show_the_own_or_the_inherited_line(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = {text: line_id for line_id, text in project.line_choices()}
    lines["1.0"] = lines["1.0 IP main line"]
    lines["1.1"] = lines["1.1 TP line"]
    building = project.get_space_tree()[0]
    project.rename_space(building.id, "House")
    room = project.create_space(building.id, "Room", "Living")
    assert room is not None
    shown: list[tuple[int | None, str]] = []
    choice: list[tuple[bool, int | None]] = [(False, None)]

    def combo(
        label: str,
        own: int | None,
        preview: str,
        choices: list[tuple[int, str]],
        none_text: str,
    ) -> tuple[bool, int | None]:
        assert [text for _id, text in choices] == [
            "0.0 IP backbone line",
            "1.0 IP main line",
            "1.1 TP line",
        ]
        assert none_text == "None"
        shown.append((own, preview))
        return choice[0]

    monkeypatch.setattr(space_details, "line_combo", combo)
    panel = SpaceDetailsPanel(
        on_rename=project.rename_space,
        on_set_description=project.set_space_description,
        on_set_number=project.set_space_number,
        line_choices=project.line_choices,
        get_current_line=project.current_line,
        on_set_current_line=project.set_space_default_line,
    )
    _render(panel, project, room)
    assert shown[-1] == (None, "1.1 TP line (inherits from House)")
    _render(panel, project, building.id)
    assert shown[-1] == (lines["1.1"], "1.1 TP line")

    choice[0] = (True, lines["1.0"])
    _render(panel, project, room)
    choice[0] = (False, lines["1.0"])
    _render(panel, project, room)
    assert shown[-1] == (lines["1.0"], "1.0 IP main line")

    choice[0] = (True, None)
    _render(panel, project, building.id)
    choice[0] = (True, None)
    _render(panel, project, room)
    choice[0] = (False, None)
    _render(panel, project, room)
    assert shown[-1] == (None, "None")


@pytest.mark.usefixtures("context")
def test_a_room_s_use_and_status_are_chosen_in_its_details(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    building = project.get_space_tree()[0].id
    room = project.create_space(building, "Room", "Living")
    assert room is not None
    from editor_gui.plugins.project.element_status import usage_options

    use = next(usage for usage, _text in usage_options() if usage)
    monkeypatch.setattr(space_details, "usage_combo", lambda *_a: use)
    monkeypatch.setattr(space_details, "status_combo", lambda *_a: "Tested")
    panel = SpaceDetailsPanel(
        on_rename=project.rename_space,
        on_set_description=project.set_space_description,
        on_set_number=project.set_space_number,
        line_choices=project.line_choices,
        get_current_line=project.current_line,
        on_set_current_line=project.set_space_default_line,
        on_set_usage=project.set_space_usage,
        get_status=lambda space_id: project.completion_status("space", space_id),
        on_set_status=lambda space_id, status: project.set_completion_status(
            "space", space_id, status
        ),
    )
    _render(panel, project, room)
    space = project.find_space(room)
    assert space is not None and space.usage == use
    assert project.completion_status("space", room) == "Tested"


@pytest.mark.usefixtures("context")
def test_the_project_status_is_chosen_in_the_project_info(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project.ui import project_info

    monkeypatch.setattr(project_info, "status_combo", lambda *_a: "Accepted")
    panel = project_info.ProjectInfoPanel(
        get_project_info=project.get_project_metadata,
        get_status=lambda: project.completion_status("project", 0),
        on_set_status=lambda status: project.set_completion_status(
            "project", 0, status
        ),
    )
    imgui.new_frame()
    imgui.begin("Project")
    panel.render()
    imgui.end()
    imgui.end_frame()
    assert project.completion_status("project", 0) == "Accepted"


@pytest.mark.usefixtures("context")
def test_the_project_info_shows_the_project_file(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project.strings import S
    from editor_gui.plugins.project.ui import project_info

    shown: list[str] = []
    monkeypatch.setattr(project_info.imgui, "text_wrapped", shown.append)
    monkeypatch.setattr(project_info.imgui, "text_disabled", shown.append)
    panel = project_info.ProjectInfoPanel(
        get_project_info=project.get_project_metadata,
        get_path=lambda: project.path,
    )
    imgui.new_frame()
    imgui.begin("Project")
    panel.render()
    imgui.end()
    imgui.end_frame()
    assert project.path is not None
    label = shown.index(S.PROJECT_INFO_FILE)
    assert shown[label + 1] == str(project.path)
