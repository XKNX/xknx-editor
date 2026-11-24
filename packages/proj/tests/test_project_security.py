"""A project's security domains: a Security Proxy secures a group address on one side only."""

from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.ga_security import SecurityContext, project_security
from xknxeditor.proj.models import Device as DeviceRow

_TOOL_KEY = base64.b64encode(bytes(range(32, 48))).decode("ascii")
_PASSWORD = "Projekt-Passwort-9"
_PROXY = "M-1_H-PROXY_HP-1"


class _Products(SecurityContext):
    """Couplers by their hardware program; the one with ``_PROXY`` has the security
    extensions."""

    def coupler(self, device: DeviceRow) -> tuple[bool, bool]:
        program = device.hardware2program_ref_id or ""
        return "PROXY" in program or "COUPLER" in program, program == _PROXY


def _project(tmp_path: Path) -> tuple[ProjectService, str, dict[str, int]]:
    """Lines 2.1 and 2.2 with a coupler each (2.1.0 commissioned securely, with the security
    extensions; 2.2.0 a plain one), an actuator 2.1.5 commissioned securely and a sensor 2.2.5
    commissioned in the clear, both on group address 1/0/1."""
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-DOM")
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    segments = {}
    for number in (1, 2):
        line_id = svc.create_line(pid, area_id, number, f"Line {number}")
        segments[number] = next(
            line.segments[0].id
            for area in svc.topology(pid, 0).areas
            for line in area.lines
            if line.id == line_id
        )
    devices: dict[str, int] = {}
    for name, line, address, program in (
        ("proxy", 1, 0, _PROXY),
        ("actuator", 1, 5, "M-1_H-1_HP-1"),
        ("coupler", 2, 0, "M-1_H-COUPLER_HP-1"),
        ("sensor", 2, 5, "M-1_H-1_HP-1"),
    ):
        devices[name] = svc.add_device(
            pid,
            segments[line],
            "M-1_H-1_P-1",
            address=address,
            name=name,
            hardware2program_ref_id=program,
            com_objects=[("M-1_A-1_O-1_R-1", None)],
        )
    group_address = svc.create_group_address(pid, 0, 0x0801, "Shared")
    for name in ("actuator", "sensor"):
        com_object = next(
            co.id
            for d in svc.devices(pid)
            if d.id == devices[name]
            for co in d.com_objects
        )
        svc.link_com_object(pid, com_object, group_address, sending=True)
    svc.close(pid)
    with closing(sqlite3.connect(path)) as db, db:
        for name in ("proxy", "actuator"):
            db.execute(
                "UPDATE devices SET extra_subtrees = ? WHERE id = ?",
                (
                    json.dumps({"Security": f'<Security ToolKey="{_TOOL_KEY}" />'}),
                    devices[name],
                ),
            )
    pid = svc.open(path)
    svc.set_project_password(pid, _PASSWORD)
    return svc, pid, devices


def test_a_security_proxy_secures_its_side(tmp_path: Path) -> None:
    svc, pid, devices = _project(tmp_path)
    svc.set_security_context(pid, _Products())
    keys, senders = svc.group_security(pid, devices["actuator"])
    # Secured behind the Security Proxy: the actuator holds the key; the proxy translates the
    # sensor's telegrams, so it is the sender.
    assert set(keys) == {0x0801}
    assert senders == [(0x2100, 0)]
    keys, _senders = svc.group_security(pid, devices["sensor"])
    assert keys == {}
    session = svc._state(pid).session  # pyright: ignore[reportPrivateUsage]
    security = project_security(session)
    assert security.secured_anywhere == {1}
    assert security.domain_of(devices["actuator"]) != security.domain_of(
        devices["sensor"]
    )
    svc.close(pid)


def test_without_the_security_extensions_one_domain(tmp_path: Path) -> None:
    svc, pid, devices = _project(tmp_path)
    # No product knowledge: both couplers count as plain couplers, the lines share a domain and
    # the sensor commissioned in the clear keeps the group address in the clear.
    keys, _senders = svc.group_security(pid, devices["actuator"])
    assert keys == {}
    session = svc._state(pid).session  # pyright: ignore[reportPrivateUsage]
    assert project_security(session).secured_anywhere == set()
    svc.close(pid)


def test_a_line_without_a_coupler_has_its_own_domain(tmp_path: Path) -> None:
    svc, pid, devices = _project(tmp_path)
    svc.remove_device(pid, devices["coupler"])
    # Nothing tells that line 2.2 is coupled transparently: the actuator's side is secured.
    keys, senders = svc.group_security(pid, devices["actuator"])
    assert set(keys) == {0x0801}
    assert senders == []
    svc.close(pid)


def test_the_security_proxy_group_key_table(tmp_path: Path) -> None:
    svc, pid, devices = _project(tmp_path)
    svc.set_security_context(pid, _Products())
    table = svc.security_proxy_table(pid, devices["proxy"], [0x0801, 0x0802])
    assert table is not None
    entries, senders = table
    # Secured on the secondary side (behind the proxy) only; its sender there is the actuator.
    assert [(address, primary) for address, _key, primary in entries] == [
        (0x0801, False)
    ]
    assert len(entries[0][1]) == 16
    assert senders == [(0x2105, 0)]
    # A device feeding no domain of its own translates nothing.
    assert svc.security_proxy_table(pid, devices["coupler"], [0x0801]) is None
    svc.close(pid)


