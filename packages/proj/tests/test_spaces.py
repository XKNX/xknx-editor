"""Building-space edits: create/rename/move/remove of location-tree nodes and assigning devices
to spaces, each an event with a working undo/redo round-trip, and the rules of which building
part goes where. Everything is driven through the public service methods (the same paths the GUI
uses)."""

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.space_rules import (
    SPACE_TYPES,
    SpaceRuleError,
    check_child,
    child_types,
    holds_devices,
)
from xknxeditor.proj.models import Segment


def _new(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0001")
    return svc, pid


def _device(svc: ProjectService, pid: str, name: str) -> int:
    """A device on a fresh area/line/segment (needed so ``unassigned_devices`` can find it)."""
    area = svc.create_area(pid, 0, 2, "A")
    line = svc.create_line(pid, area, 1, "L")
    session = svc._state(pid).session  # type: ignore[attr-defined]
    segment = session.query(Segment).filter_by(line_id=line).first()
    assert segment is not None
    return svc.add_device(pid, segment.id, "M-0001_P-1", name=name)


def _tree(svc: ProjectService, pid: str):
    return svc.space_tree(pid, 0)


def _building(svc: ProjectService, pid: str) -> int:
    """The building a new project has."""
    return _tree(svc, pid)[0].id


def _find(spaces, space_id: int):
    for s in spaces:
        if s.id == space_id:
            return s
        hit = _find(s.children, space_id)
        if hit is not None:
            return hit
    return None


def _reason(error: pytest.ExceptionInfo[SpaceRuleError]) -> str:
    return error.value.reason


def test_which_building_part_goes_where() -> None:
    assert child_types(None) == ("Building",)
    assert child_types("Building") == (
        "BuildingPart",
        "Floor",
        "Stairway",
        "Room",
        "Corridor",
    )
    assert child_types("BuildingPart") == child_types("Building")
    assert child_types("Floor") == ("BuildingPart", "Room", "Corridor")
    for holder in ("Stairway", "Room", "Corridor"):
        assert child_types(holder) == ("DistributionBoard",)
        assert holds_devices(holder)
    assert child_types("DistributionBoard") == ()
    assert holds_devices("DistributionBoard")
    for other in ("Building", "BuildingPart", "Floor", "Area"):
        assert not holds_devices(other)
    assert child_types("Area") == ()
    assert SPACE_TYPES[-1] == "DistributionBoard"
    with pytest.raises(SpaceRuleError) as error:
        check_child("Floor", "DistributionBoard")
    assert _reason(error) == "cabinet_not_here"
    assert "Cabinets can be added inside of rooms" in str(error.value)
    with pytest.raises(SpaceRuleError) as error:
        check_child("Floor", "Stairway")
    assert _reason(error) == "type_not_here"


def test_create_and_rename_with_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)

    building = svc.create_space(pid, 0, "Building", "Haus")
    floor = svc.create_space(pid, 0, "Floor", "OG", parent_id=building)
    tree = _tree(svc, pid)
    # index 0 is the seeded default building (named after the "p.xknx" file); "Haus" follows it
    assert [(s.name, s.space_type) for s in tree] == [
        ("p", "Building"),
        ("Haus", "Building"),
    ]
    assert tree[1].id == building
    assert [(c.id, c.name, c.space_type) for c in tree[1].children] == [
        (floor, "OG", "Floor")
    ]
    for parent, space_type in ((None, "Floor"), (floor, "Floor"), (floor, "Stairway")):
        with pytest.raises(SpaceRuleError):
            svc.create_space(pid, 0, space_type, "x", parent_id=parent)
    with pytest.raises(KeyError):
        svc.create_space(pid, 0, "Room", "x", parent_id=999)

    svc.rename_space(pid, floor, "Obergeschoss")
    assert _find(_tree(svc, pid), floor).name == "Obergeschoss"
    svc.undo(pid)  # undo rename
    assert _find(_tree(svc, pid), floor).name == "OG"
    svc.undo(pid)  # undo create floor
    assert _find(_tree(svc, pid), floor) is None
    svc.redo(pid)  # redo create floor
    assert _find(_tree(svc, pid), floor).name == "OG"


