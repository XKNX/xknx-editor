"""The Security of a main or middle group and of a group address, and a group address's Central
mark, as the editor shows and changes them in their details."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.ui.element_details import (
    ElementDetailsPanel,
    security_options,
)


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


def _groups(project: ProjectService) -> tuple[int, int, int]:
    """A group address 1/1/1 and its main and middle group."""
    ga = project.create_group_address("1/1/1", "Light")
    assert ga is not None
    main = project.get_group_range_tree()[0]
    return main.id, main.children[0].id, ga


def test_the_options_of_the_security_drop_down() -> None:
    assert security_options() == [
        ("Auto", "Automatic"),
        ("On", "Forced on"),
        ("Off", "Forced off"),
    ]


def test_a_forcing_group_shows_in_the_details_below_it(project: ProjectService) -> None:
    main, middle, ga = _groups(project)
    details = project.element_details("group_address", ga)
    assert details is not None
    assert (details.security, details.forced_by, details.central) == (
        "Auto",
        None,
        False,
    )
    assert project.set_element_security("group_range", main, "Off") is None
    assert project.history()[0].display_text == "Set group security"
    for kind, element_id in (("group_address", ga), ("group_range", middle)):
        details = project.element_details(kind, element_id)
        assert details is not None and details.forced_by is not None
        assert (details.forced_by.group_range_id, details.forced_by.mode) == (
            main,
            "Off",
        )
    main_details = project.element_details("group_range", main)
    assert main_details is not None
    assert (main_details.security, main_details.forced_by) == ("Off", None)
    assert main_details.central is None


def test_securing_needs_a_password(project: ProjectService) -> None:
    main, _middle, ga = _groups(project)
    assert project.set_element_security("group_address", ga, "On") == (
        project.set_element_security("group_range", main, "On")
    )
    reason = project.set_element_security("group_range", main, "On")
    assert reason is not None and "password" in reason.lower()


def test_a_group_address_is_marked_central(project: ProjectService) -> None:
    _main, _middle, ga = _groups(project)
    project.set_group_address_central(ga, True)
    details = project.element_details("group_address", ga)
    assert details is not None and details.central
    assert project.history()[0].display_text == "Mark group address as central"
    project.set_group_address_central(ga, False)
    assert project.history()[0].display_text == "Remove central mark from group address"
    listed = next(g for g in project.group_addresses if g.id == ga)
    assert not listed.central


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
def test_the_details_render_central_and_security(project: ProjectService) -> None:
    main, _middle, ga = _groups(project)
    project.set_element_security("group_range", main, "Off")
    panel = ElementDetailsPanel(
        on_rename=project.rename_element,
        on_set_description=project.set_element_description,
        on_set_address=project.set_element_address,
        on_set_security=project.set_element_security,
        on_set_central=project.set_group_address_central,
    )
    for kind, element_id in (("group_address", ga), ("group_range", main)):
        details = project.element_details(kind, element_id)
        assert details is not None
        for _frame in range(2):
            imgui.new_frame()
            imgui.begin("Details")
            panel.render(details)
            imgui.end()
            imgui.end_frame()
