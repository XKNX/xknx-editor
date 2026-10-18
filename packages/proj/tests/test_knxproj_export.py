"""Round-trip test for the simple .knxproj export: build a project via the core API, export it to
a ``.knxproj``, then re-import it (real xknxproject parse) and check topology/GAs/links survive."""

import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core.knxproj_signing import verify_directory_signature
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Device,
    Function,
    FunctionGroupAddress,
    GroupAddress,
    Installation,
    Line,
    Project,
    Space,
    Trade,
)

_REAL = Path(__file__).parent / "fixtures" / "xknx_test_project_no_password.knxproj"


def test_export_import_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-RT")

    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
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
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None)],
    )
    ga_id = svc.create_group_address(pid, 0, 0x0801, "GA One")  # 1/0/1
    svc.set_group_address_datapoint_type(pid, ga_id, "DPST-1-1")
    co_id = next(
        co.id for d in svc.devices(pid) if d.id == device_id for co in d.com_objects
    )
    svc.link_com_object(pid, co_id, ga_id, sending=True)

    # Build a location tree via the public API so the export's Locations round-trip is covered:
    # a room holding the device and a function referencing the group address.
    room_id = svc.create_space(pid, 0, "Room", "Room 1")
    svc.set_device_space(pid, device_id, room_id)
    fn_id = svc.create_function(pid, room_id, "FT-1", "Light")
    svc.add_function_group_address(pid, fn_id, ga_id, role="role")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    assert out.exists() and out.stat().st_size > 0

    round_path = tmp_path / "round.xknx"
    rpid = import_knxproj(out, round_path)
    rsvc = ProjectService()
    rsvc.open(round_path)

    inst = rsvc.topology(rpid, 0)
    addresses = {a.address for a in inst.areas}
    assert 1 in addresses  # our Area 1 survived (Area 0 backbone also present)
    gas = {g.text: g for g in rsvc.group_addresses(rpid)}
    assert "1/0/1" in gas
    assert gas["1/0/1"].name == "GA One"
    assert gas["1/0/1"].datapoint_type == "DPST-1-1"
    # the device and its sending link survived
    devices = rsvc.devices(rpid)
    assert any(d.product_ref_id == "M-1_H-1_P-1" for d in devices)
    links = rsvc.group_address_links(rpid, gas["1/0/1"].id)
    assert len(links) == 1
    assert links[0].is_sending

    # the location tree (space + its device + function) survived
    with Session(make_engine(url_for(round_path))) as session:
        rooms = session.query(Space).filter(Space.name == "Room 1").all()
        assert len(rooms) == 1
        assert session.query(Device).filter(Device.space_id == rooms[0].id).count() == 1
        funcs = session.query(Function).filter(Function.space_id == rooms[0].id).all()
        assert len(funcs) == 1
        assert funcs[0].name == "Light"
        assert (
            session.query(FunctionGroupAddress)
            .filter(FunctionGroupAddress.function_id == funcs[0].id)
            .count()
            == 1
        )


def test_export_keeps_unlinked_com_objects(tmp_path: Path) -> None:
    """An instantiated but UNLINKED com-object (e.g. a channel a function activated but that the user
    has not wired to a group address yet) must survive export/re-import — not only linked ones."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-UL")
    seg = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == seg
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None), ("M-1_A-1_O-2_R-1", None)],
    )
    ga_id = svc.create_group_address(pid, 0, 0x0801, "GA")
    co1 = next(
        co.id
        for d in svc.devices(pid)
        if d.id == device_id
        for co in d.com_objects
        if co.ref_id == "M-1_A-1_O-1_R-1"
    )
    svc.link_com_object(pid, co1, ga_id, sending=True)  # O-1 linked, O-2 left unlinked
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)

    # Inspect the exported 0.xml directly (a fake product can't be re-resolved by xknxproject):
    # both objects must be emitted, and only the linked one carries a Links attribute.
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    refs = re.findall(r"<(?:\w+:)?ComObjectInstanceRef\b[^>]*>", xml)
    joined = "\n".join(refs)
    assert (
        "O-1_R-1" in joined and "O-2_R-1" in joined
    )  # both instantiated objects emitted
    o2 = next(r for r in refs if "O-2_R-1" in r)
    assert (
        "Links=" not in o2
    )  # the unlinked object is emitted without a Links attribute
    o1 = next(r for r in refs if "O-1_R-1" in r)
    assert "Links=" in o1  # the linked object keeps its link


def test_export_emits_com_object_flag_overrides(tmp_path: Path) -> None:
    """A com-object flag override (e.g. forcing write_flag) must be emitted so it survives export;
    objects with default (None) flags emit no flag attribute (inherit the application default)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-FL")
    seg = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == seg
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None), ("M-1_A-1_O-2_R-1", None)],
    )
    co1 = next(
        co.id
        for d in svc.devices(pid)
        if d.id == device_id
        for co in d.com_objects
        if co.ref_id == "M-1_A-1_O-1_R-1"
    )
    svc.set_com_object_flag(
        pid, co1, "write_flag", True
    )  # O-1: override write; O-2: no overrides
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    refs = re.findall(r"<(?:\w+:)?ComObjectInstanceRef\b[^>]*>", xml)
    o1 = next(r for r in refs if "O-1_R-1" in r)
    o2 = next(r for r in refs if "O-2_R-1" in r)
    assert 'WriteFlag="Enabled"' in o1  # the override is emitted
    assert (
        "Flag=" not in o2
    )  # untouched object emits no flag attribute (inherits default)


def test_export_emits_com_object_text_overrides(tmp_path: Path) -> None:
    """A per-instance @Text/@FunctionText override (a group object renamed in ETS) must be re-emitted;
    an object without an override emits neither attribute (inherits the application default)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-TX")
    seg = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == seg
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None), ("M-1_A-1_O-2_R-1", None)],
    )
    svc.close(pid)

    # text_override/function_text_override/description_override are import-only (no editor setter),
    # so pin them on the row.
    with Session(make_engine(url_for(src))) as s:
        device = s.query(Device).filter_by(id=device_id).one()
        co1 = next(c for c in device.com_objects if c.ref_id == "M-1_A-1_O-1_R-1")
        co1.text_override = "Kitchen light"
        co1.function_text_override = "Switch"
        co1.description_override = "free-text note"
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    refs = re.findall(r"<(?:\w+:)?ComObjectInstanceRef\b[^>]*>", xml)
    o1 = next(r for r in refs if "O-1_R-1" in r)
    o2 = next(r for r in refs if "O-2_R-1" in r)
    assert 'Text="Kitchen light"' in o1
    assert 'FunctionText="Switch"' in o1
    assert 'Description="free-text note"' in o1
    assert "Text=" not in o2  # untouched object inherits the app default
    assert "Description=" not in o2


def test_export_emits_ip_config(tmp_path: Path) -> None:
    """A device's captured ``<IPConfig>`` (IP interface/router config) must be re-emitted verbatim
    after ``<BinaryData>``; a device without one emits no ``<IPConfig>``."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-IP")
    seg = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == seg
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Router",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)

    # ip_config is import-only (no editor setter), so pin it on the row.
    with Session(make_engine(url_for(src))) as s:
        s.query(Device).filter_by(id=device_id).one().ip_config = {
            "Assign": "Fixed",
            "IPAddress": "192.168.1.10",
            "SubnetMask": "255.255.255.0",
            "DefaultGateway": "192.168.1.1",
        }
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")

    root = ET.fromstring(xml)
    di = next(e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "DeviceInstance")
    children = [c.tag.rsplit("}", 1)[-1] for c in di]
    assert "IPConfig" in children
    ip = next(c for c in di if c.tag.rsplit("}", 1)[-1] == "IPConfig")
    assert ip.get("Assign") == "Fixed"
    assert ip.get("IPAddress") == "192.168.1.10"
    assert ip.get("SubnetMask") == "255.255.255.0"
    assert ip.get("DefaultGateway") == "192.168.1.1"
    # schema order: IPConfig comes after BinaryData (here BinaryData is absent, so it is last).
    assert children[-1] == "IPConfig"


