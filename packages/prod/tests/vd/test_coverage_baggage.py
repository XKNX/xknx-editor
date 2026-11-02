"""Baggage files: versions, file times, duplicates, compression, program and product refs."""

from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
from typing import Any

import pytest

from xknxeditor.prod.vd import baggage as vd_baggage
from xknxeditor.prod.vd.context import ConversionError

from .test_coverage_files import msi
from .test_coverage_helpers import NS, Tables, base, children, every, find, run, xml


def _zip(*entries: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries:
            info = zipfile.ZipInfo(name, (2015, 7, 3, 4, 5, 6))
            zf.writestr(info, content)
    return buffer.getvalue()


def program_file(version: tuple[int, int, int, int]) -> bytes:
    """A program file with a version resource."""
    ms = (version[0] << 16) | version[1]
    ls = (version[2] << 16) | version[3]
    info = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0\0\0"
    fixed = b"\xbd\x04\xef\xfe" + struct.pack("<III", 0x10000, ms, ls)
    return b"MZ" + bytes(100) + info + fixed + bytes(20)


def _bag(ident: int, folder: str | None, name: str, **kw: Any) -> dict[str, Any]:
    return {
        "ID": ident,
        "MANUFACTURERID": kw.get("manufacturer", 131),
        "LOCALFILEDIR": folder,
        "LOCALFILENAME": name,
        "LOCALFILEATTRIBUTES": kw.get("attributes"),
        "LOCALFILETIME": kw.get("time"),
        "LOCALFILEVERSIONHI": kw.get("hi"),
        "LOCALFILEVERSIONLO": kw.get("lo"),
        "COMPRESSION": kw.get("compression"),
        "DATA": kw.get("data"),
    }


def baggage_tables(installer: bytes | None = None) -> Tables:
    t = base()
    t["baggage"] = [
        _bag(
            900,
            "docs\\",
            "readme.txt",
            attributes=3,
            time="2014-07-01 12:00:00.000",
            hi=0x10002,
            lo=0x30004,
            compression="Z",
            data=_zip(("readme.txt", b"first")),
        ),
        # the same file again: an older version is dropped, a newer one replaces it
        _bag(901, "docs\\", "readme.txt", hi=0x10001, lo=0, data=b"older"),
        _bag(
            902,
            "DOCS\\",
            "README.TXT",
            hi=0x20000,
            lo=0,
            data=b"newer",
            time="2014-07-02",
        ),
        _bag(
            903,
            "C:\\abs\\",
            "plugin.msi",
            data=installer if installer is not None else msi({"ProductVersion": "2.1"}),
            time="2014-07-03 01:02",
        ),
        _bag(904, None, "tool.exe", data=b"MZ" + bytes(10), time="2014-07-04 10:00:00"),
        _bag(
            907, "x\\y\\", "zipped.txt", compression="Z", data=_zip(("f.txt", b"zip"))
        ),
        _bag(909, "x", "hidden.txt", data=b"h", attributes=2, time="2014-07-09"),
    ]
    t["applicationprogrambaggage"] = [
        {"ID": 1, "APPLICATIONPROGRAMID": 40, "BAGGAGEID": 900},
        {"ID": 2, "APPLICATIONPROGRAMID": 40, "BAGGAGEID": 901},
        {"ID": 3, "APPLICATIONPROGRAMID": 40, "BAGGAGEID": 903},
    ]
    t["catalogentrybaggage"] = [
        {"ID": 1, "CATALOGENTRYID": 30, "BAGGAGEID": 904},
        {"ID": 2, "CATALOGENTRYID": 30, "BAGGAGEID": 904},
    ]
    return t


def odd_tables() -> Tables:
    """Baggages the converter cannot create."""
    t = baggage_tables()
    t["baggage"] += [
        _bag(905, "x", "unknown.bin", compression="X", data=b"abc", time="2014-07-05"),
        _bag(906, "x", "two.bin", compression="z", data=_zip(("a", b"1"), ("b", b"2"))),
        _bag(908, "x", "undated.txt", data=b"u", time="yesterday"),
        _bag(910, "x", "empty.txt", data=None, time="2014-07-10"),
    ]
    t["catalogentrybaggage"].append({"ID": 3, "CATALOGENTRYID": 30, "BAGGAGEID": 950})
    return t


NOW_UTC = dt.datetime(2026, 11, 2, 11, 0, 0, 123456)


@pytest.fixture(autouse=True)
def _summer_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # file times are local times; central European summer time here, converted in winter
    monkeypatch.setattr(vd_baggage, "utc_offset", lambda _: dt.timedelta(hours=2))
    monkeypatch.setattr(vd_baggage, "is_dst", lambda local: 4 <= local.month <= 10)
    monkeypatch.setattr(vd_baggage, "now", lambda: dt.datetime(2026, 11, 2, 12))
    monkeypatch.setattr(vd_baggage, "now_utc", lambda: NOW_UTC)


BG = "M-0083_BG-"


def test_baggages() -> None:
    files, warnings, errors = run(baggage_tables())
    root = xml(files, "M-0083/Baggages.xml")
    baggages = [(dict(b.attrib), children(b)) for b in every(root, "Baggage")]
    assert baggages == [
        # the newer version replaces the file of the same target (case does not matter)
        (
            {
                "TargetPath": "DOCS",
                "Name": "README.TXT",
                "Id": f"{BG}DOCS-README.2ETXT",
            },
            [("FileInfo", {"Version": "2.0.0.0", "TimeInfo": "2014-07-01T22:00:00Z"})],
        ),
        # an absolute folder is dropped; the version of an installer package is read
        (
            {
                "TargetPath": "",
                "Name": "plugin.msi",
                "FileIntegrity": baggages[1][0]["FileIntegrity"],
                "Id": f"{BG}-plugin.2Emsi",
            },
            [("FileInfo", {"Version": "2.1.0.0", "TimeInfo": "2014-07-02T23:02:00Z"})],
        ),
        (
            {
                "TargetPath": "",
                "Name": "tool.exe",
                "FileIntegrity": "248C2338",
                "Id": f"{BG}-tool.2Eexe",
            },
            [("FileInfo", {"TimeInfo": "2014-07-04T08:00:00Z"})],
        ),
        (
            {
                "TargetPath": r"x\y",
                "Name": "zipped.txt",
                "Id": f"{BG}x.5Cy-zipped.2Etxt",
            },
            [("FileInfo", {"TimeInfo": "2015-07-03T03:05:06Z"})],
        ),
        (
            {"TargetPath": "x", "Name": "hidden.txt", "Id": f"{BG}x-hidden.2Etxt"},
            [("FileInfo", {"TimeInfo": "2014-07-08T22:00:00Z", "Hidden": "true"})],
        ),
    ]
    assert files["M-0083/Baggages/plugin.msi"] == baggage_tables()["baggage"][3]["DATA"]
    assert files["M-0083/Baggages/x/y/zipped.txt"] == b"zip"
    assert files["M-0083/Baggages/tool.exe"] == b"MZ" + bytes(10)
    assert warnings == []
    assert [e.split(":")[0] for e in errors] == ["DifferentRefIdsForOneDbId"]
    # programs refer to the first id of a replaced file; products to their baggages once
    app = xml(files, "M-0083/M-0083_A-1234-01-0000.xml")
    assert [b.get("RefId") for b in every(app, "Baggage")] == [
        f"{BG}docs-readme.2Etxt",
        f"{BG}-plugin.2Emsi",
    ]
    product = find(xml(files, "M-0083/Hardware.xml"), ".//{}Product")
    assert children(find(product, "{}Baggages")) == [
        ("Baggage", {"RefId": f"{BG}-tool.2Eexe"})
    ]


def test_replacing_file_keeps_the_first_folder_spelling() -> None:
    # folder names do not depend on case: the folder keeps the first spelling, the file the
    # name of the replacing baggage
    files, _warnings, _errors = run(baggage_tables())
    assert "M-0083/Baggages/docs/README.TXT" in files
    assert files["M-0083/Baggages/docs/README.TXT"] == b"newer"
    assert not any(
        name.lower().endswith("readme.txt") and "DOCS" in name for name in files
    )


def test_folders_of_other_baggages_keep_the_first_spelling() -> None:
    t = baggage_tables()
    t["baggage"] = [
        _bag(1, "Docs\\Sub", "a.txt", data=b"a", time="2014-07-01"),
        _bag(2, "DOCS\\sub\\More", "b.txt", data=b"b", time="2014-07-01"),
        _bag(3, "DOCS", "c.txt", data=b"c", time="2014-07-01"),
    ]
    files, _warnings, _errors = run(t)
    assert sorted(n for n in files if "/Baggages/" in n) == [
        "M-0083/Baggages/Docs/Sub/More/b.txt",
        "M-0083/Baggages/Docs/Sub/a.txt",
        "M-0083/Baggages/Docs/c.txt",
    ]
    root = xml(files, "M-0083/Baggages.xml")
    assert [b.get("TargetPath") for b in every(root, "Baggage")] == [
        "Docs\\Sub",
        "DOCS\\sub\\More",
        "DOCS",
    ]


def test_time_of_a_zipped_file() -> None:
    # the DOS time of the entry moves by an hour where daylight saving time differs between
    # then (summer) and the conversion (winter)
    files, _warnings, _errors = run(baggage_tables())
    root = xml(files, "M-0083/Baggages.xml")
    info = find(root, ".//{}Baggage[@Name='zipped.txt']/{}FileInfo")
    assert info.get("TimeInfo") == "2015-07-03T03:05:06Z"


def _zipped_tables(data: bytes) -> Tables:
    t = baggage_tables()
    t["baggage"] = [_bag(1, "x", "zipped.txt", compression="Z", data=data)]
    t["applicationprogrambaggage"] = []
    t["catalogentrybaggage"] = []
    return t


def _zipped_time(data: bytes) -> str | None:
    files, _warnings, errors = run(_zipped_tables(data))
    info = xml(files, "M-0083/Baggages.xml").find(f".//{NS}FileInfo")
    if info is None:
        # (the Baggages.xml of the manufacturer is written without the baggage)
        return f"failed: {errors[-1].split(':')[0]}"
    return info.get("TimeInfo")


def _zip_extra(extra: bytes, when: tuple[int, ...] = (2015, 1, 3, 4, 5, 6)) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        info = zipfile.ZipInfo("f.txt", when)  # type: ignore[arg-type]
        info.extra = extra
        zf.writestr(info, b"zip")
    return buffer.getvalue()


_FILETIME = (dt.datetime(2015, 7, 3, 1, 2, 3, 456789) - dt.datetime(1601, 1, 1)) // (
    dt.timedelta(microseconds=1)
) * 10 + 7
_UNIX = int(
    (dt.datetime(2015, 7, 3, 1, 2, 3) - dt.datetime(1970, 1, 1)).total_seconds()
)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # winter then and now: no correction
        (_zip_extra(b""), "2015-01-03T02:05:06Z"),
        (_zip_extra(b"", (2015, 7, 3, 4, 5, 6)), "2015-07-03T03:05:06Z"),
        # times of the extra fields are UTC
        (
            _zip_extra(struct.pack("<HHIHHqqq", 0x000A, 32, 0, 1, 24, _FILETIME, 0, 0)),
            "2015-07-03T01:02:03.4567897Z",
        ),
        (_zip_extra(struct.pack("<HHBi", 0x5455, 5, 1, _UNIX)), "2015-07-03T01:02:03Z"),
        (
            _zip_extra(struct.pack("<HHBiii", 0x5455, 13, 7, _UNIX, 0, 0)),
            "2015-07-03T01:02:03Z",
        ),
        (_zip_extra(struct.pack("<HHii", 0x5855, 8, _UNIX, 0)), "2015-07-03T01:02:03Z"),
        # NTFS times of another layout are skipped
        (
            _zip_extra(struct.pack("<HHIHHqqq", 0x000A, 32, 0, 2, 24, _FILETIME, 0, 0)),
            "2015-01-03T02:05:06Z",
        ),
        # extra fields of a size the library does not expect fail the archive
        (
            _zip_extra(struct.pack("<HHB", 0x5455, 1, 1)),
            "failed: CreatingBaggageFailed",
        ),
        (
            _zip_extra(struct.pack("<HHi", 0x000A, 4, 0)),
            "failed: CreatingBaggageFailed",
        ),
        (b"not a zip", "failed: CreatingBaggageFailed"),
    ],
)
def test_times_of_zip_entries(data: bytes, expected: str) -> None:
    assert _zipped_time(data) == expected


