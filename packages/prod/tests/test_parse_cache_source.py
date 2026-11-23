"""The parse cache found by where an application XML was read from, without reading it again, and
the application XML read as a stream."""

from pathlib import Path

import pytest

import xknxeditor.prod.application as application_module
import xknxeditor.prod.parse_cache as parse_cache
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.application import cached_applications
from xknxeditor.prod.archive import Archive

_FIXTURE = Path(__file__).parent / "fixtures" / "gira_2gang_button_interface.knxprod"
_SOURCE = "product.knxprod|1234|5678|M-0008_A-0001"


def _xml() -> tuple[str, bytes]:
    with Archive(str(_FIXTURE)) as archive:
        mid = min(archive.manufacturer_ids)
        _app_id, xml = next(iter(archive.get_application_xmls(mid).items()))
    return mid, xml


def test_a_parse_noted_by_its_source_is_found_without_the_xml(tmp_path: Path) -> None:
    mid, xml = _xml()
    assert cached_applications(_SOURCE, mid, cache_dir=tmp_path) is None
    parsed = parse_application_xml(xml, mid, cache_dir=tmp_path, source=_SOURCE)
    application_module._memory.clear()
    found = cached_applications(_SOURCE, mid, cache_dir=tmp_path)
    assert found is not None
    assert [a.id for a in found] == [a.id for a in parsed]
    assert found[0].program == parsed[0].program
    assert found[0].version == parsed[0].version
    # Its memory is estimated from the XML's size, as when it was parsed.
    assert application_module._memory[found[0].id] == len(xml) * 5


def test_without_its_cache_entry_the_source_is_read_again(tmp_path: Path) -> None:
    mid, xml = _xml()
    parse_application_xml(xml, mid, cache_dir=tmp_path, source=_SOURCE)
    for entry in tmp_path.glob("ir_*.pkl"):
        entry.unlink()
    assert cached_applications(_SOURCE, mid, cache_dir=tmp_path) is None
    # Another source has no note.
    parse_application_xml(xml, mid, cache_dir=tmp_path, source=_SOURCE)
    assert cached_applications(_SOURCE + "x", mid, cache_dir=tmp_path) is None
    assert cached_applications(_SOURCE, mid, cache_dir=None) is None


def test_notes_of_another_cache_version_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "src_0_abc.txt"
    old.write_text("v20 0 0", encoding="ascii")
    monkeypatch.setattr(parse_cache, "_SWEPT", set())
    mid, xml = _xml()
    parse_application_xml(xml, mid, cache_dir=tmp_path, source=_SOURCE)
    assert not old.exists()
    assert list(tmp_path.glob(f"src_{parse_cache.CACHE_VERSION}_*.txt"))


def test_the_application_xml_is_read_as_a_stream() -> None:
    with Archive(str(_FIXTURE)) as archive:
        mid = min(archive.manufacturer_ids)
        app_id, xml = next(iter(archive.get_application_xmls(mid).items()))
        stream = archive.open_application_xml(mid, app_id)
        assert stream is not None
        with stream:
            assert stream.read() == xml
        assert archive.open_application_xml(mid, "M-0008_A-FFFF-FF-0000") is None
