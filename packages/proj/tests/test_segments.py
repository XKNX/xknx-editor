"""Segments and the media of lines and segments: which sub-segments a line allows, when a medium
can change, the domain addresses that follow from it, and what an export and an import keep."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core.medium import (
    MAX_SEGMENTS,
    MEDIUM_IOT,
    MEDIUM_IP,
    MEDIUM_PL,
    MEDIUM_RF,
    MEDIUM_TP,
    LineView,
    MediumError,
    SegmentView,
    check_medium_change,
    check_new_sub_segment,
    device_fits,
    domain_address_changes,
    domain_addresses,
    format_domain_address,
    has_domain_address,
    new_domain_address,
    parse_domain_address,
)


def _project(tmp_path: Path) -> tuple[ProjectService, str, int]:
    """A new project with the TP line 2.1 (name "L") below the TP main line 2.0."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = svc.create_area(pid, 0, 2, "A")
    main = svc.create_line(pid, area, 0, "M")
    svc.set_line_medium(pid, main, MEDIUM_TP)
    line = svc.create_line(pid, area, 1, "L")
    return svc, pid, line


def _segments(svc: ProjectService, pid: str, line_id: int) -> list[tuple[int, str]]:
    line = next(
        ln for a in svc.topology(pid, 0).areas for ln in a.lines if ln.id == line_id
    )
    return [
        (s.number, s.medium_type) for s in sorted(line.segments, key=lambda s: s.number)
    ]


def _reason(error: pytest.ExceptionInfo[MediumError]) -> str:
    return error.value.reason


def _line(
    area: int,
    address: int,
    *media: str,
    devices: dict[int, tuple[tuple[int, int | None], ...]] | None = None,
    domains: dict[int, int] | None = None,
) -> LineView:
    """The line ``area.address`` named "L{area}{address}"; its segments have ``media`` (the main
    segment's first), ids ``area*100 + address*10 + number``, the ``devices`` and ``domains``
    of their numbers."""
    return LineView(
        id=area * 10 + address,
        area=area,
        address=address,
        name=f"L{area}{address}",
        segments=tuple(
            SegmentView(
                id=area * 100 + address * 10 + number,
                number=number,
                medium=medium,
                domain_address=(domains or {}).get(number),
                devices=(devices or {}).get(number, ()),
            )
            for number, medium in enumerate(media)
        ),
    )


def _refused(
    lines: list[LineView],
    segment_id: int,
    medium: str,
    couplers: tuple[int, ...] = (),
    ip_routers: tuple[int, ...] = (),
) -> str:
    with pytest.raises(MediumError) as error:
        check_medium_change(lines, segment_id, medium, couplers, ip_routers)
    return _reason(error)


# --- the rules alone ---------------------------------------------------------------------------


def test_a_segment_with_devices_is_refused_first() -> None:
    lines = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_IP, devices={0: ((5, 1),)})]
    assert _refused(lines, 100, MEDIUM_RF, couplers=(5,)) == "segment_has_devices"


def test_a_coupler_below_keeps_the_medium() -> None:
    sub = _line(1, 1, MEDIUM_TP, MEDIUM_TP, devices={1: ((5, 1),)})
    lines = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_IP), sub]
    assert _refused(lines, 110, MEDIUM_IP, couplers=(5,)) == "coupler_below"
    check_medium_change(lines, 110, MEDIUM_IP)
    # The backbone, below it a coupler on a main line.
    lines = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_TP, devices={0: ((6, 0),)})]
    assert _refused(lines, 0, MEDIUM_TP, couplers=(6,)) == "coupler_below"
    # A main line, below it a coupler on a sub line's main segment.
    lines = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_TP),
        _line(1, 1, MEDIUM_TP, devices={0: ((7, 0),)}),
    ]
    assert _refused(lines, 100, MEDIUM_IP, couplers=(7,)) == "coupler_below"