@pytest.mark.parametrize(
    ("packed", "expected"),
    [
        (0, dt.datetime(1995, 1, 1)),
        (0xFFFF, dt.datetime(1995, 1, 1)),
        # 62 seconds, 60 minutes and 24 hours carry over
        (
            (35 << 25 | 7 << 21 | 3 << 16) | (4 << 11 | 5 << 5 | 31),
            dt.datetime(2015, 7, 3, 4, 6),
        ),
        (
            (35 << 25 | 7 << 21 | 3 << 16) | (23 << 11 | 60 << 5),
            dt.datetime(2015, 7, 4),
        ),
        # an invalid day of 1980 becomes January 1; others are clamped
        ((0 << 25 | 0 << 21 | 0 << 16) | (4 << 11), dt.datetime(1980, 1, 1, 4)),
        ((35 << 25 | 2 << 21 | 30 << 16), dt.datetime(2015, 2, 28)),
        ((35 << 25 | 13 << 21 | 3 << 16), dt.datetime(2015, 12, 3)),
    ],
)
def test_dos_time_of_zip_entries(packed: int, expected: dt.datetime) -> None:
    assert vd_baggage._dos_time(packed) == expected  # pyright: ignore[reportPrivateUsage]


def _names(tables: Tables) -> tuple[list[str], dict[str, bytes], list[str]]:
    files, _warnings, errors = run(tables)
    root = xml(files, "M-0083/Baggages.xml")
    return [b.get("Name") or "" for b in every(root, "Baggage")], files, errors


