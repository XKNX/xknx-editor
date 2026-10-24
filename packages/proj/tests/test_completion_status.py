"""The "Status" (CompletionStatus) of the project, areas, lines, devices, spaces and functions:
undoable edits, import, export and the move out of the raw attributes of older projects."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from xknxeditor.proj import ProjectService, export_knxproj
from xknxeditor.proj.core import knxproj_import


def _project(tmp_path: Path) -> tuple[ProjectService, str, dict[str, int]]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = svc.create_area(pid, 0, 2, "A")
    line = svc.create_line(pid, area, 1, "L")
    segment = next(
        ln.segments[0].id
        for a in svc.topology(pid, 0).areas
        if a.id == area
        for ln in a.lines
        if ln.id == line
    )
    device = svc.add_device(
        pid,
        segment,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    space = svc.space_tree(pid, 0)[0].id
    function = svc.create_function(pid, space, "FT-1", "Light")
    return (
        svc,
        pid,
        {
            "project": 0,
            "area": area,
            "line": line,
            "device": device,
            "space": space,
            "function": function,
        },
    )


def test_every_kind_has_an_undoable_status(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    for kind, element_id in ids.items():
        assert svc.completion_status(pid, kind, element_id) == "Undefined"
        svc.set_completion_status(pid, kind, element_id, "Tested")
    for kind, element_id in ids.items():
        assert svc.completion_status(pid, kind, element_id) == "Tested"
    for kind, element_id in reversed(ids.items()):
        svc.undo(pid)
        assert svc.completion_status(pid, kind, element_id) == "Undefined"
    svc.redo(pid)
    assert svc.completion_status(pid, "project", 0) == "Tested"
    assert svc.completion_status(pid, "area", -1) is None
    with pytest.raises(ValueError, match="unknown status"):
        svc.set_completion_status(pid, "area", ids["area"], "Done")
    with pytest.raises(ValueError, match="no status"):
        svc.set_completion_status(pid, "group_address", 1, "Tested")


def test_the_export_writes_every_status_but_the_default(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    values = {
        "project": "Editing",
        "area": "FinishedDesign",
        "line": "FinishedCommissioning",
        "device": "Accepted",
        "space": "Locked",
    }
    for kind, status in values.items():
        svc.set_completion_status(pid, kind, ids[kind], status)
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out)
    with zipfile.ZipFile(out) as zf:
        zero = ET.fromstring(
            zf.read(next(n for n in zf.namelist() if n.endswith("/0.xml")))
        )
        info = ET.fromstring(
            zf.read(next(n for n in zf.namelist() if n.endswith("/project.xml")))
        )

    def status(root: ET.Element, tag: str, **attrs: str) -> str | None:
        element = next(
            e
            for e in root.iter()
            if e.tag.rsplit("}", 1)[-1] == tag
            and all(e.get(k) == v for k, v in attrs.items())
        )
        return element.get("CompletionStatus")

    assert status(info, "ProjectInformation") == "Editing"
    assert status(zero, "Area", Address="2") == "FinishedDesign"
    assert status(zero, "Line", Name="L") == "FinishedCommissioning"
    assert status(zero, "DeviceInstance", Name="Dev") == "Accepted"
    assert status(zero, "Space", Type="Building") == "Locked"
    # The default is not written, as in genuine projects.
    assert status(zero, "Function", Name="Light") is None


class _FakeContents:
    def __init__(self, zero_xml: bytes) -> None:
        self._zero = zero_xml

    def open_project_0(self) -> io.BytesIO:
        return io.BytesIO(self._zero)


def test_the_import_reads_the_status_of_every_element() -> None:
    body = (
        "<Topology><Area Id='P-1-0_A-1' Address='1' CompletionStatus='Tested'>"
        "<Line Id='P-1-0_L-1' Address='1' CompletionStatus='Editing'>"
        "<Segment Id='P-1-0_S-1' Number='0' MediumTypeRefId='MT-0' CompletionStatus='Locked'>"
        '<DeviceInstance Id="P-1-0_DI-1" Address="1" CompletionStatus="Accepted"/>'
        '<DeviceInstance Id="P-1-0_DI-2" Address="2" CompletionStatus="Bogus"/>'
        "</Segment></Line></Area></Topology>"
        "<Locations><Space Id='P-1-0_BP-1' Type='Building' Name='B'"
        " CompletionStatus='FinishedDesign'>"
        "<Function Id='P-1-0_F-1' Name='F' Type='FT-1' CompletionStatus='FinishedCommissioning'/>"
        "</Space></Locations>"
    )
    zero = (
        '<?xml version="1.0" encoding="utf-8"?><KNX xmlns="http://knx.org/xml/project/23">'
        f"<Project><Installations><Installation>{body}</Installation></Installations>"
        "</Project></KNX>"
    ).encode()
    extras = knxproj_import._read_device_extras(_FakeContents(zero))
    assert extras.completion_status == {
        "P-1-0_A-1": "Tested",
        "P-1-0_L-1": "Editing",
        "P-1-0_S-1": "Locked",
        "P-1-0_DI-1": "Accepted",
        "P-1-0_BP-1": "FinishedDesign",
        # A function by its id without the project prefix, as xknxproject names it.
        "F-1": "FinishedCommissioning",
    }
    # The device's status has its own column, not the raw attributes.
    assert "CompletionStatus" not in extras.device_extra_attributes.get(
        "P-1-0_DI-1", {}
    )


def test_older_projects_move_the_status_into_its_column(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    svc.close(pid)
    path = tmp_path / "p.xknx"
    # A project saved before the columns kept the device's and the project's status raw.
    con = sqlite3.connect(path)
    con.execute(
        "UPDATE devices SET extra_attributes = ? WHERE id = ?",
        (
            json.dumps({"CompletionStatus": "Accepted", "UniqueId": "U-1"}),
            ids["device"],
        ),
    )
    con.execute(
        "UPDATE projects SET extra_attributes = ?",
        (json.dumps({"CompletionStatus": "Editing"}),),
    )
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()

    svc2 = ProjectService()
    pid2 = svc2.open(path)
    assert svc2.completion_status(pid2, "device", ids["device"]) == "Accepted"
    assert svc2.completion_status(pid2, "project", 0) == "Editing"
    svc2.close(pid2)
    con = sqlite3.connect(path)
    try:
        (device_extra,) = con.execute(
            "SELECT extra_attributes FROM devices WHERE id = ?", (ids["device"],)
        ).fetchone()
        (project_extra,) = con.execute(
            "SELECT extra_attributes FROM projects"
        ).fetchone()
    finally:
        con.close()
    assert json.loads(device_extra) == {"UniqueId": "U-1"}
    assert project_extra is None