def test_export_emits_unassigned_devices(tmp_path: Path) -> None:
    """Devices captured from ``<UnassignedDevices>`` must be re-grafted as the last child of
    ``<Topology>``; an installation without any emits no such container."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-UN")
    svc.create_area(pid, 0, 2, "A")  # a placed area so Topology has areas too
    svc.close(pid)

    # unassigned_devices_xml is import-only (no editor setter), so pin it on the installation.
    with Session(make_engine(url_for(src))) as s:
        s.query(Installation).one().unassigned_devices_xml = [
            '<DeviceInstance Id="D-99" Address="7" Name="Spare" />',
            '<DeviceInstance Id="D-100" Address="8" Name="Spare 2" />',
        ]
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")

    root = ET.fromstring(xml)
    topo = next(e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Topology")
    topo_children = [c.tag.rsplit("}", 1)[-1] for c in topo]
    assert topo_children[-1] == "UnassignedDevices"  # last child of Topology
    unassigned = topo[-1]
    ids = [
        d.get("Id") for d in unassigned if d.tag.rsplit("}", 1)[-1] == "DeviceInstance"
    ]
    assert ids == ["D-99", "D-100"]  # both preserved, in order


def test_commissioning_state_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CS")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
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
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.set_device_commissioning(
        pid,
        device_id,
        serial_number="ABC12345",
        last_download="2024-01-02T03:04:05.0Z",
        individual_address_loaded=True,
        application_program_loaded=True,
        parameters_loaded=True,
    )
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    round_path = tmp_path / "round.xknx"
    rpid = import_knxproj(out, round_path)
    rsvc = ProjectService()
    rsvc.open(round_path)

    dev = next(d for d in rsvc.devices(rpid) if d.product_ref_id == "M-1_H-1_P-1")
    assert dev.serial_number == "ABC12345"
    assert dev.last_download == "2024-01-02T03:04:05.0Z"
    assert dev.individual_address_loaded is True
    assert dev.application_program_loaded is True
    assert dev.parameters_loaded is True
    # flags left unset stay False (absent attribute -> not loaded)
    assert dev.communication_part_loaded is False
    assert dev.medium_config_loaded is False


def test_commissioning_set_is_undoable(tmp_path: Path) -> None:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CSU")
    seg = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    device_id = svc.add_device(pid, seg, "M-1_H-1_P-1", address=1, name="Dev")
    svc.set_device_commissioning(pid, device_id, parameters_loaded=True)
    assert svc.device(pid, device_id).parameters_loaded is True
    svc.undo(pid)
    assert svc.device(pid, device_id).parameters_loaded is False


def test_export_emits_unfiltered_and_additional_group_addresses(tmp_path: Path) -> None:
    # KNX PR #651 project-side fields: GroupAddress/GroupRange "Unfiltered" and the line's coupler
    # "AdditionalGroupAddresses". We prepare storage + export; set them directly (no UI yet) and
    # assert the exported installation XML carries them.
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-651")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    ga_id = svc.create_group_address(pid, 0, 0x0801, "GA One")  # 1/0/1
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        s.get(GroupAddress, ga_id).unfiltered = True  # type: ignore[union-attr]
        s.get(Line, line_id).additional_group_addresses = "2049,2050"  # type: ignore[union-attr]
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as z:
        zero = next(
            z.read(n).decode("utf-8") for n in z.namelist() if n.endswith("/0.xml")
        )
    assert 'Unfiltered="true"' in zero
    assert "<AdditionalGroupAddresses" in zero
    assert 'Address="2049"' in zero and 'Address="2050"' in zero


def _single_project_setup(tmp_path: Path, pid_name: str) -> tuple[Path, int]:
    """A minimal project with one group address; returns (project path, ga id)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, pid_name)
    svc.create_area(pid, 0, 2, "Area 1")
    ga_id = svc.create_group_address(pid, 0, 0x0801, "GA One")  # 1/0/1
    svc.close(pid)
    return src, ga_id


def test_export_heals_legacy_dotted_datapoint_type(tmp_path: Path) -> None:
    # A project saved before DPT normalization may hold a dotted value that ETS cannot open.
    # Export heals it to token form rather than writing the broken value.
    src, ga_id = _single_project_setup(tmp_path, "P-HEAL")
    with Session(make_engine(url_for(src))) as s:
        s.get(GroupAddress, ga_id).datapoint_type = "1.001"  # type: ignore[union-attr]
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as z:
        zero = next(
            z.read(n).decode("utf-8") for n in z.namelist() if n.endswith("/0.xml")
        )
    assert 'DatapointType="DPST-1-1"' in zero
    assert 'DatapointType="1.001"' not in zero


def test_export_fails_loudly_on_unparseable_datapoint_type(tmp_path: Path) -> None:
    src, ga_id = _single_project_setup(tmp_path, "P-BAD")
    with Session(make_engine(url_for(src))) as s:
        s.get(GroupAddress, ga_id).datapoint_type = "garbage"  # type: ignore[union-attr]
        s.commit()

    out = tmp_path / "out.knxproj"
    with pytest.raises(ValueError, match="GA One"):
        export_knxproj(src, out)


def test_export_bundles_extra_files_and_master(tmp_path: Path) -> None:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MFR")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    master = (
        b'<?xml version="1.0"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20">'
        b'<MasterData Merged="1" Signature="QUJD"/></KNX>'
    )
    hardware = b"<Hardware/>"
    export_knxproj(
        src,
        out,
        extra_files={
            "M-9999/Hardware.xml": hardware,
            "M-9999.signature": b"sig",
            # colliding with an own path must be ignored, not overwrite our master
            "knx_master.xml": b"IGNORED",
        },
        master_xml=master,
    )

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "M-9999/Hardware.xml" in names
        assert "M-9999.signature" in names
        assert zf.read("M-9999/Hardware.xml") == hardware
        # the colliding "knx_master.xml" key is ignored: only our master, written once
        assert names.count("knx_master.xml") == 1
        assert zf.read("knx_master.xml") == master


