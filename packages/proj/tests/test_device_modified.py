"""A device's "Last Modified" time: which edits stamp it, and that undo and downloads do not set
it back or change it."""

from __future__ import annotations

import datetime
from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.modified import project_timestamp
from xknxeditor.proj.models import ComObject, Device


def _project(tmp_path: Path) -> tuple[ProjectService, str, int, int]:
    """A new project with a device with one group object, and a group address."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    segment = next(ln for ln in area.lines if ln.address == 1).segments[0].id
    device = svc.add_device(
        pid, segment, "P-1", address=5, com_objects=[("O-1_R-1", None)]
    )
    com_object = (
        svc._state(pid).session.query(ComObject).filter_by(device_id=device).one().id  # type: ignore[attr-defined]
    )
    return svc, pid, device, com_object


def _stamp(svc: ProjectService, pid: str, device: int) -> str | None:
    session = svc._state(pid).session  # type: ignore[attr-defined]
    return session.get(Device, device).last_modified


def _clear(svc: ProjectService, pid: str, device: int) -> None:
    session = svc._state(pid).session  # type: ignore[attr-defined]
    session.get(Device, device).last_modified = None
    session.commit()


def test_the_stored_time() -> None:
    moment = datetime.datetime(2026, 1, 24, 11, 38, 26, 811113, tzinfo=datetime.UTC)
    assert project_timestamp(moment) == "2026-01-24T11:38:26.811113Z"
    assert project_timestamp(moment.replace(microsecond=500000)) == (
        "2026-01-24T11:38:26.5Z"
    )
    assert project_timestamp(moment.replace(microsecond=0)) == "2026-01-24T11:38:26Z"
    assert project_timestamp().endswith("Z")


def test_edits_that_stamp_it(tmp_path: Path) -> None:
    svc, pid, device, com_object = _project(tmp_path)
    # Adding the device stamped it.
    assert _stamp(svc, pid, device)
    for edit in (
        lambda: svc.set_parameter(pid, device, "P-1_R-1", "7"),
        lambda: svc.set_com_object_flag(pid, com_object, "read_flag", True),
        lambda: svc.set_device_commissioning(pid, device, serial_number="ABC"),
    ):
        _clear(svc, pid, device)
        edit()
        assert _stamp(svc, pid, device), edit
    # A name and a description do not.
    _clear(svc, pid, device)
    svc.set_device_name(pid, device, "Dimmer")
    svc.set_device_description(pid, device, "Hall")
    assert _stamp(svc, pid, device) is None


def test_undo_and_downloads(tmp_path: Path) -> None:
    svc, pid, device, com_object = _project(tmp_path)
    svc.set_parameter(pid, device, "P-1_R-1", "7")
    _clear(svc, pid, device)
    # Undoing a parameter change leaves it; undoing a flag change stamps it again.
    svc.undo(pid)
    assert _stamp(svc, pid, device) is None
    svc.set_com_object_flag(pid, com_object, "read_flag", True)
    _clear(svc, pid, device)
    svc.undo(pid)
    assert _stamp(svc, pid, device)
    # A download changes the loaded ticks, but not "Last Modified".
    _clear(svc, pid, device)
    svc.set_device_commissioning(
        pid,
        device,
        communication_part_loaded=True,
        last_download=project_timestamp(),
        download=True,
    )
    assert _stamp(svc, pid, device) is None
    # Linking a group address marks the group communication not loaded: that stamps it.
    ga = svc.create_group_address(pid, 0, 0x0901, "Light")
    svc.link_com_object(pid, com_object, ga)
    assert _stamp(svc, pid, device)
    # Once it is not loaded, a further link changes nothing that was downloaded.
    _clear(svc, pid, device)
    other = svc.create_group_address(pid, 0, 0x0902, "Light 2")
    svc.link_com_object(pid, com_object, other)
    assert _stamp(svc, pid, device) is None
