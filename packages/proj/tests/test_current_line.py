"""A space's "Current Line" (``@DefaultLine``): set and cleared as undo steps, inherited from the
spaces above, placing the devices added to the space, cleared with its line, and kept on export,
import and in older projects."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core.events import RemoveArea, RemoveLine
from xknxeditor.proj.core.service import CurrentLine


def _lines(svc: ProjectService, pid: str) -> dict[str, int]:
    """Every line by its address ("1.1")."""
    return {
        f"{area.address}.{line.address}": line.id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
    }


def _project(tmp_path: Path) -> tuple[ProjectService, str, dict[str, int]]:
    """A new project with area 2 (lines 2.1, 2.2) and Building > Floor > Room."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = svc.create_area(pid, 0, 2, "A")
    svc.create_line(pid, area, 1, "L1")
    svc.create_line(pid, area, 2, "L2")
    building = svc.space_tree(pid, 0)[0].id
    floor = svc.create_space(pid, 0, "Floor", "F", building)
    room = svc.create_space(pid, 0, "Room", "R", floor)
    return svc, pid, {"building": building, "floor": floor, "room": room}


def test_a_new_project_s_building_has_line_1_1(tmp_path: Path) -> None:
    svc, pid, _spaces = _project(tmp_path)
    building = svc.space_tree(pid, 0)[0]
    assert building.default_line_id == _lines(svc, pid)["1.1"]


def test_a_space_without_its_own_inherits_the_line_from_above(tmp_path: Path) -> None:
    svc, pid, spaces = _project(tmp_path)
    lines = _lines(svc, pid)
    building, floor, room = spaces["building"], spaces["floor"], spaces["room"]
    assert svc.current_line(pid, room) == CurrentLine(lines["1.1"], building)

    svc.set_space_default_line(pid, floor, lines["2.1"])
    assert svc.current_line(pid, room) == CurrentLine(lines["2.1"], floor)
    assert svc.current_line(pid, building) == CurrentLine(lines["1.1"], building)
    svc.set_space_default_line(pid, room, lines["2.2"])
    assert svc.current_line(pid, room) == CurrentLine(lines["2.2"], room)

    svc.set_space_default_line(pid, building, None)
    svc.set_space_default_line(pid, room, None)
    assert svc.current_line(pid, room) == CurrentLine(lines["2.1"], floor)
    svc.set_space_default_line(pid, floor, None)
    assert svc.current_line(pid, room) is None
    assert svc.current_line_segment(pid, room) is None

    # Each change is its own undo step.
    svc.undo(pid)
    assert svc.current_line(pid, room) == CurrentLine(lines["2.1"], floor)
    svc.undo(pid)
    assert svc.current_line(pid, room) == CurrentLine(lines["2.2"], room)
    svc.redo(pid)
    assert svc.current_line(pid, room) == CurrentLine(lines["2.1"], floor)


def test_the_current_line_needs_a_known_space_and_line(tmp_path: Path) -> None:
    svc, pid, spaces = _project(tmp_path)
    with pytest.raises(ValueError, match="unknown space"):
        svc.set_space_default_line(pid, 999, None)
    with pytest.raises(ValueError, match="unknown line"):
        svc.set_space_default_line(pid, spaces["room"], 999)
    # Setting the line a space already has is no step.
    before = len(svc.history(pid))
    building = spaces["building"]
    svc.set_space_default_line(pid, building, _lines(svc, pid)["1.1"])
    assert len(svc.history(pid)) == before


def test_removing_the_line_or_its_area_clears_it_until_undone(tmp_path: Path) -> None:
    svc, pid, spaces = _project(tmp_path)
    lines = _lines(svc, pid)
    floor, room = spaces["floor"], spaces["room"]
    svc.set_space_default_line(pid, floor, lines["2.1"])
    svc.set_space_default_line(pid, room, lines["2.2"])

    svc.remove_line(pid, lines["2.2"])
    assert svc.current_line(pid, room) == CurrentLine(lines["2.1"], floor)
    svc.undo(pid)
    assert svc.current_line(pid, room) == CurrentLine(lines["2.2"], room)

    area = next(a.id for a in svc.topology(pid, 0).areas if a.address == 2)
    svc.remove_area(pid, area)
    assert svc.current_line(pid, floor) == CurrentLine(lines["1.1"], spaces["building"])
    assert svc.space_tree(pid, 0)[0].children[0].default_line_id is None
    svc.undo(pid)
    assert svc.current_line(pid, room) == CurrentLine(lines["2.2"], room)
    assert svc.current_line(pid, floor) == CurrentLine(lines["2.1"], floor)


