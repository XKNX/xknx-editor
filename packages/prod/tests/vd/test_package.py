"""A .vd1-.vd5 product database is recognized among the product sources and converted."""

from __future__ import annotations

import io
import zipfile

import pytest

import xknxeditor.prod.vd as vd
from xknxeditor.prod.errors import ArchiveError
from xknxeditor.prod.openknx import knxprod_from_source


def _zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return buffer.getvalue()


def test_holds_vd_dump() -> None:
    assert vd.holds_vd_dump(["Ets/ets.vd_", "Ets/help.hlp"])
    assert vd.holds_vd_dump(["ets2v11b/ets2.vd_"])
    assert not vd.holds_vd_dump(["knx_master.xml", "M-0001/x.vd_"])
    assert not vd.holds_vd_dump(["M-0001/Hardware.xml", "readme.txt"])


def test_product_database_is_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def convert(source: bytes, master_xml: bytes, **_: object) -> bytes:
        calls.append((source, master_xml))
        return b"converted"

    monkeypatch.setattr(vd, "vd_to_knxprod", convert)
    data = _zip("Ets/ets.vd_")
    assert knxprod_from_source(data, b"master") == b"converted"
    assert calls == [(data, b"master")]


def test_unknown_zip_is_rejected() -> None:
    with pytest.raises(ArchiveError, match=r"nor a product database \(\.vd1-\.vd5\)"):
        knxprod_from_source(_zip("readme.txt"), b"master")
