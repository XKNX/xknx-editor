"""The master data through the parse cache: the same master XML is parsed only once."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod import master, parse_cache

_MASTER = (
    b'<?xml version="1.0"?>\n'
    b'<KNX xmlns="http://knx.org/xml/project/23">'
    b'<MasterData Id="MD-1" Version="123" Signature="QUJD"/></KNX>'
)


def test_the_master_xml_is_parsed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = master.parse_master_xml(_MASTER, tmp_path)
    assert first.raw is not None and first.raw.version == 123

    def _no_parse(*_args: object) -> None:
        raise AssertionError("parsed again")

    monkeypatch.setattr(parse_cache, "to_ir", _no_parse)
    again = master.parse_master_xml(_MASTER, tmp_path)
    assert again.raw is not None and again.raw.version == 123


def test_without_a_cache_directory_nothing_is_written(tmp_path: Path) -> None:
    parsed = master.parse_master_xml(_MASTER)
    assert parsed.raw is not None and parsed.raw.version == 123
    assert list(tmp_path.iterdir()) == []
