"""Tests for the ``.knxproj`` importer.

The mapping logic is exercised through the public ``import_knxproj`` with its parse step patched to
return a lightweight fake parser that duck-types the ``xknxproject`` attributes the importer reads —
no binary fixture, no dependency on xknxproject's own parsing (which is its responsibility, not
ours). A separate smoke test runs the real ``import_knxproj`` against a local ``.knxproj`` when one
is available, and is skipped otherwise.
"""

import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from xml.etree import ElementTree as ET
from zipfile import BadZipFile

import pytest
from sqlalchemy.orm import Session
from xknxproject.exceptions import (
    InvalidPasswordException,
    InvalidProjectArchive,
    UnexpectedFileContent,
)

from xknxeditor.proj import ProjectService, import_knxproj
from xknxeditor.proj.core import knxproj_import
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import ComObject, ComObjectLink, GroupRange


def _coir(**kw: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "com_object_ref_id": None,
        "ref_id": "O-1_R-1",
        "channel": None,
        "read_flag": None,
        "write_flag": None,
        "communication_flag": None,
        "transmit_flag": None,
        "update_flag": None,
        "read_on_init_flag": None,
        "links": None,
    }
    return SimpleNamespace(**{**defaults, **kw})


def _fake_parser() -> SimpleNamespace:
    device = SimpleNamespace(
        address=5,
        identifier="P-TEST-0_DI-1",
        individual_address="1.1.5",
        name="Dev",
        product_ref="M-1_H-1_P-1",
        hardware_program_ref="M-1_H-1_HP-1",
        description="A device",
        order_number="ORD-1",
        hardware_name="HW One",
        product_name="Prod One",
        manufacturer_name="ACME",
        com_object_instance_refs=[
            _coir(
                com_object_ref_id="M-1_A-1_O-1_R-1",
                channel="CH-1",
                write_flag=True,
                communication_flag=True,
                transmit_flag=True,
                links=["GA-1", "GA-2"],
            )
        ],
        parameter_instance_refs={"M-1_A-1_P-1": SimpleNamespace(value="3")},
        module_instances=[],
    )
    line = SimpleNamespace(
        address=1, name="Line 1", medium_type="MT-0", devices=[device]
    )
    area = SimpleNamespace(address=1, name="Area 1", lines=[line])
    middle = SimpleNamespace(
        range_start=1, range_end=255, name="Middle", group_ranges=[]
    )
    main = SimpleNamespace(
        range_start=1, range_end=2047, name="Main", group_ranges=[middle]
    )
    gas = [
        SimpleNamespace(
            raw_address=1,
            name="GA One",
            dpt={"main": 1, "sub": 1},
            identifier="GA-1",
            description="GA desc",
            comment="GA comment",
            data_secure_key="k",
        ),
        SimpleNamespace(
            raw_address=2,
            name="GA Two",
            dpt={"main": 5, "sub": None},
            identifier="GA-2",
            description="",
            comment="",
            data_secure_key=None,
        ),
    ]
    room = SimpleNamespace(
        identifier="SP-ROOM",
        name="Wohnzimmer",
        space_type=SimpleNamespace(value="Room"),
        number="1",
        usage_text="Living",
        description="",
        spaces=[],
        devices=["1.1.5"],
        functions=["F-1"],
    )
    building = SimpleNamespace(
        identifier="SP-B",
        name="Haus",
        space_type=SimpleNamespace(value="Building"),
        number="",
        usage_text="",
        description="",
        spaces=[room],
        devices=[],
        functions=[],
    )
    function = SimpleNamespace(
        identifier="F-1",
        function_type="FT-1",
        name="Light",
        usage_text="Lighting",
        space_id="SP-ROOM",
        group_addresses=[
            SimpleNamespace(ref_id="GA-1", address="0/0/1", role="Switch")
        ],
    )
    return SimpleNamespace(
        project_info=SimpleNamespace(
            name="Test",
            group_address_style=SimpleNamespace(value="ThreeLevel"),
            guid="GUID-1",
            created_by="ETS6",
            last_modified="2020-01-01",
            schema_version="20",
            tool_version="6.0.0",
        ),
        areas=[area],
        group_ranges=[main],
        group_addresses=gas,
        spaces=[building],
        functions=[function],
        # Flat list of every parsed device, as the real XMLParser populates it (used for the
        # import inventory log that distinguishes "nothing parsed" from "parsed but dropped").
        devices=[device],
    )


