"""An import stops with ImportCancelled once its cancel event is set: in the decryption, the
conversion, the signing, the loader and the parallel parsing."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, ClassVar

import pytest

from xknxeditor.prod import parse_cache
from xknxeditor.prod.errors import ImportCancelled, check_cancelled
from xknxeditor.prod.loader import load
from xknxeditor.prod.vd import read_dump, vd_to_knxprod
from xknxeditor.prod.vd.convert import convert
from xknxeditor.prod.vd.database import Database
from xknxeditor.prod.vd.signing import sign

from .test_convert import MASTER, _base, _db
from .test_dump import _DUMP, _PW, _zipcrypto
from .test_signing import package

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "gira_2gang_button_interface.knxprod"
)


def _cancelled() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


def test_check_cancelled() -> None:
    check_cancelled(None)
    check_cancelled(threading.Event())
    with pytest.raises(ImportCancelled):
        check_cancelled(_cancelled())


def test_decryption_and_conversion_stop() -> None:
    data = _zipcrypto("Ets/ets.vd_", _DUMP, _PW, b"")
    assert (
        read_dump(data, passwords=(_PW,), cancel=threading.Event()).version == 0x60003
    )
    with pytest.raises(ImportCancelled):
        read_dump(data, passwords=(_PW,), cancel=_cancelled())
    with pytest.raises(ImportCancelled):
        vd_to_knxprod(data, MASTER, passwords=(_PW,), cancel=_cancelled())


def test_conversion_reports_progress_and_stops() -> None:
    steps: list[tuple[int, int]] = []
    files = convert(
        _db(_base()), MASTER, progress=lambda done, total: steps.append((done, total))
    )
    assert files
    total = steps[-1][1]
    assert total >= 1
    assert steps == [(done, total) for done in range(total + 1)]
    with pytest.raises(ImportCancelled):
        convert(Database(0x60003), MASTER, cancel=_cancelled())

    # Cancelled while converting: stops before the next application program.
    cancel = threading.Event()
    with pytest.raises(ImportCancelled):
        convert(_db(_base()), MASTER, cancel=cancel, progress=lambda *_: cancel.set())


def test_signing_stops() -> None:
    assert sign(package(), cancel=threading.Event())
    with pytest.raises(ImportCancelled):
        sign(package(), cancel=_cancelled())


def test_loader_stops() -> None:
    data = _FIXTURE.read_bytes()
    assert load(data, cancel=threading.Event()).applications
    with pytest.raises(ImportCancelled):
        load(data, cancel=_cancelled())


class _Pool:
    """A worker pool whose parses never finish (so the cancel is seen while waiting)."""

    shutdowns: ClassVar[list[bool]] = []

    def __init__(self, **_: Any) -> None:
        pass

    def __enter__(self) -> _Pool:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def submit(self, *_: object) -> Future[None]:
        return Future()

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdowns.append(cancel_futures and not wait)


def test_parallel_parsing_drops_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_cache, "ProcessPoolExecutor", _Pool)
    monkeypatch.setattr(parse_cache, "parse_workers", lambda _sizes: 2)
    monkeypatch.setattr(parse_cache, "_CANCEL_POLL", 0.01)
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    with pytest.raises(ImportCancelled):
        xml = b'<KNX xmlns="http://knx.org/xml/project/20"/>'
        parse_cache.fill_cache([(len(xml), lambda: xml)], tmp_path, cancel=cancel)
    assert _Pool.shutdowns == [True]
