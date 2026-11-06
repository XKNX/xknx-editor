"""Where a device recovered from the bus goes: always the line of its individual address, on a
segment of a medium its hardware supports."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService, _Line
from xknxeditor.proj.core.medium import MEDIUM_RF, MEDIUM_TP

# The media of the hardware programs the tests use: (media, is a coupler).
_MEDIA: dict[str, tuple[set[str], bool]] = {
    "H-rf": ({MEDIUM_RF}, False),
    "H-tp-rf": ({MEDIUM_TP, MEDIUM_RF}, False),
    "H-tp": ({MEDIUM_TP}, False),
    "H-coupler": ({MEDIUM_TP, MEDIUM_RF}, True),
    "H-no-media": (set(), False),
}


@pytest.fixture
def project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    monkeypatch.setattr(proj._catalog, "program_media", _MEDIA.get)
    yield proj
    proj.close()


def _line(project: ProjectService, area: int, line: int) -> _Line | None:
    found = next((a for a in project.get_areas() if a.area_number == area), None)
    if found is None:
        return None
    return next(
        (ln for ln in project.get_lines(found.id) if ln.line_number == line), None
    )


def _add_device(
    project: ProjectService, segment_id: int, address: int, program: str = "H-tp"
) -> None:
    pid = project._pid
    assert pid is not None
    project._svc.add_device(
        pid, segment_id, "P-1", address=address, hardware2program_ref_id=program
    )
    project._bump()


def test_an_empty_line_of_another_medium_is_changed(project: ProjectService) -> None:
    # A new project has the empty TP line 1.1: an RF device turns it into an RF line.
    before = _line(project, 1, 1)
    assert before is not None and before.medium_type == MEDIUM_TP

    segment_id = project.find_or_create_segment_for_address("1.1.3", "H-rf")

    line = _line(project, 1, 1)
    assert line is not None and line.id == before.id
    assert line.medium_type == MEDIUM_RF
    assert segment_id == line.main_segment_id


def test_a_new_line_gets_a_medium_the_device_supports(
    project: ProjectService,
) -> None:
    rf_segment = project.find_or_create_segment_for_address("2.3.1", "H-rf")
    tp_segment = project.find_or_create_segment_for_address("2.4.1", "H-tp-rf")

    rf_line = _line(project, 2, 3)
    tp_line = _line(project, 2, 4)
    assert rf_line is not None and rf_line.medium_type == MEDIUM_RF
    assert rf_segment == rf_line.main_segment_id
    # TP when the device supports it.
    assert tp_line is not None and tp_line.medium_type == MEDIUM_TP
    assert tp_segment == tp_line.main_segment_id


def test_a_line_with_devices_keeps_its_medium(project: ProjectService) -> None:
    # E.g. a media coupler at 1.1.0 came first: RF devices behind it get an RF sub-segment.
    line = _line(project, 1, 1)
    assert line is not None and line.main_segment_id is not None
    _add_device(project, line.main_segment_id, 1)

    segment_id = project.find_or_create_segment_for_address("1.1.3", "H-rf")

    after = _line(project, 1, 1)
    assert after is not None and after.medium_type == MEDIUM_TP
    assert segment_id is not None and segment_id != line.main_segment_id
    segment = project.find_segment(segment_id)
    assert segment is not None and segment.medium_type == MEDIUM_RF
    assert segment.line_id == line.id
    # The next RF device of the line goes on the same sub-segment.
    assert project.find_or_create_segment_for_address("1.1.4", "H-rf") == segment_id


def test_a_line_without_room_for_the_medium_gives_its_main_segment(
    project: ProjectService,
) -> None:
    # An RF line with devices can have no sub-segment: the main segment, where adding a
    # TP-only device is then refused with the reason.
    rf_segment = project.find_or_create_segment_for_address("1.1.3", "H-rf")
    assert rf_segment is not None
    _add_device(project, rf_segment, 3, "H-rf")

    segment_id = project.find_or_create_segment_for_address("1.1.4", "H-tp")

    line = _line(project, 1, 1)
    assert line is not None and line.medium_type == MEDIUM_RF
    assert segment_id == line.main_segment_id


def test_a_line_with_devices_offers_a_segment_of_the_medium(
    project: ProjectService,
) -> None:
    line = _line(project, 1, 1)
    assert line is not None and line.main_segment_id is not None
    _add_device(project, line.main_segment_id, 1)
    rf_segment = project.add_segment(line.id, medium_type=MEDIUM_RF)

    segment_id = project.find_or_create_segment_for_address("1.1.3", "H-rf")

    assert segment_id == rf_segment
    after = _line(project, 1, 1)
    assert after is not None and after.medium_type == MEDIUM_TP


def test_the_devices_of_one_line_all_go_on_it(project: ProjectService) -> None:
    first = project.find_or_create_segment_for_address("1.1.3", "H-rf")
    assert first is not None
    _add_device(project, first, 3)

    assert project.find_or_create_segment_for_address("1.1.4", "H-rf") == first
    line = _line(project, 1, 1)
    assert line is not None and line.medium_type == MEDIUM_RF


@pytest.mark.parametrize("program", [None, "H-unknown", "H-coupler", "H-no-media"])
def test_without_known_media_the_line_is_left_as_it_is(
    project: ProjectService, program: str | None
) -> None:
    segment_id = project.find_or_create_segment_for_address("1.1.3", program)
    new_segment = project.find_or_create_segment_for_address("3.1.1", program)

    line = _line(project, 1, 1)
    assert line is not None and line.medium_type == MEDIUM_TP
    assert segment_id == line.main_segment_id
    new_line = _line(project, 3, 1)
    assert new_line is not None and new_line.medium_type == MEDIUM_TP
    assert new_segment == new_line.main_segment_id


def test_an_address_that_is_not_one(project: ProjectService) -> None:
    assert project.find_or_create_segment_for_address("1.1", "H-rf") is None
    assert project.find_or_create_segment_for_address("a.b.c", "H-rf") is None
