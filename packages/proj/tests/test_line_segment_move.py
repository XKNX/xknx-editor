"""A sub line moved into another area and a sub-segment moved onto another line: the rules that
allow it, the numbers the line, the segment and its devices get, the domain address, the
couplers on them, and one undo step for all of it."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.coupler_rules import CouplerDevice
from xknxeditor.proj.core.medium import (
    MAX_LINES,
    MAX_SEGMENTS,
    MEDIUM_IOT,
    MEDIUM_IP,
    MEDIUM_PL,
    MEDIUM_RF,
    MEDIUM_TP,
    LineView,
    MediumError,
    SegmentView,
    check_line_move,
    check_segment_move,
)


def _view(area: int, address: int, *media: str) -> LineView:
    return LineView(
        id=area * 10 + address,
        area=area,
        address=address,
        name=f"L{area}{address}",
        segments=tuple(
            SegmentView(id=area * 100 + address * 10 + n, number=n, medium=m)
            for n, m in enumerate(media)
        ),
    )


def _line_refused(line: LineView, target: list[LineView], area: int) -> str:
    with pytest.raises(MediumError) as error:
        check_line_move(line, target, area)
    return error.value.reason


def _segment_refused(segment: SegmentView, is_main: bool, target: LineView) -> str:
    with pytest.raises(MediumError) as error:
        check_segment_move(segment, is_main, target)
    return error.value.reason


def test_where_a_line_may_go() -> None:
    tp_line = _view(1, 1, MEDIUM_TP)
    tp_area = [_view(2, 0, MEDIUM_TP), _view(2, 1, MEDIUM_TP)]
    check_line_move(tp_line, tp_area, 2)
    assert _line_refused(_view(1, 0, MEDIUM_TP), tp_area, 2) == "main_line_move"
    full = [_view(2, n, MEDIUM_TP) for n in range(MAX_LINES)]
    assert _line_refused(tp_line, full, 2) == "too_many_lines"
    assert _line_refused(tp_line, tp_area, 1) == "line_in_area"
    # An RF, PL or IoT main line takes only lines of its medium.
    for medium, reason in (
        (MEDIUM_PL, "pl_main_line"),
        (MEDIUM_IOT, "iot_main_line"),
        (MEDIUM_RF, "rf_main_line"),
    ):
        area = [_view(2, 0, medium)]
        assert _line_refused(tp_line, area, 2) == reason
        check_line_move(_view(1, 1, medium), area, 2)
    # An IP or IoT line needs an IP or IoT main line above it, the backbone in area 0.
    ip_line = _view(1, 1, MEDIUM_IP)
    assert _line_refused(ip_line, tp_area, 2) == "ip_needs_ip_main_line"
    assert _line_refused(ip_line, [_view(0, 0, MEDIUM_TP)], 0) == "ip_needs_ip_backbone"
    check_line_move(ip_line, [_view(2, 0, MEDIUM_IP)], 2)
    # An area without a main line takes any line.
    check_line_move(ip_line, [], 3)


def test_where_a_segment_may_go() -> None:
    def sub(medium: str) -> SegmentView:
        return SegmentView(id=999, number=1, medium=medium)

    tp, ip = _view(2, 1, MEDIUM_TP), _view(2, 2, MEDIUM_IP)
    for target, medium in ((tp, MEDIUM_TP), (tp, MEDIUM_RF), (ip, MEDIUM_RF)):
        check_segment_move(sub(medium), False, target)
    check_segment_move(sub(MEDIUM_IOT), False, ip)
    full = _view(2, 3, *([MEDIUM_TP] * MAX_SEGMENTS))
    assert _segment_refused(sub(MEDIUM_TP), False, full) == "too_many_segments"
    assert _segment_refused(sub(MEDIUM_TP), True, tp) == "main_segment_move"
    assert _segment_refused(sub(MEDIUM_PL), False, tp) == "pl_sub_segment"
    for medium, reason in (
        (MEDIUM_RF, "rf_no_sub_segments"),
        (MEDIUM_IOT, "iot_no_sub_segments"),
        (MEDIUM_PL, "pl_no_sub_segments"),
    ):
        assert _segment_refused(sub(MEDIUM_TP), False, _view(2, 4, medium)) == reason
    assert _segment_refused(sub(MEDIUM_IOT), False, tp) == "below_tp"
    assert _segment_refused(sub(MEDIUM_IP), False, ip) == "below_ip"


def _project(tmp_path: Path) -> tuple[ProjectService, str, dict[str, int]]:
    """Areas 2 and 3 with TP main lines and the TP lines 2.1 and 3.1."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    ids: dict[str, int] = {}
    for area in (2, 3):
        ids[f"{area}"] = svc.create_area(pid, 0, area, f"A{area}")
        main = svc.create_line(pid, ids[f"{area}"], 0, f"M{area}")
        svc.set_line_medium(pid, main, MEDIUM_TP)
        ids[f"{area}.1"] = svc.create_line(pid, ids[f"{area}"], 1, f"L{area}")
    return svc, pid, ids


