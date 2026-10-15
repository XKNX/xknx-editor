"""Project data of secure commissioning: device certificates (FDSK), Tool Keys and the keys and
senders of a device's secured group addresses."""

import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from xknxeditor.proj import (
    GroupAddressSecurityError,
    ProjectPasswordRequired,
    ProjectService,
)
from xknxeditor.proj.core.fdsk import Fdsk, InvalidFdskError, parse_fdsk

_SERIAL = bytes.fromhex("00FA12345678")
_FDSK = Fdsk(_SERIAL, bytes(range(16)))
_GA_KEY = bytes(range(16, 32))
_TOOL_KEY = base64.b64encode(bytes(range(32, 48))).decode("ascii")
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


def _project(
    tmp_path: Path, *, commissioned: bool = True
) -> tuple[ProjectService, str, int, int, Path]:
    """Two devices linked to a secured group address (both commissioned securely unless
    ``commissioned`` is false); the second one has a Sequence Number."""
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
        tool_key = f' ToolKey="{_TOOL_KEY}"' if commissioned else ""
        for device_id, sequence in (
            (devices[0], ""),
            (devices[1], ' SequenceNumber="4711"'),
        ):
            db.execute(
                "UPDATE devices SET extra_subtrees = ? WHERE id = ?",
                (
                    json.dumps({"Security": f"<Security{sequence}{tool_key} />"}),
                    device_id,
                ),
            )
    pid = svc.open(path)
    svc.set_project_password(pid, _PASSWORD)
    return svc, pid, devices[0], devices[1], path


def test_group_security(tmp_path: Path) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    keys, senders = svc.group_security(pid, first)
    assert keys[0x0801] == _GA_KEY
    # The second group address is Auto as well, between two secure devices: it is secured too,
    # with a key made for it.
    assert set(keys) == {0x0801, 0x0802}
    # The stored number is the sender's next one; the table holds the last valid one.
    assert senders == [(0x2106, 4710)]
    keys, senders = svc.group_security(pid, second)
    assert senders == [(0x2105, 0)]
    svc.close(pid)


def test_certificate_and_tool_key(tmp_path: Path) -> None:
    svc, pid, first, second, path = _project(tmp_path, commissioned=False)
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
    # The Tool Keys are no undo steps (an undo must never drop a key the device uses); undo takes
    # back the certificate.
    assert svc.undo(pid)
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.fdsk is None
    assert (keys.tool_key, keys.loaded_tool_key) == (tool_key, tool_key)
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

    svc.set_group_address_security(pid, plain, "On")
    ga = next(g for g in svc.group_addresses(pid) if g.id == plain)
    assert ga.data_secure and ga.security_mode == "On"
    keys, _ = svc.group_security(pid, first)
    assert 0x0802 in keys  # the generated key is used for the download
    first_key = keys[0x0802]

    # Turning it off keeps data_secure false but the key is kept (stable across the installation).
    svc.set_group_address_security(pid, plain, "Off")
    ga = next(g for g in svc.group_addresses(pid) if g.id == plain)
    assert not ga.data_secure and ga.security_mode == "Off"
    svc.set_group_address_security(pid, plain, "On")
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
        svc.set_group_address_security(pid, ga, "On")
    svc.close(pid)


