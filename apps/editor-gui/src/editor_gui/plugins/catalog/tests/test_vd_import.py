"""A .vd1-.vd5 product database is converted with the app's master data before it is imported."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import xknxeditor.prod.vd as vd
from editor_gui.plugins.catalog import service as catalog_service
from editor_gui.plugins.catalog.service import CatalogService
from xknxeditor.prod.errors import ArchiveError

from .test_delete_product import _fixture


def _vd_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Ets/ets.vd_", "EX-IM\r\n")
    return buffer.getvalue()


@pytest.fixture
def converted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bytes, bytes]]:
    """Replaces the conversion: records (database, master) and returns the fixture .knxprod."""
    calls: list[tuple[bytes, bytes]] = []

    def convert(source: bytes, master_xml: bytes) -> bytes:
        calls.append((source, master_xml))
        return _fixture().read_bytes()

    monkeypatch.setattr(vd, "vd_to_knxprod", convert)
    monkeypatch.setattr(
        "editor_gui.master_data.master_xml_bytes", lambda: b"<KNX master/>"
    )
    return calls


def test_only_a_product_database_is_converted(
    converted: list[tuple[bytes, bytes]],
) -> None:
    knxprod = _fixture().read_bytes()
    assert CatalogService.knxprod_bytes(knxprod) == knxprod
    assert CatalogService.knxprod_bytes(b"not a zip") == b"not a zip"
    assert CatalogService.knxprod_bytes(b"PK broken") == b"PK broken"
    assert converted == []
    assert CatalogService.knxprod_bytes(_vd_zip()) == knxprod
    assert converted == [(_vd_zip(), b"<KNX master/>")]


def test_missing_master_data_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("editor_gui.master_data.master_xml_bytes", lambda: None)
    with pytest.raises(ArchiveError, match="master data unavailable"):
        catalog_service._as_knxprod(_vd_zip())  # pyright: ignore[reportPrivateUsage]


def test_import_of_a_product_database_file(
    tmp_path: Path, converted: list[tuple[bytes, bytes]]
) -> None:
    source = tmp_path / "old.vd4"
    source.write_bytes(_vd_zip())
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    added = cat.import_knxprod(source)
    assert added and len(converted) == 1
    # Imported again through the contents path: the same versions, converted again.
    contents = cat.import_knxprod_contents(source)
    assert {p.product_ref_id for p in contents} == {p.product_ref_id for p in added}
    assert len(converted) == 2
    assert cat.import_knxprod_data(_fixture().read_bytes()) == []
