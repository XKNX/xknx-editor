"""A cancelled .knxprod import stores nothing: no catalog rows and no archive file."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from xknxeditor.catalog import CatalogService
from xknxeditor.catalog.core import upload
from xknxeditor.catalog.db import knxprod_dir_for
from xknxeditor.prod.errors import ImportCancelled

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


def _stored(db: Path) -> list[Path]:
    store = knxprod_dir_for(db)
    return list(store.glob("*.knxprod")) if store.exists() else []


def test_cancelled_before_loading(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        svc.import_knxprod(_FIXTURE.read_bytes(), cancel=cancel)
    assert svc.list_products() == []
    assert _stored(db) == []


def test_cancelled_after_loading_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cancelled once everything is parsed and ingested, but not yet committed.
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    cancel = threading.Event()
    real_ingest = upload._ingest_catalog  # pyright: ignore[reportPrivateUsage]

    def ingest_then_cancel(*args: object) -> None:
        real_ingest(*args)  # pyright: ignore[reportArgumentType]
        cancel.set()

    monkeypatch.setattr(upload, "_ingest_catalog", ingest_then_cancel)
    with pytest.raises(ImportCancelled):
        svc.import_knxprod(_FIXTURE.read_bytes(), cancel=cancel)
    assert svc.list_products() == []
    assert _stored(db) == []

    monkeypatch.undo()
    svc.import_knxprod(_FIXTURE.read_bytes(), cancel=threading.Event())
    assert svc.list_products()
    assert len(_stored(db)) == 1
