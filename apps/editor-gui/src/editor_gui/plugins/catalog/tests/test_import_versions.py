"""An import reports each product version new in the catalog, not only new products."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from editor_gui.plugins.catalog.service import CatalogService

from .test_delete_product import _fixture

_HW = "M-0008_H-8.20.2F.2F.205192.2000-0-O000A"
_PRODUCT = f"{_HW}_P-5192.2000"
_PROGRAM = f"{_HW}_HP-7072-22-AAAA-O000A"
_APP = "M-0008_A-7072-22-AAAA-O000A"


def _add_version(db: Path) -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO applications (id, name, application_number, application_version)"
            " VALUES (?, 'App', 28786, 34)",
            (_APP,),
        )
        con.execute(
            "INSERT INTO hardware_programs (id, hardware_id, application_id, knxprod_path)"
            " VALUES (?, ?, ?, '')",
            (_PROGRAM, _HW, _APP),
        )
        section = con.execute(
            "SELECT section_id FROM catalog_section_products LIMIT 1"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO catalog_section_products"
            " (id, section_id, hardware_program_id, product_ref_id, name)"
            " VALUES (?, ?, ?, ?, 'Button')",
            (f"{_PROGRAM}_CI-1", section, _PROGRAM, _PRODUCT),
        )


def test_new_version_of_a_known_product_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "c.xknxcatalog"
    cat = CatalogService(db)
    (first,) = cat.import_knxprod(_fixture())
    assert first.product_ref_id == _PRODUCT
    assert cat.import_knxprod(_fixture()) == []

    monkeypatch.setattr(
        cat._service,  # pyright: ignore[reportPrivateUsage]
        "import_knxprod",
        lambda *args, **kwargs: _add_version(db),
    )
    (added,) = cat.import_knxprod(_fixture())
    assert (added.product_ref_id, added.hardware2program_ref_id) == (
        _PRODUCT,
        _PROGRAM,
    )
    assert added.application_version == 34
    assert len(cat.get_products()) == 2
