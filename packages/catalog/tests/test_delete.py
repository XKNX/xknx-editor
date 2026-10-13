"""Deleting a product removes its catalog items and garbage-collects what is left unreferenced."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from xknxeditor.catalog import CatalogService
from xknxeditor.catalog.db import knxprod_dir_for

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)

_MFR = "M-0008"
_HW = "M-0008_H-8.20.2F.2F.205192.2000-0-O000A"
_PROGRAM = f"{_HW}_HP-7072-21-5CC3-O000A"
_APP = "M-0008_A-7072-21-5CC3-O000A"
_PRODUCT = f"{_HW}_P-5192.2000"
_LEAF = "M-0008_CS-IN-B2"
_ROOT = "M-0008_CS-IN"

_TABLES = (
    "manufacturers",
    "applications",
    "hardware",
    "hardware_programs",
    "hardware_program_medium_types",
    "catalog_sections",
    "catalog_section_products",
)


def _setup(tmp_path: Path) -> tuple[CatalogService, Path, Path]:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    stored = svc.import_knxprod(_FIXTURE.read_bytes())
    return svc, db, stored


def _ids(db: Path, table: str, column: str = "id") -> set[str]:
    with sqlite3.connect(db) as con:
        return {row[0] for row in con.execute(f"SELECT {column} FROM {table}")}


def _snapshot(db: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(db) as con:
        return {
            t: sorted(con.execute(f"SELECT * FROM {t}").fetchall(), key=repr)
            for t in _TABLES
        }


def _add_product(
    db: Path,
    *,
    product_ref: str,
    program: str,
    hardware: str,
    manufacturer: str = _MFR,
    application: str | None = None,
    section: str = _LEAF,
    parent_section: str | None = None,
    knxprod_path: str = "",
    with_item: bool = True,
) -> None:
    """Insert a synthetic product (and any rows it needs that are not there yet)."""
    with sqlite3.connect(db) as con:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute(
            "INSERT OR IGNORE INTO manufacturers (id, name) VALUES (?, ?)",
            (manufacturer, manufacturer),
        )
        if application is not None:
            con.execute(
                "INSERT OR IGNORE INTO applications (id, name) VALUES (?, ?)",
                (application, application),
            )
        con.execute(
            "INSERT OR IGNORE INTO hardware (id, manufacturer_id, name) VALUES (?, ?, ?)",
            (hardware, manufacturer, hardware),
        )
        con.execute(
            "INSERT OR IGNORE INTO hardware_programs"
            " (id, hardware_id, application_id, knxprod_path) VALUES (?, ?, ?, ?)",
            (program, hardware, application, knxprod_path),
        )
        con.execute(
            "INSERT OR IGNORE INTO hardware_program_medium_types"
            " (hardware_program_id, medium_type) VALUES (?, 'MT-0')",
            (program,),
        )
        if not with_item:
            return
        con.execute(
            "INSERT OR IGNORE INTO catalog_sections"
            " (id, manufacturer_id, parent_id, name) VALUES (?, ?, ?, ?)",
            (section, manufacturer, parent_section, section),
        )
        con.execute(
            "INSERT INTO catalog_section_products"
            " (id, section_id, hardware_program_id, product_ref_id, name)"
            " VALUES (?, ?, ?, ?, ?)",
            (f"{program}_CI-{product_ref}", section, program, product_ref, product_ref),
        )


def _freelist(db: Path) -> int:
    with sqlite3.connect(db) as con:
        return con.execute("PRAGMA freelist_count").fetchone()[0]


def test_delete_last_product_empties_catalog(tmp_path: Path) -> None:
    svc, db, stored = _setup(tmp_path)
    assert svc.get_application(_APP) is not None  # fills the parse cache
    cache_dir = stored.parent.parent / "ir_cache"
    assert list(cache_dir.glob("ir_*.pkl"))

    deletion = svc.delete_product(_PRODUCT)

    assert deletion is not None
    assert deletion.removed_programs == [_PROGRAM]
    assert deletion.program_applications == {_PROGRAM: _APP}
    assert deletion.removed_applications == [_APP]
    assert deletion.removed_hardware == [_HW]
    assert deletion.removed_sections == [_LEAF, _ROOT]
    assert deletion.removed_manufacturers == [_MFR]
    assert deletion.removed_files == [str(stored)]
    assert all(rows == [] for rows in _snapshot(db).values())
    assert not stored.exists()
    assert list(cache_dir.glob("ir_*.pkl")) == []
    assert svc.list_products() == []
    assert svc.get_application(_APP) is None
    assert _freelist(db) == 0  # vacuumed


def test_unknown_product_changes_nothing(tmp_path: Path) -> None:
    svc, db, stored = _setup(tmp_path)
    before = _snapshot(db)
    assert svc.plan_product_deletion("M-0008_H-x_P-unknown") is None
    assert svc.delete_product("M-0008_H-x_P-unknown") is None
    assert _snapshot(db) == before
    assert stored.exists()


def test_plan_matches_delete_and_changes_nothing(tmp_path: Path) -> None:
    svc, db, stored = _setup(tmp_path)
    before = _snapshot(db)
    plan = svc.plan_product_deletion(_PRODUCT)
    assert _snapshot(db) == before
    assert stored.exists()
    assert plan == svc.delete_product(_PRODUCT)


def test_shared_program_is_kept(tmp_path: Path) -> None:
    svc, db, stored = _setup(tmp_path)
    other = f"{_HW}_P-second"
    _add_product(db, product_ref=other, program=_PROGRAM, hardware=_HW)

    deletion = svc.delete_product(_PRODUCT)

    assert deletion is not None
    assert deletion.removed_programs == []
    assert deletion.removed_applications == []
    assert deletion.removed_hardware == []
    assert deletion.removed_sections == []
    assert deletion.removed_manufacturers == []
    assert deletion.removed_files == []
    assert [p.product_ref_id for p in svc.list_products()] == [other]
    assert _ids(db, "hardware_programs") == {_PROGRAM}
    assert _ids(db, "hardware_program_medium_types", "hardware_program_id") == {
        _PROGRAM
    }
    assert stored.exists()
    assert svc.get_application(_APP) is not None

    deletion = svc.delete_product(other)
    assert deletion is not None and deletion.removed_programs == [_PROGRAM]
    assert all(rows == [] for rows in _snapshot(db).values())
    assert not stored.exists()


def test_one_version_of_a_product_is_deleted(tmp_path: Path) -> None:
    svc, db, stored = _setup(tmp_path)
    program = f"{_HW}_HP-7072-22-AAAA-O000A"
    app = "M-0008_A-7072-22-AAAA-O000A"
    _add_product(
        db, product_ref=_PRODUCT, program=program, hardware=_HW, application=app
    )
    assert {p.hardware2program_ref_id for p in svc.list_products()} == {
        _PROGRAM,
        program,
    }

    plan = svc.plan_product_deletion(_PRODUCT, program)
    deletion = svc.delete_product(_PRODUCT, program)

    assert deletion is not None and deletion == plan
    assert deletion.removed_programs == [program]
    assert deletion.removed_applications == [app]
    assert deletion.removed_hardware == []
    assert deletion.removed_sections == []
    assert [p.hardware2program_ref_id for p in svc.list_products()] == [_PROGRAM]
    assert stored.exists()
    assert svc.delete_product(_PRODUCT, program) is None


def test_empty_sections_are_pruned_up_to_a_non_empty_parent(tmp_path: Path) -> None:
    svc, db, _stored = _setup(tmp_path)
    sibling = "M-0008_CS-IN-B4"
    _add_product(
        db,
        product_ref="M-0008_H-other_P-1",
        program="M-0008_H-other_HP-0001-01-0000",
        hardware="M-0008_H-other",
        section=sibling,
        parent_section=_ROOT,
        knxprod_path=str(tmp_path / "other.knxprod"),
    )
    # A grandchild leaf under the deleted product's section, holding another product.
    deep_parent = "M-0008_CS-DEEP"
    deep_leaf = "M-0008_CS-DEEP-1"
    _add_product(
        db,
        product_ref=f"{_HW}_P-deep",
        program=_PROGRAM,
        hardware=_HW,
        section=deep_parent,
    )
    _add_product(
        db,
        product_ref="M-0008_H-deep_P-1",
        program="M-0008_H-deep_HP-0001-01-0000",
        hardware="M-0008_H-deep",
        section=deep_leaf,
        parent_section=deep_parent,
        knxprod_path=str(tmp_path / "deep.knxprod"),
    )

    deletion = svc.delete_product(_PRODUCT)
    assert deletion is not None
    assert deletion.removed_sections == [_LEAF]  # the root still holds the sibling
    assert _ids(db, "catalog_sections") == {_ROOT, sibling, deep_parent, deep_leaf}

    # Its program is still bound by the product in the deep parent section.
    assert deletion.removed_programs == []
    deletion = svc.delete_product(f"{_HW}_P-deep")
    assert deletion is not None
    assert deletion.removed_sections == []  # deep_parent still has a child section
    assert deletion.removed_programs == [_PROGRAM]

    deletion = svc.delete_product("M-0008_H-deep_P-1")
    assert deletion is not None
    assert deletion.removed_sections == [deep_leaf, deep_parent]

    deletion = svc.delete_product("M-0008_H-other_P-1")
    assert deletion is not None
    assert deletion.removed_sections == [sibling, _ROOT]
    assert deletion.removed_manufacturers == [_MFR]
    assert _ids(db, "catalog_sections") == set()


def test_manufacturer_removed_only_when_nothing_left(tmp_path: Path) -> None:
    svc, db, _stored = _setup(tmp_path)
    other_mfr = "M-00FA"
    _add_product(
        db,
        product_ref="M-00FA_H-1_P-1",
        program="M-00FA_H-1_HP-0001-01-0000",
        hardware="M-00FA_H-1",
        manufacturer=other_mfr,
        section="M-00FA_CS-1",
        knxprod_path=str(tmp_path / "fa.knxprod"),
    )
    # Hardware ingested from project product data (no catalog item) keeps its manufacturer.
    project_program = "M-0008_H-proj_HP-0002-01-0000"
    _add_product(
        db,
        product_ref="unused",
        program=project_program,
        hardware="M-0008_H-proj",
        knxprod_path=str(tmp_path / "proj.knxprod"),
        with_item=False,
    )

    deletion = svc.delete_product(_PRODUCT)
    assert deletion is not None
    assert deletion.removed_hardware == [_HW]
    assert deletion.removed_manufacturers == []
    assert _ids(db, "manufacturers") == {_MFR, other_mfr}
    assert project_program in _ids(db, "hardware_programs")
    assert _ids(db, "catalog_sections") == {"M-00FA_CS-1"}

    with sqlite3.connect(db) as con:
        con.execute(
            "DELETE FROM hardware_program_medium_types WHERE hardware_program_id = ?",
            (project_program,),
        )
        con.execute("DELETE FROM hardware_programs WHERE id = ?", (project_program,))
        con.execute("DELETE FROM hardware WHERE id = 'M-0008_H-proj'")
    deletion = svc.delete_product("M-00FA_H-1_P-1")
    assert deletion is not None
    assert deletion.removed_manufacturers == [other_mfr]
    assert _ids(db, "manufacturers") == {_MFR}  # not reached from the deleted product


def test_manufacturer_removed_with_its_last_product(tmp_path: Path) -> None:
    svc, db, _stored = _setup(tmp_path)
    _add_product(
        db,
        product_ref="M-00FA_H-1_P-1",
        program="M-00FA_H-1_HP-0001-01-0000",
        hardware="M-00FA_H-1",
        manufacturer="M-00FA",
        section="M-00FA_CS-1",
        knxprod_path=str(tmp_path / "fa.knxprod"),
    )
    deletion = svc.delete_product(_PRODUCT)
    assert deletion is not None
    assert deletion.removed_manufacturers == [_MFR]
    assert _ids(db, "manufacturers") == {"M-00FA"}


def test_knxprod_removed_only_when_unreferenced(tmp_path: Path) -> None:
    svc, db, stored = _setup(tmp_path)
    second = "M-0008_H-second_P-1"
    _add_product(
        db,
        product_ref=second,
        program="M-0008_H-second_HP-0001-01-0000",
        hardware="M-0008_H-second",
        knxprod_path=str(stored),
    )
    unrelated = knxprod_dir_for(db) / "unrelated.knxprod"
    unrelated.write_bytes(b"PK")

    deletion = svc.delete_product(_PRODUCT)
    assert deletion is not None
    assert deletion.removed_programs == [_PROGRAM]
    assert deletion.removed_files == []
    assert stored.exists()

    deletion = svc.delete_product(second)
    assert deletion is not None
    assert deletion.removed_files == [str(stored)]
    assert not stored.exists()
    assert unrelated.exists()  # only archives of the deleted product are considered


def test_failure_leaves_catalog_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xknxeditor.catalog.core.delete as delete_module

    svc, db, stored = _setup(tmp_path)
    before = _snapshot(db)

    def _fail(*_args: object) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(delete_module, "_application_digests", _fail)
    with pytest.raises(RuntimeError):
        svc.delete_product(_PRODUCT)

    assert _snapshot(db) == before
    assert stored.exists()
    assert [p.product_ref_id for p in svc.list_products()] == [_PRODUCT]
