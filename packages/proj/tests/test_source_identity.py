"""An imported project keeps its ids and Puids on export: the project id, every element's ``Id`` and
``Puid`` and ``LastUsedPuid``; elements added in the editor are numbered after them. The
``ProjectType`` and each segment's ``BusAccess`` round-trip too."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Device,
    DeviceAdditionalAddress,
    GroupAddress,
    Project,
)

_BUS_ACCESS = 'Type=IpTunneling;HostAddress=192.168.16.103;Name="IP Tunneling"'


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _created(tmp_path: Path) -> Path:
    """A project with one area, line, device and group address, created in the editor."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-NEW")
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    line_id = svc.create_line(pid, area_id, 2, "Line 2")
    svc.add_device(
        pid,
        _segment_of(svc, pid, line_id),
        "M-1_H-1_P-1",
        address=5,
        name="Switch",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.create_group_address(pid, 0, 0x0802, "Light")
    svc.close(pid)
    return src


def _segment_of(svc: ProjectService, pid: str, line_id: int | None = None) -> int:
    """The first segment of the line ``line_id`` (or of the line named "Line 2")."""
    return next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == line_id or (line_id is None and line.name == "Line 2")
    )


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _with_bus_access(zero_xml: str) -> str:
    """``zero_xml`` with a BusAccess after the devices of the segment holding the device."""
    root = ET.fromstring(zero_xml)
    ns = root.tag[1:].split("}", 1)[0]
    segment = _segment_with(root, "DeviceInstance")
    devices = [
        i for i, c in enumerate(segment) if _localname(c.tag) == "DeviceInstance"
    ]
    access = ET.Element(f"{{{ns}}}BusAccess", Name="", Parameter=_BUS_ACCESS)
    segment.insert(devices[-1] + 1, access)
    ET.register_namespace("", ns)
    return ET.tostring(root, encoding="unicode")


def _as_source_project(
    exported: Path, dest: Path, *, default_line: bool = True
) -> None:
    """Rewrite an export into what a source project looks like: the project P-04BB, a device and a
    group address with numbers of a long-edited project, Puids from 1001, LastUsedPuid 3178, a
    ProjectType and a segment's BusAccess."""
    members = _members(exported)
    pid = next(n for n in members if n.endswith("/0.xml")).split("/")[0]
    out: dict[str, bytes] = {}
    for name, data in members.items():
        if name.endswith((".xml", ".info")):
            text = data.decode("utf-8").replace(pid, "P-04BB")
            text = re.sub(r"_DI-1(?!\d)", "_DI-380", text)
            text = re.sub(r'(?<=[_\s"])GA-1(?!\d)', "GA-86", text)
            text = re.sub(
                r'(?<!LastUsed)Puid="(\d+)"',
                lambda m: f'Puid="{int(m.group(1)) + 1000}"',
                text,
            )
            text = re.sub(r'LastUsedPuid="\d+"', 'LastUsedPuid="3178"', text)
            text = text.replace(
                "<ProjectInformation ",
                '<ProjectInformation ProjectType="Family House" ',
                1,
            )
            if name.endswith("/0.xml"):
                text = _with_bus_access(text)
                if not default_line:
                    text = re.sub(r' DefaultLine="[^"]*"', "", text, count=1)
            data = text.encode("utf-8")
        out[name.replace(pid, "P-04BB")] = data
    with zipfile.ZipFile(dest, "w") as zf:
        for name, data in out.items():
            zf.writestr(name, data)


def _zero(path: Path) -> ET.Element:
    members = _members(path)
    return ET.fromstring(next(d for n, d in members.items() if n.endswith("/0.xml")))


def _info(path: Path) -> ET.Element:
    members = _members(path)
    root = ET.fromstring(
        next(d for n, d in members.items() if n.endswith("/project.xml"))
    )
    return next(e for e in root.iter() if _localname(e.tag) == "ProjectInformation")


def _element(root: ET.Element, tag: str, **attrs: str) -> ET.Element:
    return next(
        e
        for e in root.iter()
        if _localname(e.tag) == tag and all(e.get(k) == v for k, v in attrs.items())
    )


def _segment_with(root: ET.Element, child: str) -> ET.Element:
    return next(
        e
        for e in root.iter()
        if _localname(e.tag) == "Segment" and any(_localname(c.tag) == child for c in e)
    )


def _imported(tmp_path: Path, *, default_line: bool = True) -> tuple[Path, ET.Element]:
    """The rewritten source project imported, and its own 0.xml."""
    first = tmp_path / "first.knxproj"
    export_knxproj(_created(tmp_path), first, schema="23")
    source = tmp_path / "source.knxproj"
    _as_source_project(first, source, default_line=default_line)
    imported = tmp_path / "imported.xknx"
    import_knxproj(source, imported)
    return imported, _zero(source)