def test_export_restamps_unreleased_tool_identity(tmp_path: Path) -> None:
    """Bundled vendor XML with an unreleased CreatedBy/ToolVersion is restamped to the released pair
    so ETS does not refuse the archive (NonReleasedToolVersionUsedException)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MT")
    svc.close(pid)

    member = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="MT" '
        b'ToolVersion="4.1.1207.40711"><ManufacturerData/></KNX>'
    )
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src,
        out,
        extra_files={
            "M-00B6/Hardware.xml": member,
            "M-00B6.signature": b"stale",
        },
    )

    with zipfile.ZipFile(out) as zf:
        rewritten = zf.read("M-00B6/Hardware.xml")
        assert b'CreatedBy="ETS5"' in rewritten
        assert b'ToolVersion="5.7.1428.39779"' in rewritten
        assert b'CreatedBy="MT"' not in rewritten
        assert b"4.1.1207.40711" not in rewritten
        # body is preserved; only the root attributes change
        assert b"<ManufacturerData/>" in rewritten
        # the stale shipped signature no longer matches the rewritten bytes -> recomputed
        assert zf.read("M-00B6.signature") != b"stale"


def test_export_fills_empty_parameter_type(tmp_path: Path) -> None:
    """A childless <ParameterType/> in a bundled member gets an explicit <TypeNone/> child so ETS
    does not abort the import with a NullReferenceException; types with children are untouched."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-PT")
    svc.close(pid)

    member = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData><Static>'
        b"<ParameterTypes>"
        b'<ParameterType Id="PT-Page" Name="Page_t" />'
        b'<ParameterType Id="PT-Empty" Name="Empty_t"></ParameterType>'
        b'<ParameterType Id="PT-Num" Name="Num_t"><TypeNumber SizeInBit="8"/></ParameterType>'
        b"</ParameterTypes>"
        b"</Static></ManufacturerData></KNX>"
    )
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, extra_files={"M-008E_A-0040/0040.xml": member})

    with zipfile.ZipFile(out) as zf:
        rewritten = zf.read("M-008E_A-0040/0040.xml")
    # the self-closed and the empty-pair form both gained a TypeNone child
    assert rewritten.count(b"<TypeNone/>") == 2
    assert b'Name="Page_t" ><TypeNone/></ParameterType>' in rewritten
    assert b'Name="Empty_t"><TypeNone/></ParameterType>' in rewritten
    # the container and a type that already has a child are left alone
    assert b"<ParameterTypes>" in rewritten
    assert (
        b'<ParameterType Id="PT-Num" Name="Num_t"><TypeNumber SizeInBit="8"/>'
        in rewritten
    )


_LEGACY_HARDWARE = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<KNX xmlns="http://knx.org/xml/project/11" CreatedBy="MT" ToolVersion="4.1.1207.40711">'
    b'<ManufacturerData><Manufacturer RefId="M-00B6">'
    b'<Hardware><Hardware Id="M-00B6_H-1" Name="Dev" SerialNumber="1" VersionNumber="1"'
    b' BusCurrent="10" HasIndividualAddress="true" HasApplicationProgram="true"'
    b' IsPowerSupply="false" IsChoke="false">'
    b'<Products><Product Id="M-00B6_H-1_P-1" Text="P" OrderNumber="ON-1" IsRailMounted="false"'
    b' WidthInMillimeter="18"/></Products>'
    b"</Hardware></Hardware></Manufacturer></ManufacturerData></KNX>"
)


def test_export_converts_legacy_manufacturer_schema(tmp_path: Path) -> None:
    """A project built from ETS3/4-era .knxprod files bundles project/11 manufacturer XML. The
    export converts those members up to the export schema so the archive's XMLs agree on one schema
    (ETS otherwise refuses it as 'Invalid import data'), keeping the vendor ids."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-LEG")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    # Export as project/20 with no master to align down, so the member is converted to project/20.
    export_knxproj(
        src,
        out,
        schema="20",
        extra_files={
            "M-00B6/Hardware.xml": _LEGACY_HARDWARE,
            "M-00B6.signature": b"stale",
        },
    )

    with zipfile.ZipFile(out) as zf:
        rewritten = zf.read("M-00B6/Hardware.xml")
    assert b'xmlns="http://knx.org/xml/project/20"' in rewritten
    assert b"project/11" not in rewritten
    assert b"ns1:" not in rewritten  # not merely a prefixed default namespace
    # vendor ids survive the conversion
    assert b'RefId="M-00B6"' in rewritten
    assert b'OrderNumber="ON-1"' in rewritten
    # restamped to the released tool identity (the #19 fix still runs after the conversion)
    assert b'ToolVersion="5.7.1428.39779"' in rewritten
    assert b"4.1.1207.40711" not in rewritten


def test_export_converts_older_manufacturer_schema_up(tmp_path: Path) -> None:
    """A project/23 export carrying a device whose product data is project/20 converts that
    manufacturer data up to /23 (namespace and tool identity, the rest unchanged) — what ETS6
    writes: an OpenKNX application in an ETS 6.4 export differs from its project/20 .knxprod only
    there, and ETS6 rejects a project/20 member ("unsupported namespace 'project/20'")."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MOD")
    svc.close(pid)
    out = tmp_path / "out.knxproj"
    v20_hardware = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="OpenKNXproducer" '
        b'ToolVersion="4.3.9.0"><ManufacturerData/></KNX>'
    )
    result = export_knxproj(
        src,
        out,
        schema="23",
        extra_files={"M-0001/Hardware.xml": v20_hardware},
    )
    assert result.schema == "23"  # project files follow the requested schema
    members = _archive_members(out)
    hardware = members["M-0001/Hardware.xml"]
    assert b'xmlns="http://knx.org/xml/project/23"' in hardware
    assert b"project/20" not in hardware
    assert b'CreatedBy="ETS6"' in hardware
    assert b"OpenKNXproducer" not in hardware
    assert hardware.endswith(b"<ManufacturerData/></KNX>")


def test_export_aligns_mixed_manufacturer_schemas(tmp_path: Path) -> None:
    """Manufacturer data from different eras (project/20 and project/23) in one project/23 archive
    all ends up at /23, as ETS6 writes it; this is NOT an error."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MIX")
    svc.close(pid)
    out = tmp_path / "out.knxproj"
    v20_hardware = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData/></KNX>'
    )
    v23_hardware = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/23"><ManufacturerData/></KNX>'
    )
    result = export_knxproj(
        src,
        out,
        schema="23",
        extra_files={
            "M-0001/Hardware.xml": v20_hardware,
            "M-0002/Hardware.xml": v23_hardware,
        },
    )
    assert result.schema == "23"
    members = _archive_members(out)
    assert b"http://knx.org/xml/project/23" in members["M-0001/Hardware.xml"]
    assert b"http://knx.org/xml/project/23" in members["M-0002/Hardware.xml"]


def test_export_substitutes_unusable_master(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An unusable bundled master (wrong namespace / empty signature, as a legacy multi-source merge
    produces) is replaced by the canonical signed master instead of being written verbatim."""
    import xknxeditor.proj.core.knxproj_export as exp

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MST")
    svc.close(pid)

    # The broken master a legacy cross-source merge yields: project/11 and no MasterData signature.
    broken_master = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/11">'
        b'<MasterData Id="MD-1" Signature=""/></KNX>'
    )
    signed_master = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20">'
        b'<MasterData Id="MD-1" Signature="QUJD"/></KNX>'
    )

    def fake_fetch(knxproj=None, timeout=15.0, schema="20"):  # type: ignore[no-untyped-def]
        return signed_master

    monkeypatch.setattr(exp, "fetch_master_xml", fake_fetch)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="20", master_xml=broken_master, fetch_master=True)

    with zipfile.ZipFile(out) as zf:
        written = zf.read("knx_master.xml")
    assert written == signed_master
    assert b"project/11" not in written


