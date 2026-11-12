"""Which edits mark a device's parameters or group communication as not loaded (so a partial
download loads them again), and that undo marks them loaded again."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.models import ComObject, Device


def _project(tmp_path: Path) -> tuple[ProjectService, str, int, int]:
    """A new project with a downloaded device with one group object."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    segment = next(ln for ln in area.lines if ln.address == 1).segments[0].id
    device = svc.add_device(
        pid, segment, "P-1", address=5, com_objects=[("O-1_R-1", None)]
    )
    svc.set_parameter(pid, device, "P-1_R-1", "1")
    svc.set_device_commissioning(
        pid,
        device,
        parameters_loaded=True,
        communication_part_loaded=True,
        download=True,
    )
    com_object = (
        svc._state(pid).session.query(ComObject).filter_by(device_id=device).one().id  # type: ignore[attr-defined]
    )
    return svc, pid, device, com_object


def _loaded(svc: ProjectService, pid: str, device: int) -> tuple[bool, bool]:
    row = svc._state(pid).session.get(Device, device)  # type: ignore[attr-defined]
    return row.parameters_loaded, row.communication_part_loaded


def test_a_changed_parameter_is_not_loaded(tmp_path: Path) -> None:
    svc, pid, device, _ = _project(tmp_path)
    svc.set_parameter(pid, device, "P-1_R-1", "7")
    assert _loaded(svc, pid, device) == (False, True)
    svc.undo(pid)
    assert _loaded(svc, pid, device) == (True, True)
    svc.redo(pid)
    assert _loaded(svc, pid, device) == (False, True)


def test_a_new_parameter_value_is_not_loaded(tmp_path: Path) -> None:
    svc, pid, device, _ = _project(tmp_path)
    svc.set_parameter(pid, device, "P-1_R-2", "3")
    assert _loaded(svc, pid, device) == (False, True)


def test_the_same_value_again_changes_nothing(tmp_path: Path) -> None:
    svc, pid, device, _ = _project(tmp_path)
    svc.set_parameter(pid, device, "P-1_R-1", "1")
    assert _loaded(svc, pid, device) == (True, True)
    svc.undo(pid)
    assert _loaded(svc, pid, device) == (True, True)


def test_a_changed_group_object_flag_is_not_loaded(tmp_path: Path) -> None:
    svc, pid, device, com_object = _project(tmp_path)
    svc.set_com_object_flag(pid, com_object, "transmit_flag", True)
    assert _loaded(svc, pid, device) == (True, False)
    svc.undo(pid)
    assert _loaded(svc, pid, device) == (True, True)


def test_a_changed_group_object_priority_is_not_loaded(tmp_path: Path) -> None:
    svc, pid, device, com_object = _project(tmp_path)
    svc.set_com_object_priority(pid, com_object, "High")
    assert _loaded(svc, pid, device) == (True, False)
    svc.undo(pid)
    assert _loaded(svc, pid, device) == (True, True)
    # The same priority again changes nothing.
    svc.set_com_object_priority(pid, com_object, None)
    assert _loaded(svc, pid, device) == (True, True)


def _values(svc: ProjectService, pid: str, device: int) -> dict[str, str]:
    from xknxeditor.proj.models import Parameter

    session = svc._state(pid).session  # type: ignore[attr-defined]
    return {
        p.ref_id: p.value
        for p in session.query(Parameter).filter_by(device_id=device).all()
    }


def test_values_a_calculation_stores_are_no_undo_step(tmp_path: Path) -> None:
    svc, pid, device, _ = _project(tmp_path)
    history = len(svc.history(pid))
    stamp = svc._state(pid).session.get(Device, device).last_modified  # type: ignore[attr-defined]
    stored = svc.store_calculated_parameters(
        pid, device, [("P-1_R-1", "1", "4"), ("P-1_R-2", None, "2")]
    )
    assert stored == ["P-1_R-1", "P-1_R-2"]
    assert _values(svc, pid, device) == {"P-1_R-1": "4", "P-1_R-2": "2"}
    assert len(svc.history(pid)) == history
    # The parameters are not loaded any more, which is a modification of the device.
    assert _loaded(svc, pid, device) == (False, True)
    assert svc._state(pid).session.get(Device, device).last_modified != stamp  # type: ignore[attr-defined]


def test_a_calculation_keeps_a_value_changed_since(tmp_path: Path) -> None:
    svc, pid, device, _ = _project(tmp_path)
    svc.set_parameter(pid, device, "P-1_R-1", "7")
    assert svc.store_calculated_parameters(pid, device, [("P-1_R-1", "1", "4")]) == []
    assert _values(svc, pid, device) == {"P-1_R-1": "7"}
    # Nothing changed: the device stays as it is.
    svc.set_device_commissioning(pid, device, parameters_loaded=True, download=True)
    assert svc.store_calculated_parameters(pid, device, [("P-1_R-1", "7", "7")]) == []
    assert _loaded(svc, pid, device) == (True, True)
