"""A main or middle group's Security: forced on everything below it, the addresses' own modes kept
for when it is back on Auto, and kept on export and import; and a group address's Central mark."""

from __future__ import annotations

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
    export_knxproj,
    import_knxproj,
)
from xknxeditor.proj.core.events import SetGroupRangeSecurity
from xknxeditor.proj.core.service import GroupAddressInfo, GroupRangeInfo

_PASSWORD = "Projekt-Passwort-9"
_TOOL_KEY = base64.b64encode(bytes(range(32, 48))).decode("ascii")


def _project(
    tmp_path: Path, *, commissioned: bool = True
) -> tuple[ProjectService, str, int]:
    """Group addresses 1/0/1 (linked to a device, commissioned securely unless
    ``commissioned`` is false) and 1/0/2 (unlinked); the project has a password."""
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-SEC")
    area = svc.create_area(pid, 0, 2, "A")
    line = svc.create_line(pid, area, 1, "L")
    segment = next(
        ln.segments[0].id
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == line
    )
    device = svc.add_device(
        pid,
        segment,
        "M-1_H-1_P-1",
        address=5,
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None)],
    )
    linked = svc.create_group_address(pid, 0, 0x0801, "Linked")
    svc.create_group_address(pid, 0, 0x0802, "Unlinked")
    com_object = next(
        co.id for d in svc.devices(pid) if d.id == device for co in d.com_objects
    )
    svc.link_com_object(pid, com_object, linked, sending=True)
    svc.close(pid)
    if commissioned:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "UPDATE devices SET extra_subtrees = ? WHERE id = ?",
                (
                    json.dumps({"Security": f'<Security ToolKey="{_TOOL_KEY}" />'}),
                    device,
                ),
            )
    pid = svc.open(path)
    svc.set_project_password(pid, _PASSWORD)
    main = svc.group_ranges(pid, 0)[0].id
    return svc, pid, main


def _main(svc: ProjectService, pid: str) -> GroupRangeInfo:
    return svc.group_ranges(pid, 0)[0]


def _addresses(svc: ProjectService, pid: str) -> dict[int, GroupAddressInfo]:
    return {ga.address: ga for ga in svc.group_addresses(pid)}


def test_a_forced_group_decides_for_everything_below(tmp_path: Path) -> None:
    svc, pid, main = _project(tmp_path)
    svc.set_group_address_security(pid, _addresses(svc, pid)[0x0802].id, "Off")
    middle = _main(svc, pid).children[0]
    assert _main(svc, pid).security_mode is None

    svc.set_group_range_security(pid, main, "On")
    gas = _addresses(svc, pid)
    assert gas[0x0802].security_mode == "Off"  # its own stays
    assert gas[0x0802].effective_security_mode == "On"
    assert gas[0x0802].data_secure and gas[0x0801].data_secure
    forced = gas[0x0802].forced_by
    assert forced is not None and (forced.group_range_id, forced.mode) == (main, "On")
    assert _main(svc, pid).children[0].forced_by == forced
    assert _main(svc, pid).forced_by is None

    # The nearest forcing group wins.
    svc.set_group_range_security(pid, middle.id, "Off")
    gas = _addresses(svc, pid)
    assert not gas[0x0801].data_secure
    forced = gas[0x0801].forced_by
    assert forced is not None and forced.group_range_id == middle.id
    # An address's own mode below a forcing group is stored and changes nothing.
    svc.set_group_address_security(pid, gas[0x0801].id, "On")
    assert not _addresses(svc, pid)[0x0801].data_secure

    svc.set_group_range_security(pid, middle.id, "Auto")
    svc.set_group_range_security(pid, main, None)
    gas = _addresses(svc, pid)
    assert gas[0x0802].effective_security_mode == "Off"
    assert gas[0x0801].data_secure  # its own On counts again
    assert gas[0x0801].forced_by is None


def test_forcing_on_gives_keys_and_undo_takes_them_back(tmp_path: Path) -> None:
    svc, pid, main = _project(tmp_path)
    keys_before = svc.secured_group_keys(pid)
    assert 0x0802 not in keys_before
    svc.set_group_range_security(pid, main, "On")
    keys = svc.secured_group_keys(pid)
    assert {0x0801, 0x0802} <= set(keys)
    # The same mode again is no step.
    steps = len(svc.history(pid))
    svc.set_group_range_security(pid, main, "On")
    assert len(svc.history(pid)) == steps
    svc.undo(pid)
    assert _main(svc, pid).security_mode is None
    assert not _addresses(svc, pid)[0x0802].data_secure


def test_forcing_on_needs_securely_commissioned_devices_and_a_password(
    tmp_path: Path,
) -> None:
    svc, pid, main = _project(tmp_path, commissioned=False)
    with pytest.raises(GroupAddressSecurityError):
        svc.set_group_range_security(pid, main, "On")
    svc.set_group_range_security(pid, main, "Off")
    with pytest.raises(ValueError, match="Unknown group range security mode"):
        svc.set_group_range_security(pid, main, "Maybe")
    with pytest.raises(KeyError):
        svc.set_group_range_security(pid, 999, "On")

    plain = ProjectService()
    ppid = plain.create(tmp_path / "plain.xknx", "P-PLN")
    plain.create_group_address(ppid, 0, 0x0801, "GA")
    with pytest.raises(ProjectPasswordRequired):
        plain.set_group_range_security(ppid, plain.group_ranges(ppid, 0)[0].id, "On")
    plain.close(ppid)


def test_the_keys_given_survive_the_stored_history() -> None:
    event = SetGroupRangeSecurity(group_range_id=3, security_mode="On", keys={7: "k"})
    assert SetGroupRangeSecurity.from_dict(event.to_dict()).keys == {7: "k"}


def test_a_group_address_can_be_central(tmp_path: Path) -> None:
    svc, pid, _main_id = _project(tmp_path)
    ga = _addresses(svc, pid)[0x0801]
    assert not ga.central
    svc.set_group_address_central(pid, ga.id, True)
    assert _addresses(svc, pid)[0x0801].central
    steps = len(svc.history(pid))
    svc.set_group_address_central(pid, ga.id, True)
    assert len(svc.history(pid)) == steps
    svc.undo(pid)
    assert not _addresses(svc, pid)[0x0801].central
    with pytest.raises(KeyError):
        svc.set_group_address_central(pid, 999, True)


def test_export_and_import_keep_the_group_security_and_central(tmp_path: Path) -> None:
    svc, pid, main = _project(tmp_path)
    svc.set_group_range_security(pid, main, "Off")
    svc.set_group_address_central(pid, _addresses(svc, pid)[0x0801].id, True)
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23", password=_PASSWORD)
    imported = tmp_path / "imported.xknx"
    import_knxproj(out, imported, password=_PASSWORD, project_password=_PASSWORD)
    svc2 = ProjectService()
    pid2 = svc2.open(imported, _PASSWORD)
    assert svc2.group_ranges(pid2, 0)[0].security_mode == "Off"
    gas = _addresses(svc2, pid2)
    assert gas[0x0801].central
    assert gas[0x0801].effective_security_mode == "Off"
    svc2.close(pid2)