def test_the_backbone_and_main_lines() -> None:
    lines = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_TP), _line(1, 1, MEDIUM_TP)]
    assert _refused(lines, 100, MEDIUM_RF) == "main_line_rf"
    assert _refused(lines, 0, MEDIUM_RF) == "main_line_rf"
    assert _refused(lines, 0, MEDIUM_PL) == "backbone_ip_or_tp"
    assert _refused(lines, 0, MEDIUM_IOT) == "backbone_ip_or_tp"
    assert _refused(lines, 100, MEDIUM_PL) == "main_line_pl_below"
    assert _refused(lines, 100, MEDIUM_IOT) == "main_line_iot_below"
    check_medium_change(lines, 0, MEDIUM_TP)
    tp_backbone = [_line(0, 0, MEDIUM_TP), *lines[1:]]
    assert _refused(tp_backbone, 100, MEDIUM_IOT) == "main_line_iot_backbone"
    iot_below = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_IP),
        _line(1, 1, MEDIUM_IOT),
    ]
    assert _refused(iot_below, 100, MEDIUM_TP) == "iot_below"
    # Only PL lines below a PL main line.
    only_pl = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_TP), _line(1, 1, MEDIUM_PL)]
    check_medium_change(only_pl, 100, MEDIUM_PL)


def test_sub_segments() -> None:
    lines = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_IP),
        _line(1, 1, MEDIUM_TP, MEDIUM_TP),
    ]
    assert _refused(lines, 111, MEDIUM_PL) == "sub_segment_ip_or_pl"
    assert _refused(lines, 111, MEDIUM_IP) == "sub_segment_ip_or_pl"
    with pytest.raises(MediumError) as error:
        check_medium_change(lines, 111, MEDIUM_IOT)
    assert _reason(error) == "sub_segment_iot"
    assert "'L11'" in str(error.value)
    check_medium_change(lines, 111, MEDIUM_RF)


def test_sub_lines() -> None:
    def area(main: str, *sub: str) -> list[LineView]:
        return [_line(0, 0, MEDIUM_IP), _line(1, 0, main), _line(1, 1, *sub)]

    assert _refused(area(MEDIUM_TP, MEDIUM_TP, MEDIUM_TP), 110, MEDIUM_RF) == (
        "rf_no_sub_segments"
    )
    assert _refused(area(MEDIUM_TP, MEDIUM_TP, MEDIUM_TP), 110, MEDIUM_PL) == (
        "pl_no_sub_segments"
    )
    with pytest.raises(MediumError) as error:
        check_medium_change(area(MEDIUM_RF, MEDIUM_RF), 110, MEDIUM_TP)
    assert _reason(error) == "rf_main_line"
    assert "'1.0 L10'" in str(error.value)
    assert _refused(area(MEDIUM_PL, MEDIUM_PL), 110, MEDIUM_TP) == "pl_main_line"
    assert _refused(area(MEDIUM_IOT, MEDIUM_IOT), 110, MEDIUM_TP) == "iot_main_line"
    assert _refused(area(MEDIUM_TP, MEDIUM_TP), 110, MEDIUM_IOT) == "sub_line_iot"
    assert _refused(area(MEDIUM_IP, MEDIUM_IP, MEDIUM_IOT), 110, MEDIUM_TP) == (
        "iot_below"
    )
    assert _refused(area(MEDIUM_IP, MEDIUM_TP, MEDIUM_TP), 110, MEDIUM_IOT) == (
        "sub_line_iot_below"
    )
    check_medium_change(area(MEDIUM_IP, MEDIUM_TP), 110, MEDIUM_IOT)
    check_medium_change(area(MEDIUM_TP, MEDIUM_TP), 110, MEDIUM_RF)