def test_export_substitutes_malformed_master(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A malformed (not well-formed XML) bundled master is also replaced by the fetched signed one;
    the schema-check parse error must not abort the export."""
    import xknxeditor.proj.core.knxproj_export as exp

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MST2")
    svc.close(pid)

    malformed_master = (
        b'<?xml version="1.0"?>\n<KNX xmlns="http://knx.org/xml/project/20"><MasterData'
    )
    signed_master = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20">'
        b'<MasterData Id="MD-1" Signature="QUJD"/></KNX>'
    )

    def fake_fetch(knxproj=None, timeout=15.0, schema="20"):  # type: ignore[no-untyped-def]
        return signed_master

    monkeypatch.setattr(exp, "fetch_master_xml", fake_fetch)

    out = tmp_path / "out.knxproj"
    export_knxproj(
        src, out, schema="20", master_xml=malformed_master, fetch_master=True
    )

    with zipfile.ZipFile(out) as zf:
        assert zf.read("knx_master.xml") == signed_master


def test_certificate_signer_embeds_certificate(tmp_path: Path) -> None:
    """A certificate_signer is called with the folder signature and its output is embedded."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CERT")
    svc.close(pid)

    seen: dict[str, object] = {}

    def signer(pid_arg: str, folder_signature: bytes, project_name: str) -> bytes:
        seen["pid"] = pid_arg
        seen["sig"] = folder_signature
        seen["name"] = project_name
        return b'CERT KNX:"P-CERT.certificate"\n\tSIGN=DEADBEEF\n'

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, certificate_signer=signer)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert f"{pid}.certificate" in names
        assert zf.read(f"{pid}.certificate").startswith(b"CERT KNX:")
        # the signer receives the raw base64 folder signature, exactly what the .signature file
        # holds (ETS writes it with no BOM)
        assert seen["pid"] == pid
        assert seen["sig"] == zf.read(f"{pid}.signature")
        assert not zf.read(f"{pid}.signature").startswith(b"\xef\xbb\xbf")
        # The server echoes project_name into the certificate's `CERT KNX:"..."` header, and ETS
        # expects it to name the certificate file -- not the human-readable project name, which
        # yields a valid certificate ETS rejects as uncertified.
        assert seen["name"] == f"{pid}.certificate"


def test_certificate_signer_returning_none_skips_certificate(tmp_path: Path) -> None:
    """A signer returning None (e.g. no license) leaves the archive without a certificate."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-NOCERT")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, certificate_signer=lambda *_: None)

    with zipfile.ZipFile(out) as zf:
        assert f"{pid}.certificate" not in zf.namelist()


def test_generated_master_carries_id_version_and_empty_signature(
    tmp_path: Path,
) -> None:
    """The generated ``knx_master.xml`` must carry the ``MasterData`` id/version that ETS
    expects and an empty (to-be-signed) signature. This is what makes the export a valid
    input for an external master-data signer.
    """
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MASTER")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, master_version=224)

    with zipfile.ZipFile(out) as zf:
        master = zf.read("knx_master.xml").decode("utf-8")
    m = re.search(r"<MasterData\b[^>]*>", master)
    assert m is not None
    el = m.group(0)
    assert 'Id="MD-1"' in el
    assert 'Version="224"' in el
    assert 'Signature=""' in el


def test_master_source_reuses_signed_master_offline(tmp_path: Path) -> None:
    """Passing ``master_source`` reuses that archive's signed ``knx_master.xml`` verbatim (no
    network), so the exported master keeps its valid signature.
    """
    # Build a stand-in signed source archive (project/20 namespace, signed MasterData attr).
    signed_master = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20">'
        b'<MasterData Id="MD-1" Version="521" Signature="QUJD"/></KNX>'
    )
    source = tmp_path / "signed.knxproj"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("knx_master.xml", signed_master)

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-REUSE")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, master_source=source)

    with zipfile.ZipFile(out) as zf:
        assert zf.read("knx_master.xml") == signed_master


def test_export_schema14_namespace_and_round_trip(tmp_path: Path) -> None:
    """A schema='14' export uses the project/14 namespace and still re-imports (ETS5 shape)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-E5")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None)],
    )
    ga_id = svc.create_group_address(pid, 0, 0x0801, "GA One")  # 1/0/1
    svc.set_group_address_datapoint_type(pid, ga_id, "DPST-1-1")
    svc.close(pid)

    out = tmp_path / "out5.knxproj"
    export_knxproj(src, out, schema="14")

    with zipfile.ZipFile(out) as zf:
        project_xml = zf.read("P-E5/project.xml").decode("utf-8")
        master_xml = zf.read("knx_master.xml").decode("utf-8")
    assert "http://knx.org/xml/project/14" in project_xml
    assert "http://knx.org/xml/project/20" not in project_xml
    assert "http://knx.org/xml/project/14" in master_xml

    # xknxproject reads the ETS5-shaped archive back.
    round_path = tmp_path / "round5.xknx"
    rpid = import_knxproj(out, round_path)
    rsvc = ProjectService()
    rsvc.open(round_path)
    gas = {g.text: g for g in rsvc.group_addresses(rpid)}
    assert "1/0/1" in gas
    assert gas["1/0/1"].name == "GA One"


