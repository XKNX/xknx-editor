"""Setting a group object or group address to the value it has already records no edit."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.models import ComObject


def _project(tmp_path: Path) -> tuple[ProjectService, str, int]:
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
    return svc, pid, com_object


def test_the_datapoint_type_already_set_is_no_edit(tmp_path: Path) -> None:
    svc, pid, co = _project(tmp_path)
    steps = len(svc.history(pid))
    assert not svc.set_com_object_datapoint_type(pid, co, None)
    assert svc.set_com_object_datapoint_type(pid, co, "DPST-1-1")
    # Another spelling of the same type changes nothing either.
    assert not svc.set_com_object_datapoint_type(pid, co, "1.001")
    assert svc.set_com_object_datapoint_type(pid, co, None)
    assert len(svc.history(pid)) == steps + 2


def test_the_priority_already_set_is_no_edit(tmp_path: Path) -> None:
    svc, pid, co = _project(tmp_path)
    steps = len(svc.history(pid))
    assert not svc.set_com_object_priority(pid, co, None)
    assert svc.set_com_object_priority(pid, co, "High")
    assert not svc.set_com_object_priority(pid, co, "High")
    assert len(svc.history(pid)) == steps + 1


def test_the_description_already_set_is_no_edit(tmp_path: Path) -> None:
    svc, pid, co = _project(tmp_path)
    steps = len(svc.history(pid))
    assert not svc.set_com_object_description(pid, co, "")
    assert svc.set_com_object_description(pid, co, "Note")
    assert not svc.set_com_object_description(pid, co, "Note")
    assert svc.set_com_object_description(pid, co, None)
    assert len(svc.history(pid)) == steps + 2


def test_the_flag_already_forced_is_no_edit(tmp_path: Path) -> None:
    svc, pid, co = _project(tmp_path)
    steps = len(svc.history(pid))
    assert not svc.set_com_object_flag(pid, co, "transmit_flag", None)
    assert svc.set_com_object_flag(pid, co, "transmit_flag", True)
    assert not svc.set_com_object_flag(pid, co, "transmit_flag", True)
    assert svc.set_com_object_flag(pid, co, "transmit_flag", None)
    assert len(svc.history(pid)) == steps + 2


def test_the_group_address_type_already_set_is_no_edit(tmp_path: Path) -> None:
    svc, pid, _ = _project(tmp_path)
    ga = svc.create_group_address(pid, 0, 0x0A03, "GA")
    steps = len(svc.history(pid))
    assert not svc.set_group_address_datapoint_type(pid, ga, None)
    assert svc.set_group_address_datapoint_type(pid, ga, "1.001")
    assert not svc.set_group_address_datapoint_type(pid, ga, "DPST-1-1")
    assert len(svc.history(pid)) == steps + 1
