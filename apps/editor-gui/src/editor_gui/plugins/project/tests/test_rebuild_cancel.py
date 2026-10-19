"""A cancelled device rebuild (a language switch) keeps the devices built before."""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService

_APP_ID = "M-0008_A-7072-21-5CC3-O000A"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


def _project_with_device(tmp_path: Path) -> ProjectService:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    (product,) = cat.get_products()
    app = cat.get_application(_APP_ID)
    assert app is not None
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    assert proj.add_device(
        product.product_ref_id, product.hardware2program_ref_id, "Button", app
    )
    assert len(proj.devices) == 1
    return proj


def test_cancelled_rebuild_keeps_the_devices(tmp_path: Path) -> None:
    proj = _project_with_device(tmp_path)
    (before,) = proj.devices

    assert not proj.refresh_catalog_resolution(rebuild=True, cancelled=lambda: True)
    assert proj.devices == [before]
    assert proj.devices[0] is before

    assert proj.refresh_catalog_resolution(rebuild=True, cancelled=lambda: False)
    (after,) = proj.devices
    assert after is not before
    assert after.node_id == before.node_id
    proj.close()