def test_baggages_that_fail_are_dropped() -> None:
    # an unknown compression, a ZIP of more than one entry, an unreadable file time
    names, _files, errors = _names(odd_tables())
    assert names == [
        "README.TXT",
        "plugin.msi",
        "tool.exe",
        "zipped.txt",
        "hidden.txt",
        "empty.txt",
    ]
    assert [e.split(":")[0] for e in errors] == [
        "DifferentRefIdsForOneDbId",
        "CreatingBaggageFailed",
        "TooManyBaggagesInArchive",
        "CreatingBaggageFailed",
        "CreatingBaggageFailed",
    ]
    assert "LocalFileName: undated.txt" in errors[-1]


def test_zip_with_a_folder_has_more_than_one_entry() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(zipfile.ZipInfo("dir/"), b"")
        zf.writestr(zipfile.ZipInfo("dir/f.txt"), b"f")
    assert _zipped_time(buffer.getvalue()) == "failed: CreatingBaggageFailed"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(zipfile.ZipInfo("dir/"), b"")
    assert _zipped_time(buffer.getvalue()) == "failed: CreatingBaggageFailed"


@pytest.mark.parametrize(
    ("folder", "name", "row", "fails"),
    [
        ("x", "a|b.txt", {}, True),
        ('x"', "a.txt", {}, True),
        ("x", "a?.txt", {}, True),
        ("x", "a:b.txt", {}, True),
        ("x", "", {}, True),
        ("x", "a/b.txt", {}, True),
        ("x/", "a.txt", {}, True),
        # one backslash between the folder names; trailing ones are dropped
        ("x\\\\y", "a.txt", {}, True),
        ("x\\y\\\\", "a.txt", {}, False),
        ("x\ty", "a.txt", {}, True),
        # a folder with a drive is ignored
        ("D:rel", "a.txt", {}, False),
        ("x", "a.txt", {"COMPRESSION": "Z", "DATA": None}, True),
        ("x", "a.txt", {"LOCALFILETIME": "2014-13-01"}, True),
    ],
)
def test_baggages_with_paths_the_converter_cannot_write(
    folder: str, name: str, row: dict[str, Any], fails: bool
) -> None:
    t = baggage_tables()
    t["baggage"] = [{**_bag(1, folder, name, data=b"a", time="2014-07-01"), **row}]
    files, _warnings, errors = run(t)
    written = [n for n in files if "/Baggages/" in n]
    assert (written == []) == fails
    assert any(e.startswith("CreatingBaggageFailed") for e in errors) == fails


