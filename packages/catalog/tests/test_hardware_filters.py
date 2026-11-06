"""Tests for ``list_hardware`` with its ``HardwareFilters``."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.catalog import CatalogService, HardwareFilters

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


def _catalog(tmp_path: Path) -> CatalogService:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_FIXTURE.read_bytes())
    return svc


def test_without_filters_lists_the_imported_hardware(tmp_path: Path) -> None:
    svc = _catalog(tmp_path)
    assert len(svc.list_hardware()) > 0
    assert [h.id for h in svc.list_hardware(HardwareFilters())] == [
        h.id for h in svc.list_hardware()
    ]


def test_manufacturer_filter(tmp_path: Path) -> None:
    svc = _catalog(tmp_path)
    hardware = svc.list_hardware()[0]
    matching = svc.list_hardware(
        HardwareFilters(manufacturer_id=[hardware.manufacturer_id])
    )
    assert hardware.id in {h.id for h in matching}
    assert svc.list_hardware(HardwareFilters(manufacturer_id=["M-FFFF"])) == []


def test_search_matches_part_of_the_name(tmp_path: Path) -> None:
    svc = _catalog(tmp_path)
    hardware = svc.list_hardware()[0]
    assert hardware.name
    term = hardware.name[1:4].lower()
    found = svc.list_hardware(HardwareFilters(search=term))
    assert hardware.id in {h.id for h in found}
    assert svc.list_hardware(HardwareFilters(search="no such hardware name")) == []


def test_limit_and_offset(tmp_path: Path) -> None:
    svc = _catalog(tmp_path)
    total = len(svc.list_hardware())
    assert len(svc.list_hardware(HardwareFilters(limit=1))) == min(total, 1)
    assert svc.list_hardware(HardwareFilters(offset=total)) == []


def test_list_defaults_are_not_shared() -> None:
    first = HardwareFilters()
    first.manufacturer_id.append("M-0001")
    assert HardwareFilters().manufacturer_id == []
