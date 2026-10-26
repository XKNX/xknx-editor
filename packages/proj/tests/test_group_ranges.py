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