def test_no_entry_for_a_group_address_alike_on_both_sides(tmp_path: Path) -> None:
    svc, pid, devices = _project(tmp_path)
    # Without the security extensions known, the proxy's line shares the domain above it.
    assert svc.security_proxy_table(pid, devices["proxy"], [0x0801]) is None
    svc.set_security_context(pid, _Products())
    # A group address linked nowhere is in the clear on both sides.
    plain = svc.create_group_address(pid, 0, 0x0803, "Unlinked")
    assert plain
    assert svc.security_proxy_table(pid, devices["proxy"], [0x0803]) == ([], [])
    svc.close(pid)


def test_secured_on_one_side_and_plain_on_the_other(tmp_path: Path) -> None:
    svc, pid, _devices = _project(tmp_path)
    svc.set_security_context(pid, _Products())
    # Secured behind the Security Proxy, in the clear on the sensor's line.
    assert svc.plain_somewhere_group_addresses(pid) == {0x0801}
    ga_id = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0801)
    report = svc.group_address_security(pid, ga_id)
    assert report["security"] == report["effective_security"] == "Auto"
    assert report["forced_by"] is None
    assert report["secured"] and report["plain_somewhere"]
    assert not report["contradiction"]
    states = {
        tuple(s["line"] for s in d["segments"]): (d["state"], d["security_proxy"])
        for d in report["domains"]
    }
    assert states == {("2.1",): ("secured", "2.1.0"), ("2.2",): ("plain", None)}
    links = {
        link["device_address"]: (link["device_secure"], link["sending"])
        for link in report["group_objects"]
    }
    assert links == {"2.1.5": (True, True), "2.2.5": (False, True)}
    by_address = {
        link["device_address"]: link["domain"] for link in report["group_objects"]
    }
    assert by_address["2.1.5"] != by_address["2.2.5"]
    assert report["bus_interfaces"] == []
    svc.close(pid)


def test_plain_in_its_only_domain(tmp_path: Path) -> None:
    svc, pid, _devices = _project(tmp_path)
    # One domain: the sensor commissioned in the clear keeps the group address in the clear.
    assert svc.plain_somewhere_group_addresses(pid) == set()
    ga_id = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0801)
    report = svc.group_address_security(pid, ga_id)
    assert not report["secured"] and not report["plain_somewhere"]
    assert [d["state"] for d in report["domains"]] == ["plain"]
    with pytest.raises(KeyError):
        svc.group_address_security(pid, ga_id + 100)
    svc.close(pid)


def test_a_secured_bus_interface_on_a_line_of_its_own(tmp_path: Path) -> None:
    """The layout of issue #334: a sensor commissioned in the clear on line 2.2, whose coupler is
    not in the project, and a secured bus interface on line 2.1 the group address is assigned
    to."""
    from sqlalchemy.orm import Session

    from xknxeditor.proj.db import make_engine, url_for
    from xknxeditor.proj.models import GroupAddress

    svc, pid, devices = _project(tmp_path)
    for name in ("actuator", "coupler"):
        svc.remove_device(pid, devices[name])
    path = tmp_path / "p.xknx"
    svc.close(pid)
    engine = make_engine(url_for(path))
    try:
        with Session(engine) as session:
            for ga in session.query(GroupAddress):
                ga.source_id = f"P-DOM-0_GA-{ga.address}"
            device = session.get(DeviceRow, devices["proxy"])
            assert device is not None
            device.extra_subtrees = {
                **(device.extra_subtrees or {}),
                "BusInterfaces": '<BusInterfaces><BusInterface RefId="M-1_A-1_BI-1">'
                '<Connectors><Connector GroupAddressRefId="P-DOM-0_GA-2049" />'
                "</Connectors></BusInterface></BusInterfaces>",
            }
            session.commit()
    finally:
        engine.dispose()
    pid = svc.open(path, _PASSWORD)
    # Secured on the interface's side, in the clear on the sensor's: the connection takes its
    # telegrams in the clear too.
    assert svc.plain_somewhere_group_addresses(pid) == {0x0801}
    ga_id = next(g.id for g in svc.group_addresses(pid) if g.address == 0x0801)
    report = svc.group_address_security(pid, ga_id)
    assert report["secured"] and report["plain_somewhere"]
    assert [
        (link["device_address"], link["secure"]) for link in report["bus_interfaces"]
    ] == [("2.1.0", True)]
    assert [link["device_address"] for link in report["group_objects"]] == ["2.2.5"]
    states = {
        segment["line"]: d["state"]
        for d in report["domains"]
        for segment in d["segments"]
    }
    assert states == {"2.1": "secured", "2.2": "plain"}
    # Forced off, it is in the clear everywhere.
    svc.set_group_address_security(pid, ga_id, "Off")
    assert svc.plain_somewhere_group_addresses(pid) == set()
    report = svc.group_address_security(pid, ga_id)
    assert report["effective_security"] == "Off" and not report["secured"]
    assert {d["state"] for d in report["domains"]} == {"plain"}
    svc.close(pid)
