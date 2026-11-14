"""A coupler's filter table must include the project's configured pass-through addresses.

``Unfiltered`` (on a group address or a whole range) and a line's ``AdditionalGroupAddresses`` mean
"route across the coupler regardless of topology". They are imported from the .knxproj and exported
back, so dropping them when programming silently blocks addresses the project says must always pass.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from xknxeditor.proj.core.service import ProjectService
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import GroupAddress, GroupRange, Line


def _project(tmp_path: Path) -> tuple[ProjectService, str, Path]:
    src = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-COUPLER")
    return svc, pid, src


def test_unfiltered_and_additional_are_collected(tmp_path: Path) -> None:
    svc, pid, src = _project(tmp_path)
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 2, "Line 2")
    plain = svc.create_group_address(pid, 0, 0x0801, "plain")
    flagged = svc.create_group_address(pid, 0, 0x0802, "flagged")
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        s.get(GroupAddress, flagged).unfiltered = True  # type: ignore[union-attr]
        s.get(Line, line_id).additional_group_addresses = "2049,2050"  # type: ignore[union-attr]
        s.commit()

    svc2 = ProjectService()
    pid2 = svc2.open(src)
    # coupler 2.2.0 -> area 2, line 2
    unfiltered, additional = svc2.coupler_pass_through(pid2, (2 << 12) | (2 << 8))
    svc2.close(pid2)

    assert 0x0802 in unfiltered
    assert 0x0801 not in unfiltered
    assert additional == [2049, 2050]
    assert plain  # the unflagged address exists but is not passed through


def test_unfiltered_range_is_inherited_by_its_addresses(tmp_path: Path) -> None:
    svc, pid, src = _project(tmp_path)
    ga_id = svc.create_group_address(pid, 0, 0x0901, "in range")
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        ga = s.get(GroupAddress, ga_id)
        assert ga is not None
        rng = s.get(GroupRange, ga.group_range_id)
        assert rng is not None
        rng.unfiltered = True  # mark the whole range, not the address
        s.commit()

    svc2 = ProjectService()
    pid2 = svc2.open(src)
    unfiltered, _additional = svc2.coupler_pass_through(pid2, (1 << 12) | (1 << 8))
    svc2.close(pid2)

    assert 0x0901 in unfiltered


def test_additional_addresses_are_scoped_to_the_couplers_own_line(
    tmp_path: Path,
) -> None:
    svc, pid, src = _project(tmp_path)
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    line_a = svc.create_line(pid, area_id, 1, "Line 1")
    line_b = svc.create_line(pid, area_id, 2, "Line 2")
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        s.get(Line, line_a).additional_group_addresses = "111"  # type: ignore[union-attr]
        s.get(Line, line_b).additional_group_addresses = "222"  # type: ignore[union-attr]
        s.commit()

    svc2 = ProjectService()
    pid2 = svc2.open(src)
    _u, additional_a = svc2.coupler_pass_through(pid2, (2 << 12) | (1 << 8))
    _u2, additional_b = svc2.coupler_pass_through(pid2, (2 << 12) | (2 << 8))
    svc2.close(pid2)

    assert additional_a == [111]
    assert additional_b == [222]


def test_additional_addresses_of_the_segment_the_coupler_feeds(
    tmp_path: Path,
) -> None:
    svc, pid, src = _project(tmp_path)
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    sub = svc.add_segment(pid, line_id)
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        line = s.get(Line, line_id)
        assert line is not None
        line.additional_group_addresses = "100,200"  # the union kept on the line
        for segment in line.segments:
            segment.additional_group_addresses = "200" if segment.id == sub else "100"
        s.commit()

    svc2 = ProjectService()
    pid2 = svc2.open(src)
    # The line coupler feeds the main segment; a segment coupler its own segment.
    _u, line_coupler = svc2.coupler_pass_through(pid2, (2 << 12) | (1 << 8))
    _u2, segment_coupler = svc2.coupler_pass_through(
        pid2, (2 << 12) | (1 << 8) | 100, sub
    )
    svc2.close(pid2)

    assert line_coupler == [100]
    assert segment_coupler == [200]


def test_a_main_segment_without_additional_addresses_adds_none(
    tmp_path: Path,
) -> None:
    svc, pid, src = _project(tmp_path)
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    sub = svc.add_segment(pid, line_id)
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        line = s.get(Line, line_id)
        assert line is not None
        line.additional_group_addresses = "200"
        for segment in line.segments:
            if segment.id == sub:
                segment.additional_group_addresses = "200"
        s.commit()

    svc2 = ProjectService()
    pid2 = svc2.open(src)
    _u, additional = svc2.coupler_pass_through(pid2, (2 << 12) | (1 << 8))
    svc2.close(pid2)

    # Only the sub segment lists one: it belongs to that segment's coupler.
    assert additional == []
