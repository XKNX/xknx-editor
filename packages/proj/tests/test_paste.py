"""Paste Special in the project core: the group a typed text names, how new group addresses and
groups are numbered, and the undoable steps copies are made of (group addresses in a given
group, groups with a given number, fields taken over)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import (
    AddressPool,
    Allocation,
    group_base,
    parse_target_range,
    place_in_group,
)
from xknxeditor.proj.models import GroupAddress, GroupRange

THREE = GroupAddressStyle.THREE_LEVEL
TWO = GroupAddressStyle.TWO_LEVEL


@pytest.mark.parametrize(
    ("text", "style", "window"),
    [
        ("1/2", THREE, ((1 << 11) | (2 << 8), (1 << 11) | (2 << 8) | 0xFF)),
        (" 0 / 0 ", THREE, (0, 0xFF)),
        ("31/7", THREE, (0xFF00, 0xFFFF)),
        ("3", TWO, (3 << 11, (3 << 11) | 0x7FF)),
        ("1", THREE, None),
        ("1/8", THREE, None),
        ("32/0", THREE, None),
        ("1/2", TWO, None),
        ("x", TWO, None),
        ("1", GroupAddressStyle.FREE, None),
    ],
)
def test_the_group_a_text_names(
    text: str, style: GroupAddressStyle, window: tuple[int, int] | None
) -> None:
    assert parse_target_range(text, style) == window


def test_a_group_starts_at_its_number_also_when_stored_from_one() -> None:
    assert group_base(1, THREE) == 0
    assert group_base((1 << 11) | (2 << 8), THREE) == (1 << 11) | (2 << 8)
    assert group_base(1, TWO) == 0
    assert group_base(100, GroupAddressStyle.FREE) == 100
    assert place_in_group((1 << 11) | (2 << 8) | 7, (1 << 11) | (2 << 8), THREE) == 7
    assert place_in_group(105, 100, GroupAddressStyle.FREE) == 5


def _take(pool: AddressPool, times: int, place: int = 0) -> list[int | None]:
    return [pool.take(place) for _ in range(times)]


def test_fill_up_takes_the_lowest_free_and_goes_on_upwards() -> None:
    pool = AddressPool(0x100, 0x1FF, {0x100, 0x102})
    assert _take(pool, 3) == [0x101, 0x103, 0x104]


def test_address_zero_is_never_given() -> None:
    assert AddressPool(0, 0xFF, set()).take() == 1
    assert AddressPool(0, 0xFF, set(), Allocation.START_WITH, start_with=0).take() == 1
    # A group number 0 is (a main or middle group).
    assert AddressPool(0, 31, set(), skip_zero=False).take() == 0


def test_append_goes_after_the_highest_used() -> None:
    pool = AddressPool(0x100, 0x1FF, {0x101, 0x105}, Allocation.APPEND)
    assert _take(pool, 2) == [0x106, 0x107]
    # Nothing used: the start of the group.
    assert AddressPool(0x100, 0x1FF, set(), Allocation.APPEND).take() == 0x100
    # The highest one used: back to the lowest free.
    assert AddressPool(0x100, 0x1FF, {0x1FF}, Allocation.APPEND).take() == 0x100


def test_start_with_counts_from_the_start_of_the_group() -> None:
    pool = AddressPool(
        0x100, 0x1FF, {0x10A}, Allocation.START_WITH, start_with=10, base=0x100
    )
    assert _take(pool, 2) == [0x10B, 0x10C]
    # Past every free one: the lowest free.
    full_above = AddressPool(
        0x100, 0x1FF, set(range(0x150, 0x200)), Allocation.START_WITH, start_with=0x60
    )
    assert full_above.take() == 0x100


def test_offset_keeps_the_place_in_the_group() -> None:
    pool = AddressPool(0x100, 0x1FF, {0x107}, Allocation.OFFSET, offset=2, base=0x100)
    assert pool.take(3) == 0x105
    # Taken: the lowest free instead.
    assert pool.take(5) == 0x100
    # Outside the group: the lowest free too.
    assert pool.take(0xFE) == 0x101


def test_a_full_group_gives_nothing() -> None:
    pool = AddressPool(0x100, 0x102, {0x100})
    assert pool.room() == 2
    assert _take(pool, 3) == [0x101, 0x102, None]
    assert pool.room() == 0


def test_after_the_end_it_goes_on_from_the_start() -> None:
    pool = AddressPool(0x100, 0x103, {0x101}, Allocation.START_WITH, start_with=3)
    assert _take(pool, 3) == [0x103, 0x100, 0x102]


def _project(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    return svc, svc.create(tmp_path / "p.xknx", "P-0C01")


def test_a_group_address_goes_into_the_given_group(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    svc.create_group_address(pid, 0, (1 << 11) | (2 << 8) | 1, "First")
    main = svc.root_group_ranges(pid, 0)[0]
    (group,) = main.children
    ga_id = svc.create_group_address_in(pid, group.id, group.range_start + 9, "Copy")
    ga = svc.row(pid, GroupAddress, ga_id)
    assert ga is not None
    assert (ga.group_range_id, ga.name) == (group.id, "Copy")
    assert svc.used_group_addresses(pid, 0) == {
        group.range_start + 1,
        group.range_start + 9,
    }
    with pytest.raises(ValueError, match="outside"):
        svc.create_group_address_in(pid, group.id, 5, "Elsewhere")
    with pytest.raises(KeyError):
        svc.create_group_address_in(pid, 999, 5, "Nowhere")
    assert svc.undo(pid)
    assert svc.row(pid, GroupAddress, ga_id) is None


def test_a_group_with_a_given_number(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    main = svc.create_group_range(pid, 0, None, "Main", number=5)
    assert main is not None
    group = svc.row(pid, GroupRange, main)
    assert group is not None
    assert group.range_start >> 11 == 5
    # Taken, or out of range: none.
    assert svc.create_group_range(pid, 0, None, "Again", number=5) is None
    assert svc.create_group_range(pid, 0, None, "Too high", number=32) is None
    middle = svc.create_group_range(pid, 0, main, "Middle", number=7)
    assert middle is not None
    middle_row = svc.row(pid, GroupRange, middle)
    assert middle_row is not None
    assert (middle_row.range_start >> 8) & 0x7 == 7
    assert [r.id for r in svc.root_group_ranges(pid, 0)] == [main]


def test_set_fields_is_undone_and_redone(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-0C01")
    ga_id = svc.create_group_address(pid, 0, 0x0901, "Light")
    svc.set_fields(
        pid,
        [["group_address", ga_id, {"comment": "{\\rtf1 x}", "central": True}]],
    )
    ga = svc.row(pid, GroupAddress, ga_id)
    assert ga is not None
    assert (ga.comment, ga.central) == ("{\\rtf1 x}", True)
    assert svc.undo(pid)
    ga = svc.row(pid, GroupAddress, ga_id)
    assert ga is not None
    assert (ga.comment, ga.central) == ("", False)
    # Kept across a reopen: redo sets the values again.
    svc.close(pid)
    pid = svc.open(path)
    assert svc.redo(pid)
    ga = svc.row(pid, GroupAddress, ga_id)
    assert ga is not None
    assert (ga.comment, ga.central) == ("{\\rtf1 x}", True)
    # Nothing to set: no step.
    entries = len(svc.history(pid))
    svc.set_fields(pid, [])
    assert len(svc.history(pid)) == entries