def test_ip_lines_and_knxnet_ip_routers() -> None:
    router = {0: ((9, 0),)}
    lines = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_IP), _line(1, 1, MEDIUM_TP)]
    assert _refused(lines, 0, MEDIUM_TP) == "ip_backbone_ip_lines"
    routers = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_TP, devices=router)]
    assert _refused(routers, 0, MEDIUM_TP, ip_routers=(9,)) == "ip_backbone_routers"
    # Not a router at device number 0: no KNXnet/IP router.
    check_medium_change(
        [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_TP, devices={0: ((9, 3),)})],
        0,
        MEDIUM_TP,
        ip_routers=(9,),
    )
    ip_sub = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_IP), _line(1, 1, MEDIUM_IP)]
    assert _refused(ip_sub, 100, MEDIUM_TP) == "ip_needs_ip_main_line"
    in_area = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_IP),
        _line(1, 1, MEDIUM_TP, devices=router),
    ]
    assert _refused(in_area, 100, MEDIUM_TP, ip_routers=(9,)) == "ip_main_line_routers"
    tp = [_line(0, 0, MEDIUM_TP), _line(1, 0, MEDIUM_TP), _line(1, 1, MEDIUM_TP)]
    assert _refused(tp, 110, MEDIUM_IP) == "ip_needs_ip_main_line"
    assert _refused(tp, 100, MEDIUM_IP) == "ip_needs_ip_backbone"
    assert _refused([*tp, _line(0, 1, MEDIUM_TP)], 10, MEDIUM_IP) == (
        "ip_needs_ip_backbone"
    )
    check_medium_change([_line(0, 0, MEDIUM_IP), *tp[1:]], 100, MEDIUM_IP)


def test_new_sub_segments() -> None:
    check_new_sub_segment(MEDIUM_TP, MEDIUM_RF)
    check_new_sub_segment(MEDIUM_IP, MEDIUM_TP)
    # Below IP also IoT (as an existing sub-segment there may be changed to IoT).
    check_new_sub_segment(MEDIUM_IP, MEDIUM_IOT)
    for main, medium, reason in (
        (MEDIUM_RF, MEDIUM_TP, "rf_no_sub_segments"),
        (MEDIUM_PL, MEDIUM_TP, "pl_no_sub_segments"),
        (MEDIUM_IOT, MEDIUM_IOT, "iot_no_sub_segments"),
        (MEDIUM_TP, MEDIUM_PL, "pl_sub_segment"),
        (MEDIUM_TP, MEDIUM_IOT, "below_tp"),
        (MEDIUM_IP, MEDIUM_PL, "pl_sub_segment"),
    ):
        with pytest.raises(MediumError) as error:
            check_new_sub_segment(main, medium)
        assert _reason(error) == reason


def test_domain_addresses_follow_the_main_line() -> None:
    lines = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_TP),
        _line(1, 1, MEDIUM_PL, domains={0: 1}),
        _line(1, 2, MEDIUM_TP),
    ]
    # A main line that becomes PL gets the lowest free address, and so do its PL lines.
    assert domain_addresses(lines, 100, MEDIUM_PL) == {100: 2, 110: 2}
    # A sub line takes its main line's address when that has the same medium.
    pl_main = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_PL, domains={0: 7})]
    assert domain_addresses([*pl_main, _line(1, 2, MEDIUM_TP)], 120, MEDIUM_PL) == {
        120: 7
    }
    # Without a domain address for the medium, none.
    assert domain_addresses(lines, 110, MEDIUM_TP) == {110: None}
    # A main line from IoT to IP gives each IoT line of its area an address of its own.
    iot = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_IOT, domains={0: 5}),
        _line(1, 1, MEDIUM_IOT, domains={0: 5}),
    ]
    changes = domain_addresses(iot, 100, MEDIUM_IP)
    assert changes[100] is None
    assert changes[110] not in (None, 5)


def test_the_helpers() -> None:
    assert new_domain_address(MEDIUM_PL, [1, 2, 4]) == 3
    assert new_domain_address(MEDIUM_PL, range(1, 256)) is None
    assert new_domain_address(MEDIUM_TP, []) is None
    rf = new_domain_address(MEDIUM_RF, [])
    assert rf is not None and 0 < rf < 2**48
    iot = new_domain_address(MEDIUM_IOT, [])
    assert iot is not None and 0 < iot < 2**32
    assert device_fits(MEDIUM_TP, ["MT-0", "MT-5"])
    assert not device_fits(MEDIUM_RF, ["MT-0"])


# --- the project service -----------------------------------------------------------------------


