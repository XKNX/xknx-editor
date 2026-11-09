"""Pending downloads follow stored flags, edits and successful downloads without bus access."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.cockpit.service import CockpitService
from editor_gui.plugins.health.service import Finding, HealthService, Severity
from editor_gui.plugins.project.commissioning import LOADED_PARTS
from editor_gui.plugins.project.service import ProjectService
from editor_gui.strings import get_locale, set_locale

from .test_device_resolution import _open, _project_with_device


@pytest.fixture
def project(tmp_path: Path) -> Iterator[tuple[ProjectService, int]]:
    path, node_id, _ = _project_with_device(tmp_path)
    project = _open(CatalogService(tmp_path / "c.xknxcatalog"), path)
    yield project, node_id
    project.close()


def test_each_flag_and_successful_partial_downloads(
    project: tuple[ProjectService, int],
) -> None:
    service, node_id = project
    assert service.pending_downloads()[node_id] == tuple(LOADED_PARTS.values())
    for flag, part in LOADED_PARTS.items():
        service.record_download(node_id, "full")
        assert service.pending_downloads() == {}
        service.set_device_commissioning(node_id, **{flag: False})
        pending = service.pending_downloads()
        assert pending == {node_id: (part,)}
        assert service.pending_downloads() is pending

    service.set_device_commissioning(
        node_id, parameters_loaded=False, communication_part_loaded=False
    )
    service.record_download(node_id, "par")
    assert service.pending_downloads()[node_id] == (
        "group_communication",
        "medium_config",
    )
    service.record_download(node_id, "grp", medium_config=True)
    assert service.pending_downloads() == {}
    service.set_device_commissioning(
        node_id, parameters_loaded=False, communication_part_loaded=False
    )
    service.record_download(node_id, "par,grp")
    assert service.pending_downloads() == {}


def test_link_edit_refreshes_health_cockpit_and_undo(
    project: tuple[ProjectService, int],
) -> None:
    service, node_id = project
    ga_id = service.create_group_address("1/1/1", "Light")
    assert ga_id is not None
    service.record_download(node_id, "full")
    health, cockpit = HealthService(service), CockpitService(service)

    def pending_findings() -> list[Finding]:
        return [f for f in health.findings() if f.category == "download-pending"]

    assert pending_findings() == []
    assert cockpit.rows()[0].loaded_count == 5
    device = service.find_device_by_node_id(node_id)
    assert device is not None
    co = next(co for co in device.get_visible_com_objects() if co.db_id is not None)
    assert co.db_id is not None
    assert service.link_com_object_to_ga(co.db_id, ga_id) is not None
    assert service.pending_downloads() == {node_id: ("group_communication",)}
    (finding,) = pending_findings()
    assert finding.device_node_id == node_id
    assert finding.severity is Severity.WARNING
    assert "Group Addresses" in finding.message
    (row,) = cockpit.rows()
    assert row.needs_attention and row.loaded_count == 4
    assert "Download pending" in row.issue_summary

    assert service.undo()
    assert pending_findings() == []
    assert cockpit.rows()[0].loaded_count == 5
    assert service.redo()
    assert len(pending_findings()) == 1
    service.record_download(node_id, "grp")
    assert pending_findings() == []
    assert cockpit.rows()[0].loaded_count == 5


def test_saved_flags_and_locale_changes(
    project: tuple[ProjectService, int], tmp_path: Path
) -> None:
    service, node_id = project
    service.record_download(node_id, "full")
    service.set_device_commissioning(node_id, parameters_loaded=False)
    service.close()
    assert service.pending_downloads() == {}
    service.open(tmp_path / "p.xknx")
    assert service.pending_downloads() == {node_id: ("parameters",)}
    health = HealthService(service)
    before = get_locale()
    try:
        set_locale("en")
        assert service.pending_download_text(node_id) == "Download pending: Parameters"
        english = health.findings()
        set_locale("de")
        assert (
            service.pending_download_text(node_id)
            == "Programmierung ausstehend: Parameter"
        )
        assert health.findings() != english
    finally:
        set_locale(before)