def test_baggage_without_data_is_an_empty_file() -> None:
    _, files, _errors = _names(odd_tables())
    assert files.get("M-0083/Baggages/x/empty.txt") == b""


def test_baggage_without_time_has_the_conversion_time() -> None:
    t = baggage_tables()
    t["baggage"] = [_bag(1, None, "plain.txt", data=b"x")]
    files, _warnings, _errors = run(t)
    info = find(xml(files, "M-0083/Baggages.xml"), ".//{}FileInfo")
    assert info.attrib == {"TimeInfo": "2026-11-02T11:00:00.123456Z"}


def test_unknown_product_baggage_has_an_empty_ref() -> None:
    files, _warnings, _errors = run(odd_tables())
    product = find(xml(files, "M-0083/Hardware.xml"), ".//{}Product")
    refs = [b.get("RefId") for b in every(product, "Baggage")]
    assert refs == [f"{BG}-tool.2Eexe", ""]


def _lost(manufacturer: int) -> Tables:
    t = baggage_tables()
    t["baggage"] = [_bag(908, "x", "lost.txt", manufacturer=manufacturer, data=b"lost")]
    t["applicationprogrambaggage"] = []
    t["catalogentrybaggage"] = []
    return t


def test_unknown_manufacturer_of_a_baggage_is_reported() -> None:
    # M-0041 is missing in the master data and left out
    files, _warnings, errors = run(_lost(0x41))
    assert errors == ["UnknownManufacturerId: Baggage x/lost.txt"]
    assert not any(name.startswith("M-0041") for name in files)


