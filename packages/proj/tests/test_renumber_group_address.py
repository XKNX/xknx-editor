"""Renumbering a group address (ETS-style "change address"): the GA keeps its id, so com-object
links move with it; missing ranges are created; the step is undoable; duplicates are refused."""

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle, parse_ga


def _addr(svc: ProjectService, pid: str, ga_id: int) -> int:
    for ga in svc.group_addresses(pid):
        if ga.id == ga_id:
            return ga.address
    raise AssertionError("group address vanished")


def test_renumber_preserves_links_and_is_undoable(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(
        tmp_path / "p.xknx", "P-0001", group_address_style=GroupAddressStyle.THREE_LEVEL
    )
    ga = svc.create_group_address(
        pid, 0, parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL), "Decke"
    )

    # Renumber 1/0/1 -> 2/1/5 (a different main AND middle group; ranges must be created).
    target = parse_ga("2/1/5", GroupAddressStyle.THREE_LEVEL)
    svc.set_group_address_address(pid, ga, target)

    assert _addr(svc, pid, ga) == target
    # Same row survived: the id is unchanged (so any link to it is still valid).
    assert any(g.id == ga and g.address == target for g in svc.group_addresses(pid))

    # Undo restores the old address.
    svc.undo(pid)
    assert _addr(svc, pid, ga) == parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL)
    # Redo re-applies.
    svc.redo(pid)
    assert _addr(svc, pid, ga) == target


def test_renumber_refuses_duplicate_address(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(
        tmp_path / "p.xknx", "P-0002", group_address_style=GroupAddressStyle.THREE_LEVEL
    )
    a = svc.create_group_address(
        pid, 0, parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL), "A"
    )
    svc.create_group_address(
        pid, 0, parse_ga("1/0/2", GroupAddressStyle.THREE_LEVEL), "B"
    )

    with pytest.raises(ValueError):
        svc.set_group_address_address(
            pid, a, parse_ga("1/0/2", GroupAddressStyle.THREE_LEVEL)
        )


def _mk(tmp_path: Path, style: GroupAddressStyle = GroupAddressStyle.THREE_LEVEL):
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-R", group_address_style=style)
    return svc, pid


def test_renumber_undo_survives_a_loaded_destination_collection(tmp_path: Path) -> None:
    """Regression: undo must not cascade-delete the moved GA (and its link) when the destination
    range's ``group_addresses`` collection has been loaded (as topology()/the GUI do)."""
    from xknxeditor.proj.models import (
        ComObject,
        ComObjectLink,
        Device,
        GroupAddress,
        Segment,
    )

    svc, pid = _mk(tmp_path)
    ga = svc.create_group_address(
        pid, 0, parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL), "A"
    )
    session = svc._state(pid).session
    seg_id = session.query(Segment.id).first()[0]
    dev = Device(segment_id=seg_id, product_ref_id="test")
    dev.com_objects.append(ComObject(ref_id="O-1"))
    session.add(dev)
    session.flush()
    link_id = ComObjectLink(
        com_object_id=dev.com_objects[0].id, group_address_id=ga, is_sending=True
    )
    session.add(link_id)
    session.commit()

    svc.set_group_address_address(
        pid, ga, parse_ga("2/1/5", GroupAddressStyle.THREE_LEVEL)
    )
    # Load the destination range's collection, the exact condition that triggered the cascade bug.
    dest = session.get(GroupAddress, ga).group_range
    _ = list(dest.group_addresses)

    svc.undo(pid)
    assert session.get(GroupAddress, ga) is not None
    assert _addr(svc, pid, ga) == parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL)
    assert session.query(ComObjectLink).filter_by(group_address_id=ga).count() == 1


def test_renumber_rejects_out_of_range(tmp_path: Path) -> None:
    svc, pid = _mk(tmp_path)
    ga = svc.create_group_address(
        pid, 0, parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL), "A"
    )
    for bad in (0, 0x10000, -1):
        with pytest.raises(ValueError):
            svc.set_group_address_address(pid, ga, bad)


def test_renumber_same_address_is_a_noop(tmp_path: Path) -> None:
    svc, pid = _mk(tmp_path)
    value = parse_ga("1/0/1", GroupAddressStyle.THREE_LEVEL)
    ga = svc.create_group_address(pid, 0, value, "A")
    before = svc.cursor(pid)
    svc.set_group_address_address(pid, ga, value)  # unchanged -> must append no event
    assert svc.cursor(pid) == before