def _trade_context_in_export(tmp_path: Path, schema: str) -> str | None:
    """Export a project carrying a Trade with a Context and return the emitted Trade@Context."""
    src = tmp_path / f"src-{schema}.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CTX")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
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
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)

    from xknxeditor.proj.models import TradeDevice

    with Session(make_engine(url_for(src))) as s:
        device = s.get(Device, device_id)
        assert device is not None
        trade = Trade(name="Electrical", number="1", context="ctx-value", order=0)
        trade.devices.append(TradeDevice(device=device, order=0))
        device.segment.line.area.installation.trades.append(trade)
        s.commit()

    out = tmp_path / f"out-{schema}.knxproj"
    export_knxproj(src, out, schema=schema)
    with zipfile.ZipFile(out) as zf:
        trade_el = None
        for entry in zf.namelist():
            if not entry.endswith(".xml"):
                continue
            root = ET.fromstring(zf.read(entry))
            trade_el = next(
                (e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Trade"), None
            )
            if trade_el is not None:
                break
    assert trade_el is not None
    return trade_el.get("Context")


def test_schema14_export_omits_trade_context(tmp_path: Path) -> None:
    """The project/14 Trade binding has no Context attribute; emitting it broke schema-14 export."""
    assert _trade_context_in_export(tmp_path, "14") is None


def test_schema20_export_keeps_trade_context(tmp_path: Path) -> None:
    """From project/20 onward Context is valid and must survive."""
    assert _trade_context_in_export(tmp_path, "20") == "ctx-value"


def test_export_rejects_unknown_schema(tmp_path: Path) -> None:
    import pytest

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    svc.create(src, "P-BAD")
    svc.close("P-BAD")
    with pytest.raises(ValueError, match="unsupported export schema"):
        export_knxproj(src, tmp_path / "x.knxproj", schema="99")


def _mini_project(tmp_path: Path, pid: str) -> Path:
    src = tmp_path / f"{pid}.xknx"
    svc = ProjectService()
    svc.create(src, pid)
    ga = svc.create_group_address(pid, 0, 0x0801, "GA One")
    svc.set_group_address_datapoint_type(pid, ga, "DPST-1-1")
    svc.close(pid)
    return src


def test_export_carries_tool_identity_per_schema(tmp_path: Path) -> None:
    """ETS 5 -> project/20 CreatedBy=ETS5; ETS 6 -> project/22 CreatedBy=ETS6; both re-import."""
    for schema, ns, tool in (
        ("20", "project/20", "ETS5"),
        ("23", "project/23", "ETS6"),
    ):
        src = _mini_project(tmp_path, f"P-T{schema}")
        out = tmp_path / f"out{schema}.knxproj"
        export_knxproj(src, out, schema=schema)
        with zipfile.ZipFile(out) as zf:
            root = zf.read(f"P-T{schema}/project.xml").decode("utf-8")
        assert f"http://knx.org/xml/{ns}" in root
        assert f'CreatedBy="{tool}"' in root
        assert 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"' in root
        # xknxproject reads it back.
        round_path = tmp_path / f"round{schema}.xknx"
        rpid = import_knxproj(out, round_path)
        rsvc = ProjectService()
        rsvc.open(round_path)
        assert "1/0/1" in {g.text for g in rsvc.group_addresses(rpid)}


def test_relidref_strips_application_parent() -> None:
    """ComObjectInstanceRef RefId must be the RELIDREF form ETS resolves (O-n_R-m), not the full id.

    Emitting the full id makes ETS unable to link the com-object to the app and it drops the whole
    device on import (the bug behind "project imports but has no devices").
    """
    from xknxeditor.proj.core.knxproj_export import _relidref

    assert _relidref("M-0083_A-003A-24-BB4E_O-120_R-538") == "O-120_R-538"
    assert _relidref("M-0002_A-A061-14-F8BA_O-0_R-3") == "O-0_R-3"
    assert _relidref("O-7_R-9") == "O-7_R-9"  # already relative -> unchanged


def test_export_project_name_override(tmp_path: Path) -> None:
    """``project_name`` sets the exported ProjectInformation Name without touching the source."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-NM")
    svc.close(pid)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, project_name="Musterhaus")
    with zipfile.ZipFile(out) as zf:
        project_xml = zf.read(f"{pid}/project.xml").decode("utf-8")
    assert 'Name="Musterhaus"' in project_xml
    # source project name is unchanged (transient override)
    svc2 = ProjectService()
    svc2.open(src)
    assert svc2.project(pid).name == "New project"


def test_export_does_not_write_source_db(tmp_path: Path) -> None:
    """Export must stay read-only on the live source DB.

    The ``project_name`` override marks the project row dirty; a plain session would autoflush it as
    an ``UPDATE`` on the next query -- a write lock on the file the GUI reads every frame, which
    raised ``database is locked`` out of the render loop. With ``autoflush=False`` the override stays
    in-memory and no write statement is issued."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    svc.close(svc.create(src, "P-RO"))

    writes: list[str] = []

    def _record(
        _conn: object,
        _cur: object,
        statement: str,
        _params: object,
        _ctx: object,
        _many: bool,
    ) -> None:
        head = statement.strip().split(None, 1)[0].upper() if statement.strip() else ""
        if head in {"UPDATE", "INSERT", "DELETE", "ALTER", "DROP"}:
            writes.append(statement.strip().splitlines()[0])

    event.listen(Engine, "after_cursor_execute", _record)
    try:
        export_knxproj(src, tmp_path / "out.knxproj", project_name="Musterhaus")
    finally:
        event.remove(Engine, "after_cursor_execute", _record)

    assert writes == [], f"export wrote to the source DB (took a write lock): {writes}"


def test_export_summary_reports_counts_and_signing(tmp_path: Path) -> None:
    """The ``ExportResult`` carries a summary of the archive for a success window: file size,
    content counts (devices, topology, spaces, group addresses, links, functions), bundled
    manufacturer folders, the project name/master version, and the signing state."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-SUM")
    area_id = svc.create_area(pid, 0, 2, "A")
    line_id = svc.create_line(pid, area_id, 1, "L")
    segment = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    device_id = svc.add_device(
        pid,
        segment,
        "M-1_H-1_P-1",
        address=1,
        name="D1",
        com_objects=[("M-1_A-1_O-1_R-1", None)],
    )
    svc.add_device(pid, segment, "M-1_H-1_P-1", address=2, name="D2")
    ga_id = svc.create_group_address(pid, 0, 0x0801, "GA1")
    svc.create_group_address(pid, 0, 0x0802, "GA2")
    svc.create_group_address(pid, 0, 0x0803, "GA3")
    co_id = next(
        co.id for d in svc.devices(pid) if d.id == device_id for co in d.com_objects
    )
    svc.link_com_object(pid, co_id, ga_id, sending=True)
    floor_id = svc.create_space(pid, 0, "Floor", "EG")
    room_id = svc.create_space(pid, 0, "Room", "Wohnzimmer", parent_id=floor_id)
    svc.create_function(pid, room_id, "FT-1", "Light")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    result = export_knxproj(
        src,
        out,
        extra_files={
            "M-0001/Hardware.xml": b"<x/>",
            "M-0001/Catalog.xml": b"<x/>",
            "M-0002/Hardware.xml": b"<x/>",
        },
    )

    assert result.file_size == out.stat().st_size > 0
    assert result.project_name == "New project"  # the seeded project name
    assert result.master_version == 1  # export_knxproj default
    assert result.device_count == 2
    assert (
        result.area_count >= 1 and result.line_count >= 1
    )  # our area/line are counted
    assert result.building_count == 1  # the default building seeded on create
    assert result.floor_count == 1
    assert result.room_count == 1
    assert result.group_address_count == 3
    assert result.com_object_link_count == 1  # the one sending link
    assert result.function_count == 1
    assert result.manufacturer_count == 2  # two distinct M- folders
    assert result.signed is True
    assert result.certificate is False


def test_export_summary_marks_certificate(tmp_path: Path) -> None:
    """``certificate`` is set when a signer embeds a MyKnx project certificate."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    svc.close(svc.create(src, "P-SUMC"))
    cert = b'CERT KNX:"P-SUMC.certificate"\r\n\tSIGN=DEADBEEF\r\n'

    result = export_knxproj(
        src, tmp_path / "out.knxproj", certificate_signer=lambda _p, _s, _n: cert
    )

    assert result.certificate is True
    assert result.signed is True


def test_certificate_export_writes_validation_and_info(tmp_path: Path) -> None:
    """A certified export carries `.validation` and `{pid}.info` in the ETS byte layout."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-VAL")
    svc.close(pid)

    cert = b'CERT KNX:"P-VAL.certificate"\r\n\tID="CloudLicense"\r\n\tSIGN=DEADBEEF\r\n'

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, certificate_signer=lambda _pid, _sig, _name: cert)

    with zipfile.ZipFile(out) as zf:
        validation = zf.read(".validation")
        signature = zf.read(f"{pid}.signature")
        info = zf.read(f"{pid}.info")

    # `.validation` inlines the certificate, then the folder signature, then the outcome lines.
    assert validation.startswith(b'certificate:CERT KNX:"P-VAL.certificate"\r\n')
    assert b"\r\n\r\n\r\nsignature:" + signature in validation
    assert validation.endswith(
        b"\r\ncertificate.IsValid:True\r\nvalidation succeeded:True\r\n"
    )
    assert b"\n" not in validation.replace(b"\r\n", b"")  # CRLF throughout

    # `{pid}.info` is two-space JSON with CRLF and no trailing newline; its Guid must match
    # the one written into project.xml, not a second random uuid4().
    assert info.startswith(b"{\r\n") and info.endswith(b"\r\n}")
    parsed = json.loads(info.decode("utf-8"))
    assert parsed["IsPasswordProtected"] is False
    with zipfile.ZipFile(out) as zf:
        project_xml = zf.read(f"{pid}/project.xml").decode("utf-8")
    assert f'Guid="{parsed["ProjectGuid"]}"' in project_xml


def test_uncertified_export_still_writes_info_but_no_validation(tmp_path: Path) -> None:
    """`{pid}.info` is unconditional; `.validation` only exists when a certificate was obtained."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-NOVAL")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert f"{pid}.info" in names
    assert ".validation" not in names


