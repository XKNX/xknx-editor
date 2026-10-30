"""The "Status" of areas, lines, functions, spaces, devices and the project, a room's "Use" and the
details of a building function, as the editor shows and changes them."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.element_status import (
    NO_USAGE_TEXT,
    status_label,
    status_options,
    usage_label,
    usage_options,
)
from editor_gui.plugins.project.service import ProjectService
from editor_gui.strings import get_locale, set_locale


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


@pytest.fixture
def german() -> Iterator[None]:
    before = get_locale()
    set_locale("de")
    yield
    set_locale(before)


def test_the_status_choices_in_their_order() -> None:
    assert status_options() == [
        ("Undefined", "Unknown"),
        ("Editing", "Editing"),
        ("FinishedDesign", "Finished design"),
        ("FinishedCommissioning", "Finished commissioning"),
        ("Tested", "Tested"),
        ("Accepted", "Accepted"),
        ("Locked", "Locked"),
    ]
    assert status_label("Bogus") == "Bogus"


def test_the_uses_are_sorted_in_the_language_shown(german: None) -> None:
    options = usage_options()
    assert options[0] == ("", NO_USAGE_TEXT)
    texts = [text for _usage, text in options[1:]]
    assert len(texts) == 27
    assert texts == sorted(texts, key=str.casefold)
    assert usage_label("SU-4") == "Wohnzimmer"
    # A use newer master data knows keeps the text the project brought.
    assert usage_label("SU-99", "Hobbyraum") == "Hobbyraum"
    assert status_label("FinishedDesign") == "Planung abgeschlossen"


def test_areas_lines_and_functions_show_their_status(project: ProjectService) -> None:
    area = project.get_areas()[0]
    line = project.get_lines(area.id)[0]
    building = project.create_space(None, "Building", "House")
    assert building is not None
    function = project.create_function(building, "FT-1", "Light")
    assert function is not None

    for kind, element_id in (("area", area.id), ("line", line.id)):
        details = project.element_details(kind, element_id)
        assert details is not None and details.status == "Undefined"
        project.set_completion_status(kind, element_id, "Tested")
        details = project.element_details(kind, element_id)
        assert details is not None and details.status == "Tested"

    project.set_element_description("function", function, "Ceiling")
    project.rename_element("function", function, "Lamp")
    project.rename_element("function", function, "")  # an empty name keeps the old one
    project.set_completion_status("function", function, "Locked")
    details = project.element_details("function", function)
    assert details is not None
    assert (details.title_kind, details.name, details.description, details.status) == (
        "function",
        "Lamp",
        "Ceiling",
        "Locked",
    )
    assert not details.has_address
    # The history lists the newest step first.
    assert project.history()[0].display_text == "Set status to Locked"
    assert project.element_details("function", -1) is None


def test_the_same_status_again_is_no_step(project: ProjectService) -> None:
    project.set_completion_status("project", 0, "Editing")
    steps = len(project.history())
    project.set_completion_status("project", 0, "Editing")
    assert len(project.history()) == steps
    assert project.completion_status("project", 0) == "Editing"


def test_a_room_gets_its_use(project: ProjectService) -> None:
    building = project.create_space(None, "Building", "House")
    room = project.create_space(building, "Room", "Living", "SU-4")
    assert room is not None
    space = project.find_space(room)
    assert space is not None
    assert (space.usage, space.usage_text) == ("SU-4", "Living room")

    project.set_space_usage(room, "SU-1")
    space = project.find_space(room)
    assert space is not None and space.usage == "SU-1"
    assert project.history()[0].display_text == "Set room use"
    project.set_space_usage(room, "")
    space = project.find_space(room)
    assert space is not None and (space.usage, space.usage_text) == ("", "")


def test_every_room_use_has_a_text_in_every_ui_language() -> None:
    from editor_gui.languages import LANGUAGES
    from editor_gui.plugins.project.space_usages import SPACE_USAGES

    codes = {language.code for language in LANGUAGES}
    for usage, texts in SPACE_USAGES.items():
        assert set(texts) == codes, usage
        assert all(text.strip() for text in texts.values()), usage
