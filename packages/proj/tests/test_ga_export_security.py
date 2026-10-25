"""A group address export (XML and CSV) carries the Security of main and middle groups and their
description, and is written back byte for byte: a synthetic file in the layout of a genuine
export (comments, BOM, CRLF, tab separated cp1252 CSV)."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.ga_export import (
    import_ga_export,
    read_ga_csv,
    read_ga_export,
    write_ga_csv,
    write_ga_export,
)

_XML_LINES = [
    '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
    '<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">',
    "  <!--GroupRange:-->",
    "  <!--  @Name: The name of the group range (1 to 255 characters)-->",
    "  <!--  @RangeStart: The inclusive start address of the range as specified in free group"
    " address style-->",
    "  <!--  @RangeEnd: The inclusive end address of the range as specified in free group"
    " address style-->",
    "  <!--  @Description: The group ranges' description if available-->",
    "  <!--GroupAddress:-->",
    "  <!--  @Name: The name of the group range (1 to 255 characters)-->",
    "  <!--  @Address: The address as specified either in three-level (1/2/3 or two-level"
    " (1/3) or numeric (12345) or hexadecimal ($1Fa3 or 0x1fa3) group address value-->",
    "  <!--  @Central: True if the group address has the Central flag set-->",
    "  <!--  @Unfiltered: True if the group address has the Unfiltered flag set-->",
    "  <!--  @Description: The group addresses' description if available-->",
    "  <!--  @DTPs: The Data Point Types associated with the group address if available, comma"
    " separated, e.g. '1.001, 1.002'-->",
    '  <GroupRange Name="Licht" RangeStart="1" RangeEnd="2047" Description="EG">',
    '    <GroupRange Name="Schalten" RangeStart="1" RangeEnd="255" Security="Off">',
    '      <GroupAddress Name="Küche" Address="0/0/1" DPTs="DPST-1-1" Security="On" />',
    '      <GroupAddress Name="Alle" Address="0/0/2" Central="true" DPTs="DPST-1-1" />',
    "    </GroupRange>",
    "  </GroupRange>",
    '  <GroupRange Name="Neu" RangeStart="2048" RangeEnd="4095" Security="On" />',
    "</GroupAddress-Export>",
]
_XML = "﻿" + "\r\n".join(_XML_LINES)

_CSV_ROWS = [
    [
        "Main",
        "Middle",
        "Sub",
        "Address",
        "Central",
        "Unfiltered",
        "Description",
        "DatapointType",
        "Security",
    ],
    ["Licht", "", "", "0/-/-", "", "", "EG", "", "Auto"],
    ["", "Schalten", "", "0/0/-", "", "", "", "", "Off"],
    ["", "", "Küche", "0/0/1", "", "", "", "DPST-1-1", "On"],
    ["", "", "Alle", "0/0/2", "true", "", "", "DPST-1-1", "Auto"],
    ["Neu", "", "", "1/-/-", "", "", "", "", "On"],
]
_CSV = "".join(
    "\t".join(f'"{cell}"' for cell in row) + "\r\n" for row in _CSV_ROWS
).encode("cp1252")


def test_the_security_of_the_groups_is_read_from_both_formats(tmp_path: Path) -> None:
    xml = tmp_path / "ga.xml"
    xml.write_bytes(_XML.encode())
    csv = tmp_path / "ga.csv"
    csv.write_bytes(_CSV)
    for export in (read_ga_export(xml), read_ga_csv(csv)):
        main, new = export.ranges
        assert (main.security, main.description) == (None, "EG")
        assert main.children[0].security == "Off"
        assert new.security == "On"
        assert {a.address: a.security for a in export.addresses} == {1: "On", 2: None}


def test_both_formats_are_written_back_byte_for_byte(tmp_path: Path) -> None:
    for name, data in (("ga.xml", _XML.encode()), ("ga.csv", _CSV)):
        source = tmp_path / name
        source.write_bytes(data)
        project = tmp_path / f"{name}.xknx"
        import_ga_export(source, project)
        xml_out, csv_out = tmp_path / f"{name}.out.xml", tmp_path / f"{name}.out.csv"
        write_ga_export(project, xml_out)
        write_ga_csv(project, csv_out, separator="\t", layout="3/1", header=True)
        assert xml_out.read_bytes() == _XML.encode()
        assert csv_out.read_bytes() == _CSV


def test_an_import_sets_the_security_of_existing_groups(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    svc.create_group_address(pid, 0, 1, "Küche")
    source = tmp_path / "ga.xml"
    source.write_bytes(_XML.encode())
    svc.import_group_addresses(pid, 0, read_ga_export(source))

    main = svc.group_ranges(pid, 0)[0]
    assert main.children[0].security_mode == "Off"
    assert svc.group_ranges(pid, 0)[1].security_mode == "On"
    svc.undo(pid)
    assert svc.group_ranges(pid, 0)[0].children[0].security_mode is None
    svc.close(pid)