def _archive_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def test_import_captures_product_provenance(tmp_path: Path) -> None:
    """Importing a real .knxproj must persist its manufacturer data: the signed ``knx_master.xml``
    and every ``M-XXXX/*`` member (plus the ``M-XXXX.signature``), stored verbatim so they can be
    re-emitted on export (issue #22 — ETS re-issues product ids on import, so the catalog can no
    longer resolve them)."""
    dest = tmp_path / "captured.xknx"
    pid = import_knxproj(_REAL, dest)

    with Session(make_engine(url_for(dest))) as session:
        project = session.query(Project).filter(Project.id == pid).one()
        assert project.knx_master_xml, "signed master not captured"
        blob = project.imported_product_members
        assert blob, "manufacturer members not captured"

    source_members = _archive_members(_REAL)
    expected = {
        n: b for n, b in source_members.items() if n.split("/", 1)[0].startswith("M-")
    }
    assert expected, "fixture precondition: archive has M- members"
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        captured = {n: zf.read(n) for n in zf.namelist()}
    assert captured == expected  # byte-identical, including the M-XXXX.signature


def test_export_reemits_imported_manufacturer_data(tmp_path: Path) -> None:
    """A project imported from a real .knxproj and exported again must carry its original
    ``M-XXXX/*`` folder *contents* and signed master, with no missing references — the manufacturer
    data survives the round trip without the catalog (issue #22). Each member is byte-identical
    except its root ``CreatedBy``/``ToolVersion``, which is restamped to the export's released tool
    identity (a genuine ETS stamps every member with its own, and a member left at a newer build
    makes an older ETS reject the whole archive). The folder ``.signature`` is derived: kept verbatim
    when it still verifies, otherwise recomputed, so it is asserted to verify under the active
    signing key rather than byte-compared."""
    captured = tmp_path / "captured.xknx"
    import_knxproj(_REAL, captured)

    out = tmp_path / "out.knxproj"
    result = export_knxproj(captured, out)
    assert not result.missing_references

    # The target tool identity every member is normalised to (project/20 -> ETS5).
    created_by, tool_version = b"ETS5", b"5.7.1428.39779"

    def _strip_identity(data: bytes) -> bytes:
        """Member bytes with the root's CreatedBy/ToolVersion blanked, to compare the body alone."""
        return re.sub(rb'(CreatedBy|ToolVersion)="[^"]*"', rb'\1=""', data)

    source_members = _archive_members(_REAL)
    out_members = _archive_members(out)
    folder_files: dict[str, dict[str, bytes]] = {}
    for name, data in source_members.items():
        top = name.split("/", 1)[0]
        if not top.startswith("M-") or "/" not in name:
            continue
        assert name in out_members, f"{name} dropped on export"
        got = out_members[name]
        # Body preserved; only the root tool identity differs.
        assert _strip_identity(got) == _strip_identity(data), f"{name} body changed"
        if b"CreatedBy=" in got:
            assert f'CreatedBy="{created_by.decode()}"'.encode() in got, (
                f"{name} CreatedBy"
            )
            assert f'ToolVersion="{tool_version.decode()}"'.encode() in got, (
                f"{name} ToolVersion"
            )
        folder_files.setdefault(top, {})[name.split("/", 1)[1]] = got

    for folder, files in folder_files.items():
        sig = f"{folder}.signature"
        assert sig in out_members, f"{sig} missing"
        assert verify_directory_signature(files, out_members[sig]), (
            f"{sig} does not verify under the active signing key"
        )


def test_export_without_import_provenance_has_no_members(tmp_path: Path) -> None:
    """A project built from scratch (never imported) stores no provenance, so the stored-members
    path is a no-op and must not fabricate manufacturer folders."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-SCRATCH")
    svc.close(pid)

    with Session(make_engine(url_for(src))) as session:
        project = session.query(Project).filter(Project.id == pid).one()
        assert project.imported_product_members is None

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    members = _archive_members(out)
    assert not any(n.split("/", 1)[0].startswith("M-") for n in members)


def test_master_schema_does_not_bump_project_schema(tmp_path: Path) -> None:
    """The project schema is the requested/provenance one; a differently-versioned master does NOT
    bump it (the earlier logic aligned the project up to a project/23 master, which then broke an
    export of project/20 devices). The master is obtained at the project schema instead."""
    from xknxeditor.proj.core.knxproj_export import _resolve_export_sources

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-OLD")
    svc.close(pid)

    hardware20 = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData/></KNX>'
    )
    master23 = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/23">'
        b'<MasterData Signature="AA=="/></KNX>'
    )
    with Session(make_engine(url_for(src))) as session:
        project = session.query(Project).filter(Project.id == pid).one()
        schema, ns, _extra, _master = _resolve_export_sources(
            project,
            "20",
            {"M-0001/Hardware.xml": hardware20},
            master23,
            None,
            False,
        )
    assert (
        schema == "20"
    )  # stays at the requested schema; the 23 master does not bump it
    assert ns.endswith("/20")


def test_import_provenance_realigns_modern_schema(tmp_path: Path) -> None:
    """Modern-family (project/23) provenance cannot be converted down, so exporting it with the
    default project/20 realigns the whole export up to project/23 and carries the stored member.
    A deliberate legacy downgrade (project/14) still honours the target and drops it (issue #22)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MODERN")
    svc.close(pid)

    master23 = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/23">'
        b'<MasterData Signature="AA=="/></KNX>'
    )
    hardware23 = (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/23"><ManufacturerData/></KNX>'
    )
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr("M-0001/Hardware.xml", hardware23)
    with Session(make_engine(url_for(src))) as session:
        project = session.query(Project).filter(Project.id == pid).one()
        project.knx_master_xml = master23
        project.imported_product_members = blob.getvalue()
        session.commit()

    out = tmp_path / "out.knxproj"
    result = export_knxproj(src, out)
    assert result.schema == "23"
    members = _archive_members(out)
    assert members.get("M-0001/Hardware.xml") == hardware23

    out14 = tmp_path / "out14.knxproj"
    result14 = export_knxproj(src, out14, schema="14")
    assert result14.schema == "14"
    members14 = _archive_members(out14)
    assert not any(n.split("/", 1)[0].startswith("M-") for n in members14)


