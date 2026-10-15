"""Project data of secure commissioning: device certificates (FDSK), Tool Keys and the keys and
senders of a device's secured group addresses."""

import base64
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectPasswordRequired, ProjectService
from xknxeditor.proj.core.fdsk import Fdsk, InvalidFdskError, parse_fdsk

_SERIAL = bytes.fromhex("00FA12345678")
_FDSK = Fdsk(_SERIAL, bytes(range(16)))
_GA_KEY = bytes(range(16, 32))
_PASSWORD = "Projekt-Passwort-9"


def test_fdsk_label_round_trip() -> None:
    label = _FDSK.label
    assert len(label) == 41 and label.count("-") == 5
    assert parse_fdsk(label) == _FDSK
    # Separators, white space and case do not matter; 0/1/8 are read as O/L/B.
    assert parse_fdsk(label.lower().replace("-", " ")) == _FDSK
    assert (
        parse_fdsk(label.replace("O", "0").replace("L", "1").replace("B", "8")) == _FDSK
    )


@pytest.mark.parametrize("code", ["", "AAAA", "A" * 37])
def test_fdsk_label_length(code: str) -> None:
    with pytest.raises(InvalidFdskError):
        parse_fdsk(code)


def test_fdsk_label_checksum() -> None:
    label = _FDSK.label
    last = "B" if label[-1] != "B" else "C"
    with pytest.raises(InvalidFdskError):
        parse_fdsk(label[:-1] + last)


def _project(tmp_path: Path) -> tuple[ProjectService, str, int, int, Path]:
    """Two devices linked to a secured group address; the second one has a Sequence Number."""
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-SEC")
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    devices = [
        svc.add_device(
            pid,
            segment_id,
            "M-1_H-1_P-1",
            address=address,
            name=f"Dev {address}",
            hardware2program_ref_id="M-1_H-1_HP-1",
            com_objects=[("M-1_A-1_O-1_R-1", None)],
        )
        for address in (5, 6)
    ]
    secured = svc.create_group_address(pid, 0, 0x0801, "Secured")
    plain = svc.create_group_address(pid, 0, 0x0802, "Plain")
    for device_id in devices:
        com_object = next(
            co.id for d in svc.devices(pid) if d.id == device_id for co in d.com_objects
        )
        svc.link_com_object(pid, com_object, secured, sending=True)
        svc.link_com_object(pid, com_object, plain, sending=False)
    svc.set_device_commissioning(
        pid, devices[0], serial_number=base64.b64encode(_SERIAL).decode("ascii")
    )
    svc.close(pid)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "UPDATE group_addresses SET data_secure = 1, security_key = ? WHERE address = ?",
            (base64.b64encode(_GA_KEY).decode("ascii"), 0x0801),
        )
        db.execute(
            "UPDATE devices SET extra_subtrees = ? WHERE id = ?",
            ('{"Security": "<Security SequenceNumber=\\"4711\\" />"}', devices[1]),
        )
    pid = svc.open(path)
    svc.set_project_password(pid, _PASSWORD)
    return svc, pid, devices[0], devices[1], path


def test_group_security(tmp_path: Path) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    keys, senders = svc.group_security(pid, first)
    assert keys == {0x0801: _GA_KEY}
    assert senders == [(0x2106, 4711)]
    keys, senders = svc.group_security(pid, second)
    assert senders == [(0x2105, 0)]
    svc.close(pid)


