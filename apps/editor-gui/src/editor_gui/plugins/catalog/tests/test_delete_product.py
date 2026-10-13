"""Deleting a local catalog product counts and unloads the open project's devices that use it."""

from __future__ import annotations

import sqlite3
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


def _project_with_device(tmp_path: Path) -> tuple[CatalogService, ProjectService, str]:
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
    return cat, proj, product.product_ref_id


def test_delete_unloads_project_device_until_reimport(tmp_path: Path) -> None:
    cat, proj, ref = _project_with_device(tmp_path)
    plan = cat.plan_delete_product(ref)
    assert plan is not None
    assert proj.count_devices_losing_product(plan) == 1

    deletion = cat.delete_product(ref)
    assert deletion == plan
    proj.forget_deleted_product(deletion, rebuild=True)
    assert cat.get_products() == []
    assert proj.devices == []
    (unloaded,) = proj.unloaded_devices
    assert proj.missing_program_refs() == [unloaded.program_ref]

    cat.import_knxprod(_fixture())
    proj.refresh_catalog_resolution(rebuild=True)
    assert len(proj.devices) == 1
    assert proj.unloaded_devices == []
    proj.close()


def test_shared_program_keeps_project_device(tmp_path: Path) -> None:
    cat, proj, ref = _project_with_device(tmp_path)
    (product,) = cat.get_products()
    with sqlite3.connect(tmp_path / "c.xknxcatalog") as db:
        db.execute(
            "INSERT INTO catalog_section_products"
            " (id, section_id, hardware_program_id, product_ref_id, name)"
            " SELECT id || '-2', section_id, hardware_program_id, ?, name"
            " FROM catalog_section_products",
            (f"{ref}-2",),
        )
    cat.refresh()

    plan = cat.plan_delete_product(ref)
    assert plan is not None and plan.removed_programs == []
    assert proj.count_devices_losing_product(plan) == 0

    deletion = cat.delete_product(ref)
    assert deletion is not None
    proj.forget_deleted_product(deletion, rebuild=True)
    assert [p.product_ref_id for p in cat.get_products()] == [f"{ref}-2"]
    (device,) = proj.devices
    assert device.app.id == product.application_id
    assert proj.unloaded_devices == []
    proj.close()


def test_count_without_open_project_is_zero(tmp_path: Path) -> None:
    cat, proj, ref = _project_with_device(tmp_path)
    proj.close()
    plan = cat.plan_delete_product(ref)
    assert plan is not None
    assert proj.count_devices_losing_product(plan) == 0