def test_import_provenance_does_not_downgrade_explicit_higher_schema(
    tmp_path: Path,
) -> None:
    """The realignment is up only: a project/20 import exported with an explicit project/23 request
    stays project/23 (honouring the request) and drops the lower-schema provenance rather than
    silently downgrading the export to project/20 (issue #22, Codex P1)."""
    captured = tmp_path / "captured.xknx"
    import_knxproj(_REAL, captured)  # the fixture is a project/20 archive

    out = tmp_path / "out23.knxproj"
    result = export_knxproj(captured, out, schema="23")
    assert result.schema == "23"
    members = _archive_members(out)
    assert not any(n.split("/", 1)[0].startswith("M-") for n in members)


def test_device_extra_attributes_round_trip_to_export(tmp_path: Path) -> None:
    """DeviceInstance attributes xknxproject drops (UniqueId/InstallationHints/IsRFRetransmitter/
    Context) are stored verbatim and re-emitted on export (all valid in project/20 and /23)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-EA")
    area_id = svc.create_area(pid, 0, 2, "A")
    line_id = svc.create_line(pid, area_id, 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)

    attrs = {
        "UniqueId": "ABC-123",
        "InstallationHints": "7",
        "IsRFRetransmitter": "true",
        "Context": "ctx",
    }
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            device = session.query(Device).one()
            device.extra_attributes = dict(attrs)
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(zf.read(zero))
    di = next(e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "DeviceInstance")
    assert {k: di.get(k) for k in attrs} == attrs


def test_parameter_instance_ref_overrides_round_trip_to_export(tmp_path: Path) -> None:
    """ParameterInstanceRef @CustomizedText/@GrantUseByCustomer (dropped by xknxproject) are stored
    and re-emitted on export (both valid in project/20 and /23)."""
    from xknxeditor.proj.models import Parameter

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-PR")
    area_id = svc.create_area(pid, 0, 2, "A")
    line_id = svc.create_line(pid, area_id, 1, "L")
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
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)

    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            session.add(
                Parameter(
                    device_id=device_id,
                    ref_id="M-1_A-1_P-1_R-1",
                    value="5",
                    customized_text="Custom label",
                    grant_use_by_customer="true",
                )
            )
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    pir = next(
        r
        for r in re.findall(r"<(?:\w+:)?ParameterInstanceRef\b[^>]*>", xml)
        if "M-1_A-1_P-1_R-1" in r
    )
    assert 'CustomizedText="Custom label"' in pir
    assert 'GrantUseByCustomer="true"' in pir


def test_additional_group_addresses_stay_per_segment(tmp_path: Path) -> None:
    """A multi-segment line's AdditionalGroupAddresses must be emitted on their own segment, not all
    collapsed onto the first (#6). Two segments carry different pass-through addresses."""
    from xknxeditor.proj.models import Line, Segment

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-AGA")
    area_id = svc.create_area(pid, 0, 2, "A")
    line_id = svc.create_line(pid, area_id, 1, "L")
    svc.close(pid)

    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            line = session.get(Line, line_id)
            assert line is not None
            line.segments[0].additional_group_addresses = "100"
            session.add(
                Segment(
                    line_id=line_id,
                    number=1,
                    medium_type=line.segments[0].medium_type,
                    additional_group_addresses="200",
                )
            )
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(zf.read(zero))

    def _ln(t: str) -> str:
        return t.rsplit("}", 1)[-1]

    segs = [e for e in root.iter() if _ln(e.tag) == "Segment"]
    per_segment = []
    for seg in segs:
        addrs = [
            ga.get("Address")
            for container in seg
            if _ln(container.tag) == "AdditionalGroupAddresses"
            for ga in container
            if _ln(ga.tag) == "GroupAddress"
        ]
        per_segment.append(set(addrs))
    assert {"100"} in per_segment and {"200"} in per_segment
    # No segment carries both (the misattribution bug would put 100+200 on the first).
    assert all(a != {"100", "200"} for a in per_segment)


def test_device_extra_subtrees_round_trip_to_export(tmp_path: Path) -> None:
    """ChannelInstances/BusInterfaces/RfFastAckSlots (dropped by xknxproject) are stored verbatim and
    re-emitted at their schema position in the DeviceInstance (ChannelInstances before ModuleInstances;
    BusInterfaces/RfFastAckSlots at the end) (#9/#5)."""
    from xknxeditor.proj.models import Device

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-ST")
    area_id = svc.create_area(pid, 0, 2, "A")
    line_id = svc.create_line(pid, area_id, 1, "L")
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
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)

    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            device = session.get(Device, device_id)
            assert device is not None
            device.extra_subtrees = {
                "ChannelInstances": '<ChannelInstances><ChannelInstance RefId="CH-1" Name="Chan 1" /></ChannelInstances>',
                "BusInterfaces": '<BusInterfaces><BusInterface Name="If 1" /></BusInterfaces>',
            }
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(zf.read(zero))

    def _ln(t: str) -> str:
        return t.rsplit("}", 1)[-1]

    di = next(e for e in root.iter() if _ln(e.tag) == "DeviceInstance")
    child_tags = [_ln(c.tag) for c in di]
    assert "ChannelInstances" in child_tags and "BusInterfaces" in child_tags
    assert child_tags.index("ChannelInstances") < child_tags.index("BusInterfaces")


def test_p2p_links_round_trip_and_placement(tmp_path: Path) -> None:
    """<P2PLinks> (dropped by xknxproject) is stored verbatim and re-emitted between GroupAddresses
    and Trades in the Installation (#5). The export renumbers the devices, so the endpoints are
    rewritten to the new ids; a link whose device is gone is dropped."""
    from xknxeditor.proj.models import Device, Installation

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-P2P")
    segment = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    for address in (1, 2):
        svc.add_device(pid, segment, "M-1_H-1_P-1", address=address, name=f"D{address}")
    svc.close(pid)
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            for device in session.query(Device):
                device.source_id = f"P-OLD-0_DI-{10 + (device.address or 0)}"
            session.query(Installation).one().p2p_links_xml = (
                '<P2PLinks><P2PLink Id="P-OLD-0_P2P-1">'
                '<DeviceEndpoint DeviceRefId="P-OLD-0_DI-11" SecurityRoles="R-1" />'
                '<DeviceEndpoint DeviceRefId="P-OLD-0_DI-12" />'
                '</P2PLink><P2PLink Id="P-OLD-0_P2P-2">'
                '<DeviceEndpoint DeviceRefId="P-OLD-0_DI-11" />'
                '<DeviceEndpoint DeviceRefId="P-OLD-0_DI-99" />'
                "</P2PLink></P2PLinks>"
            )
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(zf.read(zero))

    def _ln(t: str) -> str:
        return t.rsplit("}", 1)[-1]

    inst = next(e for e in root.iter() if _ln(e.tag) == "Installation")
    tags = [_ln(c.tag) for c in inst]
    assert "P2PLinks" in tags
    # P2PLinks comes after GroupAddresses (Trades is omitted here as there are none).
    assert tags.index("GroupAddresses") < tags.index("P2PLinks")
    ids = {
        e.get("Address"): e.get("Id")
        for e in root.iter()
        if _ln(e.tag) == "DeviceInstance"
    }
    links = [e for e in root.iter() if _ln(e.tag) == "P2PLink"]
    assert len(links) == 1  # the link to the missing device is dropped
    endpoints = list(links[0])
    assert [e.get("DeviceRefId") for e in endpoints] == [ids["1"], ids["2"]]
    assert endpoints[0].get("SecurityRoles") == "R-1"
    assert links[0].get("Id") == "P-P2P-0_P2P-1"


