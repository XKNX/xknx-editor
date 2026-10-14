"""Group address files: the XML and CSV export formats read alike, and an import into an open
project adds and updates folders and addresses as one undo step."""

import sqlite3
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.ga_export import read_group_address_file, write_ga_export

_XML = """﻿<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="Zentral" RangeStart="1" RangeEnd="2047">
    <GroupRange Name="Szenen" RangeStart="1" RangeEnd="255">
      <GroupAddress Name="Diagnose" Address="0/0/1" DPTs="DPST-5-5" />
      <GroupAddress Name="Binäruhr" Address="0/0/8" DPTs="DPST-1-1" />
    </GroupRange>
  </GroupRange>
  <GroupRange Name="Licht" RangeStart="2048" RangeEnd="4095">
    <GroupRange Name="EG" RangeStart="2048" RangeEnd="2303">
      <GroupAddress Name="Wohnen" Address="1/0/1" DPTs="DPST-1-1" Security="Off" />
    </GroupRange>
  </GroupRange>
</GroupAddress-Export>"""

_XML_WITHOUT_DPT = (
    _XML.replace(' DPTs="DPST-5-5"', "")
    .replace(' DPTs="DPST-1-1"', "")
    .replace(' Security="Off"', "")
)

_CSV_ROWS = [
    ("Zentral", "", "", "0/-/-", "", "", "", "", "Auto"),
    ("", "Szenen", "", "0/0/-", "", "", "", "", "Auto"),
    ("", "", "Diagnose", "0/0/1", "", "", "", "DPST-5-5", "Auto"),
    ("", "", "Binäruhr", "0/0/8", "", "", "", "DPST-1-1", "Auto"),
    ("Licht", "", "", "1/-/-", "", "", "", "", "Auto"),
    ("", "EG", "", "1/0/-", "", "", "", "", "Auto"),
    ("", "", "Wohnen", "1/0/1", "", "", "", "DPST-1-1", "Off"),
]


def _csv(columns: int) -> bytes:
    lines = ["\t".join(f'"{cell}"' for cell in row[:columns]) for row in _CSV_ROWS]
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


def _file(tmp_path: Path, name: str, data: bytes | str) -> Path:
    path = tmp_path / name
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def _tree(export):  # type: ignore[no-untyped-def]
    def flat(ranges, depth=0):  # type: ignore[no-untyped-def]
        for r in ranges:
            yield (depth, r.name, r.range_start, r.range_end)
            yield from flat(r.children, depth + 1)

    return list(flat(export.ranges))


@pytest.fixture(name="files")
def fixture_files(tmp_path: Path) -> dict[str, Path]:
    return {
        "xml": _file(tmp_path, "ga.xml", _XML),
        "xml_without_dpt": _file(tmp_path, "ga4.xml", _XML_WITHOUT_DPT),
        "csv": _file(tmp_path, "ga.csv", _csv(9)),
        "csv_4_columns": _file(tmp_path, "ga3.csv", _csv(4)),
    }


def test_all_formats_read_alike(files: dict[str, Path]) -> None:
    exports = {key: read_group_address_file(path) for key, path in files.items()}
    reference = exports["xml"]
    assert reference.style is GroupAddressStyle.THREE_LEVEL
    for key, export in exports.items():
        assert export.style is GroupAddressStyle.THREE_LEVEL, key
        assert _tree(export) == _tree(reference), key
        assert [(a.address, a.name) for a in export.addresses] == [
            (1, "Diagnose"),
            (8, "Binäruhr"),
            (2049, "Wohnen"),
        ], key
    for key in ("xml", "csv"):
        addresses = exports[key].addresses
        assert [a.datapoint_type for a in addresses] == [
            "DPST-5-5",
            "DPST-1-1",
            "DPST-1-1",
        ]
        assert [a.security for a in addresses] == [None, None, "Off"]
    for key in ("xml_without_dpt", "csv_4_columns"):
        assert all(a.datapoint_type is None for a in exports[key].addresses)


def test_csv_with_semicolons_and_utf8(tmp_path: Path) -> None:
    text = '"Licht";"";"";"1/-/-"\n"";"EG";"";"1/0/-"\n"";"";"Küche";"1/0/2"\n'
    export = read_group_address_file(_file(tmp_path, "semi.csv", text))
    assert [(a.address, a.name) for a in export.addresses] == [(2050, "Küche")]
    assert _tree(export) == [(0, "Licht", 2048, 4095), (1, "EG", 2048, 2303)]


def test_csv_without_addresses_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        read_group_address_file(_file(tmp_path, "empty.csv", '"a";"b"\n'))


def _new(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    return svc, svc.create(tmp_path / "p.xknx", "P-0001")


def _state(
    svc: ProjectService, pid: str
) -> tuple[list[tuple[str, str]], dict[str, tuple]]:
    names = [(r.name, str(r.range_start)) for r in svc.group_ranges(pid, 0)]
    gas = {
        ga.text: (ga.name, ga.datapoint_type, ga.description)
        for ga in svc.group_addresses(pid)
    }
    return names, gas


def test_import_adds_updates_and_undoes_in_one_step(
    tmp_path: Path, files: dict[str, Path]
) -> None:
    svc, pid = _new(tmp_path)
    existing = svc.create_group_address(pid, 0, 2049, "Alt")
    before = _state(svc, pid)

    event = svc.import_group_addresses(
        pid, 0, read_group_address_file(files["csv"]), label="Import"
    )
    assert (event.added, event.updated, event.skipped) == (2, 1, 0)
    folders, gas = _state(svc, pid)
    assert [name for name, _start in folders] == ["Zentral", "Licht"]
    assert gas == {
        "0/0/1": ("Diagnose", "DPST-5-5", ""),
        "0/0/8": ("Binäruhr", "DPST-1-1", ""),
        "1/0/1": ("Wohnen", "DPST-1-1", ""),
    }
    assert any(ga.id == existing for ga in svc.group_addresses(pid))

    svc.undo(pid)
    assert _state(svc, pid) == before
    svc.redo(pid)
    assert _state(svc, pid) == (folders, gas)


def test_import_keeps_values_the_file_has_not(
    tmp_path: Path, files: dict[str, Path]
) -> None:
    svc, pid = _new(tmp_path)
    svc.import_group_addresses(pid, 0, read_group_address_file(files["xml"]))
    svc.import_group_addresses(pid, 0, read_group_address_file(files["csv_4_columns"]))
    _folders, gas = _state(svc, pid)
    assert gas["1/0/1"] == ("Wohnen", "DPST-1-1", "")


def test_import_rejects_another_address_style(
    tmp_path: Path, files: dict[str, Path]
) -> None:
    svc = ProjectService()
    pid = svc.create(
        tmp_path / "two.xknx", "P-0002", group_address_style=GroupAddressStyle.TWO_LEVEL
    )
    with pytest.raises(ValueError):
        svc.import_group_addresses(pid, 0, read_group_address_file(files["xml"]))


def test_import_then_export_reproduces_the_file(
    tmp_path: Path, files: dict[str, Path]
) -> None:
    svc, pid = _new(tmp_path)
    svc.import_group_addresses(pid, 0, read_group_address_file(files["xml"]))
    project = tmp_path / "p.xknx"
    svc.close(pid)
    with sqlite3.connect(project) as db:
        assert db.execute(
            "select security_mode from group_addresses where address = 2049"
        ).fetchone() == ("Off",)
    out = tmp_path / "out.xml"
    write_ga_export(project, out)
    expected = _XML.replace("\n", "\r\n").encode("utf-8")
    assert out.read_bytes() == expected