def test_p2p_security_resolves_partners(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session as _Session

    from xknxeditor.proj.db import make_engine, url_for
    from xknxeditor.proj.models import Device as _Device
    from xknxeditor.proj.models import Installation as _Installation

    svc, pid, first, second, path = _project(tmp_path)
    key_b64 = base64.b64encode(bytes(range(16))).decode()
    svc.close(pid)
    engine = make_engine(url_for(path))
    try:
        with _Session(engine) as session:
            devices = {d.id: d for d in session.query(_Device)}
            devices[first].source_id = "DI-1"
            devices[second].source_id = "DI-2"
            installation = session.query(_Installation).one()
            installation.p2p_links_xml = (
                f'<P2PLinks><P2PLink Id="L-1" Key="{key_b64}">'
                '<DeviceEndpoint DeviceRefId="DI-1" />'
                '<BusInterfaceEndpoint DeviceRefId="DI-2" BusInterfaceRefId="M-1_A-1_BI-1"'
                ' SecurityRoles="M-1_A-1_SR-2 M-2_A-1_SR-1" />'
                "</P2PLink></P2PLinks>"
            )
            session.commit()
    finally:
        engine.dispose()

    pid = svc.open(path, _PASSWORD)
    partners = svc.p2p_security(pid, first)
    # device "first" (2.1.5) links to "second" (2.1.6 = 0x2106), key resolved, no roles known.
    assert partners == [(0x2106, bytes(range(16)), 0)]
    # The partner side resolves back to "first" (2.1.5 = 0x2105).
    assert svc.p2p_security(pid, second) == [(0x2105, bytes(range(16)), 0)]
    # The roles the partner's endpoint lists that are this device's application's (3/5/1 6.3.6.1).
    masks = {"M-1_A-1_SR-1": 0x0001, "M-1_A-1_SR-2": 0x0004}
    assert svc.p2p_security(pid, first, masks) == [(0x2106, bytes(range(16)), 0x0004)]
    assert svc.p2p_security(pid, second, masks) == [(0x2105, bytes(range(16)), 0)]
    svc.close(pid)


def test_ip_backbone_key(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session as _Session

    from xknxeditor.proj.db import make_engine, url_for
    from xknxeditor.proj.models import Installation as _Installation

    svc, pid, _first, _second, path = _project(tmp_path)
    assert svc.ip_backbone_key(pid) is None
    key_b64 = base64.b64encode(bytes(range(16, 32))).decode()
    svc.close(pid)
    engine = make_engine(url_for(path))
    try:
        with _Session(engine) as session:
            installation = session.query(_Installation).one()
            installation.extra_attributes = {"IPRoutingBackboneKey": key_b64}
            session.commit()
    finally:
        engine.dispose()
    pid = svc.open(path, _PASSWORD)
    assert svc.ip_backbone_key(pid) == bytes(range(16, 32))
    svc.close(pid)


def test_a_switched_off_group_address_keeps_its_key_unused(tmp_path: Path) -> None:
    svc, pid, first, _second, path = _project(tmp_path)
    secured = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0801)
    svc.set_group_address_security(pid, secured, "Off")
    assert 0x0801 not in svc.group_security(pid, first)[0]
    # A password change encrypts the kept key in the undo history too.
    svc.set_project_password(pid, "Anderes-Passwort")
    svc.close(pid)
    assert base64.b64encode(_GA_KEY) not in path.read_bytes()
    pid = svc.open(path, "Anderes-Passwort")
    assert svc.undo(pid)  # back to secured, with the same key
    assert svc.group_security(pid, first)[0][0x0801] == _GA_KEY
    svc.close(pid)


def _co(svc: ProjectService, pid: str, device_id: int) -> int:
    return next(
        co.id for d in svc.devices(pid) if d.id == device_id for co in d.com_objects
    )


def test_auto_follows_the_linked_devices(tmp_path: Path) -> None:
    """Auto: secured while every linked device is commissioned securely (however the links or
    the devices change), plain otherwise; an unlinked one is plain."""
    svc, pid, first, second, _ = _project(tmp_path)
    gas = {g.address: g for g in svc.group_addresses(pid)}
    assert gas[0x0801].data_secure and gas[0x0802].data_secure
    # A new group address linked to the two secure devices is secured, with a key made for it.
    third = svc.create_group_address(pid, 0, 0x0803, "Third")
    assert not next(g for g in svc.group_addresses(pid) if g.id == third).data_secure
    svc.link_com_object(pid, _co(svc, pid, first), third)
    svc.link_com_object(pid, _co(svc, pid, second), third)
    keys, _ = svc.group_security(pid, first)
    assert 0x0803 in keys and len(keys[0x0803]) == 16
    assert svc.group_security(pid, second)[0][0x0803] == keys[0x0803]
    # Deactivating one device's secure commissioning makes its Auto group addresses plain.
    impact = svc.secure_commissioning_impact(pid, second)
    assert impact.removed_links == [] and len(impact.unsecured) == 3
    svc.set_secure_commissioning(pid, second, False)
    assert not svc.secure_commissioning(pid, second)
    assert not any(g.data_secure for g in svc.group_addresses(pid))
    assert svc.group_security(pid, first)[0] == {}
    # One undo step brings it back.
    assert svc.undo(pid)
    assert svc.secure_commissioning(pid, second)
    assert svc.group_security(pid, first)[0][0x0803] == keys[0x0803]
    svc.close(pid)


def test_secured_group_addresses_take_secure_devices_only(tmp_path: Path) -> None:
    svc, pid, _first, second, _ = _project(tmp_path)
    plain = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0802)
    svc.set_group_address_security(pid, plain, "On")
    # Deactivating a device removes its links to "On" group addresses, in the same undo step.
    impact = svc.secure_commissioning_impact(pid, second)
    assert len(impact.removed_links) == 1
    svc.set_secure_commissioning(pid, second, False)
    assert all(
        link.group_address_id != plain
        for link in svc.com_object_links(pid, _co(svc, pid, second))
    )
    # A device that is not commissioned securely cannot be linked to it ...
    with pytest.raises(GroupAddressSecurityError):
        svc.link_com_object(pid, _co(svc, pid, second), plain)
    # ... nor can a group address it is linked to be switched on.
    secured = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0801)
    with pytest.raises(GroupAddressSecurityError):
        svc.set_group_address_security(pid, secured, "On")
    assert svc.undo(pid)
    assert any(
        link.group_address_id == plain
        for link in svc.com_object_links(pid, _co(svc, pid, second))
    )
    svc.close(pid)


