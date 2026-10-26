"""Group-address folder edits: create/rename/remove of GroupRange folders (main/middle groups),
each an event with a working undo/redo round-trip. Folders are auto-numbered (next free number per
level); deleting a folder removes the group addresses in it (restored on undo)."""

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addressing import GroupAddressStyle, format_group_range


def _new(
    tmp_path: Path,
    style: GroupAddressStyle = GroupAddressStyle.THREE_LEVEL,
    name: str = "p.xknx",
) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / name, "P-0001", group_address_style=style)
    return svc, pid


def _roots(svc: ProjectService, pid: str):
    return svc.group_ranges(pid, 0)


def _find(nodes, range_id: int):
    for n in nodes:
        if n.id == range_id:
            return n
        hit = _find(n.children, range_id)
        if hit is not None:
            return hit
    return None


def test_create_main_group_auto_numbered_with_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)

    first = svc.create_group_range(pid, 0, None, "Beleuchtung")
    second = svc.create_group_range(pid, 0, None, "Jalousie")
    assert first is not None and second is not None

    roots = _roots(svc, pid)
    by_id = {r.id: r for r in roots}
    # Auto-numbered from the lowest free main number: 0 << 11 -> stored start max(1, 0) = 1;
    # 1 << 11 = 2048.
    assert by_id[first].range_start == 1
    assert by_id[second].range_start == 2048
    assert by_id[first].name == "Beleuchtung"

    svc.undo(pid)  # remove "Jalousie"
    assert _find(_roots(svc, pid), second) is None
    svc.redo(pid)  # back
    assert _find(_roots(svc, pid), second) is not None


