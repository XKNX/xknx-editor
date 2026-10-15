"""The KNX Secure keys of devices and the installation survive an export and the import back."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core.service import DeviceKeys
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Device, Installation, Project

_KEY = "AAECAwQFBgcICQoLDA0ODw=="
_SECURITY = {
    "ToolKey": _KEY,
    "LoadedToolKey": _KEY,
    "DeviceAuthenticationCode": "auth-code",
    "SequenceNumber": "4711",
    "SequenceNumberTimestamp": "2024-01-02T03:04:05Z",
    "UnicastBroadcastBlocking": "On",
    "LoadedMasterSalt": "salt",
}
_INSTALLATION_KEYS = {"BCUKey": "305419896", "IPRoutingBackboneKey": _KEY}
_SALT = "c2FsdA=="
_TRACING_PASSWORD = "trace"


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _project(tmp_path: Path, security: dict[str, str]) -> tuple[Path, int]:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-SEC")
    area_id = svc.create_area(pid, 0, 2, "Area 2")
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
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            device = session.get(Device, device_id)
            assert device is not None
            attrs = " ".join(f'{k}="{v}"' for k, v in security.items())
            device.extra_subtrees = {
                "Security": f'<Security {attrs}><Role RefId="R-1" Address="1" /></Security>'
            }
            installation = session.query(Installation).one()
            installation.extra_attributes = {
                **(installation.extra_attributes or {}),
                **_INSTALLATION_KEYS,
            }
            device.segment.master_salt = _SALT
            project = session.query(Project).one()
            project.extra_attributes = {"ProjectTracingPassword": _TRACING_PASSWORD}
            session.commit()
    finally:
        engine.dispose()
    return src, device_id


def _exported(out: Path) -> ET.Element:
    with zipfile.ZipFile(out) as archive:
        zero = next(n for n in archive.namelist() if n.endswith("/0.xml"))
        return ET.fromstring(archive.read(zero))


@pytest.mark.parametrize(
    ("schema", "dropped", "roles"),
    [
        ("14", {"UnicastBroadcastBlocking", "LoadedMasterSalt"}, 1),
        ("20", {"LoadedMasterSalt"}, 0),
        ("22", set[str](), 0),
        ("23", set[str](), 0),
    ],
)
def test_export_keeps_what_the_schema_defines(
    tmp_path: Path, schema: str, dropped: set[str], roles: int
) -> None:
    src, _ = _project(tmp_path, _SECURITY)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema=schema)
    root = _exported(out)

    device = next(e for e in root.iter() if _ln(e.tag) == "DeviceInstance")
    tags = [_ln(c.tag) for c in device]
    assert tags.count("Security") == 1
    security = device[tags.index("Security")]
    expected = {k: v for k, v in _SECURITY.items() if k not in dropped}
    assert dict(security.attrib) == expected
    assert sum(1 for c in security if _ln(c.tag) == "Role") == roles
    if "IPConfig" in tags:
        assert tags.index("IPConfig") < tags.index("Security")

    installation = next(e for e in root.iter() if _ln(e.tag) == "Installation")
    assert {k: installation.get(k) for k in _INSTALLATION_KEYS} == _INSTALLATION_KEYS
    salts = {e.get("MasterSalt") for e in root.iter() if _ln(e.tag) == "Segment"}
    # Only the project/23 Segment has a MasterSalt.
    assert (_SALT in salts) is (schema == "23")


def test_import_keeps_the_keys(tmp_path: Path) -> None:
    src, _ = _project(tmp_path, _SECURITY)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    again = tmp_path / "again.xknx"
    pid = import_knxproj(out, again)

    svc = ProjectService()
    svc.open(again)
    device = next(d for d in svc.devices(pid) if d.product_ref_id == "M-1_H-1_P-1")
    stored = ET.fromstring((device.extra_subtrees or {})["Security"])
    assert dict(stored.attrib) == _SECURITY
    installation = svc.installations(pid)[0]
    attributes = installation.extra_attributes or {}
    assert {k: attributes.get(k) for k in _INSTALLATION_KEYS} == _INSTALLATION_KEYS
    assert svc.project(pid).extra_attributes == {
        "ProjectTracingPassword": _TRACING_PASSWORD
    }
    salts = {
        s.master_salt
        for a in installation.areas
        for line in a.lines
        for s in line.segments
    }
    assert _SALT in salts
    assert svc.has_secure_data(pid)
    key = bytes(range(16))
    assert svc.device_keys(pid, device.id) == DeviceKeys(
        tool_key=key, loaded_tool_key=key
    )
    svc.close(pid)


def test_device_keys(tmp_path: Path) -> None:
    src, device_id = _project(tmp_path, {"ToolKey": _KEY, "SequenceNumber": "1"})
    svc = ProjectService()
    pid = svc.open(src)
    assert svc.device_keys(pid, device_id) == DeviceKeys(
        tool_key=bytes(range(16)), loaded_tool_key=None
    )
    svc.close(pid)
    (tmp_path / "b").mkdir()
    src, device_id = _project(
        tmp_path / "b", {"ToolKey": "bm8ta2V5", "SequenceNumber": "1"}
    )
    pid = svc.open(src)
    assert svc.device_keys(pid, device_id) is None  # not a 16-byte key
    svc.close(pid)


def test_a_point_to_point_link_key_is_secure_data(tmp_path: Path) -> None:
    src, _ = _project(tmp_path, {"SequenceNumber": "1"})
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            installation = session.query(Installation).one()
            installation.extra_attributes = {}
            installation.p2p_links_xml = (
                f'<P2PLinks><P2PLink Id="L-1" Key="{_KEY}" /></P2PLinks>'
            )
            session.commit()
    finally:
        engine.dispose()
    svc = ProjectService()
    pid = svc.open(src)
    assert svc.has_secure_data(pid)
    svc.close(pid)


@pytest.mark.parametrize(
    ("security", "secure"),
    [
        ({"SequenceNumber": "1"}, False),
        ({"LoadedDeviceAuthenticationCodeHash": "aGFzaA=="}, False),
        ({"LoadedToolKey": _KEY, "SequenceNumber": "1"}, True),
        ({"ToolKey": _KEY}, True),
        ({"DeviceAuthenticationCode": "code"}, True),
        ({"DeviceManagementPassword": "pass"}, True),
    ],
)
def test_secure_data_needs_a_secret(
    tmp_path: Path, security: dict[str, str], secure: bool
) -> None:
    src, _ = _project(tmp_path, security)
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            installation = session.query(Installation).one()
            installation.extra_attributes = {"BCUKey": "305419896"}
            session.commit()
    finally:
        engine.dispose()
    svc = ProjectService()
    pid = svc.open(src)
    assert svc.has_secure_data(pid) is secure
    svc.close(pid)