def test_move_space_reparent_and_guards(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    building = svc.create_space(pid, 0, "Building", "Haus")
    floor_a = svc.create_space(pid, 0, "Floor", "EG", parent_id=building)
    floor_b = svc.create_space(pid, 0, "Floor", "OG", parent_id=building)
    room = svc.create_space(pid, 0, "Room", "Flur", parent_id=floor_a)
    cabinet = svc.create_space(pid, 0, "DistributionBoard", "UV", parent_id=room)

    # Move the room from EG to OG.
    svc.move_space(pid, room, floor_b)
    assert _find(_tree(svc, pid), floor_a).children == []
    assert [c.id for c in _find(_tree(svc, pid), floor_b).children] == [room]

    svc.undo(pid)  # back under EG
    assert [c.id for c in _find(_tree(svc, pid), floor_a).children] == [room]

    # A move into itself or below itself is refused.
    for target in (room, cabinet):
        with pytest.raises(SpaceRuleError) as error:
            svc.move_space(pid, building, target)
        assert _reason(error) in ("target_is_child", "type_not_here")
    with pytest.raises(SpaceRuleError) as error:
        svc.move_space(pid, floor_a, cabinet)
    assert _reason(error) == "target_is_child"
    # Only buildings go to the top level, a cabinet only into a stairway, room or corridor.
    with pytest.raises(SpaceRuleError) as error:
        svc.move_space(pid, room, None)
    assert _reason(error) == "type_not_here"
    with pytest.raises(SpaceRuleError) as error:
        svc.move_space(pid, cabinet, floor_b)
    assert _reason(error) == "cabinet_not_here"
    svc.move_space(pid, building, None)  # already there: nothing
    with pytest.raises(KeyError):
        svc.move_space(pid, 999, None)


def test_assign_device_to_space_with_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    room = svc.create_space(pid, 0, "Room", "Flur", parent_id=_building(svc, pid))
    dev = _device(svc, pid, "Schalter")

    assert [d.id for d in svc.unassigned_devices(pid, 0)] == [dev]
    # A building holds no devices.
    with pytest.raises(SpaceRuleError) as error:
        svc.set_device_space(pid, dev, _building(svc, pid))
    assert _reason(error) == "no_devices"
    with pytest.raises(KeyError):
        svc.set_device_space(pid, dev, 999)

    svc.set_device_space(pid, dev, room)
    assert [d.id for d in _find(_tree(svc, pid), room).devices] == [dev]
    assert svc.unassigned_devices(pid, 0) == []

    svc.undo(pid)  # unassign again
    assert _find(_tree(svc, pid), room).devices == []
    assert [d.id for d in svc.unassigned_devices(pid, 0)] == [dev]

    svc.redo(pid)  # reassign
    assert [d.id for d in _find(_tree(svc, pid), room).devices] == [dev]


def test_a_device_is_added_to_a_space_that_holds_devices(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    building = _building(svc, pid)
    session = svc._state(pid).session  # type: ignore[attr-defined]
    segment = session.query(Segment).first()
    assert segment is not None
    with pytest.raises(SpaceRuleError):
        svc.add_device(pid, segment.id, "M-0001_P-1", space_id=building)
    with pytest.raises(KeyError):
        svc.add_device(pid, segment.id, "M-0001_P-1", space_id=999)


def test_remove_space_unassigns_devices_and_restores_on_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    building = svc.create_space(pid, 0, "Building", "Haus")
    room = svc.create_space(pid, 0, "Room", "Flur", parent_id=building)
    dev = _device(svc, pid, "Schalter")
    svc.set_device_space(pid, dev, room)

    # Removing the building removes the child room too and unassigns the device.
    svc.remove_space(pid, building)
    assert _find(_tree(svc, pid), building) is None
    assert _find(_tree(svc, pid), room) is None
    assert [d.id for d in svc.unassigned_devices(pid, 0)] == [dev]

    # Undo restores the subtree AND the device's original room placement.
    svc.undo(pid)
    node = _find(_tree(svc, pid), room)
    assert node is not None and [d.id for d in node.devices] == [dev]
    assert svc.unassigned_devices(pid, 0) == []


def test_space_devices_sorted_by_address(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    room = svc.create_space(pid, 0, "Room", "Flur", parent_id=_building(svc, pid))
    area = svc.create_area(pid, 0, 2, "A")
    line = svc.create_line(pid, area, 1, "L")
    session = svc._state(pid).session  # type: ignore[attr-defined]
    segment = session.query(Segment).filter_by(line_id=line).first()
    assert segment is not None
    ids = {
        address: svc.add_device(
            pid, segment.id, "M-0001_P-1", address=address, name="Aktor"
        )
        for address in (49, None, 5, 10)
    }
    expected = ["2.1.5", "2.1.10", "2.1.49", None]

    assert [d.individual_address for d in svc.unassigned_devices(pid, 0)] == expected
    for device_id in ids.values():
        svc.set_device_space(pid, device_id, room)
    node = _find(_tree(svc, pid), room)
    assert [d.individual_address for d in node.devices] == expected


def test_set_number_and_description_with_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    floor = svc.create_space(pid, 0, "Floor", "OG", parent_id=_building(svc, pid))

    svc.set_space_number(pid, floor, "1.OG")
    svc.set_space_description(pid, floor, "Bedrooms and bath")
    node = _find(_tree(svc, pid), floor)
    assert node is not None
    assert (node.number, node.description) == ("1.OG", "Bedrooms and bath")

    svc.undo(pid)  # undo description
    assert _find(_tree(svc, pid), floor).description == ""
    svc.undo(pid)  # undo number
    assert _find(_tree(svc, pid), floor).number == ""
    svc.redo(pid)
    svc.redo(pid)
    node = _find(_tree(svc, pid), floor)
    assert (node.number, node.description) == ("1.OG", "Bedrooms and bath")
