"""Segments and the media of lines and segments: which sub-segments a line allows, when a medium
can change, the domain address of an RF or PL segment, and what an export and an import keep."""

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
    MediumError,
    check_sub_segment,
    device_fits,
    new_domain_address,
)


def _project(tmp_path: Path) -> tuple[ProjectService, str, int]:
    """A new project with the TP line 2.1 (name "L")."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = svc.create_area(pid, 0, 2, "A")
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
    assert _reason(error) == "sub_segment_iot"
    assert "'S'" in str(error.value) and "'L'" in str(error.value)
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, line, medium_type=MEDIUM_PL)
    assert _reason(error) == "sub_segment_medium"
    with pytest.raises(KeyError):
        svc.add_segment(pid, 999)
    svc.undo(pid)
    assert _segments(svc, pid, line) == [(0, MEDIUM_TP), (1, MEDIUM_TP)]


def test_an_ip_line_gets_tp_by_default_and_an_rf_line_none(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    svc.set_line_medium(pid, line, MEDIUM_IP)
    svc.add_segment(pid, line)
    svc.add_segment(pid, line, medium_type=MEDIUM_IOT)
    assert _segments(svc, pid, line)[1:] == [(1, MEDIUM_TP), (2, MEDIUM_IOT)]

    other = svc.create_line(pid, svc.topology(pid, 0).areas[-1].id, 2, "R")
    svc.set_line_medium(pid, other, MEDIUM_RF)
    with pytest.raises(MediumError) as error:
        svc.add_segment(pid, other)
    assert _reason(error) == "no_sub_segments"
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


def test_a_line_with_sub_segments_keeps_a_medium_that_allows_them(
    tmp_path: Path,
) -> None:
    svc, pid, line = _project(tmp_path)
    svc.add_segment(pid, line, medium_type=MEDIUM_RF)
    for medium in (MEDIUM_RF, MEDIUM_PL, MEDIUM_IOT):
        with pytest.raises(MediumError) as error:
            svc.set_line_medium(pid, line, medium)
        assert _reason(error) == "line_has_sub_segments"
    svc.set_line_medium(pid, line, MEDIUM_IP)
    assert _segments(svc, pid, line) == [(0, MEDIUM_IP), (1, MEDIUM_RF)]


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


def test_the_rules_alone() -> None:
    assert new_domain_address(MEDIUM_PL, [1, 2, 4]) == 3
    assert new_domain_address(MEDIUM_PL, range(1, 256)) is None
    assert new_domain_address(MEDIUM_TP, []) is None
    rf = new_domain_address(MEDIUM_RF, [])
    assert rf is not None and 0 < rf < 2**48
    check_sub_segment(MEDIUM_IP, MEDIUM_IOT, "S", "L")
    assert device_fits(MEDIUM_TP, ["MT-0", "MT-5"])
    assert not device_fits(MEDIUM_RF, ["MT-0"])


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


def test_export_and_import_keep_the_segments(tmp_path: Path) -> None:
    svc, pid, line = _project(tmp_path)
    sub = svc.add_segment(pid, line, name="Garden")
    svc.set_segment_medium(pid, sub, MEDIUM_RF)
    svc.set_topology_description(pid, "segment", sub, "RF sensors")
    svc.set_comment(pid, "segment", sub, "Note")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23")
    segments = [e for e in _zero(out).iter() if e.tag.rsplit("}", 1)[-1] == "Segment"]
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
