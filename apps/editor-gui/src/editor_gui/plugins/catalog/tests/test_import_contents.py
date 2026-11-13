"""An import lists every product version of the file and tells which of them are new."""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugins.catalog.service import CatalogService

from .test_delete_product import _fixture


def test_contents_tell_the_new_versions(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")

    first = cat.import_knxprod_contents(_fixture())
    assert first.products
    assert all(first.is_new(product) for product in first.products)
    assert len(first.new) == len(first.products)

    # Imported again: the same versions are listed, none of them new.
    again = cat.import_knxprod_contents(_fixture())
    assert [p.product_ref_id for p in again.products] == [
        p.product_ref_id for p in first.products
    ]
    assert again.new == frozenset()