def test_the_cleared_spaces_survive_the_stored_history() -> None:
    event = RemoveLine(target_id=3, space_lines={7: 3})
    assert RemoveLine.from_dict(event.to_dict()).space_lines == {7: 3}
    area = RemoveArea(target_id=2, space_lines={5: 4, 6: 4})
    assert RemoveArea.from_dict(area.to_dict()).space_lines == {5: 4, 6: 4}


def test_a_device_added_to_a_space_is_placed_on_its_current_line(
    tmp_path: Path,
) -> None:
    svc, pid, spaces = _project(tmp_path)
    lines = _lines(svc, pid)
    svc.set_space_default_line(pid, spaces["floor"], lines["2.2"])
    segment = svc.current_line_segment(pid, spaces["room"])
    line = next(
        ln
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == lines["2.2"]
    )
    assert segment == line.segments[0].id

    device = svc.add_device(
        pid,
        segment,
        "M-1_H-1_P-1",
        address=1,
        hardware2program_ref_id="M-1_H-1_HP-1",
        space_id=spaces["room"],
    )
    room = svc.space_tree(pid, 0)[0].children[0].children[0]
    assert [d.id for d in room.devices] == [device]
    # Adding it to the space is part of the same undo step.
    svc.undo(pid)
    room = svc.space_tree(pid, 0)[0].children[0].children[0]
    assert room.devices == []
    assert svc.unassigned_devices(pid, 0) == []


def _zero(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(
            zf.read(next(n for n in zf.namelist() if n.endswith("/0.xml")))
        )


def _elements(root: ET.Element, tag: str) -> list[ET.Element]:
    return [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == tag]


def test_export_and_import_keep_the_current_line(tmp_path: Path) -> None:
    svc, pid, spaces = _project(tmp_path)
    lines = _lines(svc, pid)
    svc.set_space_default_line(pid, spaces["room"], lines["2.2"])
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23")
    zero = _zero(out)
    line_ids = {
        f"{area.get('Address')}.{line.get('Address')}": line.get("Id")
        for area in _elements(zero, "Area")
        for line in _elements(area, "Line")
    }
    defaults = {s.get("Name"): s.get("DefaultLine") for s in _elements(zero, "Space")}
    assert defaults["R"] == line_ids["2.2"]
    assert defaults["F"] is None
    assert defaults[next(iter(defaults))] == line_ids["1.1"]

    imported = tmp_path / "imported.xknx"
    import_knxproj(out, imported)
    svc2 = ProjectService()
    pid2 = svc2.open(imported)
    lines2 = _lines(svc2, pid2)
    building = svc2.space_tree(pid2, 0)[0]
    room = building.children[0].children[0]
    assert building.default_line_id == lines2["1.1"]
    assert room.default_line_id == lines2["2.2"]
    svc2.close(pid2)


def test_older_projects_link_the_stored_line_id(tmp_path: Path) -> None:
    svc, pid, spaces = _project(tmp_path)
    lines = _lines(svc, pid)
    svc.close(pid)
    path = tmp_path / "p.xknx"
    # A project of version 3 kept the line's raw @Id in a text column.
    con = sqlite3.connect(path)
    con.execute(
        "UPDATE lines SET source_id = 'P-0C01-0_L-7' WHERE id = ?", (lines["2.1"],)
    )
    con.execute(
        "ALTER TABLE spaces ADD COLUMN default_line VARCHAR NOT NULL DEFAULT ''"
    )
    con.execute("UPDATE spaces SET default_line_id = NULL")
    con.execute(
        "UPDATE spaces SET default_line = 'P-0C01-0_L-7' WHERE id = ?",
        (spaces["floor"],),
    )
    con.execute(
        "UPDATE spaces SET default_line = 'P-0C01-0_L-99' WHERE id = ?",
        (spaces["room"],),
    )
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()

    svc2 = ProjectService()
    pid2 = svc2.open(path)
    # The line it names is linked; a line the project no longer has is dropped.
    assert svc2.current_line(pid2, spaces["room"]) == CurrentLine(
        lines["2.1"], spaces["floor"]
    )
    assert svc2.current_line(pid2, spaces["building"]) is None
    svc2.close(pid2)
    con = sqlite3.connect(path)
    try:
        columns = {row[1] for row in con.execute('PRAGMA table_info("spaces")')}
        (version,) = con.execute("PRAGMA user_version").fetchone()
    finally:
        con.close()
    assert "default_line" not in columns
    assert version >= 4
