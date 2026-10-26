"""Areas, lines and segments added with a medium, as the "Add" dialog adds them: the media a new
line may have, an area with its main line in one undo step, and a new sub-segment's domain
address."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.medium import (
    MEDIUM_IOT,
    MEDIUM_IP,
    MEDIUM_PL,
    MEDIUM_RF,
    MEDIUM_TP,
    MediumError,
)


def _project(tmp_path: Path) -> tuple[ProjectService, str]:
    """A new project: the IP backbone 0.0, the area 1 with its IP main line 1.0 and line 1.1."""
    svc = ProjectService()
    return svc, svc.create(tmp_path / "p.xknx", "P-0C01")


def _media(svc: ProjectService, pid: str, area: int) -> dict[int, list[str]]:
    """The media of the segments of each line of an area, by line number."""
    return {
        line.address: [s.medium_type for s in line.segments]
        for a in svc.topology(pid, 0).areas
        if a.address == area
        for line in a.lines
    }


def test_the_media_of_a_new_line_follow_the_rules(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    # A main line is no RF line; below the IP backbone it may be IP or IoT.
    assert svc.new_line_media(pid, 0, 2, 0) == [
        MEDIUM_TP,
        MEDIUM_PL,
        MEDIUM_IP,
        MEDIUM_IOT,
    ]
    with pytest.raises(MediumError):
        svc.check_new_line(pid, 0, 2, 0, MEDIUM_RF)
    svc.create_area(pid, 0, 2, "", medium_type=MEDIUM_PL)
    # Below a PL main line only PL lines.
    assert svc.new_line_media(pid, 0, 2, 1) == [MEDIUM_PL]


def test_an_area_comes_with_its_main_line_in_one_undo_step(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    entries = len(svc.history(pid))
    svc.create_area(pid, 0, 3, "Garden", medium_type=MEDIUM_PL)
    assert _media(svc, pid, 3) == {0: [MEDIUM_PL]}
    main = next(
        line for a in svc.topology(pid, 0).areas if a.address == 3 for line in a.lines
    )
    assert main.segments[0].domain_address is not None
    assert len(svc.history(pid)) == entries + 1
    area = next(a.id for a in svc.topology(pid, 0).areas if a.address == 3)
    svc.create_line(pid, area, 1, "")
    assert _media(svc, pid, 3)[1] == [MEDIUM_PL]
    assert svc.undo(pid) and svc.undo(pid)
    assert 3 not in {a.address for a in svc.topology(pid, 0).areas}
    # Without a medium, an area has no main line yet.
    svc.create_area(pid, 0, 4, "")
    assert _media(svc, pid, 4) == {}
    # A refused medium adds nothing.
    with pytest.raises(MediumError):
        svc.create_area(pid, 0, 5, "", medium_type=MEDIUM_RF)
    assert 5 not in {a.address for a in svc.topology(pid, 0).areas}


def test_a_line_gets_the_medium_asked_for(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    area = next(a.id for a in svc.topology(pid, 0).areas if a.address == 1)
    entries = len(svc.history(pid))
    svc.create_line(pid, area, 2, "", medium_type=MEDIUM_TP)
    svc.create_line(pid, area, 3, "", medium_type=MEDIUM_RF)
    assert _media(svc, pid, 1)[2] == [MEDIUM_TP]
    assert _media(svc, pid, 1)[3] == [MEDIUM_RF]
    # One undo step each.
    assert len(svc.history(pid)) == entries + 2
    with pytest.raises(ValueError, match="already used"):
        svc.create_line(pid, area, 3, "", medium_type=MEDIUM_TP)


def test_a_new_rf_segment_gets_a_domain_address(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    line = next(
        line.id
        for a in svc.topology(pid, 0).areas
        if a.address == 1
        for line in a.lines
        if line.address == 1
    )
    entries = len(svc.history(pid))
    svc.add_segment(pid, line, medium_type=MEDIUM_RF)
    (rf,) = [
        s
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == line
        for s in ln.segments
        if s.number == 1
    ]
    assert rf.medium_type == MEDIUM_RF and rf.domain_address is not None
    assert len(svc.history(pid)) == entries + 1
    with pytest.raises(MediumError):
        svc.add_segment(pid, line, medium_type=MEDIUM_IOT)