def test_export_rewrites_bus_interface_connectors(tmp_path: Path) -> None:
    """A bus interface's ``Connector`` names its group address by id; the export renumbers the
    group addresses, so it is rewritten (and dropped when the group address is gone). The same
    holds for an unassigned device's links, and the renumbered devices keep clear of its id."""
    from xknxeditor.proj.models import Device, GroupAddress, Installation

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-BI")
    segment = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    svc.add_device(pid, segment, "M-1_H-1_P-1", address=1, name="IP")
    svc.create_group_address(pid, 0, 0x0801, "First")
    svc.create_group_address(pid, 0, 0x0802, "Second")
    svc.close(pid)
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            for ga in session.query(GroupAddress):
                ga.source_id = f"P-OLD-0_GA-{ga.address}"
            session.query(Device).one().extra_subtrees = {
                "BusInterfaces": '<BusInterfaces><BusInterface RefId="M-1_A-1_BI-1">'
                '<Connectors><Connector GroupAddressRefId="P-OLD-0_GA-2050" />'
                '<Connector GroupAddressRefId="P-OLD-0_GA-9" /></Connectors>'
                "</BusInterface></BusInterfaces>"
            }
            session.query(Installation).one().unassigned_devices_xml = [
                '<DeviceInstance Id="P-BI-0_DI-1" ProductRefId="M-1_H-1_P-1">'
                "<ComObjectInstanceRefs>"
                '<ComObjectInstanceRef RefId="O-1_R-1" Links="GA-2049 P-OLD-0_GA-9" />'
                "</ComObjectInstanceRefs></DeviceInstance>"
            ]
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(zf.read(zero))

    def _ln(t: str) -> str:
        return t.rsplit("}", 1)[-1]

    ga_ids = {
        e.get("Address"): e.get("Id")
        for e in root.iter()
        if _ln(e.tag) == "GroupAddress"
    }
    connectors = [e for e in root.iter() if _ln(e.tag) == "Connector"]
    assert [c.get("GroupAddressRefId") for c in connectors] == [ga_ids["2050"]]
    device_ids = [e.get("Id") for e in root.iter() if _ln(e.tag) == "DeviceInstance"]
    assert len(device_ids) == len(set(device_ids)) == 2
    unassigned = next(e for e in root.iter() if _ln(e.tag) == "ComObjectInstanceRef")
    assert unassigned.get("Links") == ga_ids["2049"].rsplit("_", 1)[-1]


def test_project_information_collections_round_trip_to_export(tmp_path: Path) -> None:
    """ProjectInformation child collections xknxproject drops (Tags/HistoryEntries/ToDoItems/
    DeviceCertificates) are stored verbatim and re-emitted in schema order. DeviceCertificates
    follows ProjectTraces; Tags/HistoryEntries/ToDoItems precede it (all valid in project/20 and /23).
    """
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-INFO")
    svc.close(pid)

    subtrees = {
        "Tags": '<Tags><Tag Name="T1" /></Tags>',
        "HistoryEntries": '<HistoryEntries><HistoryEntry Date="2024-01-01T00:00:00" '
        'User="alice" Text="created" /></HistoryEntries>',
        "ToDoItems": '<ToDoItems><ToDoItem Description="wire line 2" Status="Open" /></ToDoItems>',
        "DeviceCertificates": '<DeviceCertificates><DeviceCertificate SerialNumber="00FA12345678" '
        'FDSK="AAAAAA-BBBBBB-CCCCCC-DDDDDD-EEEEEE-FFFFFF" /></DeviceCertificates>',
    }
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            session.query(Project).one().info_subtrees = dict(subtrees)
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/project.xml"))
        root = ET.fromstring(zf.read(name))

    def _ln(t: str) -> str:
        return t.rsplit("}", 1)[-1]

    info = next(e for e in root.iter() if _ln(e.tag) == "ProjectInformation")
    tags = [_ln(c.tag) for c in info]
    for name in subtrees:
        assert name in tags, f"{name} missing from ProjectInformation"
    # Schema order: Tags, HistoryEntries, ToDoItems, (ProjectTraces,) DeviceCertificates.
    assert tags.index("Tags") < tags.index("HistoryEntries") < tags.index("ToDoItems")
    assert tags.index("ToDoItems") < tags.index("DeviceCertificates")
    # Content survives verbatim and is namespaced by the exporter.
    cert = next(e for e in info.iter() if _ln(e.tag) == "DeviceCertificate")
    assert cert.get("SerialNumber") == "00FA12345678"
    assert cert.tag.startswith("{")


def test_project_information_collections_survive_full_round_trip(
    tmp_path: Path,
) -> None:
    """Import -> export -> re-import keeps the stored ProjectInformation collections intact."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-RT")
    svc.close(pid)
    stored = {"Tags": '<Tags><Tag Name="only" /></Tags>'}
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            session.query(Project).one().info_subtrees = dict(stored)
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    round_path = tmp_path / "round.xknx"
    import_knxproj(out, round_path)
    with Session(make_engine(url_for(round_path))) as session:
        reimported = session.query(Project).one().info_subtrees or {}
    assert "Tags" in reimported
    tag = ET.fromstring(reimported["Tags"]).find("Tag")
    assert tag is not None and tag.get("Name") == "only"


def test_project_user_files_round_trip_to_export(tmp_path: Path) -> None:
    """Project-level UserFiles (dropped by xknxproject) are stored verbatim and re-emitted directly
    under Project after ProjectInformation (confirmed against a real ETS5 export)."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-UF")
    svc.close(pid)
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            session.query(Project).one().extra_subtrees = {
                "UserFiles": '<UserFiles><UserFile Filename="test.txt" /></UserFiles>'
            }
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/project.xml"))
        root = ET.fromstring(zf.read(name))

    def _ln(t: str) -> str:
        return t.rsplit("}", 1)[-1]

    project = next(e for e in root.iter() if _ln(e.tag) == "Project")
    children = [_ln(c.tag) for c in project]
    assert children.index("ProjectInformation") < children.index("UserFiles")
    user_file = next(e for e in project.iter() if _ln(e.tag) == "UserFile")
    assert user_file.get("Filename") == "test.txt"
    assert user_file.tag.startswith("{")


def test_export_adds_dummy_license_marker_when_unsigned(tmp_path: Path) -> None:
    """An unsigned export (no project certificate) carries an empty '{pid}_M-dummy' license marker
    in the archive root so a dongle-licensed ETS imports it without a 'signature missing' error."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-DUMMY")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        markers = [n for n in zf.namelist() if n.endswith("_M-dummy")]
        assert len(markers) == 1, f"expected one dummy marker, got {markers}"
        assert "/" not in markers[0]  # archive root, not inside a folder
        assert zf.read(markers[0]) == b""  # empty


def test_export_omits_dummy_marker_when_certificate_signed(tmp_path: Path) -> None:
    """A certified export does not need the dummy marker, so it must not be added."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CERT")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(
        src, out, certificate_signer=lambda _pid, _sig, _name: b"dummy-certificate"
    )
    with zipfile.ZipFile(out) as zf:
        assert not any(n.endswith("_M-dummy") for n in zf.namelist())
