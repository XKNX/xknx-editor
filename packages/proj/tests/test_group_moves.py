"""A group address moves into another group and a middle group below another main group: each
keeps its number there when free, else gets the lowest free one; undone in one step."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga
from xknxeditor.proj.core.service import GroupRangeInfo


def _ga(main: int, middle: int, sub: int) -> int:
    return (main << 11) | (middle << 8) | sub


def _project(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    for address, name in (
        (_ga(1, 1, 5), "Light"),
        (_ga(1, 2, 1), "Blind"),
        (_ga(2, 1, 5), "Taken"),
        (_ga(2, 2, 1), "Other"),
    ):
        svc.create_group_address(pid, 0, address, name)
    return svc, pid


def _range(
    svc: ProjectService, pid: str, main: int, middle: int | None = None
) -> GroupRangeInfo:
    top = next(r for r in svc.group_ranges(pid, 0) if r.range_start >> 11 == main)
    if middle is None:
        return top
    return next(r for r in top.children if (r.range_start >> 8) & 7 == middle)


def _addresses(svc: ProjectService, pid: str) -> dict[str, str]:
    return {
        g.name: format_ga(g.address, GroupAddressStyle.THREE_LEVEL)
        for g in svc.group_addresses(pid)
    }


def test_a_group_address_keeps_its_number_or_takes_the_lowest_free(
    tmp_path: Path,
) -> None:
    svc, pid = _project(tmp_path)
    light = next(g.id for g in svc.group_addresses(pid) if g.name == "Light")
    blind = next(g.id for g in svc.group_addresses(pid) if g.name == "Blind")
    svc.move_group_address(pid, blind, _range(svc, pid, 2, 2).id)
    assert _addresses(svc, pid)["Blind"] == "2/2/0"  # 2/2/1 is taken
    svc.move_group_address(pid, light, _range(svc, pid, 1, 2).id)
    assert _addresses(svc, pid)["Light"] == "1/2/5"
    svc.undo(pid)
    assert _addresses(svc, pid)["Light"] == "1/1/5"
    # Into the group it is in: nothing; into a main group (three-level): refused.
    steps = len(svc.history(pid))
    svc.move_group_address(pid, light, _range(svc, pid, 1, 1).id)
    assert len(svc.history(pid)) == steps
    with pytest.raises(ValueError, match="holds no group addresses"):
        svc.move_group_address(pid, light, _range(svc, pid, 2).id)
    with pytest.raises(KeyError):
        svc.move_group_address(pid, light, 999)


def test_a_middle_group_moves_with_its_group_addresses(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    blinds = _range(svc, pid, 1, 2)
    # 2/2 is taken: the lowest free middle group of main group 2 is 0.
    svc.move_group_range(pid, blinds.id, _range(svc, pid, 2).id)
    assert _addresses(svc, pid)["Blind"] == "2/0/1"
    assert history_text(svc, pid) == "MoveGroupRange"
    svc.undo(pid)
    assert _addresses(svc, pid)["Blind"] == "1/2/1"
    assert blinds.id in [r.id for r in _range(svc, pid, 1).children]
    # Its own number when free.
    lights = _range(svc, pid, 1, 1)
    svc.create_group_range(pid, 0, None, "Three")
    three = next(r for r in svc.group_ranges(pid, 0) if r.name == "Three")
    svc.move_group_range(pid, lights.id, three.id)
    moved = svc.group_addresses(pid)
    light = next(g for g in moved if g.name == "Light")
    assert (
        light.address >> 8
    ) & 7 == 1 and light.address >> 11 == three.range_start >> 11
    # Only a middle group, only below a main group, only with a free middle group.
    with pytest.raises(ValueError, match="only a middle group"):
        svc.move_group_range(pid, _range(svc, pid, 2).id, three.id)
    for middle in range(8):
        svc.create_group_address(pid, 0, _ga(4, middle, 1), f"x{middle}")
    with pytest.raises(ValueError, match="no free middle group"):
        svc.move_group_range(pid, _range(svc, pid, 2, 2).id, _range(svc, pid, 4).id)


def history_text(svc: ProjectService, pid: str) -> str:
    return svc.history(pid)[0].event_type


def test_a_full_group_takes_no_group_address(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    target = _range(svc, pid, 2, 1)
    taken = {g.address for g in svc.group_addresses(pid)}
    for sub in range(256):
        if _ga(2, 1, sub) not in taken:
            svc.create_group_address(pid, 0, _ga(2, 1, sub), f"f{sub}")
    light = next(g.id for g in svc.group_addresses(pid) if g.name == "Light")
    with pytest.raises(ValueError, match="no free group address"):
        svc.move_group_address(pid, light, target.id)
