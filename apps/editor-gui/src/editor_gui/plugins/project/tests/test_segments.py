"""Segments and the media of lines and segments as the editor shows and changes them: the
details of a line and a segment, the reasons a medium cannot change, the check of a device's
medium, and a line's devices split onto its segments in the Devices tree."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.device import UnloadedDevice
from editor_gui.hardware_text import bus_current_text, current_text
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.medium_text import (
    line_media,
    medium_error_text,
    segment_media,
)
from editor_gui.plugins.project.service import (
    ProjectService,
    format_bus_access,
    format_domain_address,
    interface_bus_access,
)
from editor_gui.plugins.project.ui.devices import (
    Area,
    DevicesPanel,
    Line,
    Segment,
    _split_line,
)
from editor_gui.plugins.project.ui.element_details import (
    ElementDetailsPanel,
    latency_options,
)
from editor_gui.strings import get_locale, set_locale
from xknxeditor.catalog import ProductSummary
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
    assert reason == (
        "Unter einem Hauptsegment mit dem Medientyp RF sind keine Segmente erlaubt."
    )
    # An unnamed main line is called as the tree shows it.
    main = next(
        line.id
        for area in project.get_areas()
        if area.area_number == 1
        for line in project.get_lines(area.id)
        if line.line_number == 0
    )
    reason = project.set_element_medium("line", main, "MT-1")
    assert reason is not None and "'IP Hauptlinie'" in reason
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


def test_couplers_and_ip_routers_come_from_the_catalog(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = next(a for a in project.get_areas() if a.area_number == 1)
    main = next(ln for ln in project.get_lines(area.id) if ln.line_number == 0)
    line_id = _line_1_1(project)
    line = project.find_line(line_id)
    assert line is not None and line.main_segment_id is not None
    pid = project._pid
    assert pid is not None
    router = project._svc.add_device(
        pid, line.main_segment_id, "P-R", address=0, hardware2program_ref_id="R"
    )
    project._bump()
    roles = {"R": (True, "MV-091A")}
    monkeypatch.setattr(project._catalog, "program_roles", lambda ids: roles)
    assert project._device_roles() == ({router}, {router})
    # A coupler below keeps the medium of the main line.
    reason = project.set_element_medium("line", main.id, MEDIUM_TP)
    assert reason is not None and "coupler" in reason
    # A KNXnet/IP router (at device number 0) in the area keeps it IP.
    roles["R"] = (False, "MV-091A")
    assert project._device_roles() == (set(), {router})
    reason = project.set_element_medium("line", main.id, MEDIUM_TP)
    assert reason is not None and "KNXnet/IP" in reason
    monkeypatch.setattr(project._catalog, "program_roles", lambda ids: {})
    assert project._device_roles() == (set(), set())
    assert project.set_element_medium("line", main.id, MEDIUM_TP) is None


def test_the_backbone_details(project: ProjectService) -> None:
    backbone = next(a for a in project.get_areas() if a.area_number == 0)
    details = project.element_details("backbone", backbone.id)
    assert details is not None
    assert (details.medium, details.latency, details.multicast_address) == (
        "MT-5",
        2000,
        "224.0.23.12",
    )
    assert (details.backbone_security, details.bus_access) == ("Auto", "")
    assert project.set_ip_routing(latency=400) is None
    assert project.set_ip_routing(multicast_address="10.0.0.1") is not None
    assert project.set_ip_routing(multicast_address="239.0.0.9") is None
    parameter = interface_bus_access("IP-Interface", "192.168.16.88", 3671)
    project.set_backbone_bus_connection(parameter)
    details = project.element_details("backbone", backbone.id)
    assert details is not None
    assert (details.latency, details.multicast_address) == (400, "239.0.0.9")
    assert details.bus_access == parameter
    assert details.bus_connection == "IP-Interface (192.168.16.88:3671 Udp)"
    assert project.history()[0].display_text == "Set bus connection of the backbone"
    assert project.history()[1].display_text == "Set IP routing of the backbone"
    project.set_backbone_bus_connection("")
    details = project.element_details("backbone", backbone.id)
    assert details is not None and details.bus_access == ""
    # The backbone offers IP and TP; TP refused while an IP main line is below it.
    reason = project.set_element_medium("backbone", backbone.id, MEDIUM_TP)
    assert reason is not None


def test_a_domain_address_is_entered_or_generated(
    project: ProjectService, german: None
) -> None:
    line_id = _line_1_1(project)
    segment = project.add_segment(line_id)
    assert segment is not None
    assert project.set_element_medium("segment", segment, MEDIUM_RF) is None
    assert (
        project.set_element_domain_address("segment", segment, "0002:00000001") is None
    )
    details = project.element_details("segment", segment)
    assert details is not None and details.domain_address == "0002:00000001"
    assert project.set_element_domain_address("segment", segment, None) is None
    details = project.element_details("segment", segment)
    assert details is not None and details.domain_address != "0002:00000001"
    assert project.set_element_domain_address("segment", segment, "12") == (
        "Die angegebene Domänenadresse ist ungültig."
    )
    assert project.set_element_medium("line", line_id, "MT-1") is not None
    project.remove_segment(segment)
    assert project.set_element_medium("line", line_id, "MT-1") is None
    assert project.set_element_domain_address("line", line_id, "0") == (
        "Die Domänenadresse ist ungültig. Gültige Werte sind 1 bis 255."
    )
    assert project.set_element_domain_address("line", line_id, "17") is None
    details = project.element_details("line", line_id)
    assert details is not None and details.domain_address == "17"
    assert project.history()[0].display_text == "Domänenadresse festlegen"
    # A TP or IP element has none to set.
    assert project.set_element_domain_address("line", -1, "1") is None


@pytest.mark.usefixtures("context")
def test_the_details_render_the_domain_address(project: ProjectService) -> None:
    line_id = _line_1_1(project)
    project.set_element_medium("line", line_id, "MT-1")
    calls: list[str | None] = []
    panel = ElementDetailsPanel(
        on_rename=project.rename_element,
        on_set_description=project.set_element_description,
        on_set_address=project.set_element_address,
        on_set_domain_address=lambda _k, _i, text: calls.append(text) or None,
    )
    details = project.element_details("line", line_id)
    assert details is not None
    for _frame in range(2):
        imgui.new_frame()
        imgui.begin("Details")
        panel.render(details)
        imgui.end()
        imgui.end_frame()
    assert panel._domain == "1" and calls == []


def test_bus_connections_as_shown() -> None:
    assert format_bus_access(None) == ""
    assert (
        format_bus_access(
            {
                "Name": "",
                "Parameter": "Type=IpTunneling;HostAddress=192.168.16.104;"
                'ProtocolType=Udp;Name="IP Tunneling UDP"',
            }
        )
        == "IP Tunneling UDP (192.168.16.104:3671 Udp)"
    )
    assert format_bus_access({"Name": "USB", "Parameter": "Type=Usb"}) == "USB"
    assert interface_bus_access("X", "10.0.0.1", 3700) == (
        'Type=IpTunneling;HostAddress=10.0.0.1;Port=3700;ProtocolType=Udp;Name="X"'
    )
    assert latency_options()[0] == (400, "LAN (< 400ms)")
    assert [value for value, _text in latency_options()] == [
        400,
        1000,
        2000,
        4000,
        8000,
    ]


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
def test_the_tree_shows_the_backbone_the_areas_and_the_segments() -> None:
    line = Line(1, 1, 1, "L", [Segment(5, "Garden"), Segment(6, "")])
    backbone = Line(2, 0, 0, "", medium_type="MT-5")
    main = Line(3, 1, 0, "")
    lines = {0: [backbone], 1: [main, line]}
    devices = [_device(1, "1.1.1"), _device(2, "1.1.2"), _device(3, "1.0.1")]
    panel = DevicesPanel(
        get_devices=list,
        get_areas=lambda: [Area(0, 0, ""), Area(1, 1, "Hauptlinie")],
        get_lines=lambda area_id: lines[area_id],
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
    area = Area(1, 1, "A")
    assert panel._format_segment_label(area, line, line.segments[0]) == "1.1 Garden"
    assert panel._format_segment_label(area, line, line.segments[1]) == "1.1"
    assert panel._format_line_label(area, line) == "1.1 L"
    # An area shows as its main line; one without a name is called after its medium.
    assert panel._format_area_label(Area(0, 0, ""), backbone) == "0.0 IP backbone line"
    assert panel._format_area_label(Area(1, 1, "Hauptlinie"), main) == "1.0 Hauptlinie"
    assert panel._format_area_label(Area(2, 2, ""), main) == "2.0 TP main line"


def test_lines_and_segments_show_their_devices_and_bus_current(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    line_id = _line_1_1(project)
    line = project.find_line(line_id)
    assert line is not None and line.main_segment_id is not None
    pid = project._pid
    assert pid is not None
    sensor = project._svc.add_device(
        pid, line.main_segment_id, "P-1", address=1, hardware2program_ref_id="H-1"
    )
    project._svc.add_device(
        pid, line.main_segment_id, "P-2", address=2, hardware2program_ref_id="H-2"
    )
    project._bump()
    power = {"H-1": (12.0, False, False), "H-2": (None, True, False)}
    monkeypatch.setattr(
        project._catalog, "program_power", lambda ids: {i: power[i] for i in ids}
    )
    assert project.bus_load_texts("line", line_id) == (2, "12 mA")
    assert project.bus_load_texts("segment", line.main_segment_id) == (2, "12 mA")
    area = next(a for a in project.get_areas() if a.area_number == 1)
    assert project.bus_load_texts("area", area.id) == (0, "-")
    assert project.bus_load_texts("group_range", 1) is None
    assert project.device_bus_current_text(sensor) == "12 mA"
    power["H-1"] = (None, False, False)
    project._bump()
    assert project.bus_load_texts("line", line_id) == (2, "10 mA (estimated)")
    assert project.device_bus_current_text(sensor) == "10 mA (estimated)"
    assert project.device_bus_current_text(999) == "-"


def test_bus_current_texts() -> None:
    product = ProductSummary("P", "H", "Name", None, None, "M-1", None)
    assert bus_current_text(None, "(estimated)") == "-"
    assert bus_current_text(product, "(estimated)") == "10 mA (estimated)"
    product.is_power_supply = True
    assert bus_current_text(product, "(est)") == "320 mA (est)"
    product.bus_current = 640.0
    assert bus_current_text(product, "(est)") == "640 mA"
    assert current_text(0.0, True, "(est)") == "-"