def _address(svc: ProjectService, pid: str, device: int) -> str:
    placement = svc.device_placement(pid, device)
    line = next(
        (a.address, ln.address)
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == placement.line_id
    )
    return f"{line[0]}.{line[1]}.{placement.address}"


def _main_segment(svc: ProjectService, pid: str, line_id: int) -> int:
    return next(
        s.id
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == line_id
        for s in ln.segments
        if s.number == 0
    )


def test_a_line_moves_into_another_area(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    device = svc.add_device(
        pid, _main_segment(svc, pid, ids["2.1"]), "M-1_H-1_P-1", address=3
    )
    # Line 3.1 is taken: it becomes 3.2; its device keeps its number.
    assert svc.move_line(pid, ids["2.1"], ids["3"]) == 2
    assert _address(svc, pid, device) == "3.2.3"
    svc.undo(pid)
    assert _address(svc, pid, device) == "2.1.3"
    svc.redo(pid)
    # Back into area 2, where number 2 is free: it keeps it.
    assert svc.move_line(pid, ids["2.1"], ids["2"]) == 2
    assert _address(svc, pid, device) == "2.2.3"
    with pytest.raises(MediumError, match="already assigned"):
        svc.move_line(pid, ids["2.1"], ids["2"])
    main_2 = next(
        ln.id for a in svc.topology(pid, 0).areas if a.address == 2 for ln in a.lines
    )
    with pytest.raises(MediumError, match="main line cannot be moved"):
        svc.move_line(pid, main_2, ids["3"])
    with pytest.raises(KeyError):
        svc.move_line(pid, 999, ids["3"])


def test_a_moved_line_takes_the_domain_address_of_its_new_area(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    area = svc.create_area(pid, 0, 4, "PL")
    pl_main = svc.create_line(pid, area, 0, "M4")
    svc.set_line_medium(pid, pl_main, MEDIUM_PL)
    pl_line = svc.create_line(pid, area, 1, "L4")
    domain = svc.topology(pid, 0)
    main_domain = next(
        s.domain_address
        for a in domain.areas
        for ln in a.lines
        if ln.id == pl_main
        for s in ln.segments
    )

    def line_domain(line_id: int) -> int | None:
        return next(
            s.domain_address
            for a in svc.topology(pid, 0).areas
            for ln in a.lines
            if ln.id == line_id
            for s in ln.segments
            if s.number == 0
        )

    assert line_domain(pl_line) == main_domain
    # Into a TP area: a new one of its own; a TP line into the PL area is refused.
    svc.move_line(pid, pl_line, ids["2"])
    new = line_domain(pl_line)
    assert new is not None and new != main_domain
    with pytest.raises(MediumError):
        svc.move_line(pid, ids["3.1"], area)
    # Back below the PL main line: its address again, in the same undo step.
    svc.move_line(pid, pl_line, area)
    assert line_domain(pl_line) == main_domain
    svc.undo(pid)
    assert line_domain(pl_line) == new


def test_a_coupler_on_a_moved_line_is_checked_where_it_ends_up(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    coupler = svc.add_device(
        pid, _main_segment(svc, pid, ids["2.1"]), "M-1_H-1_P-1", address=0
    )
    asked: list[int] = []

    def coupler_of(device_id: int, _program: str | None) -> tuple[CouplerDevice, str]:
        asked.append(device_id)
        # A KNXnet/IP router needs an IP backbone; this project's backbone is TP.
        return (
            CouplerDevice(
                media=frozenset({MEDIUM_IP, MEDIUM_TP}),
                mask="MV-091A",
                is_coupler=True,
                ip_router=True,
            ),
            "Router",
        )

    with pytest.raises(MediumError):
        svc.move_line(pid, ids["2.1"], ids["3"], coupler_of=coupler_of)
    assert asked == [coupler]
    assert _address(svc, pid, coupler) == "2.1.0"


def test_a_segment_moves_onto_another_line(tmp_path: Path) -> None:
    svc, pid, ids = _project(tmp_path)
    garden = svc.add_segment(pid, ids["2.1"], name="Garden")
    first = svc.add_device(pid, garden, "M-1_H-1_P-1", address=3)
    second = svc.add_device(pid, garden, "M-1_H-1_P-1", address=4)
    svc.add_device(pid, _main_segment(svc, pid, ids["3.1"]), "M-1_H-1_P-1", address=3)
    # 3.1.3 is taken: that device gets the lowest free number, the other keeps its own.
    assert svc.move_segment(pid, garden, ids["3.1"]) == 1
    assert (_address(svc, pid, first), _address(svc, pid, second)) == (
        "3.1.1",
        "3.1.4",
    )
    svc.undo(pid)
    assert (_address(svc, pid, first), _address(svc, pid, second)) == (
        "2.1.3",
        "2.1.4",
    )
    svc.redo(pid)
    # On its line already: nothing changes.
    assert svc.move_segment(pid, garden, ids["3.1"]) == 1
    with pytest.raises(MediumError, match="main segment cannot be moved"):
        svc.move_segment(pid, _main_segment(svc, pid, ids["2.1"]), ids["3.1"])
    with pytest.raises(KeyError):
        svc.move_segment(pid, 999, ids["3.1"])


def test_two_devices_without_a_number_keep_every_number_and_rf_takes_no_segment(
    tmp_path: Path,
) -> None:
    svc, pid, ids = _project(tmp_path)
    garden = svc.add_segment(pid, ids["2.1"])
    moved = svc.add_device(pid, garden, "M-1_H-1_P-1", address=5)
    svc.add_device(pid, garden, "M-1_H-1_P-1", address=None)
    svc.add_device(pid, garden, "M-1_H-1_P-1", address=None)
    svc.add_device(pid, _main_segment(svc, pid, ids["3.1"]), "M-1_H-1_P-1", address=5)
    # Two devices without a number: none is renumbered.
    svc.move_segment(pid, garden, ids["3.1"])
    assert _address(svc, pid, moved) == "3.1.5"
    # An RF segment does not go onto an RF line.
    svc.undo(pid)
    rf_line = svc.create_line(pid, ids["3"], 4, "RF")
    svc.set_line_medium(pid, rf_line, MEDIUM_RF)
    with pytest.raises(MediumError, match="RF"):
        svc.move_segment(pid, garden, rf_line)


def _loaded(svc: ProjectService, pid: str, device: int) -> tuple[bool, bool, bool]:
    info = svc.device(pid, device)
    return (
        info.individual_address_loaded,
        info.communication_part_loaded,
        info.medium_config_loaded,
    )


def test_a_new_individual_address_is_no_longer_the_downloaded_one(
    tmp_path: Path,
) -> None:
    svc, pid, ids = _project(tmp_path)
    main = _main_segment(svc, pid, ids["2.1"])
    device = svc.add_device(pid, main, "M-1_H-1_P-1", address=3)
    coupler = svc.add_device(pid, main, "M-1_H-1_P-1", address=0)
    svc.assign_additional_addresses(pid, device, 1)

    def downloaded() -> None:
        for d in (device, coupler):
            svc.set_device_commissioning(
                pid,
                d,
                individual_address_loaded=True,
                communication_part_loaded=True,
                medium_config_loaded=True,
            )

    downloaded()
    # Another device number: the address, and the additional addresses' medium configuration.
    svc.set_individual_address(pid, device, "2.1.9", couplers={coupler})
    assert _loaded(svc, pid, device) == (False, True, False)
    svc.undo(pid)
    assert _loaded(svc, pid, device) == (True, True, True)
    # A moved line: every device of it, a coupler's group communication too.
    svc.move_line(pid, ids["2.1"], ids["3"], couplers={coupler})
    assert _loaded(svc, pid, device) == (False, True, False)
    assert _loaded(svc, pid, coupler) == (False, False, True)
    svc.undo(pid)
    assert _loaded(svc, pid, coupler) == (True, True, True)
    # A renumbered line and a moved segment.
    svc.set_topology_address(pid, "line", ids["2.1"], 5)
    assert _loaded(svc, pid, device)[0] is False
    svc.undo(pid)
    garden = svc.add_segment(pid, ids["2.1"])
    downloaded()
    # Onto another segment of its line with the same number: the address stays the downloaded one.
    svc.move_device(pid, device, garden, 3)
    assert _loaded(svc, pid, device)[0] is True
    svc.move_segment(pid, garden, ids["3.1"])
    assert _loaded(svc, pid, device)[0] is False