def test_activating_secure_commissioning_makes_a_tool_key(tmp_path: Path) -> None:
    svc, pid, first, _second, path = _project(tmp_path, commissioned=False)
    assert not svc.secure_commissioning(pid, first)
    svc.set_secure_commissioning(pid, first, True)
    keys = svc.device_keys(pid, first)
    assert (
        keys is not None and keys.tool_key is not None and keys.loaded_tool_key is None
    )
    tool_key = keys.tool_key
    svc.set_tool_key_loaded(pid, first)
    # Deactivating keeps the key the device holds: the next download switches it to plain.
    svc.set_secure_commissioning(pid, first, False)
    keys = svc.device_keys(pid, first)
    assert (
        keys is not None and keys.tool_key is None and keys.loaded_tool_key == tool_key
    )
    svc.set_project_password(pid, "Anderes-Passwort")
    svc.close(pid)
    assert base64.b64encode(tool_key) not in path.read_bytes()
    pid = svc.open(path, "Anderes-Passwort")
    assert svc.undo(pid)
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.tool_key == tool_key
    svc.close(pid)


def test_secure_commissioning_needs_a_password(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-SC")
    segment = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    device = svc.add_device(pid, segment, "M-1_H-1_P-1", address=1, name="D")
    with pytest.raises(ProjectPasswordRequired):
        svc.set_secure_commissioning(pid, device, True)
    svc.close(pid)


def test_only_senders_join_the_address_table(tmp_path: Path) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    second_co = _co(svc, pid, second)
    _keys, senders = svc.group_security(pid, first, lambda co: co != second_co)
    assert senders == []
    svc.close(pid)


def test_sequence_number_is_recorded(tmp_path: Path) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    svc.set_device_sequence_number(pid, first, 1_000_000)
    _keys, senders = svc.group_security(pid, second)
    assert senders == [(0x2105, 999_999)]
    svc.close(pid)
