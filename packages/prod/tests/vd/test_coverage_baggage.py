"""Baggage files: versions, file times, duplicates, compression, program and product refs."""

from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
from typing import Any

import pytest

from xknxeditor.prod.vd import baggage as vd_baggage

from .test_coverage_files import msi
from .test_coverage_helpers import Tables, base, children, every, find, run, xml


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


@pytest.fixture(autouse=True)
def _summer_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # file times are local times; central European summer time here
    monkeypatch.setattr(vd_baggage, "utc_offset", lambda _: dt.timedelta(hours=2))


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
            [("FileInfo", {"TimeInfo": baggages[3][1][0][1]["TimeInfo"]})],
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


@pytest.mark.xfail(
    reason="the converter writes the replacing file into the folder of the first one "
    "(docs/README.TXT)",
    strict=True,
)
def test_replacing_file_keeps_the_first_folder_spelling() -> None:
    files, _warnings, _errors = run(baggage_tables())
    assert "M-0083/Baggages/docs/README.TXT" in files


@pytest.mark.xfail(
    reason="the converter reads the time of a zipped file one hour later in summer "
    "(2015-07-03T03:05:06Z for an entry of 04:05:06)",
    strict=True,
)
def test_time_of_a_zipped_file() -> None:
    files, _warnings, _errors = run(baggage_tables())
    root = xml(files, "M-0083/Baggages.xml")
    info = find(root, ".//{}Baggage[@Name='zipped.txt']/{}FileInfo")
    assert info.get("TimeInfo") == "2015-07-03T03:05:06Z"


def _names(tables: Tables) -> tuple[list[str], dict[str, bytes], list[str]]:
    files, _warnings, errors = run(tables)
    root = xml(files, "M-0083/Baggages.xml")
    return [b.get("Name") or "" for b in every(root, "Baggage")], files, errors


@pytest.mark.xfail(
    reason="the converter drops baggages with an unknown compression, a ZIP of more than "
    "one entry or an unreadable file time (CreatingBaggageFailed, TooManyBaggagesInArchive)",
    strict=True,
)
def test_baggages_that_fail_are_dropped() -> None:
    names, _files, errors = _names(odd_tables())
    assert "unknown.bin" not in names
    assert "two.bin" not in names
    assert "undated.txt" not in names
    assert any(e.startswith("CreatingBaggageFailed") for e in errors)


@pytest.mark.xfail(
    reason="the converter writes an empty file for a baggage without data", strict=True
)
def test_baggage_without_data_is_an_empty_file() -> None:
    _, files, _errors = _names(odd_tables())
    assert files.get("M-0083/Baggages/x/empty.txt") == b""


@pytest.mark.xfail(
    reason='the converter refers to an unknown baggage of a product with RefId=""',
    strict=True,
)
def test_unknown_product_baggage_has_an_empty_ref() -> None:
    files, _warnings, _errors = run(odd_tables())
    product = find(xml(files, "M-0083/Hardware.xml"), ".//{}Product")
    refs = [b.get("RefId") for b in every(product, "Baggage")]
    assert refs == [f"{BG}-tool.2Eexe", ""]


def _lost() -> Tables:
    t = baggage_tables()
    t["baggage"] = [_bag(908, "x", "lost.txt", manufacturer=99, data=b"lost")]
    t["applicationprogrambaggage"] = []
    t["catalogentrybaggage"] = []
    return t


def test_unknown_manufacturer_of_a_baggage_is_reported() -> None:
    _files, _warnings, errors = run(_lost())
    assert errors == ["UnknownManufacturerId: Baggage x/lost.txt"]


@pytest.mark.xfail(
    reason="the converter writes the baggages of a manufacturer missing in the master data "
    "(M-0063/Baggages.xml with the file)",
    strict=True,
)
def test_baggage_of_an_unknown_manufacturer_is_written() -> None:
    files, _warnings, _errors = run(_lost())
    assert "M-0063/Baggages/x/lost.txt" in files


def test_program_file_version() -> None:
    t = baggage_tables()
    t["baggage"] = [_bag(1, None, "tool.dll", data=program_file((1, 0, 83, 1)))]
    files, _warnings, _errors = run(t)
    info = find(xml(files, "M-0083/Baggages.xml"), ".//{}FileInfo")
    assert info.attrib == {"Version": "1.0.83.1"}


def test_baggage_times_and_versions() -> None:
    assert vd_baggage.parse_time("2014-7-1") == dt.datetime(2014, 7, 1)
    assert vd_baggage.parse_time("2014-02-30 10:00") is None
    assert vd_baggage.parse_time("") is None
