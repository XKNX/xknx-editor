"""Importing a file again: skipped only when the catalog has everything of it, and listing every
version the file provides, whichever file the catalog has it from."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from xknxeditor.catalog import CatalogService, ProductSummary
from xknxeditor.catalog.core import upload
from xknxeditor.catalog.core.archive_versions import archive_versions

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)

_HW = "M-0008_H-8.20.2F.2F.205192.2000-0-O000A"
_PROGRAM = f"{_HW}_HP-7072-21-5CC3-O000A"
_PRODUCT = f"{_HW}_P-5192.2000"
_ITEM = f"{_PROGRAM}_CI-5192.2000-1"
# The second version two_versions() adds: another product on another hardware program.
_PROGRAM_2 = f"{_HW}_HP-7072-22-5CC3-O000A"
_PRODUCT_2 = f"{_HW}_P-5192.2001"
_ITEM_2 = f"{_PROGRAM_2}_CI-5192.2001-1"


def two_versions(content: bytes) -> bytes:
    """The Gira fixture with a second version: product 5192 01 on its own hardware program (with
    the same application)."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content)) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("/Hardware.xml"):
                data = data.replace(
                    b"</Products>",
                    f'<Product Id="{_PRODUCT_2}" Text="Button interface 2" OrderNumber="5192 01"'
                    ' IsRailMounted="false" DefaultLanguage="en-US" /></Products>'.encode(),
                ).replace(
                    b"</Hardware2Programs>",
                    f'<Hardware2Program Id="{_PROGRAM_2}" MediumTypes="MT-0">'
                    '<ApplicationProgramRef RefId="M-0008_A-7072-21-5CC3-O000A" />'
                    "</Hardware2Program></Hardware2Programs>".encode(),
                )
            elif name.endswith("/Catalog.xml"):
                # Into the inner section, the first one to close.
                data = data.replace(
                    b"</CatalogSection>",
                    f'<CatalogItem Id="{_ITEM_2}" Name="Button interface 2" Number="1"'
                    f' ProductRefId="{_PRODUCT_2}" Hardware2ProgramRefId="{_PROGRAM_2}"'
                    ' DefaultLanguage="en-US" /></CatalogSection>'.encode(),
                    1,
                )
            out.writestr(name, data)
    return buffer.getvalue()


def _versions(products: list[ProductSummary]) -> set[tuple[str, str]]:
    return {(p.product_ref_id, p.hardware2program_ref_id) for p in products}


def test_the_versions_of_an_archive_are_read_from_its_lists() -> None:
    provided = archive_versions(two_versions(_FIXTURE.read_bytes()))
    assert provided.items == {_ITEM, _ITEM_2}
    assert provided.programs == {_PROGRAM, _PROGRAM_2}
    assert provided.versions == {(_PRODUCT, _PROGRAM), (_PRODUCT_2, _PROGRAM_2)}


def test_a_file_the_catalog_has_completely_is_not_loaded_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    content = two_versions(_FIXTURE.read_bytes())
    stored = svc.import_knxprod(content)

    def no_load(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the archive was loaded again")

    monkeypatch.setattr(upload, "load", no_load)
    assert svc.import_knxprod(content) == stored
    assert _versions(svc.products_for_archive(content)) == {
        (_PRODUCT, _PROGRAM),
        (_PRODUCT_2, _PROGRAM_2),
    }


def test_a_deleted_version_comes_back_from_the_same_file(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    content = two_versions(_FIXTURE.read_bytes())
    stored = svc.import_knxprod(content)
    deletion = svc.delete_product(_PRODUCT_2, _PROGRAM_2)
    assert deletion is not None and deletion.removed_programs == [_PROGRAM_2]
    # The other version still reads its data from the stored archive.
    assert stored.exists()
    assert _versions(svc.list_products()) == {(_PRODUCT, _PROGRAM)}

    assert svc.import_knxprod(content) == stored
    assert _versions(svc.list_products()) == {
        (_PRODUCT, _PROGRAM),
        (_PRODUCT_2, _PROGRAM_2),
    }
    assert svc.get_program_source(_PROGRAM_2) == (str(stored), "M-0008")


def test_versions_from_another_file_are_listed(tmp_path: Path) -> None:
    small = _FIXTURE.read_bytes()
    large = two_versions(small)
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    small_stored = svc.import_knxprod(small)
    svc.import_knxprod(large)
    # The shared version stays with the file it came from, and is listed for both files.
    assert svc.get_program_source(_PROGRAM) == (str(small_stored), "M-0008")
    assert _versions(svc.products_for_archive(large)) == {
        (_PRODUCT, _PROGRAM),
        (_PRODUCT_2, _PROGRAM_2),
    }
    assert _versions(svc.products_for_archive(small)) == {(_PRODUCT, _PROGRAM)}


def test_the_smaller_file_after_the_larger_one_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = _FIXTURE.read_bytes()
    large = two_versions(small)
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    large_stored = svc.import_knxprod(large)

    def no_load(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the archive was loaded")

    monkeypatch.setattr(upload, "load", no_load)
    small_stored = svc.import_knxprod(small)
    # Nothing refers to the smaller file, so it is not stored.
    assert not small_stored.exists()
    assert svc.get_program_source(_PROGRAM) == (str(large_stored), "M-0008")
    assert _versions(svc.products_for_archive(small)) == {(_PRODUCT, _PROGRAM)}
