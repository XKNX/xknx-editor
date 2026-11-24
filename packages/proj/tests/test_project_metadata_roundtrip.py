"""Descriptive project metadata that xknxproject drops must survive import -> export -> re-read.

Beyond the coupler routing data (covered in ``test_coupler_roundtrip``), a genuine ``.knxproj``
carries free-text metadata all over the tree: the project comment, Area/Line/GroupRange/Space/
Function comments and descriptions, the DeviceInstance comment + LastModified, the group-address
``Central`` flag, and the whole ``<Trades>`` (Gewerke) tree. xknxproject surfaces almost none of it,
so the importer reads it from the raw XML and the exporter re-emits it. Every attribute used here is
valid in BOTH project/20 (ETS5) and project/23 (ETS6), so the round-trip is checked against both.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core import knxproj_import
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Area,
    Device,
    Function,
    GroupAddress,
    Line,
    Project,
    Space,
    Trade,
    TradeDevice,
)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _FakeContents:
    """Duck-types the ``open_project_0`` that the raw-XML readers use."""

    def __init__(self, zero_xml: bytes) -> None:
        self._zero = zero_xml

    def open_project_0(self) -> io.BytesIO:
        return io.BytesIO(self._zero)


def _zero_xml(installation_body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<KNX xmlns="http://knx.org/xml/project/23">'
        "<Project><Installations><Installation>"
        f"{installation_body}"
        "</Installation></Installations></Project></KNX>"
    ).encode()


def _read_zero_from_knxproj(knxproj: Path) -> bytes:
    with zipfile.ZipFile(knxproj) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        return zf.read(name)


def _read_project_xml_from_knxproj(knxproj: Path) -> bytes:
    with zipfile.ZipFile(knxproj) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/project.xml"))
        return zf.read(name)


def _find(root: ET.Element, local: str, **attrs: str) -> ET.Element:
    return next(
        e
        for e in root.iter()
        if _localname(e.tag) == local and all(e.get(k) == v for k, v in attrs.items())
    )


# --- raw-XML parsing (import side) ----------------------------------------


def test_reads_area_line_comments_and_central_and_grouprange_description() -> None:
    body = (
        "<Topology>"
        '<Area Address="1" Comment="area-c">'
        '<Line Address="2" Comment="line-c"><Segment Number="0" MediumTypeRefId="MT-0"/></Line>'
        "</Area></Topology>"
        "<GroupAddresses><GroupRanges>"
        '<GroupRange RangeStart="1" RangeEnd="2047" Name="Main" Description="range-d">'
        '<GroupAddress Address="1" Name="central" Central="true"/>'
        '<GroupAddress Address="2" Name="plain"/>'
        "</GroupRange></GroupRanges></GroupAddresses>"
    )
    extras = knxproj_import._read_device_extras(_FakeContents(_zero_xml(body)))

    assert extras.area_comment[1] == "area-c"
    assert extras.line_comment[(1, 2)] == "line-c"
    assert extras.grouprange_description[(1, 2047)] == "range-d"
    assert extras.central_ga == {1}


def test_reads_device_comment_space_comment_and_function_meta() -> None:
    body = (
        "<Topology><Area Address='1'><Line Address='1'><Segment Number='0' "
        "MediumTypeRefId='MT-0'>"
        '<DeviceInstance Id="P-1-0_DI-1" Address="1" Comment="dev-c"/>'
        "</Segment></Line></Area></Topology>"
        "<Locations>"
        '<Space Type="Room" Id="P-1-0_BP-1" Name="Room" Comment="space-c">'
        '<Function Id="P-1-0_F-1" Name="Light" Comment="fn-c" Description="fn-d"/>'
        "</Space></Locations>"
    )
    extras = knxproj_import._read_device_extras(_FakeContents(_zero_xml(body)))

    assert extras.device_comment["P-1-0_DI-1"] == "dev-c"
    assert extras.space_comment["P-1-0_BP-1"] == "space-c"
    # Keyed by the identifier xknxproject exposes: the @Id with its leading project-prefix segment
    # stripped (``Id.split("_", 1)[1]``), so the builder's function_meta lookup matches on import.
    assert extras.function_meta["F-1"] == ("fn-c", "fn-d")


def test_reads_trades_tree_with_nested_trades_and_device_refs() -> None:
    body = (
        "<Trades>"
        '<Trade Id="P-1-0_T-4" Name="Electrical" Number="1" Comment="t-c" '
        'Description="t-d">'
        '<Trade Name="Lighting"><DeviceInstanceRef RefId="P-1-0_DI-2"/></Trade>'
        '<DeviceInstanceRef RefId="P-1-0_DI-1"/>'
        "</Trade>"
        "</Trades>"
    )
    trades = knxproj_import._read_trades(ET.fromstring(_zero_xml(body)))

    assert len(trades) == 1
    top = trades[0]
    assert (top.name, top.number, top.comment, top.description) == (
        "Electrical",
        "1",
        "t-c",
        "t-d",
    )
    assert top.device_ids == ["P-1-0_DI-1"]
    assert top.identifier == "P-1-0_T-4"
    assert len(top.children) == 1
    assert top.children[0].name == "Lighting"
    assert top.children[0].device_ids == ["P-1-0_DI-2"]
    assert top.children[0].identifier == ""


# --- full export -> re-read round-trip ------------------------------------


def _project_with_metadata(tmp_path: Path) -> Path:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-META")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 2, "Line 2")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Switch",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    ga_id = svc.create_group_address(pid, 0, 0x0802, "central-ga")
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        project = s.get(Project, pid)
        assert project is not None
        project.comment = "proj-comment"
        area = s.get(Area, area_id)
        assert area is not None
        area.comment = "area-comment"
        area.description = "area-desc"
        line = s.get(Line, line_id)
        assert line is not None
        line.comment = "line-comment"
        line.description = "line-desc"
        device = s.get(Device, device_id)
        assert device is not None
        device.comment = "dev-comment"
        device.last_modified = "2024-05-06T07:08:09"
        ga = s.get(GroupAddress, ga_id)
        assert ga is not None
        ga.central = True
        installation = area.installation
        # Root GroupRange carrying comment + description.
        gr = next(g for g in installation.group_ranges if g.parent_id is None)
        gr.comment = "gr-comment"
        gr.description = "gr-desc"
        # A Space + Function with metadata, plus a Trade referencing the device.
        space = Space(
            space_type="Room",
            name="Room 1",
            comment="space-comment",
            description="space-desc",
            order=0,
        )
        space.functions.append(
            Function(name="Light", comment="fn-comment", description="fn-desc", order=0)
        )
        installation.spaces.append(space)
        trade = Trade(
            name="Electrical",
            number="1",
            comment="trade-comment",
            description="trade-desc",
            order=0,
            source_id="P-04BB-0_T-7",
        )
        trade.devices.append(TradeDevice(device=device, order=0))
        installation.trades.append(trade)
        # A trade added in the editor: no id of its own.
        installation.trades.append(Trade(name="Lighting", order=0, parent=trade))
        s.commit()
    return src


def _assert_metadata_present(root: ET.Element) -> None:
    area = _find(root, "Area", Name="Area 1")
    assert area.get("Comment") == "area-comment"
    assert area.get("Description") == "area-desc"

    line = _find(root, "Line", Name="Line 2")
    assert line.get("Comment") == "line-comment"
    assert line.get("Description") == "line-desc"

    device = _find(root, "DeviceInstance", Name="Switch")
    assert device.get("Comment") == "dev-comment"
    assert device.get("LastModified") == "2024-05-06T07:08:09"

    grange = _find(root, "GroupRange", Comment="gr-comment")
    assert grange.get("Description") == "gr-desc"

    central = _find(root, "GroupAddress", Name="central-ga")
    assert central.get("Central") == "true"

    space = _find(root, "Space", Name="Room 1")
    assert space.get("Comment") == "space-comment"
    assert space.get("Description") == "space-desc"

    function = _find(root, "Function", Name="Light")
    assert function.get("Comment") == "fn-comment"
    assert function.get("Description") == "fn-desc"

    trade = _find(root, "Trade", Name="Electrical")
    assert trade.get("Number") == "1"
    assert trade.get("Comment") == "trade-comment"
    assert trade.get("Description") == "trade-desc"
    ref = _find(trade, "DeviceInstanceRef")
    assert ref.get("RefId") == device.get("Id")
    # The imported trade keeps its id; the one added is numbered after it.
    pid = (device.get("Id") or "").split("_", 1)[0]
    assert trade.get("Id") == f"{pid}_T-7"
    assert _find(trade, "Trade", Name="Lighting").get("Id") == f"{pid}_T-8"


def test_a_reimported_export_keeps_the_trade_ids(tmp_path: Path) -> None:
    out = tmp_path / "out.knxproj"
    export_knxproj(_project_with_metadata(tmp_path), out, schema="23")
    again = tmp_path / "again.xknx"
    import_knxproj(out, again)

    with Session(make_engine(url_for(again))) as s:
        ids = {trade.name: trade.source_id for trade in s.query(Trade)}
    assert ids["Electrical"].endswith("-0_T-7")
    assert ids["Lighting"].endswith("-0_T-8")


def test_export_emits_all_metadata_for_project_23(tmp_path: Path) -> None:
    src = _project_with_metadata(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    _assert_metadata_present(ET.fromstring(_read_zero_from_knxproj(out)))
    info = _find(
        ET.fromstring(_read_project_xml_from_knxproj(out)), "ProjectInformation"
    )
    assert info.get("Comment") == "proj-comment"


def test_export_emits_all_metadata_for_project_20(tmp_path: Path) -> None:
    src = _project_with_metadata(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="20")
    _assert_metadata_present(ET.fromstring(_read_zero_from_knxproj(out)))
    info = _find(
        ET.fromstring(_read_project_xml_from_knxproj(out)), "ProjectInformation"
    )
    assert info.get("Comment") == "proj-comment"
