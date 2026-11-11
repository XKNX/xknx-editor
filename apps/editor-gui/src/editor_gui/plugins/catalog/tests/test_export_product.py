"""The catalog keeps and exports a product's data without the project it was imported from."""

from __future__ import annotations

import hashlib
import io
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from editor_gui.plugins.catalog.plugin import CatalogPlugin
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.catalog.strings import S

_FIXTURE = (
    Path(__file__).parents[7]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


def _knxproj(*, catalog: bool = True) -> bytes:
    """The fixture's product data in a .knxproj, next to a project (as the editor exports one,
    with Catalog.xml, or as ETS does, without)."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(_FIXTURE) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for info in src.infolist():
            if catalog or not info.filename.endswith("/Catalog.xml"):
                out.writestr(info, src.read(info))
        for name in ("P-0001/project.xml", "P-0001/0.xml", "P-0002.zip"):
            out.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), b"project")
    return buffer.getvalue()


def _names(path: str | Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _source(cat: CatalogService) -> tuple[str, Path]:
    (product,) = cat.get_products()
    program = product.hardware2program_ref_id
    source = cat.get_program_source(program)
    assert source is not None
    return program, Path(source[0])


def test_a_project_import_keeps_no_project(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    assert cat.import_project_archive(_knxproj())
    _program, stored = _source(cat)
    assert _names(stored) == _names(_FIXTURE)


def test_the_same_project_again_adds_nothing(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    assert len(cat.import_project_archive(_knxproj())) == 1
    assert cat.import_project_archive(_knxproj()) == []
    # Also after a restart, which tidies the store.
    assert (
        CatalogService(tmp_path / "c.xknxcatalog").import_project_archive(_knxproj())
        == []
    )


def test_a_project_stored_by_an_older_version_is_stripped_at_start(
    tmp_path: Path,
) -> None:
    db = tmp_path / "c.xknxcatalog"
    cat = CatalogService(db)
    cat.import_project_archive(_knxproj())
    program, stored = _source(cat)
    # As an older version left it: the whole .knxproj in the store.
    whole = _knxproj()
    old = stored.with_name(f"{hashlib.sha3_256(whole).hexdigest()}.knxprod")
    old.write_bytes(whole)
    stored.unlink()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE hardware_programs SET knxprod_path = ?", (str(old),))
        conn.execute("DELETE FROM catalog_markers")

    started = CatalogService(db)
    source = started.get_program_source(program)
    assert source is not None and source[0] == str(stored)
    assert stored.exists() and old.exists()  # removed by a start an hour later
    assert _names(stored) == _names(_FIXTURE)


class _Refusals:
    def __init__(self) -> None:
        self.refused: list[str] = []
        self.notified: list[str] = []


def _export(cat: CatalogService, dest: Path) -> _Refusals:
    """Run the context menu's "Export .knxprod…" on the catalog's one product."""
    seen = _Refusals()
    plugin: Any = SimpleNamespace(
        _api=SimpleNamespace(catalog=cat, tell_refused=seen.refused.append),
        _log=SimpleNamespace(info=_ignore, warning=_ignore, error=_ignore),
        _notify=seen.notified.append,
    )
    (product,) = cat.get_products()
    CatalogPlugin._export_product(plugin, product, str(dest))  # pyright: ignore[reportPrivateUsage]
    return seen


def _ignore(*_args: object, **_kwargs: object) -> None:
    pass


def test_export_writes_the_product_without_the_project(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_project_archive(_knxproj())
    dest = tmp_path / "out.knxprod"
    seen = _export(cat, dest)
    assert seen.refused == []
    assert len(seen.notified) == 1
    assert _names(dest) == _names(_FIXTURE)

    again = CatalogService(tmp_path / "again.xknxcatalog")
    assert [p.hardware2program_ref_id for p in again.import_knxprod(dest)] == [
        p.hardware2program_ref_id for p in cat.get_products()
    ]


def test_export_refuses_product_data_it_cannot_make_a_knxprod_of(
    tmp_path: Path,
) -> None:
    full = CatalogService(tmp_path / "full.xknxcatalog")
    full.import_knxprod(_FIXTURE)
    (product,) = full.get_products()
    # The program's data comes from an ETS project now: no Catalog.xml.
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_project_archive(_knxproj(catalog=False))
    cat.get_products = lambda: [product]  # type: ignore[method-assign]

    dest = tmp_path / "out.knxprod"
    seen = _export(cat, dest)
    assert not dest.exists()
    assert seen.refused == [S.EXPORT_KNXPROD_NO_CATALOG.format(name=product.name)]


def test_export_of_a_product_without_stored_archive_is_refused(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_FIXTURE)
    (product,) = cat.get_products()
    cat.delete_product(product.product_ref_id)
    cat.get_products = lambda: [product]  # type: ignore[method-assign]

    seen = _export(cat, tmp_path / "out.knxprod")
    assert seen.refused == [
        S.EXPORT_KNXPROD_UNAVAILABLE.format(name=product.name or product.product_ref_id)
    ]
