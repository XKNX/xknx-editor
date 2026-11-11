"""The catalog stores and exports only product data, never the project a ``.knxproj`` carries."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from xknxeditor.catalog import CatalogService
from xknxeditor.catalog.core import upload
from xknxeditor.catalog.core.product_members import (
    NoCatalogError,
    has_other_members,
    is_product_member,
    product_members,
)
from xknxeditor.catalog.core.upload import StoreTidying
from xknxeditor.catalog.db import knxprod_dir_for
from xknxeditor.catalog.models import CatalogMarker
from xknxeditor.prod.errors import ArchiveError

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)
_APP_ID = "M-0008_A-7072-21-5CC3-O000A"
# What a .knxproj holds besides the product data: the project (open, or a password-protected
# P-XXXX.zip) and files of its own.
_PROJECT_FILES = {
    "P-0001/project.xml": b"<KNX><Project Id='P-0001'/></KNX>",
    "P-0001/0.xml": b"<KNX><GroupAddress Name='Kitchen light'/></KNX>",
    "P-0001/UserFiles/notes.txt": b"secret",
    "P-0002.zip": b"PK encrypted project",
    "P-0002.signature": b"sig",
    "[Content_Types].xml": b"<Types/>",
}


def _rezip(content: bytes, add: dict[str, bytes], drop: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(content)) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for name in src.namelist():
            if not name.endswith(drop):
                out.writestr(src.getinfo(name), src.read(name))
        for name, data in add.items():
            # A fixed date: the same archive each time, as a file on disk is.
            out.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()


def _knxproj() -> bytes:
    return _rezip(_FIXTURE.read_bytes(), _PROJECT_FILES)


def _names(path: str | Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _program_id(svc: CatalogService) -> str:
    (product,) = svc.list_products()
    return product.hardware2program_ref_id


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("knx_master.xml", True),
        ("M-0008/Hardware.xml", True),
        ("M-0008/Baggages/x.png", True),
        ("M-0008/", True),
        ("M-00fa.signature", True),
        ("P-0001/project.xml", False),
        ("P-0001.zip", False),
        ("P-0001.signature", False),
        ("[Content_Types].xml", False),
        ("M-0008", False),
        ("M-08/Hardware.xml", False),
        ("sub/knx_master.xml", False),
    ],
)
def test_product_members_are_master_manufacturer_folders_and_signatures(
    name: str, expected: bool
) -> None:
    assert is_product_member(name) is expected


def test_only_the_given_manufacturers_are_members() -> None:
    assert is_product_member("M-0008/Hardware.xml", {"M-0008"})
    assert is_product_member("knx_master.xml", {"M-0008"})
    assert not is_product_member("M-0083/Hardware.xml", {"M-0008"})
    assert not is_product_member("M-0083.signature", {"M-0008"})


def test_a_knxprod_keeps_its_bytes() -> None:
    content = _FIXTURE.read_bytes()
    assert product_members(content) is content


def test_a_project_is_left_out_the_same_way_each_time() -> None:
    knxproj = _knxproj()
    stripped = product_members(knxproj)
    assert _names_of(stripped) == _names(_FIXTURE)
    assert product_members(knxproj) == stripped
    with (
        zipfile.ZipFile(io.BytesIO(stripped)) as out,
        zipfile.ZipFile(_FIXTURE) as original,
    ):
        for info in original.infolist():
            kept = out.getinfo(info.filename)
            assert kept.date_time == info.date_time
            assert kept.compress_type == info.compress_type
            assert out.read(info.filename) == original.read(info.filename)


def _names_of(content: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return set(archive.namelist())


class _Unseekable(io.RawIOBase):
    """A stream zipfile cannot seek in, so it writes a data descriptor after each member."""

    def __init__(self) -> None:
        self.data = io.BytesIO()

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        return self.data.write(b)


def test_members_are_copied_without_compressing_them_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knxproj = _knxproj()
    expected = product_members(knxproj)

    def no_compression(*_args: object) -> None:
        raise AssertionError("a member was compressed again")

    monkeypatch.setattr(zlib, "compressobj", no_compression)
    # So the stored name does not depend on the zlib this Python has.
    assert product_members(knxproj) == expected
    with zipfile.ZipFile(io.BytesIO(expected)) as out, zipfile.ZipFile(_FIXTURE) as src:
        for info in src.infolist():
            assert out.read(info.filename) == src.read(info.filename)


def test_members_with_a_data_descriptor_are_copied() -> None:
    stream = _Unseekable()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("knx_master.xml", b"<KNX/>" * 100)
        archive.writestr("P-0001/0.xml", b"project")
        archive.writestr("M-0008/Hardware.xml", b"<Hardware/>" * 100)
    with zipfile.ZipFile(io.BytesIO(stream.data.getvalue())) as written:
        assert all(i.flag_bits & 0x08 for i in written.infolist())

    stripped = product_members(stream.data.getvalue())
    with zipfile.ZipFile(io.BytesIO(stripped)) as out:
        assert out.namelist() == ["knx_master.xml", "M-0008/Hardware.xml"]
        assert out.testzip() is None
        assert out.read("M-0008/Hardware.xml") == b"<Hardware/>" * 100


def _segments(content: bytes) -> list[bytes]:
    """The bytes of each member as stored: from its local header to the next member's."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        offsets = [info.header_offset for info in archive.infolist()]
        directory = archive.start_dir
    ends = [*offsets[1:], directory]
    return [content[start:end] for start, end in zip(offsets, ends, strict=True)]