def test_certificate_and_tool_key(tmp_path: Path) -> None:
    svc, pid, first, second, path = _project(tmp_path)
    assert svc.device_keys(pid, first) is None
    svc.add_device_certificate(pid, _FDSK.label.lower())
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.fdsk == _FDSK.key and keys.tool_key is None
    # The other device has no certificate (no matching serial number).
    assert svc.device_keys(pid, second) is None

    tool_key = svc.ensure_tool_key(pid, first)
    assert svc.ensure_tool_key(pid, first) == tool_key
    keys = svc.device_keys(pid, first)
    assert (
        keys is not None and keys.tool_key == tool_key and keys.loaded_tool_key is None
    )
    svc.set_tool_key_loaded(pid, first)
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.loaded_tool_key == tool_key
    svc.close(pid)

    data = path.read_bytes()
    for secret in (
        _FDSK.label.encode(),
        base64.b64encode(tool_key),
        base64.b64encode(_GA_KEY),
    ):
        assert secret not in data

    pid = svc.open(path, _PASSWORD)
    assert svc.undo(pid)  # the Tool Key is no longer recorded as loaded
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.loaded_tool_key is None
    assert keys.tool_key == tool_key
    svc.close(pid)


def test_secrets_need_a_password(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-PLAIN")
    with pytest.raises(ProjectPasswordRequired):
        svc.add_device_certificate(pid, _FDSK.label)
    svc.close(pid)


def _loaded(svc: ProjectService, pid: str) -> dict[int, bool]:
    return {d.id: d.communication_part_loaded for d in svc.devices(pid)}


def test_link_changes_need_a_group_download(tmp_path: Path) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    for device_id in (first, second):
        svc.set_device_commissioning(pid, device_id, communication_part_loaded=True)
    gas = {g.address: g for g in svc.group_addresses(pid)}
    com_object = next(
        co.id for d in svc.devices(pid) if d.id == first for co in d.com_objects
    )
    # A plain group address: only the device of the group object.
    third = svc.create_group_address(pid, 0, 0x0803, "Plain 2")
    svc.link_com_object(pid, com_object, third, sending=False)
    assert _loaded(svc, pid) == {first: False, second: True}
    assert svc.undo(pid)
    assert _loaded(svc, pid) == {first: True, second: True}
    # The secured group address: every device linked to it (its sender table changes).
    link = next(
        link.id
        for link in svc.com_object_links(pid, com_object)
        if link.group_address_id == gas[0x0801].id
    )
    svc.unlink_com_object(pid, link)
    assert _loaded(svc, pid) == {first: False, second: False}
    assert svc.undo(pid)
    assert _loaded(svc, pid) == {first: True, second: True}
    svc.close(pid)


def test_set_group_address_security(tmp_path: Path) -> None:
    svc, pid, first, _second, path = _project(tmp_path)
    for device_id in (first,):
        svc.set_device_commissioning(pid, device_id, communication_part_loaded=True)
    plain = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0802)

    svc.set_group_address_security(pid, plain, "On", secured=True)
    ga = next(g for g in svc.group_addresses(pid) if g.id == plain)
    assert ga.data_secure and ga.security_mode == "On"
    keys, _ = svc.group_security(pid, first)
    assert 0x0802 in keys  # the generated key is used for the download
    first_key = keys[0x0802]

    # Turning it off keeps data_secure false but the key is kept (stable across the installation).
    svc.set_group_address_security(pid, plain, "Off", secured=False)
    ga = next(g for g in svc.group_addresses(pid) if g.id == plain)
    assert not ga.data_secure and ga.security_mode == "Off"
    svc.set_group_address_security(pid, plain, "On", secured=True)
    assert svc.group_security(pid, first)[0][0x0802] == first_key

    # The key is stored encrypted.
    svc.close(pid)
    assert base64.b64encode(first_key) not in path.read_bytes()
    pid = svc.open(path, _PASSWORD)
    # Undo (On -> Off) brings data_secure back; the flags of linked devices are restored.
    assert svc.undo(pid)
    ga = next(g for g in svc.group_addresses(pid) if g.id == plain)
    assert not ga.data_secure
    svc.close(pid)


def test_group_address_security_needs_a_password(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-GA")
    ga = svc.create_group_address(pid, 0, 0x0801, "GA")
    with pytest.raises(ProjectPasswordRequired):
        svc.set_group_address_security(pid, ga, "On", secured=True)
    svc.close(pid)