def test_create_middle_group_and_reuse_by_new_ga(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    main = svc.create_group_range(pid, 0, None, "Beleuchtung")  # main 0
    assert main is not None
    middle = svc.create_group_range(pid, 0, main, "EG")  # middle 0/0
    assert middle is not None
    assert [c.id for c in _find(_roots(svc, pid), main).children] == [middle]

    # Adding a GA at 0/0/1 must reuse the existing folders, not duplicate them, and keep our names.
    svc.create_group_address(pid, 0, 1, "Licht Flur")  # 0/0/1
    main_node = _find(_roots(svc, pid), main)
    assert main_node.name == "Beleuchtung"
    assert len(main_node.children) == 1
    assert main_node.children[0].name == "EG"
    assert [ga.name for ga in main_node.children[0].group_addresses] == ["Licht Flur"]


def test_rename_folder_with_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    main = svc.create_group_range(pid, 0, None, "Alt")
    assert main is not None
    svc.rename_group_range(pid, main, "Neu")
    assert _find(_roots(svc, pid), main).name == "Neu"
    svc.undo(pid)
    assert _find(_roots(svc, pid), main).name == "Alt"


def test_remove_folder_takes_gas_and_restores_on_undo(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    main = svc.create_group_range(pid, 0, None, "Beleuchtung")  # main 0
    assert main is not None
    ga = svc.create_group_address(pid, 0, 1, "Licht")  # 0/0/1, nested under main 0

    svc.remove_group_range(pid, main)
    assert _find(_roots(svc, pid), main) is None
    assert all(g.address != 1 for g in svc.group_addresses(pid))

    svc.undo(pid)  # folder subtree (incl. the GA) comes back with original ids
    node = _find(_roots(svc, pid), main)
    assert node is not None and node.name == "Beleuchtung"
    assert ga in {g.id for g in svc.group_addresses(pid)}


def test_format_group_range_levels() -> None:
    S = GroupAddressStyle
    # ThreeLevel: level 0 is the main group, level 1 the main/middle group.
    assert format_group_range(1 << 11, S.THREE_LEVEL, 0) == "1"
    assert format_group_range((1 << 11) | (2 << 8), S.THREE_LEVEL, 1) == "1/2"
    # TwoLevel has only main groups; Free has no numbered folders.
    assert format_group_range(3 << 11, S.TWO_LEVEL, 0) == "3"
    assert format_group_range(5, S.FREE, 0) == ""


def test_folder_address_numbers(tmp_path: Path) -> None:
    svc, pid = _new(tmp_path)
    m0 = svc.create_group_range(pid, 0, None, "Sicherheit")  # main 0
    m1 = svc.create_group_range(pid, 0, None, "Licht")  # main 1
    mid = svc.create_group_range(pid, 0, m0, "Alarm")  # middle 0/0
    assert m0 is not None and m1 is not None and mid is not None

    roots = _roots(svc, pid)
    assert _find(roots, m0).address == "0"
    assert _find(roots, m1).address == "1"
    assert _find(roots, mid).address == "0/0"

    # TwoLevel: a main group carries its number, too.
    svc2, pid2 = _new(tmp_path, GroupAddressStyle.TWO_LEVEL, name="two.xknx")
    main2 = svc2.create_group_range(pid2, 0, None, "Main")
    assert main2 is not None
    assert _find(_roots(svc2, pid2), main2).address == "0"


def test_folders_gated_by_style(tmp_path: Path) -> None:
    # Free style has no folders at all.
    svc, pid = _new(tmp_path, GroupAddressStyle.FREE)
    assert svc.create_group_range(pid, 0, None, "x") is None

    # TwoLevel has main groups but no middle groups.
    svc2, pid2 = _new(tmp_path, GroupAddressStyle.TWO_LEVEL, name="two.xknx")
    main = svc2.create_group_range(pid2, 0, None, "Main")
    assert main is not None
    assert svc2.create_group_range(pid2, 0, main, "Middle") is None


def test_free_style_ranges_have_any_span_without_overlap(tmp_path: Path) -> None:
    import pytest

    svc, pid = _new(tmp_path, GroupAddressStyle.FREE)
    top = svc.create_free_group_range(pid, 0, None, "Lights", 1, 1024)
    inner = svc.create_free_group_range(pid, 0, top, "Hall", 1, 256)
    assert _find(_roots(svc, pid), inner) is not None
    with pytest.raises(ValueError, match="overlaps"):
        svc.create_free_group_range(pid, 0, None, "Blinds", 1000, 2000)
    with pytest.raises(ValueError, match="not within"):
        svc.create_free_group_range(pid, 0, top, "Out", 1000, 2000)
    assert svc.undo(pid)
    assert _find(_roots(svc, pid), inner) is None
    # Only the Free style.
    svc2, pid2 = _new(tmp_path, name="three.xknx")
    with pytest.raises(ValueError, match="Free style"):
        svc2.create_free_group_range(pid2, 0, None, "x", 1, 10)


def test_a_range_is_placed_where_it_fits() -> None:
    from xknxeditor.proj.core.paste import Allocation, place_range

    taken = [(1, 100), (201, 300)]
    assert place_range(1, 1000, taken, 100) == 101
    assert place_range(1, 1000, taken, 101) == 301
    assert place_range(1, 1000, taken, 50, Allocation.APPEND) == 301
    assert place_range(1, 1000, taken, 10, Allocation.START_WITH, 150) == 150
    # The first place from there on; with none, the lowest one.
    assert place_range(1, 1000, taken, 10, Allocation.START_WITH, 295) == 301
    assert place_range(1, 310, taken, 20, Allocation.START_WITH, 295) == 101
    assert place_range(1, 300, taken, 101) is None
    assert place_range(1, 300, [], 0) is None


def _ga_place(svc: ProjectService, pid: str, ga_id: int) -> tuple[int, int] | None:
    """The address of the group address ``ga_id`` and the range it is in."""
    stack = list(_roots(svc, pid))
    while stack:
        node = stack.pop()
        for ga in node.group_addresses:
            if ga.id == ga_id:
                return ga.address, node.id
        stack.extend(node.children)
    return None


def test_free_style_ranges_move_into_ranges_and_to_the_top(tmp_path: Path) -> None:
    import pytest

    svc, pid = _new(tmp_path, GroupAddressStyle.FREE)
    lights = svc.create_free_group_range(pid, 0, None, "Lights", 1, 1024)
    blinds = svc.create_free_group_range(pid, 0, None, "Blinds", 1025, 2048)
    hall = svc.create_free_group_range(pid, 0, lights, "Hall", 1, 256)
    ga = svc.create_group_address_in(pid, hall, 10, "Hall light")
    # Into a range that spans its addresses it keeps them...
    big = svc.create_free_group_range(pid, 0, None, "All", 3000, 3999)
    svc.move_group_range(pid, hall, big)
    node = _find(_roots(svc, pid), hall)
    assert (node.range_start, node.range_end) == (3000, 3255)
    assert _ga_place(svc, pid, ga) == (3009, hall)
    assert svc.undo(pid)
    node = _find(_roots(svc, pid), hall)
    assert (node.range_start, _ga_place(svc, pid, ga)) == (1, (10, hall))
    # ...where they overlap a range there, it goes to the next place it fits: at the top
    # "Blinds" spans them, so it goes after it.
    inner = svc.create_free_group_range(pid, 0, blinds, "Kitchen", 1025, 1100)
    svc.move_group_range(pid, inner, None)
    assert inner in [n.id for n in _roots(svc, pid)]
    assert _find(_roots(svc, pid), inner).range_start == 2049
    # Kept at the top (not lost from its old parent), and back below it on undo.
    svc._state(pid).session.expire_all()  # pyright: ignore[reportPrivateUsage]
    assert inner in [n.id for n in _roots(svc, pid)]
    assert svc.undo(pid)
    assert [c.id for c in _find(_roots(svc, pid), blinds).children] == [inner]
    assert svc.redo(pid)
    assert _find(_roots(svc, pid), inner).range_start == 2049
    # Not into itself or a range in it; nothing when it is where it goes.
    with pytest.raises(ValueError, match="into itself"):
        svc.move_group_range(pid, lights, hall)
    svc.move_group_range(pid, hall, lights)
    # A range too large for the place does not fit.
    with pytest.raises(ValueError, match="does not fit"):
        svc.move_group_range(pid, lights, inner)


def test_a_moved_range_needs_its_new_addresses_free(tmp_path: Path) -> None:
    import pytest

    svc, pid = _new(tmp_path, GroupAddressStyle.FREE)
    top = svc.create_free_group_range(pid, 0, None, "Top", 1, 1000)
    child = svc.create_free_group_range(pid, 0, top, "Child", 1, 100)
    svc.create_group_address_in(pid, child, 5, "a")
    other = svc.create_free_group_range(pid, 0, None, "Other", 2000, 2100)
    # The parent itself holds the address the child's group address would move to.
    svc.create_group_address_in(pid, other, 2004, "b")
    with pytest.raises(ValueError, match="is taken"):
        svc.move_group_range(pid, child, other)


def test_free_style_group_addresses_move_into_any_range(tmp_path: Path) -> None:
    from xknxeditor.proj.core.events import SetGroupAddressAddress

    svc, pid = _new(tmp_path, GroupAddressStyle.FREE)
    top = svc.create_free_group_range(pid, 0, None, "Top", 1, 1000)
    child = svc.create_free_group_range(pid, 0, top, "Child", 1, 100)
    far = svc.create_free_group_range(pid, 0, None, "Far", 2000, 2100)
    ga = svc.create_group_address_in(pid, top, 50, "a")
    # Into a range that spans it, it keeps its address.
    svc.move_group_address(pid, ga, child)
    assert _ga_place(svc, pid, ga) == (50, child)
    # Elsewhere it gets the range's lowest free one; undo puts it back.
    svc.create_group_address_in(pid, far, 2000, "b")
    svc.move_group_address(pid, ga, far)
    assert _ga_place(svc, pid, ga) == (2001, far)
    assert svc.undo(pid)
    assert _ga_place(svc, pid, ga) == (50, child)
    assert svc.redo(pid)
    assert _ga_place(svc, pid, ga) == (2001, far)
    svc.move_group_address(pid, ga, far)  # where it is: nothing
    # Stored events without the range keep the group address where it is.
    event = SetGroupAddressAddress.from_dict(
        {"group_address_id": 1, "installation_id": 1, "address": 2}
    )
    assert event.group_range_id is None
    assert event.to_dict()["group_range_id"] is None
