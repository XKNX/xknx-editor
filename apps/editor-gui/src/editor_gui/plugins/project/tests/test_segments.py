"""Segments and the media of lines and segments as the editor shows and changes them: the
details of a line and a segment, the reasons a medium cannot change, the check of a device's
medium, and a line's devices split onto its segments in the Devices tree."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.device import UnloadedDevice
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.medium_text import (
    line_media,
    medium_error_text,
    segment_media,
)
from editor_gui.plugins.project.service import ProjectService, format_domain_address
from editor_gui.plugins.project.ui.devices import (
    Area,
    DevicesPanel,
    Line,
    Segment,
    _split_line,
)
from editor_gui.strings import get_locale, set_locale
from xknxeditor.proj.core.medium import MEDIUM_RF, MEDIUM_TP, MediumError


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


def _line_1_1(project: ProjectService) -> int:
    area = next(a for a in project.get_areas() if a.area_number == 1)
    return next(ln.id for ln in project.get_lines(area.id) if ln.line_number == 1)


def test_the_media_offered() -> None:
    assert [name for _id, name in line_media()] == ["TP", "PL", "RF", "IP", "IoT"]
    assert [name for _id, name in segment_media()] == ["TP", "RF", "IoT"]
    assert format_domain_address(MEDIUM_RF, 0x0001_2345_6789) == "0001:23456789"
    assert format_domain_address("MT-1", 7) == "7"
    assert format_domain_address(MEDIUM_RF, None) == ""


def test_a_segment_shows_its_details(project: ProjectService) -> None:
    line_id = _line_1_1(project)
    segment_id = project.add_segment(line_id)
    assert segment_id is not None
    line = project.find_line(line_id)
    assert line is not None
    assert [s.name for s in line.segments] == ["segment"]
    assert line.main_segment_id is not None
    assert project.segment_medium(line.main_segment_id) == (MEDIUM_TP, True)
    assert project.segment_medium(segment_id) == (MEDIUM_TP, False)
    assert project.segment_medium(-1) is None

    project.rename_element("segment", segment_id, "Garden")
    project.set_element_description("segment", segment_id, "RF sensors")
    assert project.set_element_medium("segment", segment_id, MEDIUM_RF) is None
    project.set_completion_status("segment", segment_id, "Tested")
    details = project.element_details("segment", segment_id)
    assert details is not None
    assert (details.title_kind, details.name, details.description) == (
        "segment",
        "Garden",
        "RF sensors",
    )
    assert (details.medium, details.status, details.has_address) == (
        MEDIUM_RF,
        "Tested",
        False,
    )
    assert ":" in details.domain_address
    assert project.history()[0].display_text == "Set status to Tested"
    assert project.history()[1].display_text == "Set medium to RF"
    assert project.selected_topology is None
    project.select_element("segment", segment_id)
    assert project.selected_topology == ("segment", segment_id)
    project.remove_segment(segment_id)
    assert project.element_details("segment", segment_id) is None


def test_a_line_shows_its_medium(project: ProjectService) -> None:
    line_id = _line_1_1(project)
    details = project.element_details("line", line_id)
    assert details is not None and details.medium == MEDIUM_TP
    assert project.set_element_medium("line", line_id, "MT-1") is None
    details = project.element_details("line", line_id)
    assert details is not None
    assert (details.medium, details.domain_address) == ("MT-1", "1")
    assert project.history()[0].display_text == "Set medium to PL"


def test_a_medium_the_rules_refuse_tells_why(
    project: ProjectService, german: None
) -> None:
    line_id = _line_1_1(project)
    project.add_segment(line_id)
    reason = project.set_element_medium("line", line_id, MEDIUM_RF)
    assert reason is not None
    assert reason.startswith("Der Medientyp der Linie")
    assert "RF" in reason
    error = MediumError("device_segment_medium", device="Taster")
    assert medium_error_text(error) == (
        "Das Gerät 'Taster' ist nicht kompatibel zu dem Medientyp des Segmentes."
    )
    unknown = MediumError("unknown_medium", medium="MT-9")
    assert medium_error_text(unknown) == "Unknown medium 'MT-9'."


def test_a_device_fits_the_media_of_its_hardware(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    line_id = _line_1_1(project)
    line = project.find_line(line_id)
    assert line is not None and line.main_segment_id is not None
    rf = project.add_segment(line_id)
    assert rf is not None
    project.set_segment_medium(rf, MEDIUM_RF)
    media: dict[str, tuple[set[str], bool] | None] = {
        "TP": ({MEDIUM_TP}, False),
        "coupler": ({MEDIUM_TP}, True),
        "unknown": None,
        "no media": (set(), False),
    }
    monkeypatch.setattr(project._catalog, "program_media", media.get)
    project.check_device_medium("TP", line.main_segment_id, "Taster")
    for program in ("coupler", "unknown", "no media", None):
        project.check_device_medium(program, rf, "Taster")
    with pytest.raises(MediumError) as error:
        project.check_device_medium("TP", rf, "Taster")
    assert error.value.reason == "device_segment_medium"
    ip_main = next(
        ln.main_segment_id
        for ln in project.get_lines(
            next(a.id for a in project.get_areas() if a.area_number == 1)
        )
        if ln.line_number == 0
    )
    assert ip_main is not None
    with pytest.raises(MediumError) as error:
        project.check_device_medium("TP", ip_main, "Taster")
    assert error.value.reason == "device_line_medium"


def _device(node_id: int, address: str) -> UnloadedDevice:
    return UnloadedDevice(node_id, "", "Product", address, None)


def test_a_line_s_devices_split_onto_its_segments() -> None:
    line = Line(1, 1, 1, "L", [Segment(5, "Garden"), Segment(6, "Cellar")])
    devices = [_device(1, "1.1.1"), _device(2, "1.1.2"), _device(3, "1.1.3")]
    segments, main = _split_line(line, list(devices), {1: 4, 2: 6, 3: 9})
    assert [(s.id, [d.node_id for d in ds]) for s, ds in segments] == [
        (5, []),
        (6, [2]),
    ]
    # A device on the main segment, or on a segment the line does not list, shows under the line.
    assert [d.node_id for d in main] == [1, 3]


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
def test_the_tree_shows_the_segments() -> None:
    line = Line(1, 1, 1, "L", [Segment(5, "Garden"), Segment(6, "")])
    devices = [_device(1, "1.1.1"), _device(2, "1.1.2")]
    panel = DevicesPanel(
        get_devices=list,
        get_areas=lambda: [Area(1, 1, "A")],
        get_lines=lambda _a: [line],
        on_select_device=lambda _d: None,
        on_move_device=lambda _d, _a, _l: None,
        on_create_area=lambda _n, _s: None,
        on_remove_area=lambda _a: None,
        on_rename_area=lambda _a, _n: None,
        on_create_line=lambda _a, _n, _s: None,
        on_remove_line=lambda _l: None,
        on_rename_line=lambda _l, _n: None,
        get_unloaded_devices=lambda: devices,
        get_selected_topology=lambda: ("segment", 5),
        get_device_segments=lambda: {2: 5},
    )
    for _frame in range(2):
        imgui.new_frame()
        imgui.begin("Devices")
        panel.render()
        imgui.end()
        imgui.end_frame()
    assert panel._format_segment_label(Area(1, 1, "A"), line, line.segments[0]) == (
        "Garden (Segment 1.1)"
    )
    assert panel._format_segment_label(Area(1, 1, "A"), line, line.segments[1]) == (
        "Segment 1.1"
    )
