"""Device snapshots are stored per device, replaced, and removed with their device."""

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle


def _project(tmp_path: Path) -> tuple[ProjectService, str, list[int]]:
    svc = ProjectService()
    pid = svc.create(
        tmp_path / "p.xknx", "P-0001", group_address_style=GroupAddressStyle.THREE_LEVEL
    )
    seg = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    ids: list[int] = []
    for address in (1, 2):
        device_id = svc.add_device(
            pid,
            seg,
            "M-0001_H-x-1_P-1",
            address=address,
            hardware2program_ref_id="M-0001_H-x-1_HP-0010-01-AAAA",
            parameters=[("M-0001_A-0010-01-AAAA_P-1_R-1", "42")],
        )
        assert device_id is not None
        ids.append(device_id)
    return svc, pid, ids


def test_snapshots_are_stored_and_replaced(tmp_path: Path) -> None:
    svc, pid, (a, b) = _project(tmp_path)
    assert svc.device_snapshots(pid) == {}
    svc.store_device_snapshots(pid, {a: ("k1", "d1"), b: ("k2", "d2")})
    svc.store_device_snapshots(pid, {a: ("k3", "d3")})
    assert svc.device_snapshots(pid) == {a: ("k3", "d3"), b: ("k2", "d2")}
    svc.close(pid)
    reopened = ProjectService()
    pid = reopened.open(tmp_path / "p.xknx")
    assert reopened.device_snapshots(pid) == {a: ("k3", "d3"), b: ("k2", "d2")}
    reopened.close(pid)


def test_snapshot_goes_with_its_device(tmp_path: Path) -> None:
    svc, pid, (a, b) = _project(tmp_path)
    svc.store_device_snapshots(pid, {a: ("k1", "d1"), b: ("k2", "d2")})
    svc.remove_device(pid, a)
    assert svc.device_snapshots(pid) == {b: ("k2", "d2")}
    # A snapshot of a device that no longer exists is not stored.
    svc.store_device_snapshots(pid, {a: ("k1", "d1")})
    assert svc.device_snapshots(pid) == {b: ("k2", "d2")}
    svc.close(pid)


def test_devices_with_contents(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    devices = svc.devices(pid, with_contents=True)
    assert [d.id for d in devices] == ids
    assert [[p.value for p in d.parameters] for d in devices] == [["42"], ["42"]]
    assert [svc.individual_address(pid, d.id) for d in devices] == ["0.0.1", "0.0.2"]
    svc.close(pid)