def test_a_tp_line_gets_tp_or_rf_sub_segments(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    svc.add_segment(pid, line)
    svc.add_segment(pid, line, medium_type=MEDIUM_RF)
    assert _segments(svc, pid, line) == [
        (0, MEDIUM_TP),
        (1, MEDIUM_TP),
        (2, MEDIUM_RF),
    ]
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, line, medium_type=MEDIUM_IOT, name="S")
    assert _reason(error) == "below_tp"
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, line, medium_type=MEDIUM_PL)
    assert _reason(error) == "pl_sub_segment"
    with pytest.raises(KeyError):
        svc.add_segment(pid, 999)
    svc.undo(pid)
    assert _segments(svc, pid, line) == [(0, MEDIUM_TP), (1, MEDIUM_TP)]


def test_an_ip_line_gets_tp_by_default_and_an_rf_line_none(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    main = next(
        ln.id
        for a in svc.topology(pid, 0).areas
        if a.address == 1
        for ln in a.lines
        if ln.address == 0
    )
    svc.add_segment(pid, main)
    assert _segments(svc, pid, main) == [(0, MEDIUM_IP), (1, MEDIUM_TP)]
    # An IoT sub-segment below IP, with its domain address.
    iot = svc.add_segment(pid, main, medium_type=MEDIUM_IOT)
    assert _segments(svc, pid, main) == [
        (0, MEDIUM_IP),
        (1, MEDIUM_TP),
        (2, MEDIUM_IOT),
    ]
    assert iot is not None
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, main, medium_type=MEDIUM_PL)
    assert _reason(error) == "pl_sub_segment"

    _svc, _pid, line = (
        svc,
        pid,
        svc.create_line(pid, svc.topology(pid, 0).areas[-1].id, 5, "R"),
    )
    svc.set_line_medium(pid, line, MEDIUM_RF)
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, line)
    assert _reason(error) == "rf_no_sub_segments"
    assert str(error.value).endswith("medium type RF.")


def test_a_line_has_at_most_128_segments(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    for _ in range(MAX_SEGMENTS - 1):
        svc.add_segment(pid, line)
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, line)
    assert _reason(error) == "too_many_segments"


def test_the_main_segment_stays(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line, name="Sub")
    main = next(
        s.id
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == line
        for s in ln.segments
        if s.number == 0
    )
    with pytest.raises(MediumError) as error:
        svc.remove_segment(pid, main)
    assert _reason(error) == "main_segment"
    svc.remove_segment(pid, sub)
    assert _segments(svc, pid, line) == [(0, MEDIUM_TP)]
    svc.undo(pid)
    assert _segments(svc, pid, line) == [(0, MEDIUM_TP), (1, MEDIUM_TP)]


def test_a_segment_with_devices_keeps_its_medium(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line)
    svc.add_device(pid, sub, "M-1_H-1_P-1", address=3)
    with pytest.raises(MediumError) as error:
        svc.set_segment_medium(pid, sub, MEDIUM_RF)
    assert _reason(error) == "segment_has_devices"
    with pytest.raises(MediumError) as error:
        svc.set_segment_medium(pid, sub, "MT-9")
    assert _reason(error) == "unknown_medium"
    with pytest.raises(KeyError):
        svc.set_segment_medium(pid, 999, MEDIUM_TP)
    with pytest.raises(KeyError):
        svc.set_line_medium(pid, 999, MEDIUM_TP)


def test_couplers_and_routers_come_from_the_caller(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line)
    coupler = svc.add_device(
        pid, sub, "M-1_H-1_P-1", address=0, hardware2program_ref_id="M-1_H-1_HP-1"
    )
    svc.add_device(pid, sub, "M-1_H-2_P-2", address=4)
    with pytest.raises(MediumError) as error:
        svc.set_line_medium(pid, line, MEDIUM_RF, couplers={coupler})
    assert _reason(error) == "coupler_below"
    # Devices without a hardware program are left out.
    assert svc.device_programs(pid) == {coupler: "M-1_H-1_HP-1"}


