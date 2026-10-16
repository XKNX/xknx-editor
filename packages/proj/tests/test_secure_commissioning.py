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
from xknxeditor.proj.models import Device as _DeviceModel

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

    svc.set_secure_commissioning(pid, first, True)
    keys = svc.device_keys(pid, first)
    assert (
        keys is not None and keys.tool_key is not None and keys.loaded_tool_key is None
    )
    tool_key = keys.tool_key
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
    # The loaded Tool Key is no undo step (an undo must never drop a key the device uses): undoing
    # the activation leaves it, then the certificate goes.
    assert svc.undo(pid)
    keys = svc.device_keys(pid, first)
    assert keys is not None and (keys.tool_key, keys.loaded_tool_key) == (
        None,
        tool_key,
    )
    assert svc.undo(pid)
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.fdsk is None and keys.loaded_tool_key == tool_key
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


def test_ip_secrets_and_what_was_loaded(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session as _Session

    from xknxeditor.proj.db import make_engine, url_for
    from xknxeditor.proj.models import Device as _Device

    svc, pid, first, _second, path = _project(tmp_path)
    assert svc.ip_secrets(pid, first).management_password is None
    svc.close(pid)
    engine = make_engine(url_for(path))
    try:
        with _Session(engine) as session:
            device = session.get(_Device, first)
            assert device is not None
            device.extra_subtrees = {
                "Security": '<Security DeviceAuthenticationCode="auth"'
                ' DeviceManagementPassword="mgmt" />',
                "BusInterfaces": '<BusInterfaces><BusInterface RefId="M-1_A-1_BI-1"'
                ' Password="tunnel" /><BusInterface RefId="M-1_A-1_BI-2" />'
                "</BusInterfaces>",
            }
            session.commit()
    finally:
        engine.dispose()
    pid = svc.open(path, _PASSWORD)
    secrets = svc.ip_secrets(pid, first)
    assert (secrets.device_authentication_code, secrets.management_password) == (
        "auth",
        "mgmt",
    )
    # In the device's order, which numbers the users; one without a password keeps its place.
    assert [(b.ref_id, b.password) for b in secrets.bus_interfaces] == [
        ("M-1_A-1_BI-1", "tunnel"),
        ("M-1_A-1_BI-2", None),
    ]
    svc.set_ip_secure_loaded(
        pid,
        first,
        backbone_key=bytes(range(16)),
        authentication_code_hash=bytes(range(48, 64)),
        management_password_hash=bytes([1] * 16),
    )
    svc.close(pid)
    # The backbone key and the hashes (the keys a client authenticates with) are encrypted.
    data = path.read_bytes()
    for secret in (bytes(range(16)), bytes(range(48, 64)), bytes([1] * 16)):
        assert base64.b64encode(secret) not in data


def test_objects_that_require_security_keep_off_plain_group_addresses(
    tmp_path: Path,
) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    required = {_co(svc, pid, first)}
    # The first device's object requires security: deactivating the second device would leave
    # both (Auto) group addresses in the clear, so the second device's links to them go.
    impact = svc.secure_commissioning_impact(pid, second, required.__contains__)
    assert impact.unsecured == [] and len(impact.removed_links) == 2
    svc.set_secure_commissioning(pid, second, False, required.__contains__)
    assert svc.com_object_links(pid, _co(svc, pid, second)) == []
    assert all(g.data_secure for g in svc.group_addresses(pid))
    svc.close(pid)


def test_ip_secure_passwords_are_made_once(tmp_path: Path) -> None:
    from xknx.secure.security_primitives import (
        derive_device_authentication_password,
        derive_user_password,
    )

    from xknxeditor.proj.core import ip_secure

    svc, pid, first, _second, path = _project(tmp_path)
    refs = ["M-1_A-1_BI-0", "M-1_A-1_BI-1"]
    assert svc.ensure_ip_secrets(pid, first, refs)
    secrets = svc.ip_secrets(pid, first)
    passwords = [
        secrets.device_authentication_code,
        secrets.management_password,
        *(b.password for b in secrets.bus_interfaces),
    ]
    assert [b.ref_id for b in secrets.bus_interfaces] == refs
    assert all(
        p is not None and len(p) == 8 and all(" " <= c <= "~" for c in p)
        for p in passwords
    )
    # Kept once made.
    assert not svc.ensure_ip_secrets(pid, first, refs)
    assert svc.ip_secrets(pid, first) == secrets
    # The hashes are the standard derivations.
    assert ip_secure.authentication_code_hash("authcode") == (
        derive_device_authentication_password("authcode")
    )
    assert ip_secure.password_hash("pässwort") == derive_user_password("pässwort")
    svc.close(pid)
    data = path.read_bytes()
    for value in passwords:
        assert value is not None and value.encode() not in data


def test_ip_secure_passwords_can_be_changed(tmp_path: Path) -> None:
    svc, pid, first, _second, path = _project(tmp_path)
    svc.ensure_ip_secrets(pid, first, ["M-1_A-1_BI-1"])
    special = "a\"<b>&c'"
    svc.set_ip_secrets(
        pid,
        first,
        management_password=special,
        bus_interface_passwords={"M-1_A-1_BI-1": "tunnel-1"},
        secure_tunnelling=False,
    )
    secrets = svc.ip_secrets(pid, first)
    assert secrets.management_password == special
    assert secrets.bus_interfaces[0].password == "tunnel-1"
    assert not secrets.bus_interfaces[0].secure
    svc.set_ip_secrets(pid, first, secure_tunnelling=True)
    assert svc.ip_secrets(pid, first).bus_interfaces[0].secure
    for wrong in ("", "x" * 21, "€uro"):
        with pytest.raises(ValueError):
            svc.set_ip_secrets(pid, first, authentication_code=wrong)
    svc.close(pid)
    pid = svc.open(path, _PASSWORD)
    assert svc.ip_secrets(pid, first).management_password == special
    svc.close(pid)


def test_keyring_material(tmp_path: Path) -> None:
    svc, pid, first, second, path = _project(tmp_path)
    keys = svc.secured_group_keys(pid)
    assert keys[0x0801] == _GA_KEY and len(keys[0x0802]) == 16
    assert svc.group_address_senders(pid, 0x0801) == [0x2105, 0x2106]
    second_co = _co(svc, pid, second)
    assert svc.group_address_senders(pid, 0x0801, lambda co: co != second_co) == [
        0x2105
    ]
    assert svc.device_sequence_number(pid, second) == 4711
    assert svc.device_sequence_number(pid, first) is None
    svc.close(pid)
    from sqlalchemy.orm import Session as _Session

    from xknxeditor.proj.db import make_engine, url_for
    from xknxeditor.proj.models import GroupAddress as _GroupAddress

    engine = make_engine(url_for(path))
    try:
        with _Session(engine) as session:
            for ga in session.query(_GroupAddress):
                ga.source_id = f"P-OLD-0_GA-{ga.address}"
            device = session.get(_DeviceModel, first)
            assert device is not None
            device.extra_subtrees = {
                **(device.extra_subtrees or {}),
                "BusInterfaces": '<BusInterfaces><BusInterface RefId="M-1_A-1_BI-1">'
                '<Connectors><Connector GroupAddressRefId="P-OLD-0_GA-2049" />'
                '<Connector GroupAddressRefId="P-OLD-0_GA-7" /></Connectors>'
                "</BusInterface></BusInterfaces>",
            }
            session.commit()
    finally:
        engine.dispose()
    pid = svc.open(path, _PASSWORD)
    assert svc.bus_interface_group_addresses(pid, first) == {"M-1_A-1_BI-1": [0x0801]}
    svc.close(pid)


def test_senders_for_the_connection_and_tunnels(tmp_path: Path) -> None:
    svc, pid, first, second, _ = _project(tmp_path)
    # Every device that can send on a secured group address, with its last valid number.
    assert svc.secure_senders(pid) == {0x2105: 0, 0x2106: 4710}
    # A tunnel assigned to a secured group address sends on it too.
    _keys, senders = svc.group_security(pid, first, tunnel_senders={0x0801: [0x21FA]})
    assert (0x21FA, 0) in senders
    svc.close(pid)


def test_a_factory_reset_forgets_the_loaded_tool_key(tmp_path: Path) -> None:
    svc, pid, first, _second, _ = _project(tmp_path)
    svc.set_tool_key_loaded(pid, first)
    keys = svc.device_keys(pid, first)
    assert keys is not None and keys.loaded_tool_key is not None
    svc.set_tool_key_unloaded(pid, first)
    keys = svc.device_keys(pid, first)
    # The device uses its FDSK again; the Tool Key it gets next stays.
    assert keys is not None and keys.loaded_tool_key is None and keys.tool_key
    svc.close(pid)


def test_backbone_security(tmp_path: Path) -> None:
    svc, pid, _first, _second, path = _project(tmp_path)
    assert svc.backbone_security(pid) is None and svc.ip_backbone_key(pid) is None
    # Off: no key is made for it.
    svc.set_backbone_security(pid, "Off")
    assert not svc.ensure_backbone_key(pid)
    svc.set_backbone_security(pid, "On")
    key = svc.ip_backbone_key(pid)
    assert key is not None and len(key) == 16
    # Kept once made.
    assert not svc.ensure_backbone_key(pid) and svc.ip_backbone_key(pid) == key
    svc.set_backbone_security(pid, None)
    assert svc.backbone_security(pid) is None
    svc.close(pid)
    assert base64.b64encode(key) not in path.read_bytes()


def test_device_certificates_can_be_removed(tmp_path: Path) -> None:
    svc, pid, _first, _second, _ = _project(tmp_path)
    svc.add_device_certificate(pid, _FDSK.label)
    assert set(svc.device_certificates(pid)) == {
        base64.b64encode(_SERIAL).decode("ascii")
    }
    svc.remove_device_certificate(pid, _SERIAL)
    assert svc.device_certificates(pid) == {}
    assert svc.undo(pid)
    assert len(svc.device_certificates(pid)) == 1
    svc.close(pid)
