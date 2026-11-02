"""A .knxproj picked in the "Load KNX Product" dialog must be imported as a project, not catalog.

The file filter is only advisory on macOS, so a full project archive (.knxproj) can be selected
in the catalog-import dialog. Feeding it to the catalog importer only extracts the embedded product
XMLs and builds no devices, which looks like "the project loaded but has no devices".
``KnxGuiApp._load_product`` therefore routes a .knxproj to the project importer (as "Open Project"
already does). These tests drive that routing decision against a stub so no imgui app is built,
and the product import worker with its Cancel.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.main import KnxGuiApp
from editor_gui.strings import S
from xknxeditor.prod.errors import ImportCancelled


def _stub() -> SimpleNamespace:
    calls: dict[str, list[Any]] = {"prompt_import": [], "run_bg": []}

    def run_bg(
        label: str, worker: Callable[[], None], *, cancel: threading.Event | None = None
    ) -> None:
        # Record that the catalog worker would have started, without running it.
        calls["run_bg"].append((label, worker, cancel))

    return SimpleNamespace(
        _calls=calls,
        _prompt_import_dest=lambda source: calls["prompt_import"].append(source),
        _run_bg=run_bg,
    )


def _load(stub: SimpleNamespace, path: str) -> None:
    KnxGuiApp._load_product(stub, path)  # type: ignore[arg-type]


def test_knxproj_routes_to_project_importer() -> None:
    stub = _stub()
    _load(stub, "/Users/hco/Documents/KNX/Wilhelmshohe.knxproj")
    assert stub._calls["prompt_import"] == [
        "/Users/hco/Documents/KNX/Wilhelmshohe.knxproj"
    ]
    assert stub._calls["run_bg"] == []  # catalog import must not start for a .knxproj


def test_knxproj_routing_is_case_insensitive() -> None:
    stub = _stub()
    _load(stub, "/tmp/Project.KNXPROJ")
    assert stub._calls["prompt_import"] == ["/tmp/Project.KNXPROJ"]
    assert stub._calls["run_bg"] == []


def test_knxprod_still_loads_as_catalog() -> None:
    stub = _stub()
    _load(stub, "/tmp/product.knxprod")
    assert (
        stub._calls["prompt_import"] == []
    )  # a real product goes to the catalog importer
    ((label, _worker, cancel),) = stub._calls["run_bg"]
    assert label == S.PROGRESS_LOAD_KNXPROD
    assert isinstance(cancel, threading.Event)  # the progress modal offers Cancel


def test_product_database_import_can_be_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _stub()
    log: list[str] = []
    stub._log = SimpleNamespace(
        info=lambda event, **_: log.append(event),
        error=lambda event, **_: log.append(event),
    )
    stub._knxprod_result = None
    stub._conversion_progress = lambda done, total: None

    def import_contents(
        path: Path, *, cancel: threading.Event, progress: object
    ) -> list[object]:
        assert path == Path("/tmp/old.vd5")
        cancel.set()  # the user pressed Cancel during the conversion
        raise ImportCancelled

    stub._catalog_service = SimpleNamespace(import_knxprod_contents=import_contents)
    _load(stub, "/tmp/old.vd5")
    ((label, worker, cancel),) = stub._calls["run_bg"]
    assert label == S.PROGRESS_CONVERT_VD
    worker()
    assert cancel.is_set()
    assert stub._knxprod_result == S.IMPORT_CANCELLED
    assert log == ["loading knxprod", "knxprod import cancelled"]


def test_cancel_in_the_progress_modal() -> None:
    progress: list[tuple[float | None, str]] = []
    stub = SimpleNamespace(
        _progress_cancel=None,
        set_progress=lambda fraction, stage="": progress.append((fraction, stage)),
    )
    cancel = threading.Event()
    KnxGuiApp._offer_cancel(stub, cancel)  # type: ignore[arg-type]
    stub._progress_cancel()
    assert cancel.is_set()
    assert progress == [(None, S.PROGRESS_CANCELLING)]
    KnxGuiApp._cancel_no_longer_possible(stub)  # type: ignore[arg-type]
    assert stub._progress_cancel is None


def test_conversion_progress() -> None:
    progress: list[tuple[float | None, str]] = []
    stub = SimpleNamespace(
        set_progress=lambda fraction, stage="": progress.append((fraction, stage))
    )
    KnxGuiApp._conversion_progress(stub, 1, 4)  # type: ignore[arg-type]
    KnxGuiApp._conversion_progress(stub, 4, 4)  # type: ignore[arg-type]
    assert progress == [
        (0.25, S.PROGRESS_APPLICATION_PROGRAMS.format(done=1, total=4)),
        (None, S.PROGRESS_LOAD_KNXPROD),
    ]


def test_files_import_stops_at_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    # The first file imports, the user cancels during the second: the third is not read and the
    # first file's versions are listed.
    monkeypatch.setattr("editor_gui.main.master_xml_bytes", lambda: b"<KNX/>")
    stub = _stub()
    stub._log = SimpleNamespace(
        info=lambda *_, **__: None,
        debug=lambda *_, **__: None,
        error=lambda *_, **__: None,
    )
    stub._knxprod_result = None
    stub._import_rows_result = None
    stub._conversion_progress = lambda done, total: None
    stub._product_import_rows = lambda added: [f"row {p.product_ref_id}" for p in added]
    stub._project_service = SimpleNamespace(
        refresh_catalog_resolution=lambda rebuild: None
    )
    imported: list[bytes] = []

    def import_contents(
        data: bytes, master: bytes, *, cancel: threading.Event, progress: object
    ) -> list[object]:
        imported.append(data)
        if data == b"second":
            cancel.set()
            raise ImportCancelled
        return [SimpleNamespace(product_ref_id="P-1", hardware2program_ref_id="HP-1")]

    stub._catalog_service = SimpleNamespace(
        import_product_source_contents=import_contents
    )
    files = [
        SimpleNamespace(read_bytes=lambda name=name: name)
        for name in (b"first", b"second", b"third")
    ]
    KnxGuiApp._start_product_files_import(  # type: ignore[arg-type]
        stub, "folder", lambda: files, ("importing", "empty", "failed {error}")
    )
    ((label, worker, cancel),) = stub._calls["run_bg"]
    assert label == "importing" and isinstance(cancel, threading.Event)
    worker()
    assert imported == [b"first", b"second"]
    assert stub._import_rows_result == (
        S.KNXPROD_IMPORT_SUMMARY.format(count=1, name="folder"),
        ["row P-1"],
    )
    assert stub._knxprod_result is None