def test_an_export_keeps_the_ids_and_puids_of_the_source(tmp_path: Path) -> None:
    imported, source = _imported(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(imported, out, schema="23")

    assert any(n.startswith("P-04BB/") for n in _members(out))
    zero = _zero(out)

    def identities(root: ET.Element) -> set[tuple[str, str, str]]:
        return {
            (_localname(e.tag), e.get("Id", ""), e.get("Puid", ""))
            for e in root.iter()
            if "Puid" in e.attrib
        }

    assert identities(zero) == identities(source)
    assert (
        _element(zero, "DeviceInstance", Name="Switch").get("Id") == "P-04BB-0_DI-380"
    )
    assert _element(zero, "GroupAddress", Name="Light").get("Id") == "P-04BB-0_GA-86"
    assert _info(out).get("LastUsedPuid") == "3178"


def test_elements_added_in_the_editor_are_numbered_after_the_source(
    tmp_path: Path,
) -> None:
    imported, _ = _imported(tmp_path)
    svc = ProjectService()
    pid = svc.open(imported)
    svc.add_device(
        pid,
        _segment_of(svc, pid),
        "M-1_H-1_P-1",
        address=6,
        name="Added",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)
    out = tmp_path / "out.knxproj"
    export_knxproj(imported, out, schema="23")

    added = _element(_zero(out), "DeviceInstance", Name="Added")
    assert added.get("Id") == "P-04BB-0_DI-381"
    assert added.get("Puid") == "3179"
    assert _info(out).get("LastUsedPuid") == "3179"


def test_the_import_keeps_the_source_identities(tmp_path: Path) -> None:
    imported, _ = _imported(tmp_path)
    with Session(make_engine(url_for(imported))) as session:
        project = session.query(Project).one()
        assert (project.original_project_id, project.last_used_puid) == ("P-04BB", 3178)
        device = session.query(Device).one()
        assert device.source_id == "P-04BB-0_DI-380"
        assert device.puid is not None and device.puid > 1000
        ga = session.query(GroupAddress).one()
        assert ga.puid is not None and ga.puid > 1000


def test_project_type_and_bus_access_round_trip(tmp_path: Path) -> None:
    imported, _ = _imported(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(imported, out, schema="23")

    assert _info(out).get("ProjectType") == "Family House"
    segment = _segment_with(_zero(out), "BusAccess")
    children = [_localname(c.tag) for c in segment]
    assert _element(segment, "BusAccess").get("Parameter") == _BUS_ACCESS
    # Schema order: the devices first, then the bus access.
    assert children.index("BusAccess") > children.index("DeviceInstance")


def test_project_20_has_no_project_type_and_the_bus_access_on_the_line(
    tmp_path: Path,
) -> None:
    imported, _ = _imported(tmp_path)
    # Without the stored project/23 product data, an export to project/20 stays at 20.
    with Session(make_engine(url_for(imported))) as session:
        project = session.query(Project).one()
        project.knx_master_xml = None
        project.imported_product_members = None
        session.commit()
    out = tmp_path / "out.knxproj"
    export_knxproj(imported, out, schema="20")

    assert _zero(out).tag == "{http://knx.org/xml/project/20}KNX"
    assert _info(out).get("ProjectType") is None
    line = _element(_zero(out), "Line", Name="Line 2")
    access = [c for c in line if _localname(c.tag) == "BusAccess"]
    assert [a.get("Parameter") for a in access] == [_BUS_ACCESS]


def _installation(root: ET.Element) -> ET.Element:
    return _element(root, "Installation")


def test_the_default_line_of_the_source_is_kept(tmp_path: Path) -> None:
    imported, source = _imported(tmp_path)
    default_line = _installation(source).get("DefaultLine")
    assert default_line
    out = tmp_path / "out.knxproj"
    export_knxproj(imported, out, schema="23")
    assert _installation(_zero(out)).get("DefaultLine") == default_line


def test_no_default_line_when_the_source_had_none(tmp_path: Path) -> None:
    imported, source = _imported(tmp_path, default_line=False)
    assert _installation(source).get("DefaultLine") is None
    out = tmp_path / "out.knxproj"
    export_knxproj(imported, out, schema="23")
    assert _installation(_zero(out)).get("DefaultLine") is None


def test_a_project_created_in_the_editor_gets_a_default_line(tmp_path: Path) -> None:
    out = tmp_path / "out.knxproj"
    export_knxproj(_created(tmp_path), out, schema="23")
    default_line = _installation(_zero(out)).get("DefaultLine")
    assert default_line is not None
    assert _element(_zero(out), "Line", Id=default_line).get("Name") == "Line 2"


def test_empty_area_and_line_names(tmp_path: Path) -> None:
    """An empty name is left out in project/23 and kept in project/20."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-NAMES")
    area_id = svc.create_area(pid, 0, 3, "")
    svc.create_line(pid, area_id, 1, "")
    svc.close(pid)
    for schema, name in (("23", None), ("20", "")):
        out = tmp_path / f"out{schema}.knxproj"
        export_knxproj(src, out, schema=schema)
        area = _element(_zero(out), "Area", Address="3")
        assert area.get("Name") == name
        assert _element(area, "Line", Address="1").get("Name") == name


def test_an_additional_address_always_has_a_name(tmp_path: Path) -> None:
    src = _created(tmp_path)
    with Session(make_engine(url_for(src))) as session:
        device = session.query(Device).one()
        device.additional_addresses.append(DeviceAdditionalAddress(address=200))
        session.commit()
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    address = _element(_zero(out), "Address", Address="200")
    assert address.get("Name") == ""
