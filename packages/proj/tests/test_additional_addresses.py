"""Additional individual addresses (tunnelling addresses of IP interfaces): assigned from the
highest free device numbers of the line, validated and edited as undoable steps, assigned along
with a new device and again when the device moves to another line."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.proj import AdditionalAddressError, ProjectService


def _segment(svc: ProjectService, pid: str, area_no: int, line_no: int) -> int:
    return next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.address == area_no
        for line in area.lines
        if line.address == line_no
    )


Project = tuple[ProjectService, str, int, int]


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A new project (it has the lines 1.0 and 1.1); the segment ids of both lines."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-ADD1")
    return svc, pid, _segment(svc, pid, 1, 0), _segment(svc, pid, 1, 1)


def _device(
    svc: ProjectService, pid: str, segment_id: int, address: int, count: int = 0
) -> int:
    return svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=address,
        name=f"Device {address}",
        additional_addresses=count,
    )


def _octets(svc: ProjectService, pid: str, device_id: int) -> list[int | None]:
    return [entry.octet for entry in svc.additional_addresses(pid, device_id)]


def test_highest_free_numbers_counting_down(project: Project) -> None:
    svc, pid, segment, _ = project
    first = _device(svc, pid, segment, 3, count=8)
    assert _octets(svc, pid, first) == list(range(255, 247, -1))
    entries = svc.additional_addresses(pid, first)
    assert entries[0].index == 1
    assert entries[0].address == "1.0.255"
    second = _device(svc, pid, segment, 4, count=16)
    assert _octets(svc, pid, second) == list(range(247, 231, -1))
    assert svc.device(pid, second).additional_addresses[0].address == "1.0.247"


def test_assignment_skips_device_numbers(project: Project) -> None:
    svc, pid, segment, _ = project
    _device(svc, pid, segment, 255)
    _device(svc, pid, segment, 253)
    device = _device(svc, pid, segment, 1)
    svc.assign_additional_addresses(pid, device, 3)
    assert _octets(svc, pid, device) == [254, 252, 251]


def test_assign_fills_entries_without_an_address(project: Project) -> None:
    svc, pid, segment, _ = project
    device = _device(svc, pid, segment, 1, count=3)
    svc.set_additional_address(pid, device, 2, None)
    assert _octets(svc, pid, device) == [255, None, 253]
    svc.assign_additional_addresses(pid, device)
    assert _octets(svc, pid, device) == [255, 254, 253]
    # Nothing is missing: no new history entry.
    entries = len(svc.history(pid))
    svc.assign_additional_addresses(pid, device, 3)
    assert len(svc.history(pid)) == entries


def test_not_enough_free_numbers(project: Project) -> None:
    svc, pid, segment, _ = project
    device = _device(svc, pid, segment, 1, count=250)
    with pytest.raises(AdditionalAddressError) as error:
        svc.assign_additional_addresses(pid, device, 260)
    assert error.value.kind == AdditionalAddressError.NOT_ENOUGH
    assert str(error.value) == (
        "The line does not provide enough addresses for all necessary additional addresses."
    )
    assert len(svc.additional_addresses(pid, device)) == 250
    with pytest.raises(AdditionalAddressError):
        _device(svc, pid, segment, 2, count=5)


def test_set_address_validation(project: Project) -> None:
    svc, pid, segment, other_segment = project
    device = _device(svc, pid, segment, 3, count=2)
    other = _device(svc, pid, segment, 4, count=1)  # gets 1.0.253
    _device(svc, pid, other_segment, 100)

    def kind(address: int | str | None, index: int = 1) -> str:
        with pytest.raises(AdditionalAddressError) as error:
            svc.set_additional_address(pid, device, index, address)
        return error.value.kind

    assert kind(0) == AdditionalAddressError.INVALID
    assert kind(256) == AdditionalAddressError.INVALID
    assert kind("1.0.x") == AdditionalAddressError.INVALID
    assert kind("1.1.200") == AdditionalAddressError.OTHER_LINE
    assert kind(3) == AdditionalAddressError.IN_USE  # the device's own address
    assert kind(4) == AdditionalAddressError.IN_USE  # another device
    assert (
        kind("1.0.253") == AdditionalAddressError.IN_USE
    )  # another additional address
    assert kind(254) == AdditionalAddressError.IN_USE  # its own entry 2
    assert _octets(svc, pid, device) == [255, 254]
    assert _octets(svc, pid, other) == [253]
    # 1.1.100 is a device of the other line: free in this one.
    svc.set_additional_address(pid, device, 1, "1.0.100")
    svc.set_additional_address(pid, device, 2, "250")
    assert _octets(svc, pid, device) == [100, 250]
    assert svc.additional_address_text(pid, device, "17") == "1.0.17"
    assert svc.additional_address_text(pid, device, "") is None


def test_set_undo_redo(project: Project) -> None:
    svc, pid, segment, _ = project
    device = _device(svc, pid, segment, 3, count=2)
    svc.set_additional_address(pid, device, 1, 200, label="Changed")
    svc.set_additional_address_name(pid, device, 2, "Tunnel 2")
    svc.set_additional_address_description(pid, device, 2, "Visualisation")
    entries = svc.additional_addresses(pid, device)
    assert [(e.octet, e.name, e.description) for e in entries] == [
        (200, "", ""),
        (254, "Tunnel 2", "Visualisation"),
    ]
    assert svc.peek_undo(pid) is not None
    svc.undo(pid)
    svc.undo(pid)
    assert svc.additional_addresses(pid, device)[1].name == ""
    svc.undo(pid)
    assert _octets(svc, pid, device) == [255, 254]
    svc.redo(pid)
    assert _octets(svc, pid, device) == [200, 254]
    peek = svc.peek_undo(pid)
    assert peek is not None
    assert peek[0] == "SetDeviceAdditionalAddresses"
    assert peek[1]["label"] == "Changed"


def test_assign_undo(project: Project) -> None:
    svc, pid, segment, _ = project
    device = _device(svc, pid, segment, 3)
    svc.assign_additional_addresses(pid, device, 2)
    assert _octets(svc, pid, device) == [255, 254]
    svc.undo(pid)
    assert _octets(svc, pid, device) == []
    svc.redo(pid)
    assert _octets(svc, pid, device) == [255, 254]


def test_add_device_undo_removes_the_addresses(project: Project) -> None:
    svc, pid, segment, _ = project
    device = _device(svc, pid, segment, 3, count=4)
    assert _octets(svc, pid, device) == [255, 254, 253, 252]
    svc.undo(pid)
    assert all(row.id != device for row in svc.devices(pid))
    # The numbers are free again.
    again = _device(svc, pid, segment, 4, count=1)
    assert _octets(svc, pid, again) == [255]
    svc.undo(pid)
    svc.redo(pid)
    assert _octets(svc, pid, again) == [255]


def test_moving_to_another_line_assigns_again(project: Project) -> None:
    svc, pid, segment, other_segment = project
    _device(svc, pid, other_segment, 255)
    device = _device(svc, pid, segment, 3, count=2)
    svc.set_additional_address_name(pid, device, 1, "Tunnel 1")
    svc.set_individual_address(pid, device, "1.1.7")
    entries = svc.additional_addresses(pid, device)
    assert [e.address for e in entries] == ["1.1.254", "1.1.253"]
    assert entries[0].name == "Tunnel 1"
    # One undo step moves it back with its old addresses.
    svc.undo(pid)
    assert svc.individual_address(pid, device) == "1.0.3"
    assert _octets(svc, pid, device) == [255, 254]
    # Within the line the additional addresses stay.
    svc.set_individual_address(pid, device, "1.0.9")
    assert _octets(svc, pid, device) == [255, 254]


def test_device_addresses_skip_additional_addresses(project: Project) -> None:
    svc, pid, segment, _ = project
    interface = _device(svc, pid, segment, 3, count=2)
    svc.set_additional_address(pid, interface, 1, 1)
    svc.set_additional_address(pid, interface, 2, 2)
    line_id = next(
        line.id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if any(s.id == segment for s in line.segments)
    )
    assert svc.next_free_individual_address(pid, line_id) == 4
    with pytest.raises(ValueError, match="additional address"):
        _device(svc, pid, segment, 2)
    other = _device(svc, pid, segment, 10)
    with pytest.raises(ValueError, match="additional address"):
        svc.set_individual_address(pid, other, "1.0.1")
