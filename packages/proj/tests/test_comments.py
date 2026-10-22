"""Comments of all elements and device installation hints: undoable edits, import and export."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj
from xknxeditor.proj.core import knxproj_import
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Device

_RTF = (
    "{\\rtf1\\ansi\\ansicpg1252\\uc1\\htmautsp\\deff2{\\fonttbl{\\f0\\fcharset0 Times New Roman;}"
    "{\\f2\\fcharset0 Segoe UI;}}{\\colortbl\\red0\\green0\\blue0;\\red255\\green255\\blue255;}"
    "\\loch\\hich\\dbch\\pard\\plain\\ltrpar\\itap0{\\lang1033\\fs16\\f2\\cf0 \\cf0\\ql{\\f2 "
    "{\\b\\ltrch bold}\\li0\\ri0\\sa0\\sb0\\fi0\\ql\\par}\r\n}\r\n}"
)


def _project(tmp_path: Path) -> tuple[ProjectService, str, int, int, int]:
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
    return svc, pid, area, line, device


def test_comments_of_every_kind_are_undoable(tmp_path: Path) -> None:
    svc, pid, area, line, device = _project(tmp_path)
    range_id = svc.create_group_range(pid, 0, None, "Main")
    assert range_id is not None
    ga = svc.create_group_address(pid, 0, 1, "Light")
    space = svc.space_tree(pid, 0)[0].id
    targets = [
        ("project", 0),
        ("area", area),
        ("line", line),
        ("device", device),
        ("space", space),
        ("group_range", range_id),
        ("group_address", ga),
    ]
    for kind, element_id in targets:
        svc.set_comment(pid, kind, element_id, f"{kind} {_RTF}")
    for kind, element_id in targets:
        assert svc.comment(pid, kind, element_id) == f"{kind} {_RTF}"
    for kind, element_id in reversed(targets):
        svc.undo(pid)
        assert svc.comment(pid, kind, element_id) == ""
    svc.redo(pid)
    assert svc.comment(pid, "project", 0) == f"project {_RTF}"
    assert svc.comment(pid, "area", -1) is None


def test_installation_hints_have_their_own_column(tmp_path: Path) -> None:
    svc, pid, _area, _line, device = _project(tmp_path)
    svc.close(pid)
    # A project saved before the column existed keeps them with the other raw attributes.
    engine = make_engine(url_for(tmp_path / "p.xknx"))
    try:
        with Session(engine) as session:
            row = session.query(Device).one()
            row.extra_attributes = {"InstallationHints": "old", "UniqueId": "U-1"}
            session.commit()
    finally:
        engine.dispose()
    pid = svc.open(tmp_path / "p.xknx")
    assert svc.comment(pid, "device", device, "installation_hints") == "old"

    svc.set_comment(pid, "device", device, _RTF, "installation_hints")
    assert svc.comment(pid, "device", device, "installation_hints") == _RTF
    assert svc.comment(pid, "device", device) == ""
    svc.undo(pid)
    assert svc.comment(pid, "device", device, "installation_hints") == "old"
    svc.redo(pid)
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out)
    with zipfile.ZipFile(out) as zf:
        zero = zf.read(next(n for n in zf.namelist() if n.endswith("/0.xml")))
    di = next(
        e
        for e in ET.fromstring(zero).iter()
        if e.tag.rsplit("}", 1)[-1] == "DeviceInstance"
    )
    assert di.get("InstallationHints") == _RTF
    assert di.get("UniqueId") == "U-1"


class _FakeContents:
    def __init__(self, zero_xml: bytes) -> None:
        self._zero = zero_xml

    def open_project_0(self) -> io.BytesIO:
        return io.BytesIO(self._zero)


def test_the_import_keeps_formatted_comments() -> None:
    # xknxproject turns group address and range comments into plain text: they are read raw.
    rtf = _RTF.replace("\r", "&#xD;").replace("\n", "&#xA;")
    body = (
        "<Topology><Area Address='1'><Line Address='1'><Segment Number='0' MediumTypeRefId='MT-0'>"
        f'<DeviceInstance Id="P-1-0_DI-1" Address="1" Comment="c" InstallationHints="{rtf}"/>'
        "</Segment></Line></Area></Topology>"
        "<GroupAddresses><GroupRanges>"
        f'<GroupRange RangeStart="1" RangeEnd="2047" Name="Main" Comment="{rtf}">'
        f'<GroupAddress Address="1" Name="Light" Comment="{rtf}"/>'
        "</GroupRange></GroupRanges></GroupAddresses>"
    )
    zero = (
        '<?xml version="1.0" encoding="utf-8"?><KNX xmlns="http://knx.org/xml/project/23">'
        f"<Project><Installations><Installation>{body}</Installation></Installations>"
        "</Project></KNX>"
    ).encode()
    extras = knxproj_import._read_device_extras(_FakeContents(zero))
    assert extras.device_installation_hints["P-1-0_DI-1"] == _RTF
    assert "InstallationHints" not in extras.device_extra_attributes.get(
        "P-1-0_DI-1", {}
    )
    assert extras.grouprange_comment[(1, 2047)] == _RTF
    assert extras.ga_comment[1] == _RTF