def test_members_with_a_zip64_data_descriptor_are_copied_whole() -> None:
    stream = _Unseekable()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("knx_master.xml", "M-0008/Hardware.xml", "P-0001/0.xml"):
            with archive.open(name, "w", force_zip64=True) as member:
                member.write(name.encode() * 50)
    source = stream.data.getvalue()

    stripped = product_members(source)
    # The kept members are byte for byte what the source holds, descriptors included.
    assert _segments(stripped) == _segments(source)[:2]


def test_a_project_import_stores_only_the_product_data(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    stored = svc.import_knxprod(_knxproj(), require_catalog=False)

    assert _names(stored) == _names(_FIXTURE)
    assert not has_other_members(str(stored))
    assert stored.name == f"{hashlib.sha3_256(stored.read_bytes()).hexdigest()}.knxprod"
    assert svc.get_program_source(_program_id(svc)) == (str(stored), "M-0008")

    # The same project again is recognised by what is stored: nothing new.
    assert svc.import_knxprod(_knxproj(), require_catalog=False) == stored
    assert list(knxprod_dir_for(tmp_path / "cat.xknxcatalog").iterdir()) == [stored]


def test_a_project_of_known_programs_stores_no_file(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    # The project's programs are in the catalog already, from the .knxprod: they keep it.
    svc.import_knxprod(_knxproj(), require_catalog=False)
    assert list(knxprod_dir_for(tmp_path / "cat.xknxcatalog").iterdir()) == [stored]
    assert svc.get_program_source(_program_id(svc)) == (str(stored), "M-0008")


def test_a_stored_file_cut_short_is_written_again(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    # As an older version left it when it was stopped while writing the file in place.
    stored.write_bytes(_FIXTURE.read_bytes()[:1000])
    assert svc.import_knxprod(_FIXTURE.read_bytes()) == stored
    assert stored.read_bytes() == _FIXTURE.read_bytes()


def test_an_interrupted_write_leaves_no_file_under_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    store = knxprod_dir_for(tmp_path / "cat.xknxcatalog")

    def killed(*_args: object) -> None:
        raise OSError("killed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", killed)
        with pytest.raises(OSError, match="killed"):
            svc.import_knxprod(_FIXTURE.read_bytes())
    assert [f.suffix for f in store.iterdir()] == [".tmp"]
    assert svc.list_products() == []  # nothing committed

    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    assert stored.read_bytes() == _FIXTURE.read_bytes()


def _store_whole(db: Path, svc: CatalogService, knxproj: bytes) -> Path:
    """Make the catalog look like one an older version filled: the .knxproj stored whole."""
    stored = svc.import_knxprod(knxproj, require_catalog=False)
    old = stored.with_name(f"{hashlib.sha3_256(knxproj).hexdigest()}.knxprod")
    old.write_bytes(knxproj)
    stored.unlink()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE hardware_programs SET knxprod_path = ?", (str(old),))
        conn.execute("DELETE FROM catalog_markers")
    return old


def test_a_stored_project_is_rewritten_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    old = _store_whole(db, svc, _knxproj())
    program = _program_id(svc)
    assert svc.get_program_source(program) == (str(old), "M-0008")

    assert svc.tidy_store() == StoreTidying(rewritten=1, removed=0)

    source = svc.get_program_source(program)
    assert source is not None
    new = Path(source[0])
    assert old.exists()  # dated now: an editor running already may still read it
    assert _names(new) == _names(_FIXTURE)
    assert new.name == f"{hashlib.sha3_256(new.read_bytes()).hexdigest()}.knxprod"
    assert svc.get_application(_APP_ID) is not None

    # Marked as done: later starts do not open the stored archives again.
    def opened(_path: str) -> bool:
        raise AssertionError("a stored archive was opened again")

    monkeypatch.setattr(upload, "has_other_members", opened)
    assert svc.tidy_store() == StoreTidying()
    # A start an hour later removes the old file, as nothing refers to it.
    assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying(removed=1)
    assert not old.exists()
    # The project imported again is recognised.
    assert svc.import_knxprod(_knxproj(), require_catalog=False) == new


def test_an_interrupted_rewrite_loses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    old = _store_whole(db, svc, _knxproj())
    program = _program_id(svc)

    def failed(_self: Session) -> None:
        raise OperationalError("COMMIT", None, Exception("database is locked"))

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", failed)
        assert svc.tidy_store() == StoreTidying()
    # The programs still read the intact old file; the new one is complete but unused.
    assert svc.get_program_source(program) == (str(old), "M-0008")
    assert old.read_bytes() == _knxproj()

    # Not marked as done: the next start completes it.
    assert svc.tidy_store() == StoreTidying(rewritten=1, removed=0)
    source = svc.get_program_source(program)
    assert source is not None and source[0] != str(old)
    assert old.exists()
    assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying(removed=1)


def test_two_editors_tidying_at_once_do_no_harm(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    first = CatalogService(db)
    old = _store_whole(db, first, _knxproj())
    second = CatalogService(db)
    program = _program_id(first)
    # Both see the rewrite to do; one completes it and marks it, the other then runs it again.
    real_get = Session.get

    def unmarked(self: Session, entity: Any, ident: Any, **kw: Any) -> Any:
        if entity is CatalogMarker:
            return None
        return real_get(self, entity, ident, **kw)

    assert first.tidy_store() == StoreTidying(rewritten=1, removed=0)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Session, "get", unmarked)
        assert second.tidy_store() == StoreTidying()
    source = second.get_program_source(program)
    assert source is not None and source[0] != str(old)
    assert Path(source[0]).exists() and old.exists()


def test_a_locked_catalog_is_tidied_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    old = _store_whole(db, svc, _knxproj())
    stray = knxprod_dir_for(db) / "0123.knxprod"
    stray.write_bytes(b"data")

    def locked(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT", None, Exception("database is locked"))

    with monkeypatch.context() as patch:
        patch.setattr(Session, "get", locked)
        assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying()
    assert old.exists() and stray.exists()  # nothing removed without the references

    assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying(
        rewritten=1, removed=2
    )
    assert not old.exists() and not stray.exists()


def test_files_outside_the_store_are_never_removed(tmp_path: Path) -> None:
    # A catalog copied with its store still names the files of the original.
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    stored = _store_whole(db, svc, _knxproj())
    original = tmp_path / "original" / stored.name
    original.parent.mkdir()
    stored.rename(original)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE hardware_programs SET knxprod_path = ?", (str(original),))

    assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying(rewritten=1)
    source = svc.get_program_source(_program_id(svc))
    assert source is not None and Path(source[0]).parent == knxprod_dir_for(db)
    assert original.read_bytes() == _knxproj()


def test_a_reused_store_file_is_dated_now(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    os.utime(stored, (0, 0))
    # So a tidying that starts now does not take it for an old unreferenced file.
    upload.write_stored(stored, _FIXTURE.read_bytes())
    assert stored.stat().st_mtime > time.time() - 60


def test_an_archive_broken_in_another_way_does_not_stop_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    old = _store_whole(db, svc, _knxproj())

    def broken(_path: str) -> bool:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(upload, "has_other_members", broken)
    assert svc.tidy_store() == StoreTidying()
    assert svc.get_program_source(_program_id(svc)) == (str(old), "M-0008")

    # Nothing to rewrite there: done.
    def opened(_path: str) -> bool:
        raise AssertionError("a stored archive was opened again")

    monkeypatch.setattr(upload, "has_other_members", opened)
    assert svc.tidy_store() == StoreTidying()


def test_a_store_that_cannot_be_listed_does_not_stop_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_FIXTURE.read_bytes())

    def denied(_self: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", denied)
    assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying()


def test_an_import_of_no_zip_is_an_archive_error(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    with pytest.raises(ArchiveError, match="Invalid ZIP archive"):
        svc.import_knxprod(b"not a zip")


def test_an_unreadable_stored_archive_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    old = _store_whole(db, svc, _knxproj())
    old.write_bytes(b"not a zip")
    assert svc.tidy_store() == StoreTidying()
    assert svc.get_program_source(_program_id(svc)) == (str(old), "M-0008")
    assert old.read_bytes() == b"not a zip"

    # Nothing to rewrite there: done, it is not opened at every start.
    def opened(_path: str) -> bool:
        raise AssertionError("a stored archive was opened again")

    monkeypatch.setattr(upload, "has_other_members", opened)
    assert svc.tidy_store() == StoreTidying()


def test_a_missing_stored_archive_does_not_stop_the_rewrite(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    old = _store_whole(db, svc, _knxproj())
    old.unlink()
    assert svc.tidy_store() == StoreTidying()
    assert svc.get_program_source(_program_id(svc)) == (str(old), "M-0008")


def test_unreferenced_files_are_removed_once_old(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    store = stored.parent
    stray = store / "0123.knxprod"  # e.g. another editor's import, not committed yet
    stray.write_bytes(b"data")
    partial = store / f"{stored.name}.4711.tmp"
    partial.write_bytes(b"part")
    other = store / "notes.txt"
    other.write_bytes(b"kept")

    assert svc.tidy_store(now=time.time()) == StoreTidying()
    assert stray.exists() and partial.exists()

    assert svc.tidy_store(now=time.time() + 2 * 3600) == StoreTidying(removed=2)
    assert sorted(f.name for f in store.iterdir()) == sorted([stored.name, "notes.txt"])


def test_the_export_is_a_knxprod_of_the_products_manufacturers(tmp_path: Path) -> None:
    # A bundle with a second manufacturer the product does not use.
    bundle = _rezip(
        _FIXTURE.read_bytes(),
        {"M-00FA/Baggages/other.png": b"png", "M-00FA.signature": b"sig"},
    )
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(bundle)
    program = _program_id(svc)

    exported = svc.product_archive(program)
    assert exported is not None
    assert _names_of(exported) == _names(_FIXTURE)

    again = CatalogService(tmp_path / "again.xknxcatalog")
    again.import_knxprod(exported)
    assert [p.hardware2program_ref_id for p in again.list_products()] == [program]
    assert again.get_application(_APP_ID) is not None


def test_a_product_from_a_project_exports_as_a_knxprod(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_knxproj(), require_catalog=False)
    program = _program_id(svc)

    exported = svc.product_archive(program)
    assert exported is not None
    assert not any(name.startswith("P-") for name in _names_of(exported))
    # Imports again as a regular .knxprod, where Catalog.xml is required.
    again = CatalogService(tmp_path / "again.xknxcatalog")
    again.import_knxprod(exported)
    assert [p.hardware2program_ref_id for p in again.list_products()] == [program]


def test_a_knxprod_bundle_of_one_manufacturer_exports_unchanged(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(_FIXTURE.read_bytes())
    assert svc.product_archive(_program_id(svc)) == _FIXTURE.read_bytes()
    assert svc.product_archive("M-0000_H-x_HP-0000-00-0000") is None


def test_product_data_without_catalog_is_not_exported(tmp_path: Path) -> None:
    full = CatalogService(tmp_path / "full.xknxcatalog")
    full.import_knxprod(_FIXTURE.read_bytes())
    program = _program_id(full)

    # Product data from a project without Catalog.xml: a .knxprod import would refuse it.
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    svc.import_knxprod(
        _rezip(_knxproj(), {}, drop=("/Catalog.xml",)), require_catalog=False
    )
    with pytest.raises(NoCatalogError) as refused:
        svc.product_archive(program)
    assert refused.value.manufacturer_id == "M-0008"
    assert isinstance(refused.value, ArchiveError)


def test_a_missing_stored_archive_is_not_exported(tmp_path: Path) -> None:
    svc = CatalogService(tmp_path / "cat.xknxcatalog")
    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    stored.unlink()
    with pytest.raises(OSError):
        svc.product_archive(_program_id(svc))