# The fake project has a secured group address, so it is protected with a password.
_PASSWORD = "test-password"


def _import_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ProjectService, str, Path]:
    # Replace the xknxproject-backed parse with our fake so the public entry point is exercised.
    # ``_parse`` returns ``(parser, extras)``; the fake has no raw XML, so extras are empty.
    def _fake(
        *_a: object, **_k: object
    ) -> tuple[SimpleNamespace, knxproj_import._RawExtras]:
        return _fake_parser(), knxproj_import._RawExtras()

    monkeypatch.setattr(knxproj_import, "_parse", _fake)
    dest = tmp_path / "imported.xknx"
    pid = import_knxproj(
        "unused.knxproj", dest, project_id="P-TEST", project_password=_PASSWORD
    )
    svc = ProjectService()
    svc.open(dest, _PASSWORD)
    return svc, pid, dest


def test_project_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    project = svc.project(pid)
    assert project.name == "Test"
    assert project.group_address_style == GroupAddressStyle.THREE_LEVEL


def test_topology(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    inst = svc.topology(pid, 0)
    assert [a.address for a in inst.areas] == [1]
    line = inst.areas[0].lines[0]
    assert line.address == 1
    assert line.segments[0].medium_type == "MT-0"


def test_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    devices = svc.devices(pid)
    assert len(devices) == 1
    dev = devices[0]
    assert dev.product_ref_id == "M-1_H-1_P-1"
    assert dev.hardware2program_ref_id == "M-1_H-1_HP-1"
    assert svc.individual_address(pid, dev.id) == "1.1.5"
    assert [(p.ref_id, p.value) for p in dev.parameters] == [("M-1_A-1_P-1", "3")]
    assert len(dev.com_objects) == 1
    co = dev.com_objects[0]
    assert (
        co.ref_id == "M-1_A-1_O-1_R-1"
    )  # app-prefixed form the GUI's DynamicUI expects
    assert co.channel_id == "CH-1"
    assert (co.write_flag, co.communication_flag, co.read_flag) == (True, True, None)


def test_group_addresses_and_dpt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    gas = {g.address: g for g in svc.group_addresses(pid)}
    assert gas[1].name == "GA One"
    assert gas[1].datapoint_type == "DPST-1-1"
    assert gas[2].datapoint_type == "DPT-5"  # no sub-type


def test_group_ranges_preserve_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, dest = _import_fake(tmp_path, monkeypatch)
    engine = make_engine(url_for(dest))
    with Session(engine) as session:
        ranges = session.query(GroupRange).order_by(GroupRange.range_start).all()
        names = {(r.range_start, r.range_end): r.name for r in ranges}
        assert names[(1, 2047)] == "Main"
        assert names[(1, 255)] == "Middle"
        # the group address at value 1 sits under the smallest (leaf) range that contains it
        middle = next(r for r in ranges if (r.range_start, r.range_end) == (1, 255))
        assert {ga.address for ga in middle.group_addresses} == {1, 2}
    engine.dispose()


def test_group_ranges_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    roots = svc.group_ranges(pid, 0)
    assert len(roots) == 1
    main = roots[0]
    assert main.name == "Main"
    assert (main.range_start, main.range_end) == (1, 2047)
    assert len(main.children) == 1
    middle = main.children[0]
    assert middle.name == "Middle"
    assert {ga.address for ga in middle.group_addresses} == {1, 2}


def test_project_metadata_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    p = svc.project(pid)
    assert p.created_by == "ETS6"
    assert p.tool_version == "6.0.0"
    assert p.guid == "GUID-1"
    assert p.schema_version == "20"


def test_device_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    dev = svc.devices(pid)[0]
    info = svc.device(pid, dev.id)
    assert info.order_number == "ORD-1"
    assert info.hardware_name == "HW One"
    assert info.manufacturer_name == "ACME"
    assert info.description == "A device"


def test_group_address_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    gas = {g.address: g for g in svc.group_addresses(pid)}
    assert gas[1].description == "GA desc"
    assert gas[1].comment == "GA comment"
    # The key is kept, but the group address is linked to a device without a Tool Key, so in the
    # default Auto mode it communicates in the clear.
    assert svc.has_secure_data(pid)
    assert gas[1].data_secure is False
    assert gas[2].data_secure is False


def test_space_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, pid, _ = _import_fake(tmp_path, monkeypatch)
    roots = svc.space_tree(pid, 0)
    assert len(roots) == 1
    building = roots[0]
    assert building.space_type == "Building"
    assert building.name == "Haus"
    assert len(building.children) == 1
    room = building.children[0]
    assert room.space_type == "Room"
    # the device placed in the room
    assert [d.individual_address for d in room.devices] == ["1.1.5"]
    # the function assigned to the room, with its GA and role
    assert len(room.functions) == 1
    fn = room.functions[0]
    assert fn.name == "Light"
    assert [(g.text, g.role) for g in fn.group_addresses] == [("0/0/1", "Switch")]


def test_links_sending_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, dest = _import_fake(tmp_path, monkeypatch)
    engine = make_engine(url_for(dest))
    with Session(engine) as session:
        links = session.query(ComObjectLink).all()
        assert len(links) == 2
        by_addr = {link.group_address.address: link.is_sending for link in links}
        assert by_addr == {
            1: True,
            2: False,
        }  # first link of the com-object is the sender
    engine.dispose()


def test_com_object_overrides_captured_by_refid_without_id() -> None:
    """ETS 6 writes ``<ComObjectInstanceRef>`` with ``@RefId`` only (no ``@Id``). The per-instance
    overrides must still be captured, keyed by ``(RefId, ChannelId)`` so a channel object that
    repeats the same ``RefId`` once per ``ChannelId`` keeps its own override. Objects with no
    override attribute are skipped."""
    device = ET.fromstring(
        "<DeviceInstance>"
        '<ComObjectInstanceRef RefId="O-28_R-2284" UpdateFlag="Enabled"'
        ' DatapointType="DPST-5-1" Links="GA-143" />'
        '<ComObjectInstanceRef RefId="O-32_R-1" Text="Dimmkanal 1 - Ausgang"'
        ' ChannelId="CH-1" Links="GA-95" />'
        '<ComObjectInstanceRef RefId="O-32_R-1" Text="Dimmkanal 2 - Ausgang"'
        ' ChannelId="CH-2" Links="GA-96" />'
        '<ComObjectInstanceRef RefId="O-9_R-9" Links="GA-1" />'  # no override -> skipped
        "</DeviceInstance>"
    )
    overrides = dict(knxproj_import._read_com_object_text_overrides(device))
    assert overrides[("O-28_R-2284", None)].update_flag is True
    assert overrides[("O-28_R-2284", None)].datapoint_type == "DPST-5-1"
    assert overrides[("O-32_R-1", "CH-1")].text == "Dimmkanal 1 - Ausgang"
    assert overrides[("O-32_R-1", "CH-2")].text == "Dimmkanal 2 - Ausgang"
    assert ("O-9_R-9", None) not in overrides


def test_import_applies_com_object_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A captured override is matched to its parsed com-object by ``(ref_id, channel)`` and stored
    as the ``*_override`` fields, so a plain import -> export keeps flags, DatapointType and texts
    that ETS wrote on the ComObjectInstanceRef (previously lost because the match used the absent
    ``@Id``)."""
    extras = knxproj_import._RawExtras()
    extras.com_object_text_overrides["P-TEST-0_DI-1"] = {
        ("O-1_R-1", "CH-1"): knxproj_import._CoirOverride(
            text="Kitchen light",
            datapoint_type="DPST-5-1",
            read_flag=True,
            update_flag=True,
        )
    }

    def _fake(
        *_a: object, **_k: object
    ) -> tuple[SimpleNamespace, knxproj_import._RawExtras]:
        return _fake_parser(), extras

    monkeypatch.setattr(knxproj_import, "_parse", _fake)
    dest = tmp_path / "imported.xknx"
    import_knxproj(
        "unused.knxproj", dest, project_id="P-TEST", project_password=_PASSWORD
    )

    engine = make_engine(url_for(dest))
    with Session(engine) as session:
        co = session.query(ComObject).filter_by(instance_ref_id="O-1_R-1").one()
        assert co.text_override == "Kitchen light"
        assert co.datapoint_type_override == "DPST-5-1"
        assert co.read_flag_override is True
        assert co.update_flag_override is True
    engine.dispose()


def test_device_extra_attributes_captures_unmodelled_only() -> None:
    """Generic DeviceInstance passthrough: every attribute except the ones the export writes from a
    dedicated column is captured verbatim, so unmodelled attributes (CheckSums, LoadedImage,
    IsActivityCalculated, LastUsedAPDULength) survive a round-trip and managed ones aren't
    duplicated (which would clobber the structural value on export)."""
    device = ET.fromstring(
        '<DeviceInstance Id="DI-1" Address="5" Name="Dev" ProductRefId="M-1_H-1_P-1"'
        ' Hardware2ProgramRefId="M-1_H-1_HP-1" Comment="c" Puid="7"'
        ' CheckSums="aGVsbG8=" LoadedImage="00FF" IsActivityCalculated="true"'
        ' LastUsedAPDULength="55" />'
    )
    assert knxproj_import._device_extra_attributes(device) == {
        "CheckSums": "aGVsbG8=",
        "LoadedImage": "00FF",
        "IsActivityCalculated": "true",
        "LastUsedAPDULength": "55",
    }


@pytest.mark.parametrize(
    "raised",
    [
        zlib.error("bad"),
        BadZipFile("bad"),
        RuntimeError("Bad password for file"),
        # xknxproject's report of a zlib.error: a wrong password that passed the one-byte
        # check of the standard zip encryption
        InvalidProjectArchive("Could not decompress project data."),
    ],
)
def test_parse_failure_with_password_is_invalid_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception
) -> None:
    def _boom(*_a: object, **_k: object) -> SimpleNamespace:
        raise raised

    monkeypatch.setattr(knxproj_import, "_parse", _boom)
    with pytest.raises(InvalidPasswordException):
        import_knxproj("x.knxproj", tmp_path / "o.xknx", password="whatever")


def test_parse_failure_without_password_is_unexpected_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> SimpleNamespace:
        raise zlib.error("garbage")

    monkeypatch.setattr(knxproj_import, "_parse", _boom)
    with pytest.raises(UnexpectedFileContent):
        import_knxproj("x.knxproj", tmp_path / "o.xknx")


def test_missing_password_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> SimpleNamespace:
        raise InvalidPasswordException("Password required.")

    monkeypatch.setattr(knxproj_import, "_parse", _boom)
    with pytest.raises(InvalidPasswordException):
        import_knxproj("x.knxproj", tmp_path / "o.xknx")


def test_reimport_overwrites_existing_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake(
        *_a: object, **_k: object
    ) -> tuple[SimpleNamespace, knxproj_import._RawExtras]:
        return _fake_parser(), knxproj_import._RawExtras()

    monkeypatch.setattr(knxproj_import, "_parse", _fake)
    dest = tmp_path / "reimport.xknx"
    import_knxproj("x.knxproj", dest, project_id="P-1", project_password=_PASSWORD)
    # A second import to the same file must overwrite, not clash on unique rows (installation index).
    import_knxproj("x.knxproj", dest, project_id="P-2", project_password=_PASSWORD)
    svc = ProjectService()
    assert svc.open(dest, _PASSWORD) == "P-2"


def test_failed_parse_preserves_existing_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake(
        *_a: object, **_k: object
    ) -> tuple[SimpleNamespace, knxproj_import._RawExtras]:
        return _fake_parser(), knxproj_import._RawExtras()

    monkeypatch.setattr(knxproj_import, "_parse", _fake)
    dest = tmp_path / "keep.xknx"
    import_knxproj("x.knxproj", dest, project_id="P-KEEP", project_password=_PASSWORD)
    before = dest.read_bytes()

    def _boom(*_a: object, **_k: object) -> SimpleNamespace:
        raise zlib.error("garbage")

    monkeypatch.setattr(knxproj_import, "_parse", _boom)
    with pytest.raises(UnexpectedFileContent):
        import_knxproj("x.knxproj", dest)
    assert (
        dest.read_bytes() == before
    )  # the failed import must not touch the existing project


# End-to-end against a real, bundled ETS project (xknxproject's own MIT-licensed, unencrypted
# sample). This exercises the real xknxproject parse + OUR mapping (topology/GAs/links), which the
# fake-parser tests above deliberately skip — it is what caught e.g. the project/11 master-data bug.
_REAL = Path(__file__).parent / "fixtures" / "xknx_test_project_no_password.knxproj"


def test_import_real_file(tmp_path: Path) -> None:
    dest = tmp_path / "real.xknx"
    pid = import_knxproj(_REAL, dest)
    svc = ProjectService()
    svc.open(dest)
    assert svc.devices(pid)
    assert svc.group_addresses(pid)


@pytest.mark.parametrize(
    "target",
    [
        "_read_one_device_extras",
        "_read_unassigned_devices",
        "_read_coupler_extras",
        "_read_locations_extras",
        "_read_trades",
        "_detect_import_losses",
        "_read_project_meta",
    ],
)
def test_import_survives_optional_reader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A raw-XML enrichment reader that raises must degrade gracefully (regression guard for issue
    #17): topology, group addresses and devices are built from the parser independently of these
    optional extras, so a single reader failure must still yield a non-empty project rather than an
    empty devices/group-address view."""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("simulated malformed element")

    monkeypatch.setattr(knxproj_import, target, _boom)
    dest = tmp_path / "resilient.xknx"
    pid = import_knxproj(_REAL, dest)
    svc = ProjectService()
    svc.open(dest)
    assert svc.devices(pid)
    assert svc.group_addresses(pid)


def test_read_com_object_text_overrides() -> None:
    from xml.etree import ElementTree as ET

    device = ET.fromstring(
        """
        <DeviceInstance Id="DI-1">
          <ComObjectInstanceRefs>
            <ComObjectInstanceRef Id="C-1" RefId="O-1_R-1" Text="Kitchen" FunctionText="Switch" />
            <ComObjectInstanceRef Id="C-2" RefId="O-2_R-1" FunctionText="Status" />
            <ComObjectInstanceRef Id="C-3" RefId="O-3_R-1" />
            <ComObjectInstanceRef Id="C-4" RefId="O-4_R-1" Description="free-text note" />
          </ComObjectInstanceRefs>
        </DeviceInstance>
        """
    )
    overrides = dict(knxproj_import._read_com_object_text_overrides(device))
    ov = knxproj_import._CoirOverride
    assert overrides == {
        ("O-1_R-1", None): ov(text="Kitchen", function_text="Switch"),
        ("O-2_R-1", None): ov(function_text="Status"),
        ("O-4_R-1", None): ov(description="free-text note"),
    }  # C-3 has no override attributes and is skipped; @Id is ignored (ETS 6 omits it)


def test_read_com_object_non_text_overrides() -> None:
    from xml.etree import ElementTree as ET

    device = ET.fromstring(
        """
        <DeviceInstance Id="DI-1">
          <ComObjectInstanceRefs>
            <ComObjectInstanceRef Id="C-1" RefId="O-1_R-1" Priority="Alert"
                DatapointType="DPST-1-1" Acknowledges="O-9_R-1" />
          </ComObjectInstanceRefs>
        </DeviceInstance>
        """
    )
    overrides = dict(knxproj_import._read_com_object_text_overrides(device))
    assert overrides[("O-1_R-1", None)] == knxproj_import._CoirOverride(
        priority="Alert", datapoint_type="DPST-1-1", acknowledges="O-9_R-1"
    )


def test_build_com_object_applies_text_override() -> None:
    coir = _coir(com_object_ref_id="M-1_A-1_O-1_R-1", ref_id="O-1_R-1")
    row = knxproj_import._build_com_object(
        coir,
        knxproj_import._CoirOverride(
            text="Kitchen",
            function_text="Switch",
            description="note",
            priority="Alert",
            datapoint_type="DPST-1-1",
            acknowledges="O-9_R-1",
        ),
    )
    assert (
        row.text_override,
        row.function_text_override,
        row.description_override,
        row.priority,
        row.datapoint_type_override,
        row.acknowledges,
    ) == ("Kitchen", "Switch", "note", "Alert", "DPST-1-1", "O-9_R-1")
    bare = knxproj_import._build_com_object(coir)
    assert (
        bare.text_override,
        bare.function_text_override,
        bare.description_override,
        bare.priority,
        bare.datapoint_type_override,
        bare.acknowledges,
    ) == (None, None, None, None, None, None)


def test_read_ip_config() -> None:
    from xml.etree import ElementTree as ET

    device = ET.fromstring(
        """
        <DeviceInstance Id="DI-1">
          <IPConfig Assign="Fixed" IPAddress="192.168.1.10" SubnetMask="255.255.255.0" />
        </DeviceInstance>
        """
    )
    assert knxproj_import._read_ip_config(device) == {
        "Assign": "Fixed",
        "IPAddress": "192.168.1.10",
        "SubnetMask": "255.255.255.0",
    }
    # No <IPConfig> -> None (distinct from an empty element).
    bare = ET.fromstring('<DeviceInstance Id="DI-2" />')
    assert knxproj_import._read_ip_config(bare) is None


def test_read_unassigned_devices() -> None:
    from xml.etree import ElementTree as ET

    root = ET.fromstring(
        """
        <Project xmlns="http://knx.org/xml/project/23">
          <Topology>
            <Area Address="1" />
            <UnassignedDevices>
              <DeviceInstance Id="D-99" Address="7" />
              <DeviceInstance Id="D-100" Address="8" />
            </UnassignedDevices>
          </Topology>
        </Project>
        """
    )
    entries = knxproj_import._read_unassigned_devices(root)
    assert len(entries) == 2
    # Captured namespace-stripped (local names only), so re-parsing yields the bare tag and @Id.
    first = ET.fromstring(entries[0])
    assert first.tag == "DeviceInstance"
    assert first.get("Id") == "D-99"
    # No <UnassignedDevices> -> empty list.
    assert knxproj_import._read_unassigned_devices(ET.fromstring("<Project />")) == []


def test_installation_name_is_captured(tmp_path: Path) -> None:
    """The <Installation @Name> and the extra attributes are read from the raw XML
    (xknxproject surfaces only the project name). Patch a fixture and confirm they round-trip."""
    import zipfile

    from xknxeditor.proj.models import Installation

    fixture = (
        Path(__file__).parent / "fixtures" / "xknx_test_project_no_password.knxproj"
    )
    patched = tmp_path / "named.knxproj"
    with zipfile.ZipFile(fixture) as zin, zipfile.ZipFile(patched, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("/0.xml"):
                data = data.replace(
                    b'<Installation Name=""',
                    b'<Installation Name="Villa Alpha" '
                    b'IPRoutingMulticastAddress="224.0.23.12" MulticastTTL="16"',
                    1,
                )
            zout.writestr(item, data)

    xknx = tmp_path / "p.xknx"
    import_knxproj(patched, xknx)
    with Session(make_engine(url_for(xknx))) as s:
        inst = s.query(Installation).one()
        assert inst.name == "Villa Alpha"
        assert inst.extra_attributes is not None
        assert inst.extra_attributes["IPRoutingMulticastAddress"] == "224.0.23.12"
        assert inst.extra_attributes["MulticastTTL"] == "16"


def test_read_locations_extras_captures_scalars_and_ga_ref_names() -> None:
    """Space/Function non-displayed scalars and the GroupAddressRef @Name/@Context (all dropped by
    xknxproject) are captured so they round-trip (#7)."""
    from xml.etree import ElementTree as ET

    root = ET.fromstring(
        """
        <Installation>
          <Locations>
            <Space Id="P-1-0_S-1" Type="Room" DefaultLine="P-1-0_L-1" Context="ctx-s">
              <Function Id="P-1-0_F-1" Implements="FT-6" Number="N1"
                  DefaultGroupRange="P-1-0_GR-1" Context="ctx-f">
                <GroupAddressRef Id="P-1-0_F-1_GF-1" RefId="P-1-0_GA-1"
                    Name="Switch command" Context="ctx-g" />
              </Function>
            </Space>
          </Locations>
        </Installation>
        """
    )
    extras = knxproj_import._RawExtras()
    knxproj_import._read_locations_extras(root, extras)
    assert extras.space_extra["P-1-0_S-1"] == {
        "DefaultLine": "P-1-0_L-1",
        "Context": "ctx-s",
    }
    assert extras.function_extra["F-1"] == {
        "Implements": "FT-6",
        "Number": "N1",
        "DefaultGroupRange": "P-1-0_GR-1",
        "Context": "ctx-f",
    }
    assert extras.function_ga_refs["F-1"]["P-1-0_GA-1"] == {
        "Name": "Switch command",
        "Context": "ctx-g",
    }


def test_project_information_extra_attributes_captured(tmp_path: Path) -> None:
    """ProjectInformation attributes xknxproject drops (e.g. ProjectStart) are captured
    into Project.extra_attributes (#4)."""
    from xknxeditor.proj.models import Project

    fixture = (
        Path(__file__).parent / "fixtures" / "xknx_test_project_no_password.knxproj"
    )
    xknx = tmp_path / "p.xknx"
    import_knxproj(fixture, xknx)
    with Session(make_engine(url_for(xknx))) as s:
        project = s.query(Project).one()
        assert project.extra_attributes is not None
        assert "ProjectStart" in project.extra_attributes


def test_group_address_security_captured() -> None:
    """GroupAddress @Security/@Key/@Context (xknxproject reduces these to a bool) are captured for a
    faithful round-trip (#1). @Key is persisted on explicit authorisation."""
    from xml.etree import ElementTree as ET

    root = ET.fromstring(
        "<Installation><GroupAddresses><GroupRange>"
        '<GroupAddress Address="2049" Name="x" Security="Auto" Key="0123abcd" Context="c"/>'
        "</GroupRange></GroupAddresses></Installation>"
    )
    extras = knxproj_import._RawExtras()
    knxproj_import._read_coupler_extras(root, extras)
    assert extras.ga_security[2049] == {
        "Security": "Auto",
        "Key": "0123abcd",
        "Context": "c",
    }


_ADDRESSLESS_XML = """
<Project><Installations><Installation><Topology>
  <Area Address="1"><Line Address="1">
    <DeviceInstance Id="P-1_DI-1" Address="5" ProductRefId="M-1_H-1_P-1" />
    <DeviceInstance Id="P-1_DI-2" Name="Power supply" ProductRefId="M-1_H-2_P-2"
      Hardware2ProgramRefId="M-1_H-2_HP-2" SerialNumber="AAECAwQF"
      IndividualAddressLoaded="true" Comment="Cabinet A" />
  </Line></Area>
</Topology></Installation></Installations></Project>
"""


def test_read_addressless_devices() -> None:
    extras = knxproj_import._RawExtras()
    knxproj_import._read_coupler_extras(ET.fromstring(_ADDRESSLESS_XML), extras)
    (device,) = extras.addressless_devices[(1, 1)]
    assert device.identifier == "P-1_DI-2"
    assert device.address is None
    assert (device.name, device.product_ref, device.hardware_program_ref) == (
        "Power supply",
        "M-1_H-2_P-2",
        "M-1_H-2_HP-2",
    )
    assert device.serial_number == "AAECAwQF"
    assert device.individual_address_loaded
    assert not device.parameters_loaded


def test_device_without_address_is_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extras = knxproj_import._RawExtras()
    knxproj_import._read_coupler_extras(ET.fromstring(_ADDRESSLESS_XML), extras)
    (supply,) = extras.addressless_devices[(1, 1)]
    supply.product_name = "Power Supply 640 mA"
    extras.device_comment[supply.identifier] = "Cabinet A"

    def _fake(*_a: object, **_k: object) -> tuple[SimpleNamespace, Any]:
        return _fake_parser(), extras

    monkeypatch.setattr(knxproj_import, "_parse", _fake)
    dest = tmp_path / "imported.xknx"
    pid = import_knxproj(
        "unused.knxproj", dest, project_id="P-TEST", project_password=_PASSWORD
    )
    svc = ProjectService()
    svc.open(dest, _PASSWORD)
    by_product = {d.product_ref_id: d for d in svc.devices(pid)}
    assert set(by_product) == {"M-1_H-1_P-1", "M-1_H-2_P-2"}
    device = by_product["M-1_H-2_P-2"]
    assert device.address is None
    assert svc.individual_address(pid, device.id) in (None, "")
    assert (device.name, device.product_name, device.comment) == (
        "Power supply",
        "Power Supply 640 mA",
        "Cabinet A",
    )
    assert device.serial_number == "AAECAwQF"
    assert device.individual_address_loaded
    assert device.parameters == [] and device.com_objects == []
    svc.close(pid)


_CONFIGURED_WITHOUT_ADDRESS = """
<Topology xmlns="http://knx.org/xml/project/23"><Area Address="1"><Line Address="1">
  <DeviceInstance Id="P-1_DI-3" Name="Not addressed yet" ProductRefId="M-1_H-3_P-3"
    Hardware2ProgramRefId="M-1_H-3_HP-3">
    <ParameterInstanceRefs><ParameterInstanceRef RefId="P-1_R-1" Value="7" /></ParameterInstanceRefs>
    <ComObjectInstanceRefs><ComObjectInstanceRef RefId="O-1_R-1" Links="GA-1" /></ComObjectInstanceRefs>
    <ModuleInstances><ModuleInstance Id="MD-1_M-1_MI-1" RefId="MD-1_M-1" /></ModuleInstances>
  </DeviceInstance>
</Line></Area></Topology>
"""


def test_devices_without_address_are_read_with_their_configuration() -> None:
    from xknxproject.loader.project_loader import (
        _TopologyLoader,  # pyright: ignore[reportPrivateUsage]
    )

    topology = ET.fromstring(_CONFIGURED_WITHOUT_ADDRESS)
    found: set[str] = set()
    with knxproj_import._read_devices_without_address(found, set()):
        (area,) = _TopologyLoader(cast("Any", SimpleNamespace(schema_version=23))).load(
            topology
        )
    (device,) = area.lines[0].devices
    assert found == {"P-1_DI-3"}
    assert device.parameter_instance_refs["P-1_R-1"].value == "7"
    assert [c.ref_id for c in device.com_object_instance_refs] == ["O-1_R-1"]
    assert [m.identifier for m in device.module_instances] == ["MD-1_M-1_MI-1"]
    # The placeholder address is gone again, and without the patch the device is skipped.
    assert all(
        d.get("Address") is None
        for d in topology.iter()
        if d.tag.endswith("}DeviceInstance")
    )
    (area,) = _TopologyLoader(cast("Any", SimpleNamespace(schema_version=23))).load(
        topology
    )
    assert area.lines[0].devices == []


def test_parse_falls_back_without_them_when_they_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xknxproject.loader.project_loader import (
        _TopologyLoader,  # pyright: ignore[reportPrivateUsage]
    )

    attempts: list[int] = []

    class _Parser:
        def __init__(self, _contents: object) -> None:
            self.devices: list[Any] = []

        def parse(self, _language: str | None) -> None:
            attempts.append(len(attempts))
            _TopologyLoader(cast("Any", SimpleNamespace(schema_version=23))).load(
                ET.fromstring(_CONFIGURED_WITHOUT_ADDRESS)
            )
            if len(attempts) == 1:
                raise ValueError("application of the unaddressed device")

    monkeypatch.setattr(knxproj_import, "XMLParser", _Parser)
    _parser, read = knxproj_import._parse_project(object(), None)
    assert attempts == [0, 1]
    assert read.found == set()


def test_devices_on_no_line_are_read_with_their_configuration() -> None:
    from xknxproject.loader.project_loader import (
        _TopologyLoader,  # pyright: ignore[reportPrivateUsage]
    )

    topology = ET.fromstring(
        _CONFIGURED_WITHOUT_ADDRESS.replace(
            "</Line></Area></Topology>",
            "</Line></Area><UnassignedDevices>"
            '<DeviceInstance Id="P-1_DI-9" ProductRefId="M-1_H-1_P-1" '
            'Hardware2ProgramRefId="M-1_H-1_HP-1">'
            '<ParameterInstanceRefs><ParameterInstanceRef RefId="P-1_R-2" Value="3" />'
            "</ParameterInstanceRefs></DeviceInstance>"
            "</UnassignedDevices></Topology>",
        )
    )
    found: set[str] = set()
    unassigned: set[str] = set()
    with knxproj_import._read_devices_without_address(found, unassigned):
        areas = _TopologyLoader(cast("Any", SimpleNamespace(schema_version=23))).load(
            topology
        )
    # Read on a placeholder line of a placeholder area, which the import leaves out.
    assert unassigned == {"P-1_DI-9"}
    assert found == {"P-1_DI-3", "P-1_DI-9"}
    placeholder = next(
        a for a in areas if a.address == knxproj_import._PLACEHOLDER_AREA
    )
    (device,) = placeholder.lines[0].devices
    assert device.parameter_instance_refs["P-1_R-2"].value == "3"
