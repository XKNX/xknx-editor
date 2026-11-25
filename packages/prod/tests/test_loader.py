"""Loading a product with several application programs: one application at a time, and the same
registry with the parse cache as without it."""

from __future__ import annotations

import gc
import io
import re
import zipfile
from pathlib import Path

import pytest

from xknxeditor.namespaces.intermediate.knx import Knx
from xknxeditor.prod import parse_cache
from xknxeditor.prod.archive import Archive
from xknxeditor.prod.errors import ArchiveError
from xknxeditor.prod.loader import load
from xknxeditor.prod.registry import ApplicationFacts

_FIXTURES = Path(__file__).parent / "fixtures"
# Hand-made application programs of manufacturer M-00FA, each with its own id.
_APPLICATIONS = ("allocator", "calculations", "activation", "download_invisible")
_MASTER = b"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/23">
  <MasterData Id="MD-1" Version="1" Signature="QUJD">
    <Manufacturers>
      <Manufacturer Id="M-00FA" KnxManufacturerId="250" Name="Test" DefaultLanguage="en-US" />
    </Manufacturers>
  </MasterData>
</KNX>"""


def _application_id(xml: bytes) -> str:
    match = re.search(rb'<ApplicationProgram Id="([^"]+)"', xml)
    assert match is not None
    return match.group(1).decode()


def _xmls() -> dict[str, bytes]:
    """The application XMLs of the product, by application id."""
    xmls = [
        (_FIXTURES / f"{name}_application.xml").read_bytes() for name in _APPLICATIONS
    ]
    return {_application_id(xml): xml for xml in xmls}


def _product() -> bytes:
    """A product of only application programs (a manufacturer without hardware)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("knx_master.xml", _MASTER)
        for app_id, xml in _xmls().items():
            archive.writestr(f"M-00FA/{app_id}.xml", xml)
    return buffer.getvalue()


def _held() -> int:
    """How many parsed application XMLs are in memory."""
    return sum(
        1
        for item in gc.get_objects()
        if isinstance(item, Knx) and item.manufacturer_data is not None
    )


def test_the_archive_lists_its_applications_without_reading_them() -> None:
    xmls = _xmls()
    with Archive(_product()) as archive:
        assert archive.application_ids("M-00FA") == sorted(xmls)
        for app_id, xml in xmls.items():
            assert archive.application_xml_size("M-00FA", app_id) == len(xml)
            assert archive.read_application_xml("M-00FA", app_id) == xml
        assert archive.get_application_xmls("M-00FA") == dict(sorted(xmls.items()))
        with pytest.raises(ArchiveError):
            archive.read_application_xml("M-00FA", "M-00FA_A-FFFF-FF-0000")


def test_applications_are_read_and_parsed_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each application XML is read right before it is parsed, and when the next one is parsed,
    neither the previous XML's parse is in memory any more nor is it kept in the registry."""
    events: list[str] = []
    read = Archive.read_application_xml
    to_ir = parse_cache.to_ir

    def reading(archive: Archive, manufacturer_id: str, app_id: str) -> bytes:
        events.append(f"read {app_id}")
        return read(archive, manufacturer_id, app_id)

    def parsing(xml: bytes, version: str) -> Knx:
        if b"<ApplicationProgram " in xml:  # not the master data
            events.append(f"parse {_application_id(xml)} beside {_held() - before}")
        return to_ir(xml, version)

    content = _product()
    gc.collect()
    before = _held()
    monkeypatch.setattr(Archive, "read_application_xml", reading)
    monkeypatch.setattr(parse_cache, "to_ir", parsing)
    registry = load(content, cache_dir=tmp_path)

    assert events == [
        step
        for app_id in sorted(_xmls())
        for step in (f"read {app_id}", f"parse {app_id} beside 0")
    ]
    assert list(registry.applications) == sorted(_xmls())
    assert not any(app.loaded for app in registry.applications.values())
    assert _held() == before


def test_with_the_cache_the_registry_is_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A load through the parse cache knows the same applications and facts as one that keeps
    every parsed program; each program is read back from the cache when it is used."""
    content = _product()
    kept = load(content)
    progress: list[tuple[int, int]] = []
    cached = load(
        content,
        cache_dir=tmp_path,
        progress=lambda done, total: progress.append((done, total)),
    )
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert list(cached.applications) == list(kept.applications) == sorted(_xmls())
    assert cached.application_facts == kept.application_facts
    assert cached.application_facts["M-00FA_A-0001-01-0000"] == ApplicationFacts(
        application_number=1,
        application_version=1,
        mask_version="MV-07B0",
        is_secure_enabled=False,
    )

    def no_parse(*_args: object) -> None:
        raise AssertionError("parsed again")

    monkeypatch.setattr(parse_cache, "to_ir", no_parse)
    for app_id, app in cached.applications.items():
        original = kept.applications[app_id]
        assert not app.loaded
        assert (app.name, app.version, app.manufacturer_id) == (
            original.name,
            original.version,
            original.manufacturer_id,
        )
        assert app.program == original.program


def test_a_missing_cache_entry_is_parsed_again(tmp_path: Path) -> None:
    content = _product()
    registry = load(content, cache_dir=tmp_path)
    for entry in tmp_path.glob("ir_*.pkl"):
        entry.unlink()
    app = registry.applications["M-00FA_A-0002-01-0000"]
    assert app.program.id == "M-00FA_A-0002-01-0000"
    assert len(list(tmp_path.glob("ir_*.pkl"))) == 1


def _sizes() -> list[int]:
    return [len(xml) for _app_id, xml in sorted(_xmls().items())]


def test_the_parsed_share_follows_the_xml_parsed() -> None:
    """In this process: the share rises while each application XML is parsed, ends at 1, and
    the applications are counted by their size."""
    shares: list[float] = []
    load(_product(), parsed_share=shares.append)
    assert shares == sorted(set(shares))
    assert shares[-1] == 1.0
    total = sum(_sizes())
    ends = [sum(_sizes()[: count + 1]) / total for count in range(len(_sizes()))]
    assert all(end in shares for end in ends)


def test_the_parsed_share_of_a_lone_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An application parsed in a worker process is counted there and heard here."""
    monkeypatch.setattr(parse_cache, "LONE_WORKER_MIN_BYTES", 1)
    started: list[int] = []
    real_pool = parse_cache.ProcessPoolExecutor

    def pool(**kwargs: object) -> object:
        started.append(int(kwargs["max_workers"]))  # type: ignore[call-overload]
        return real_pool(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parse_cache, "ProcessPoolExecutor", pool)
    shares: list[float] = []
    registry = load(_product(), cache_dir=tmp_path, parsed_share=shares.append)
    assert started == [1]
    assert shares[-1] == 1.0
    assert shares == sorted(set(shares))
    assert list(registry.applications) == sorted(_xmls())
    assert len(list(tmp_path.glob("ir_*.pkl"))) == len(_xmls())
