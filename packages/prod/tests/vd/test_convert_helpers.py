"""Helpers of the .vd conversion: ids, texts, sorting, number formats, load records, file versions."""

from __future__ import annotations

import datetime as dt
import random
import struct
from functools import cmp_to_key

import pytest

from xknxeditor.prod.vd import baggage
from xknxeditor.prod.vd.baggage import (
    baggage_version,
    file_integrity,
    to_utc,
    utc_offset,
)
from xknxeditor.prod.vd.ids import (
    application_id,
    fragment,
    hardware2program_id,
    hardware_id,
)
from xknxeditor.prod.vd.loadctl import LoadControls
from xknxeditor.prod.vd.msi import product_version
from xknxeditor.prod.vd.netsort import list_sort
from xknxeditor.prod.vd.pefile import file_version, pe_version
from xknxeditor.prod.vd.text import code_page, culture_name, from_db, strip_invalid
from xknxeditor.prod.vd.xmlout import El, document, double, float32, serialize


def test_id_fragments() -> None:
    assert fragment("5WG1 148-1AB02") == "5WG1.20148.2D1AB02"
    assert fragment("hp_00291") == "hp.5F00291"
    assert fragment("Grün") == "Grün"
    assert fragment("½") == ".C2.BD"


def test_ids() -> None:
    assert (
        application_id("M-0004", 0x2072, 1, "M-000A") == "M-0004_A-2072-01-0000-O000A"
    )
    assert hardware_id("M-0001", "hp_00291", 1) == "M-0001_H-hp.5F00291-1"
    refs = ["M-0001_A-7002-01-0000", "M-0001_A-7003-02-0000"]
    assert (
        hardware2program_id("M-0001_H-x-1", refs)
        == "M-0001_H-x-1_HP-7002-01-0000-7003-02-0000"
    )


def test_texts_in_language_code_pages() -> None:
    assert culture_name(1031) == "de-DE"
    assert code_page("ru-RU") == 1251
    # the dump holds the Windows-1251 bytes of a Russian text, read as Windows-1252
    russian = "Свет".encode("cp1251").decode("cp1252")
    assert from_db("ru-RU", russian) == "Свет"
    assert from_db("de-DE", "Grün") == "Grün"
    assert from_db("de-DE", None) == ""
    assert strip_invalid("a\x01b\tc") == "a b\tc"


def test_list_sort_like_the_converter_runtime() -> None:
    values = list(range(100))
    random.Random(1).shuffle(values)
    list_sort(values, lambda a, b: (a > b) - (a < b))
    assert values == list(range(100))
    # equal keys do not keep their order: two or three equal elements end up reversed
    for n in (2, 3):
        items = [(0, i) for i in range(n)]
        list_sort(items, lambda a, b: (a[0] > b[0]) - (a[0] < b[0]))
        assert [i for _, i in items] == list(reversed(range(n)))
    items = [random.Random(2).randrange(5) for _ in range(300)]
    copy = list(items)
    list_sort(copy, lambda a, b: (a > b) - (a < b))
    assert copy == sorted(items, key=cmp_to_key(lambda a, b: (a > b) - (a < b)))


def test_number_formats() -> None:
    assert float32(4) == "4"
    assert float32(17.5 * 2) == "35"
    assert float32(0.1) == "0.1"
    assert double(1e16) == "1E+16"
    assert double(0.5) == "0.5"


def test_serialize() -> None:
    m = El("Manufacturer", {"RefId": "M-0001"})
    m.sub("Baggages").sub("Baggage", {"Id": "x", "Name": 'a"<&\n', "TargetPath": ""})
    data = serialize(document(m))
    assert data.startswith(
        b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>\r\n<KNX xmlns:xsi='
    )
    assert b'<Baggage TargetPath="" Name="a&quot;&lt;&amp;&#xA;" Id="x" />' in data
    assert data.endswith(b"</KNX>")
    assert serialize(document(m), bom=False).startswith(b'<?xml version="1.0"?>\r\n')


def test_baggage_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    # File times are local times of this computer; its time zone gives the offset.
    local = dt.datetime(2014, 9, 22, 16, 2, 10)
    offset = local.astimezone().utcoffset()
    assert offset is not None
    assert utc_offset(local) == offset
    assert to_utc(local) == local - offset
    assert isinstance(utc_offset(dt.datetime(1960, 1, 1)), dt.timedelta)
    monkeypatch.setattr(baggage, "utc_offset", lambda _: dt.timedelta(hours=2))
    assert to_utc(local) == dt.datetime(2014, 9, 22, 14, 2, 10)
    assert baggage_version(0x10002, 0x30004) == "1.2.3.4"
    assert file_integrity("a.txt", b"x") is None
    assert file_integrity("a.DLL", b"x") == "5BCC55BD"


def _pe(version: tuple[int, int, int, int]) -> bytes:
    ms = (version[0] << 16) | version[1]
    ls = (version[2] << 16) | version[3]
    info = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0\0\0"
    fixed = b"\xbd\x04\xef\xfe" + struct.pack("<III", 0x10000, ms, ls)
    return b"MZ" + bytes(100) + info + fixed + bytes(20)


def test_file_versions() -> None:
    assert pe_version(_pe((1, 0, 83, 1))) == "1.0.83.1"
    assert file_version("x.dll", _pe((1, 1, 4, 11))) == "1.1.4.11"
    assert file_version("x.txt", b"hello") is None
    assert product_version(b"not an installer") is None


def test_standard_load_records() -> None:
    lc = LoadControls(5)
    rec = bytes([0x13, 0x00, 0x00, 0x40, 0x00, 0x40, 0xFF, 0x32, 0x01, 0x00]) + bytes(6)
    el = lc.process(rec, "auto")
    assert el is not None
    assert el.tag == "LdCtrlAbsSegment"
    assert el.attrs == {
        "LsmIdx": "1",
        "SegType": "0",
        "Address": "16384",
        "Size": "256",
        "Access": "50",
        "MemType": "1",
        "SegFlags": "0",
    }
    prop = lc.process(
        bytes([0x05, 0x01, 0x02, 0x00, 0x01, 0x01, 0xAA]) + bytes(9), "full"
    )
    assert prop is not None
    assert prop.attrs == {
        "ObjIdx": "1",
        "PropId": "2",
        "Verify": "false",
        "InlineData": "AA000000000000000000",
        "AppliesTo": "full",
    }
    assert lc.process(bytes([0x0D, 0x01, 0xF4]) + bytes(13), "auto") is not None
    assert lc.process(bytes([0xFF]) + bytes(15), "auto") is None
    assert lc.proc_type(7) == "auto"
    assert lc.errors


def test_extended_load_records() -> None:
    lc = LoadControls(5)
    image = lc.process(
        bytes([0xF0, 0x04, 0x05, 0x02, 0x0D, 0x00, 0x01, 0x01]) + bytes(8), "auto"
    )
    assert image is not None
    assert image.tag == "LdCtrlLoadImageProp"
    assert image.attrs == {"ObjIdx": "7", "PropId": "13"}
    merge = lc.process(bytes([0xF0, 0x0B, 0x02]) + bytes(13), "par")
    assert merge is not None
    assert merge.attrs == {"MergeId": "2"}
    variable = lc.process(bytes([0xF0, 0x05, 0x01]) + bytes(13), "auto")
    assert variable is not None
    assert variable.attrs == {"Name": "EnableOptimisticWrite", "Value": "true"}
    assert lc.process(bytes([0xF0, 0x7F]) + bytes(14), "auto") is None
