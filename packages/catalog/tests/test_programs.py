"""A hardware program resolves to its application independently of catalog items."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from xknxeditor.catalog import CatalogService

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


def test_program_application_id_without_catalog_item(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    svc.import_knxprod(_FIXTURE.read_bytes())
    product = svc.list_products()[0]
    program = product.hardware2program_ref_id
    assert program is not None
    assert svc.get_program_application_id(program) == product.application_id

    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM catalog_section_products")
    assert svc.list_products() == []
    assert svc.get_program_application_id(program) == product.application_id
    assert svc.get_program_application_id("M-0000_H-unknown_HP-0000-00-0000") is None


def test_coupler_capabilities(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session as _Session

    from xknxeditor.catalog.models import HardwareProgramCouplerCapability

    service = CatalogService(tmp_path / "cat.xknxcatalog")
    service.import_knxprod(_FIXTURE.read_bytes())
    program = service.list_products()[0].hardware2program_ref_id
    assert program is not None
    assert service.coupler_capabilities(program) == set()
    with _Session(service._engine) as db:  # pyright: ignore[reportPrivateUsage]
        db.add(
            HardwareProgramCouplerCapability(
                hardware_program_id=program, capability="SecurityProxy"
            )
        )
        db.commit()
    assert service.coupler_capabilities(program) == {"SecurityProxy"}