def test_rf_and_pl_segments_get_a_domain_address_of_their_own(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    first = svc.add_segment(pid, line)
    second = svc.add_segment(pid, line)
    svc.set_segment_medium(pid, first, MEDIUM_RF)
    svc.set_segment_medium(pid, second, MEDIUM_RF)

    def domains() -> dict[int, int | None]:
        return {
            s.id: s.domain_address
            for a in svc.topology(pid, 0).areas
            for ln in a.lines
            for s in ln.segments
        }

    assert domains()[first] is not None
    assert domains()[first] != domains()[second]
    svc.undo(pid)
    assert domains()[second] is None
    svc.set_segment_medium(pid, first, MEDIUM_TP)
    assert domains()[first] is None

    other = svc.create_line(pid, svc.topology(pid, 0).areas[-1].id, 2, "P")
    svc.set_line_medium(pid, other, MEDIUM_PL)
    assert (
        next(
            s.domain_address
            for a in svc.topology(pid, 0).areas
            for ln in a.lines
            if ln.id == other
            for s in ln.segments
        )
        == 1
    )


def test_an_ip_backbone_is_no_longer_forced_secure(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    areas = {a.address: a for a in svc.topology(pid, 0).areas}
    backbone = areas[0].lines[0].id
    main = next(ln.id for ln in areas[1].lines if ln.address == 0)
    svc.set_line_medium(pid, main, MEDIUM_TP)
    svc.set_line_medium(pid, backbone, MEDIUM_TP)
    svc.set_project_password(pid, "geheim")
    svc.set_backbone_security(pid, "On")
    svc.set_line_medium(pid, backbone, MEDIUM_IP)
    assert svc.backbone_security(pid) is None
    svc.undo(pid)
    assert svc.backbone_security(pid) == "On"
    assert _segments(svc, pid, backbone) == [(0, MEDIUM_TP)]


def test_the_ip_routing_of_the_backbone(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    svc.set_ip_routing(pid, latency=400, multicast_address="239.1.2.3")
    attributes = svc.installation_attributes(pid)
    assert attributes["IPRoutingLatencyTolerance"] == "400"
    assert attributes["IPRoutingMulticastAddress"] == "239.1.2.3"
    svc.set_ip_routing(pid, multicast_address="224.0.23.12")
    assert "IPRoutingMulticastAddress" not in svc.installation_attributes(pid)
    svc.undo(pid)
    assert svc.installation_attributes(pid)["IPRoutingMulticastAddress"] == "239.1.2.3"
    for bad in ("10.0.0.1", "nope"):
        with pytest.raises(ValueError):
            svc.set_ip_routing(pid, multicast_address=bad)
    with pytest.raises(ValueError):
        svc.set_ip_routing(pid, latency=0)


def test_domain_addresses_entered() -> None:
    assert parse_domain_address(MEDIUM_RF, " 0001:0000ABCD ") == (1 << 32) | 0xABCD
    assert parse_domain_address(MEDIUM_PL, "7") == 7
    for medium, text in ((MEDIUM_RF, "12"), (MEDIUM_RF, "x:1"), (MEDIUM_PL, "a")):
        with pytest.raises(MediumError) as error:
            parse_domain_address(medium, text)
        assert _reason(error) == "domain_invalid"
    assert format_domain_address(MEDIUM_IOT, 9) == "9"
    assert has_domain_address(MEDIUM_PL) and not has_domain_address(MEDIUM_IP)

    rf = 1 << 32
    lines = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_PL, domains={0: 5}),
        _line(1, 1, MEDIUM_PL, domains={0: 5}),
        _line(1, 2, MEDIUM_TP, MEDIUM_RF, domains={1: rf + 1}),
        _line(1, 3, MEDIUM_RF, domains={0: rf + 2}),
    ]
    # A main line's address goes to the lines of its area with its medium.
    assert domain_address_changes(lines, 100, 6) == {100: 6, 110: 6}
    assert domain_address_changes(lines, 110, 9) == {110: 9}
    for segment_id, address, reason in (
        (100, 0, "pl_domain_range"),
        (100, 256, "pl_domain_range"),
        (121, 5, "rf_domain_minimum"),
        (121, 1 << 48, "domain_maximum"),
        (121, rf + 2, "rf_domain_unique"),
        (0, 1, "domain_invalid"),
    ):
        with pytest.raises(MediumError) as error:
            domain_address_changes(lines, segment_id, address)
        assert _reason(error) == reason
    with pytest.raises(MediumError) as error:
        domain_address_changes(lines, 121, 1 << 48)
    assert "'RF'" in str(error.value) and "FFFF:FFFFFFFF" in str(error.value)
    # An RF or IoT sub line keeps the address of a main line of its medium.
    iot = [
        _line(0, 0, MEDIUM_IP),
        _line(1, 0, MEDIUM_IOT, domains={0: 3}),
        _line(1, 1, MEDIUM_IOT, domains={0: 3}),
    ]
    with pytest.raises(MediumError) as error:
        domain_address_changes(iot, 110, 4)
    assert _reason(error) == "iot_sub_line_domain"
    assert domain_address_changes(iot, 110, 3) == {110: 3}
    rf_main = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_RF, domains={0: rf})]
    with pytest.raises(MediumError) as error:
        domain_address_changes([*rf_main, _line(1, 1, MEDIUM_RF)], 110, rf + 5)
    assert _reason(error) == "rf_sub_line_domain"
    # A new RF address is at least 0001:00000000.
    assert all((new_domain_address(MEDIUM_RF, []) or 0) >= rf for _ in range(50))


