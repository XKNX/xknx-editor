"""Serialize a project into ETS6 internal project-store streams and check their shape.

Builds a synthetic project (no copyrighted fixtures) and asserts the store streams the dongle
export writes: section decomposition, the compact tag/id scheme, the com-object write gate, and the
integrity sidecars (``.ets5hash``, ``storeVersion``, ``{pid}.store``).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.orm import Session

from xknxeditor.proj.core import ets_store
from xknxeditor.proj.core.ets_store_serialize import (
    build_store_streams,
    next_free_pid,
    store_pid_exists,
    write_project_store,
)
from xknxeditor.proj.db import make_engine
from xknxeditor.proj.models import (
    Area,
    Base,
    ComObject,
    ComObjectLink,
    Device,
    DeviceAdditionalAddress,
    GroupAddress,
    GroupRange,
    Installation,
    Line,
    ModuleInstance,
    Parameter,
    Project,
    Segment,
    Space,
)

PID = "P-9001"
GUID = "17727eba-16e9-4445-a058-4b22c580963f"
INSTALLER_GUID = "8084aff1-f6d2-4368-b7f8-186ae9dadcaa"


def _project(session: Session) -> tuple[Project, Installation]:
    project = Project(
        id=PID,
        name="Taster",
        group_address_style="ThreeLevel",
        guid=GUID,
        comment="",
    )
    inst = Installation(index=0, name="")

    # Two areas: backbone (no devices) and a line that holds the device.
    backbone = Area(address=0, name="")
    backbone_line = Line(address=0, name="")
    backbone_line.segments = [Segment(number=0, medium_type="MT-5")]
    backbone.lines = [backbone_line]

    main = Area(address=1, name="")
    main_line = Line(address=1, name="")
    segment = Segment(number=0, medium_type="MT-0")
    main_line.segments = [segment]
    main.lines = [main_line]
    inst.areas = [backbone, main]

    # Group address the sending object links to.
    gr_main = GroupRange(range_start=1, range_end=2047, name="New main group")
    gr_mid = GroupRange(range_start=1, range_end=255, name="New middle group")
    ga = GroupAddress(address=1, name="Switch", datapoint_type="DPST-1-1")
    gr_mid.group_addresses = [ga]
    gr_main.children = [gr_mid]
    inst.group_ranges = [gr_main, gr_mid]

    # One device with: a parameter, a linked object, an active-but-unlinked object carried by the
    # group-object tree, and a module instance.
    device = Device(
        address=1,
        name="",
        product_ref_id="M-0083_H-1_P-X",
        hardware2program_ref_id="M-0083_H-1_HP-Y",
        comment="",
        description="",
        group_object_tree=(
            '<GroupObjectTree GroupObjectInstances="O-1_R-1 O-2_R-2">'
            '<Nodes><Node Type="Channel" RefId="CH-1" GroupObjectInstances="O-2_R-2" />'
            "</Nodes></GroupObjectTree>"
        ),
    )
    device.parameters = [Parameter(ref_id="M-0083_A-1_P-183_R-243", value="Time")]
    linked = ComObject(ref_id="O-1_R-1", instance_ref_id="O-1_R-1", channel_id=None)
    linked.links = [ComObjectLink(group_address=ga, is_sending=True)]
    unlinked = ComObject(ref_id="O-2_R-2", instance_ref_id="O-2_R-2", channel_id="CH-1")
    device.com_objects = [linked, unlinked]
    device.module_instances = [
        ModuleInstance(
            instance_id="MD-1_M-1_MI-1",
            ref_id="MD-1_M-1",
            repeat_index="548x1",
            arguments=[{"ref_id": "MD-1_A-1", "value": "0"}],
        )
    ]
    segment.devices = [device]

    # A building holding the device.
    building = Space(space_type="Building", name="Taster", order=0)
    room = Space(space_type="Room", name="Room", order=0)
    room.devices = [device]
    building.children = [room]
    inst.spaces = [building, room]

    session.add_all([project, inst])
    session.flush()
    return project, inst


def _session() -> Session:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _di(streams: dict[str, bytes], name: str) -> ET.Element:
    area = ET.fromstring(streams[name].decode("utf-8-sig"))
    di = area.find(".//DI")
    assert di is not None
    return di


def test_section_decomposition() -> None:
    with _session() as session:
        project, inst = _project(session)
        built = build_store_streams(project, inst)

    assert set(built.streams) >= {
        "P",
        "B",
        "I",
        "U",
        "G",
        "PT",
        f"{PID}-0_A-1",
        f"{PID}-0_A-2",
    }
    # P header: project id and the project guid flow into the mask.
    proj = ET.fromstring(built.streams["P"].decode("utf-8-sig"))
    assert proj.get("Id") == PID
    info = proj.find("ProjInfo")
    assert info is not None
    assert info.get("Guid") == GUID
    assert info.get("Birdy-DeviceCount") == "1"
    assert info.get("Birdy-RoomCount") == "1"
    assert info.get("Birdy-LineCount") == "2"
    # Unassigned-device and traces sections are unused -- a genuine store has no base file for
    # those, only a ``.ets5hash`` of zero bytes (see write_store_stream), so the in-memory stream
    # content here is the same "empty" b"" rather than a serialized empty root element.
    assert built.streams["U"] == b""
    assert built.streams["PT"] == b""


def test_building_tree_links_device() -> None:
    with _session() as session:
        project, inst = _project(session)
        built = build_store_streams(project, inst)
    ls = ET.fromstring(built.streams["B"].decode("utf-8-sig"))
    building = ls.find("Sp")
    assert building is not None and building.get("Type") == "Building"
    room = building.find("Sp")
    assert room is not None and room.get("Type") == "Room"
    dirs = room.findall("DIR")
    assert [d.get("RefId") for d in dirs] == [f"{PID}-0_DI-1"]


def test_device_streams_and_write_gate() -> None:
    with _session() as session:
        project, inst = _project(session)
        built = build_store_streams(project, inst)
    di = _di(built.streams, f"{PID}-0_A-2")
    assert di.get("Id") == f"{PID}-0_DI-1"
    assert di.get("IsActivityCalculated") == "true"

    # Parameter instance carries the full ref id.
    pis = di.find("PIs")
    assert pis is not None
    assert [p.get("RefId") for p in pis.findall("PI")] == ["M-0083_A-1_P-183_R-243"]

    # Write gate: only the linked object gets a CI; the unlinked one is carried by the tree.
    cis = di.find("CIs")
    assert cis is not None
    refs = [c.get("RefId") for c in cis.findall("CI")]
    assert refs == ["O-1_R-1"]
    linked_ci = cis.find("CI")
    assert linked_ci is not None
    assert linked_ci.get("Links") == "GA-1"
    assert linked_ci.get("Birdy-SecurityState") == "false"

    # Module instance round-trips with its arguments.
    modinsts = di.find("ModInsts")
    assert modinsts is not None
    modinst = modinsts.find("ModInst")
    assert modinst is not None
    assert modinst.get("Id") == "MD-1_M-1_MI-1"
    assert modinst.get("RepeatIndex") == "548x1"
    arg = modinst.find("Arguments/Argument")
    assert arg is not None and arg.get("RefId") == "MD-1_A-1"

    # Group-object tree is renamed and keeps both active instances.
    tree = di.find("GrpObjTree")
    assert tree is not None
    assert set((tree.get("GroupObjectInstances") or "").split()) == {
        "O-1_R-1",
        "O-2_R-2",
    }
    node = tree.find("Nodes/Node")
    assert node is not None and node.get("GroupObjectInstances") == "O-2_R-2"


def test_ets5_store_drops_what_ets5_cannot_read() -> None:
    """ETS 5's object model rejects the ETS 6 topology.

    A line must carry ``MediumTypeRefId`` with the devices directly under it (a ``<Segment>``
    makes ETS 5 drop the line). ``Birdy-SecurityState`` is an ETS 6 annotation a genuine ETS 5
    store never carries. The legacy sections ETS 5 always accounts for are present but empty.
    """
    with _session() as session:
        project, inst = _project(session)
        built = build_store_streams(
            project, inst, store_format=ets_store.StoreFormat.ETS5
        )

    for name in ("AI", "BA", "D", "HE", "IPSD", "S", "T", "UF"):
        assert built.streams[name] == b""

    installation = ET.fromstring(built.streams["I"].decode("utf-8-sig"))
    assert installation.tag == "I"
    assert installation.get("DefaultLine") == ""

    info = ET.fromstring(built.streams["P"].decode("utf-8-sig")).find("ProjInfo")
    assert info is not None
    assert info.get("CompletionStatus") == "Editing"
    assert info.get("Birdy-DeviceCount") == "1"
    assert info.get("Birdy-RoomCount") is None

    area = ET.fromstring(built.streams[f"{PID}-0_A-2"].decode("utf-8-sig"))
    assert area.find("Segment") is None
    line = area.find("Line")
    assert line is not None and line.get("MediumTypeRefId") == "MT-0"
    assert line.find("DI") is not None
    ci = area.find(".//CI")
    assert ci is not None and ci.get("Birdy-SecurityState") is None


def test_store_writes_coupler_ip_config_and_extra_addresses() -> None:
    """An IP interface keeps the address ETS programs it at, and the tunnel addresses it reserves."""
    with _session() as session:
        project, inst = _project(session)
        device = inst.areas[1].lines[0].segments[0].devices[0]
        device.ip_config = {
            "Assign": "Fixed",
            "IPAddress": "192.168.3.23",
            "SubnetMask": "255.255.255.0",
            "DefaultGateway": "192.168.3.1",
            "MACAddress": "70:B3:D5:DC:84:06",
        }
        device.additional_addresses.append(DeviceAdditionalAddress(address=2, name=""))
        session.flush()
        built = build_store_streams(
            project, inst, store_format=ets_store.StoreFormat.ETS5
        )
    di = _di(built.streams, f"{PID}-0_A-2")
    ip = di.find("IP")
    assert ip is not None
    assert ip.get("Assign") == "Fixed"
    assert ip.get("IPAddress") == "192.168.3.23"
    assert ip.get("MACAddress") == "70:B3:D5:DC:84:06"
    extra = di.find("AA")
    assert extra is not None
    assert [(a.get("Address"), a.get("Name")) for a in extra.findall("Addr")] == [
        ("2", "")
    ]


def test_write_project_store_sidecars(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        store_dir = tmp_path / PID
        built = write_project_store(
            store_dir,
            project,
            inst,
            INSTALLER_GUID,
            product_members={"M-0083/Hardware.xml": b"<KNX/>"},
        )

    mask = ets_store.project_mask(built.guid, built.pid)
    # Every stream has a self-consistent .ets5hash sidecar.
    for name, data in built.streams.items():
        sidecar = (store_dir / f"{name}.ets5hash").read_text(encoding="ascii")
        assert sidecar == ets_store.ets5hash(data, mask)
    # storeVersion, the installation-bound descriptor, product data and the empty folders.
    assert (store_dir / "storeVersion").read_text(
        encoding="utf-8", newline=""
    ) == ets_store.StoreFormat.ETS6.store_version
    assert (store_dir / f"{PID}.store").read_text(
        encoding="utf-8"
    ) == ets_store.store_descriptor(PID, INSTALLER_GUID)
    assert (store_dir / "M-0083" / "Hardware.xml").read_bytes() == b"<KNX/>"
    for folder in ("BinaryData", "UserFiles", "AddinData"):
        assert (store_dir / folder).is_dir()
    # Every manufacturer folder gets its own installer-bound descriptor, the same mechanism as the
    # project folder's own {pid}.store (verified against a genuine ProjectStore: a manufacturer
    # folder without one is otherwise treated as missing even though its files are all correct).
    assert (store_dir / "M-0083" / "M-0083.store").read_text(
        encoding="utf-8"
    ) == ets_store.store_descriptor("M-0083", INSTALLER_GUID)
    # Unused sections (no unassigned devices, no traces) get no base file at all, only a
    # .ets5hash of zero bytes -- matching a genuine store rather than a present-but-empty file.
    for name in ("U", "PT"):
        assert not (store_dir / name).exists()
        assert (store_dir / f"{name}.ets5hash").read_text(
            encoding="ascii"
        ) == ets_store.ets5hash(b"", mask)


def test_existing_folder_overwrite_is_clean(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        store_root = tmp_path
        write_project_store(store_root / PID, project, inst, INSTALLER_GUID)
        assert store_pid_exists(store_root, PID)
        # A stale stream from a hypothetical earlier export must not survive a re-export.
        stale = store_root / PID / f"{PID}-0_A-9"
        stale.write_bytes(b"stale")
        write_project_store(store_root / PID, project, inst, INSTALLER_GUID, pid=PID)
    assert not stale.exists()


def test_reexport_preserves_content_this_writer_does_not_own(tmp_path: Path) -> None:
    """Re-exporting into an existing, genuine ETS folder must not destroy what ETS put there.

    Regression test for the "project is damaged" / "M-XXXX folders are missing" reports: an
    earlier version of ``write_project_store`` unconditionally ``rmtree``'d the destination before
    swapping the freshly built folder in, which silently dropped every stream type this writer does
    not model yet (``AI``/``BA``/``D``/``HE``/``IPSD``/``S``/``T``/``UF`` -- see ets_store.py), any
    manufacturer folder this export could not resolve, and real ``BinaryData``/``UserFiles``
    attachments.
    """
    with _session() as session:
        project, inst = _project(session)
        store_dir = tmp_path / PID
        write_project_store(
            store_dir,
            project,
            inst,
            INSTALLER_GUID,
            product_members={
                "M-0083/Hardware.xml": b"<old/>",
                "M-0083/Stale.xml": b"gone",
            },
        )

        # Simulate a genuine ETS install having since touched this folder: a legacy stream type
        # this writer never emits, a real binary attachment, and a manufacturer this export's
        # catalog cannot resolve (e.g. its source .knxprod was removed).
        (store_dir / "AI.ets5hash").write_text("untouched", encoding="ascii")
        (store_dir / "BinaryData" / "note.pdf").write_bytes(b"real attachment")
        other_manufacturer = store_dir / "M-00FF" / "Hardware.xml"
        other_manufacturer.parent.mkdir(parents=True)
        other_manufacturer.write_bytes(b"<other/>")

        write_project_store(
            store_dir,
            project,
            inst,
            INSTALLER_GUID,
            pid=PID,
            product_members={"M-0083/Hardware.xml": b"<new/>"},
        )

    # Unmodelled stream type and the real attachment: untouched.
    assert (store_dir / "AI.ets5hash").read_text(encoding="ascii") == "untouched"
    assert (store_dir / "BinaryData" / "note.pdf").read_bytes() == b"real attachment"
    # Manufacturer this export could not resolve: untouched.
    assert other_manufacturer.read_bytes() == b"<other/>"
    # Manufacturer this export *did* resolve: replaced wholesale (new content; stale file from
    # the previous export of the same manufacturer is gone) and still has its own descriptor.
    assert (store_dir / "M-0083" / "Hardware.xml").read_bytes() == b"<new/>"
    assert not (store_dir / "M-0083" / "Stale.xml").exists()
    assert (store_dir / "M-0083" / "M-0083.store").read_text(
        encoding="utf-8"
    ) == ets_store.store_descriptor("M-0083", INSTALLER_GUID)


def test_new_copy_uses_free_id_and_tagged_name(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        store_root = tmp_path
        write_project_store(store_root / PID, project, inst, INSTALLER_GUID)

        pid = next_free_pid(store_root)
        assert pid != PID
        assert not (store_root / pid).exists()
        built = write_project_store(
            store_root / pid,
            project,
            inst,
            INSTALLER_GUID,
            pid=pid,
            project_name=f"{project.name} (XKNX Export)",
        )
    assert built.pid == pid
    info = ET.fromstring(built.streams["P"].decode("utf-8-sig")).find("ProjInfo")
    assert info is not None
    assert info.get("Name") == "Taster (XKNX Export)"
    # The original folder is untouched.
    assert store_pid_exists(store_root, PID)


def test_new_copy_remaps_default_line_pid(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        # A building whose DefaultLine references the source project id.
        building = next(s for s in inst.spaces if s.space_type == "Building")
        building.default_line = f"{PID}-0_L-3"
        new_pid = "P-0001"
        built = write_project_store(
            tmp_path / new_pid, project, inst, INSTALLER_GUID, pid=new_pid
        )
    sp = ET.fromstring(built.streams["B"].decode("utf-8-sig")).find("Sp")
    assert sp is not None
    assert (
        sp.get("DefaultLine") == f"{new_pid}-0_L-3"
    )  # remapped, not dangling at the old pid


def test_new_copy_refuses_to_clobber_taken_slot(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        taken = tmp_path / "P-0001"
        taken.mkdir()
        (taken / "sentinel").write_bytes(b"keep")
        with pytest.raises(FileExistsError):
            write_project_store(
                tmp_path / "P-0001",
                project,
                inst,
                INSTALLER_GUID,
                pid="P-0001",
                overwrite=False,
            )
    assert (taken / "sentinel").read_bytes() == b"keep"  # untouched


def test_unsafe_project_id_rejected(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        with pytest.raises(ValueError, match="unsafe project id"):
            write_project_store(
                tmp_path / "victim", project, inst, INSTALLER_GUID, pid="../victim"
            )


def test_traversal_product_member_dropped(tmp_path: Path) -> None:
    with _session() as session:
        project, inst = _project(session)
        write_project_store(
            tmp_path / PID,
            project,
            inst,
            INSTALLER_GUID,
            product_members={"M-0083/../../escape.xml": b"x", "M-0083/ok.xml": b"y"},
        )
    assert not (tmp_path / "escape.xml").exists()
    assert (tmp_path / PID / "M-0083" / "ok.xml").read_bytes() == b"y"
