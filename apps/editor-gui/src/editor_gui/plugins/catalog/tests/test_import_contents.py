"""An import lists every product version of the file and tells which of them are new."""

from __future__ import annotations

import io
import zipfile
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


def test_contents_tell_the_share_parsed(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    shares: list[float] = []
    cat.import_knxprod_contents(_fixture(), parsed_share=shares.append)
    assert shares == sorted(set(shares))
    assert shares[-1] == 1.0


_HW = "M-0008_H-8.20.2F.2F.205192.2000-0-O000A"
_PROGRAM_2 = f"{_HW}_HP-7072-22-5CC3-O000A"
_PRODUCT_2 = f"{_HW}_P-5192.2001"


def _two_versions(tmp_path: Path) -> Path:
    """The fixture with a second version (another product on another hardware program), so it
    shares its first version with the fixture itself."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(_fixture()) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("/Hardware.xml"):
                data = data.replace(
                    b"</Products>",
                    f'<Product Id="{_PRODUCT_2}" Text="Two" OrderNumber="5192 01"'
                    ' IsRailMounted="false" DefaultLanguage="en-US" /></Products>'.encode(),
                ).replace(
                    b"</Hardware2Programs>",
                    f'<Hardware2Program Id="{_PROGRAM_2}" MediumTypes="MT-0">'
                    '<ApplicationProgramRef RefId="M-0008_A-7072-21-5CC3-O000A" />'
                    "</Hardware2Program></Hardware2Programs>".encode(),
                )
            elif name.endswith("/Catalog.xml"):
                data = data.replace(
                    b"</CatalogSection>",
                    f'<CatalogItem Id="{_PROGRAM_2}_CI-5192.2001-1" Name="Two" Number="1"'
                    f' ProductRefId="{_PRODUCT_2}" Hardware2ProgramRefId="{_PROGRAM_2}"'
                    ' DefaultLanguage="en-US" /></CatalogSection>'.encode(),
                    1,
                )
            out.writestr(name, data)
    path = tmp_path / "two.knxprod"
    path.write_bytes(buffer.getvalue())
    return path


def test_a_larger_file_lists_the_versions_the_catalog_has_from_another(
    tmp_path: Path,
) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod_contents(_fixture())

    larger = cat.import_knxprod_contents(_two_versions(tmp_path))
    assert len(larger.products) == 2
    assert {p.product_ref_id for p in larger.products if larger.is_new(p)} == {
        _PRODUCT_2
    }


def test_a_smaller_file_lists_its_versions_from_the_larger_one(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod_contents(_two_versions(tmp_path))

    smaller = cat.import_knxprod_contents(_fixture())
    assert len(smaller.products) == 1
    assert smaller.new == frozenset()


def test_a_deleted_version_is_imported_again(tmp_path: Path) -> None:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    source = _two_versions(tmp_path)
    cat.import_knxprod_contents(source)
    assert cat.delete_product(_PRODUCT_2, _PROGRAM_2) is not None

    again = cat.import_knxprod_contents(source)
    assert len(again.products) == 2
    assert {p.product_ref_id for p in again.products if again.is_new(p)} == {_PRODUCT_2}