def test_a_domain_address_is_set_or_generated(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    svc.set_line_medium(pid, line, MEDIUM_PL)
    segment = next(
        s.id
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == line
        for s in ln.segments
    )

    def domain() -> int | None:
        return next(
            s.domain_address
            for a in svc.topology(pid, 0).areas
            for ln in a.lines
            for s in ln.segments
            if s.id == segment
        )

    assert domain() == 1
    assert svc.set_domain_address(pid, segment, 42) == 42
    assert domain() == 42
    svc.undo(pid)
    assert domain() == 1
    other = svc.create_line(pid, svc.topology(pid, 0).areas[-1].id, 2, "Q")
    svc.set_line_medium(pid, other, MEDIUM_PL)
    # A new one is the lowest free one: 1 is its own, 2 the other line's.
    assert svc.set_domain_address(pid, segment) == 3
    with pytest.raises(MediumError):
        svc.set_domain_address(pid, segment, 300)
    with pytest.raises(KeyError):
        svc.set_domain_address(pid, 999, 1)


def test_a_new_line_takes_the_medium_above_it(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")

    def main_segment(line_id: int) -> tuple[str, int | None]:
        segment = next(
            s
            for a in svc.topology(pid, 0).areas
            for ln in a.lines
            if ln.id == line_id
            for s in ln.segments
            if s.number == 0
        )
        return segment.medium_type, segment.domain_address

    # A main line takes the backbone's medium (IP), a sub line its main line's.
    area = svc.create_area(pid, 0, 3, "A")
    main = svc.create_line(pid, area, 0, "M")
    assert main_segment(main) == (MEDIUM_IP, None)
    svc.set_line_medium(pid, main, MEDIUM_PL)
    _medium, domain = main_segment(main)
    assert domain is not None
    # Its domain address too.
    assert main_segment(svc.create_line(pid, area, 1, "L")) == (MEDIUM_PL, domain)
    # Without a main line, TP; a backbone always starts with TP.
    other = svc.create_area(pid, 0, 4, "B")
    assert main_segment(svc.create_line(pid, other, 1, "S")) == (MEDIUM_TP, None)
    backbone = svc.topology(pid, 0).areas[0]
    svc.remove_line(pid, backbone.lines[0].id)
    assert main_segment(svc.create_line(pid, backbone.id, 0, "")) == (MEDIUM_TP, None)
    svc.undo(pid)
    svc.undo(pid)
    assert [ln.address for ln in svc.topology(pid, 0).areas[0].lines] == [0]


def test_the_bus_connection_of_a_segment(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    segment = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    parameter = 'Type=IpTunneling;HostAddress=10.0.0.9;ProtocolType=Udp;Name="IF"'
    svc.set_segment_bus_access(pid, segment, parameter)

    def bus_access() -> dict[str, str] | None:
        return svc.topology(pid, 0).areas[0].lines[0].segments[0].bus_access

    assert bus_access() == {"Name": "", "Parameter": parameter}
    svc.set_segment_bus_access(pid, segment, None)
    assert bus_access() is None
    svc.undo(pid)
    assert bus_access() == {"Name": "", "Parameter": parameter}
    with pytest.raises(KeyError):
        svc.set_segment_bus_access(pid, 999, None)


def test_an_address_in_the_same_line_keeps_the_segment(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line)
    device = svc.add_device(pid, sub, "M-1_H-1_P-1", address=3)
    svc.set_individual_address(pid, device, "2.1.9")
    placement = svc.device_placement(pid, device)
    assert (placement.segment_id, placement.address) == (sub, 9)
    assert svc.device_segments(pid) == {device: sub}
    other = svc.create_line(pid, svc.topology(pid, 0).areas[-1].id, 2, "M")
    svc.set_individual_address(pid, device, "2.2.1")
    placement = svc.device_placement(pid, device)
    assert placement.line_id == other
    assert placement.segment_id != sub
    with pytest.raises(KeyError):
        svc.device_placement(pid, 999)


def test_a_segment_has_a_name_description_comment_and_status(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line, name="Sub")
    svc.rename_segment(pid, sub, "Garden")
    svc.set_topology_description(pid, "segment", sub, "RF sensors")
    svc.set_comment(pid, "segment", sub, "Note")
    svc.set_completion_status(pid, "segment", sub, "Tested")
    segment = next(
        s
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        for s in ln.segments
        if s.id == sub
    )
    assert (segment.name, segment.description) == ("Garden", "RF sensors")
    assert svc.comment(pid, "segment", sub) == "Note"
    assert svc.completion_status(pid, "segment", sub) == "Tested"


def _zero(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(
            zf.read(next(n for n in zf.namelist() if n.endswith("/0.xml")))
        )


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def test_export_and_import_keep_the_segments(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line, name="Garden")
    svc.set_segment_medium(pid, sub, MEDIUM_RF)
    svc.set_topology_description(pid, "segment", sub, "RF sensors")
    svc.set_comment(pid, "segment", sub, "Note")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23")
    segments = [e for e in _zero(out).iter() if _local(e) == "Segment"]
    exported = next(e for e in segments if e.get("Name") == "Garden")
    assert exported.get("MediumTypeRefId") == MEDIUM_RF
    assert exported.get("Description") == "RF sensors"
    assert exported.get("Comment") == "Note"
    assert exported.get("DomainAddress")

    imported = tmp_path / "imported.xknx"
    import_knxproj(out, imported)
    svc2 = ProjectService()
    pid2 = svc2.open(imported)
    segment = next(
        s
        for a in svc2.topology(pid2, 0).areas
        for ln in a.lines
        for s in ln.segments
        if s.name == "Garden"
    )
    assert segment.medium_type == MEDIUM_RF
    assert segment.description == "RF sensors"
    assert segment.comment == "Note"
    assert segment.domain_address == int(exported.get("DomainAddress") or 0)
    svc2.close(pid2)


def test_export_and_import_keep_the_backbone(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    backbone = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    parameter = 'Type=IpTunneling;HostAddress=192.168.16.104;ProtocolType=Udp;Name="IP Tunneling UDP"'
    svc.set_segment_bus_access(pid, backbone, parameter)
    svc.set_ip_routing(pid, latency=8000, multicast_address="239.0.0.7")
    svc.set_backbone_security(pid, "Off")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23")
    root = _zero(out)
    installation = next(e for e in root.iter() if _local(e) == "Installation")
    assert installation.get("IPRoutingLatencyTolerance") == "8000"
    assert installation.get("IPRoutingMulticastAddress") == "239.0.0.7"
    assert installation.get("IPRoutingBackboneSecurity") == "Off"
    access = next(e for e in root.iter() if _local(e) == "BusAccess")
    assert access.attrib == {"Name": "", "Parameter": parameter}

    imported = tmp_path / "imported.xknx"
    import_knxproj(out, imported)
    svc2 = ProjectService()
    pid2 = svc2.open(imported)
    attributes = svc2.installation_attributes(pid2)
    assert attributes["IPRoutingLatencyTolerance"] == "8000"
    assert attributes["IPRoutingMulticastAddress"] == "239.0.0.7"
    assert svc2.backbone_security(pid2) == "Off"
    segment = svc2.topology(pid2, 0).areas[0].lines[0].segments[0]
    assert segment.bus_access == {"Name": "", "Parameter": parameter}
    svc2.close(pid2)
