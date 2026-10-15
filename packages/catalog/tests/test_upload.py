"""Tests for the .knxprod ingest, especially dedup that must stay consistent with the DB.

Regression: the store used to skip ingest whenever the hash-named file existed, so deleting the
catalog DB while keeping the file cache left re-imports as no-ops (the DB stayed empty and every
project device was skipped). Dedup is now DB-driven.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.catalog import CatalogService
from xknxeditor.catalog.db import knxprod_dir_for
from xknxeditor.prod import parse_cache

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


def test_import_populates_catalog(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_FIXTURE.read_bytes())
    assert len(svc.list_products()) > 0


def test_import_fills_the_parse_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_FIXTURE.read_bytes())
    application_id = svc.list_products()[0].application_id
    assert application_id is not None

    def no_parse(*_args: object) -> None:
        raise AssertionError("the application was parsed again")

    # Loading the application afterwards reads the parse the import cached.
    monkeypatch.setattr(parse_cache, "to_ir", no_parse)
    app = svc.get_application(application_id)
    assert app is not None and app.id == application_id


def test_reimport_repopulates_after_db_reset(tmp_path: Path) -> None:
    # First import fills the DB and writes the file cache.
    content = _FIXTURE.read_bytes()
    db = tmp_path / "cat.xknxcatalog"
    first = CatalogService(db)
    first.import_knxprod(content)
    stored = list(knxprod_dir_for(db).glob("*.knxprod"))
    assert stored, "the .knxprod should have been cached to the store"

    # Simulate the user deleting only the catalog DB (the file cache survives). On Windows the
    # file can only be deleted once the service released it.
    first.close()
    db.unlink()
    svc = CatalogService(db)  # fresh, empty DB — same store dir, file still present
    assert svc.list_products() == []

    # Re-import must repopulate the DB even though the store file already exists.
    svc.import_knxprod(content)
    assert len(svc.list_products()) > 0


def test_reimport_rewrites_missing_store_file(tmp_path: Path) -> None:
    # Opposite drift: DB has the data but the cached file was deleted -> re-import restores it.
    content = _FIXTURE.read_bytes()
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    svc.import_knxprod(content)
    for f in knxprod_dir_for(db).glob("*.knxprod"):
        f.unlink()
    path = svc.import_knxprod(content)
    assert path.exists()


def _project_shaped(content: bytes) -> bytes:
    """Re-zip a .knxprod the way ETS embeds product data in a .knxproj: drop every ``Catalog.xml``
    AND the application XMLs, keeping ``Hardware.xml`` (whose programs still reference an application
    that is now absent). Exercises the missing-application-FK path."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content)) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for name in src.namelist():
            if name.endswith("/Catalog.xml"):
                continue
            if "_A-" in name and name.endswith(".xml"):  # an application program XML
                continue
            out.writestr(name, src.read(name))
    return buffer.getvalue()


def test_project_mode_ingest_tolerates_missing_application_xml(tmp_path: Path) -> None:
    """A .knxproj's Hardware.xml lists programs whose application XML is not embedded. Ingesting with
    require_catalog=False must still succeed: writing a HardwareProgram.application_id pointing at an
    absent application would violate the FK and roll back the whole ingest (every device unresolved).
    """
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_project_shaped(_FIXTURE.read_bytes()), require_catalog=False)
    present, programs = svc.manufacturer_catalog_status("M-0008")
    assert present and programs > 0  # hardware programs ingested, no IntegrityError


def test_project_mode_does_not_downgrade_existing_full_product(tmp_path: Path) -> None:
    """A full .knxprod (with Catalog.xml + baggages) imported first must NOT be downgraded when a
    leaner project archive (same program id, require_catalog=False) is imported afterwards: the
    program keeps pointing at the full archive so help/icons and the application stay resolvable."""
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    full = _FIXTURE.read_bytes()
    svc.import_knxprod(full)
    products = svc.list_products()
    assert products, "full import should list catalog products"
    program_id = products[0].hardware2program_ref_id
    full_source = svc.get_program_source(program_id)
    full_app = svc.get_program_application_id(program_id)
    assert full_source is not None and full_app is not None

    # Now import the same product in project-mode shape (no Catalog.xml / no app XMLs).
    svc.import_knxprod(_project_shaped(full), require_catalog=False)

    assert svc.get_program_source(program_id) == full_source  # source NOT downgraded
    assert (
        svc.get_program_application_id(program_id) == full_app
    )  # application NOT nulled
