"""An export with ``fetch_master`` writes the current signed master: downloaded on every export and
cached; without a download the newest of the cached, stored and supplied masters."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

import xknxeditor.proj.core.knxproj_export as exp
from xknxeditor.proj import ProjectService, export_knxproj, master_cache_path
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Project


def _master(version: int) -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/23">'
        b'<MasterData Version="%d" Signature="QUJD"/></KNX>' % version
    )


def _project(tmp_path: Path, stored: bytes | None) -> Path:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-0A1B")
    svc.close(pid)
    with Session(make_engine(url_for(src))) as session:
        project = session.query(Project).filter(Project.id == pid).one()
        project.knx_master_xml = stored
        session.commit()
    return src


def _written(src: Path, tmp_path: Path, **kwargs: object) -> bytes:
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23", **kwargs)  # type: ignore[arg-type]
    with zipfile.ZipFile(out) as zf:
        return zf.read("knx_master.xml")


def _fetch_returns(master: bytes, calls: list[str]) -> object:
    def fetch(knxproj: object = None, timeout: float = 15.0, schema: str = "") -> bytes:
        calls.append(schema)
        return master

    return fetch


def _offline(knxproj: object = None, timeout: float = 15.0, schema: str = "") -> bytes:
    raise OSError("offline")


def test_export_writes_the_downloaded_master_and_caches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _project(tmp_path, _master(304))
    calls: list[str] = []
    monkeypatch.setattr(exp, "fetch_master_xml", _fetch_returns(_master(308), calls))
    cache_dir = tmp_path / "cache"

    assert _written(src, tmp_path, fetch_master=True, master_cache_dir=cache_dir) == (
        _master(308)
    )
    assert calls == ["23"]
    assert master_cache_path(cache_dir, "23").read_bytes() == _master(308)
    assert [p.name for p in cache_dir.iterdir()] == ["knx_master.xml"]


def test_every_export_downloads_the_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _project(tmp_path, _master(304))
    calls: list[str] = []
    monkeypatch.setattr(exp, "fetch_master_xml", _fetch_returns(_master(308), calls))

    _written(src, tmp_path, fetch_master=True)
    _written(src, tmp_path, fetch_master=True)
    assert calls == ["23", "23"]


@pytest.mark.parametrize(
    ("cached", "stored", "expected"),
    [(306, 304, 306), (300, 304, 304), (304, 304, 304)],
)
def test_offline_export_writes_the_newest_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cached: int,
    stored: int,
    expected: int,
) -> None:
    src = _project(tmp_path, _master(stored))
    monkeypatch.setattr(exp, "fetch_master_xml", _offline)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    master_cache_path(cache_dir, "23").write_bytes(_master(cached))

    written = _written(src, tmp_path, fetch_master=True, master_cache_dir=cache_dir)
    assert written == _master(expected)


def test_offline_export_skips_an_unusable_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _project(tmp_path, _master(304))
    monkeypatch.setattr(exp, "fetch_master_xml", _offline)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    master_cache_path(cache_dir, "23").write_bytes(b"<KNX")

    written = _written(src, tmp_path, fetch_master=True, master_cache_dir=cache_dir)
    assert written == _master(304)


def test_offline_export_without_cache_keeps_the_stored_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _project(tmp_path, _master(304))
    monkeypatch.setattr(exp, "fetch_master_xml", _offline)

    assert _written(src, tmp_path, fetch_master=True) == _master(304)


def test_offline_export_takes_a_newer_supplied_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _project(tmp_path, None)
    monkeypatch.setattr(exp, "fetch_master_xml", _offline)

    written = _written(src, tmp_path, fetch_master=True, master_xml=_master(305))
    assert written == _master(305)


def test_without_fetch_master_nothing_is_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _project(tmp_path, _master(304))
    calls: list[str] = []
    monkeypatch.setattr(exp, "fetch_master_xml", _fetch_returns(_master(308), calls))

    assert _written(src, tmp_path) == _master(304)
    assert calls == []


def test_master_cache_path(tmp_path: Path) -> None:
    assert master_cache_path(tmp_path, "23") == tmp_path / "knx_master.xml"
    assert master_cache_path(tmp_path, "14") == tmp_path / "knx_master-14.xml"