def test_baggage_of_a_manufacturer_missing_in_the_master_data_fails() -> None:
    # any other manufacturer the master data lacks: the master data is outdated
    with pytest.raises(ConversionError, match="manufacturer id = M-0063"):
        run(_lost(99))


def test_program_file_version() -> None:
    t = baggage_tables()
    t["baggage"] = [_bag(1, None, "tool.dll", data=program_file((1, 0, 83, 1)))]
    files, _warnings, _errors = run(t)
    info = find(xml(files, "M-0083/Baggages.xml"), ".//{}FileInfo")
    assert info.attrib == {
        "Version": "1.0.83.1",
        "TimeInfo": "2026-11-02T11:00:00.123456Z",
    }


def test_baggage_times_and_versions() -> None:
    assert vd_baggage.parse_time("2014-7-1") == dt.datetime(2014, 7, 1)
    assert vd_baggage.parse_time("2014-02-30 10:00") is None
    assert vd_baggage.parse_time("") is None
    assert vd_baggage.parse_time("07/01/2014 10:20:30.5") == dt.datetime(
        2014, 7, 1, 10, 20, 30, 500000
    )
    assert vd_baggage.xml_time(dt.datetime(2014, 7, 1, 10, 20, 30, 500000)) == (
        "2014-07-01T10:20:30.5Z"
    )
