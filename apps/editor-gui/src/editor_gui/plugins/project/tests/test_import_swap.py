"""The temp-file import + atomic swap: a .knxproj is parsed/written into a sibling temp file, then
os.replace'd into place and opened, so we never rewrite the project file under an open SQLite
connection. Re-importing over the currently-open project used to raise "malformed database schema";
this covers that path end to end against the real fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService


def _fixture() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages/proj/tests/fixtures/xknx_test_project_no_password.knxproj"
        )
        if cand.exists():
            return cand
    return None


def _project(tmp_path: Path) -> ProjectService:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    return proj


def test_reimport_over_open_project_swaps_cleanly(tmp_path: Path) -> None:
    fx = _fixture()
    if fx is None:
        pytest.skip("knxproj fixture not available")
    proj = _project(tmp_path)
    dest = tmp_path / "p.xknx"

    proj.import_knxproj(fx, dest)
    assert proj.path == dest  # opened the imported project
    # Group addresses are project data (catalog-independent), so a real parse populates them
    # regardless of the device-view/catalog resolution.
    first_gas = len(proj.group_addresses)
    assert first_gas > 0

    # Re-import over the SAME open project file — the case that raced the schema DDL and crashed.
    proj.import_knxproj(fx, dest)
    assert len(proj.group_addresses) == first_gas

    # No temp artifact left behind, and the previously-crashing read paths work.
    assert not list(tmp_path.glob("*.import-*.tmp"))
    _ = proj.get_unassigned_devices()
    _ = proj.group_address_style


def test_import_leaves_no_temp_file(tmp_path: Path) -> None:
    fx = _fixture()
    if fx is None:
        pytest.skip("knxproj fixture not available")
    proj = _project(tmp_path)
    dest = tmp_path / "fresh.xknx"
    proj.import_knxproj(fx, dest)
    assert dest.exists()
    assert not list(tmp_path.glob("*.import-*.tmp"))


def test_import_resolves_embedded_products_without_catalog_xml(tmp_path: Path) -> None:
    """Regression for #52: a .knxproj embeds its product data WITHOUT a Catalog.xml (only
    Hardware.xml + application XMLs). The import must still ingest it so devices resolve their
    application - previously the strict archive validator rejected the whole set and every device
    showed 'product data missing'."""
    fx = _fixture()
    if fx is None:
        pytest.skip("knxproj fixture not available")
    proj = _project(tmp_path)
    proj.import_knxproj(fx, tmp_path / "p.xknx")
    assert proj.devices, "the embedded-product device should resolve"
    assert proj.unloaded_devices == []
    app = proj.devices[0].app
    assert app is not None
    assert app.id == "M-0002_A-A066-14-550B"


def test_resolve_app_falls_back_to_application_ref(tmp_path: Path) -> None:
    """When a device's hardware-program id is not in the catalog (old / reissued product ids), the
    application still resolves via the device's exact application-program ref (#51)."""
    fx = _fixture()
    if fx is None:
        pytest.skip("knxproj fixture not available")
    proj = _project(tmp_path)
    proj.import_knxproj(fx, tmp_path / "p.xknx")
    app = proj.devices[0].app
    assert app is not None
    app_id = app.id  # ingested application id (e.g. M-0002_A-A066-14-550B)

    # Unknown hardware program + the exact application ref -> resolves via the fallback.
    assert proj._resolve_app("M-9999_H-x_HP-x", app_id) is not None
    # Unknown hardware program + no application ref -> unresolved.
    assert proj._resolve_app("M-9999_H-x_HP-x", None) is None
