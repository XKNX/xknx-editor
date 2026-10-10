"""Tests for lossy-import detection, persistence and export echo (see core.import_notes).

Covers the round-trip serialization, the read-only ``_detect_import_losses`` classifier, the
multi-segment/duplicate-topology builders, and that ``ExportResult`` echoes the notes stored on the
project row so the GUI can remind the user at export time.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core import import_notes as notes
from xknxeditor.proj.core import knxproj_import
from xknxeditor.proj.core.import_notes import (
    COM_OBJECT_TEXT_OVERRIDES,
    DROPPED_DUPLICATE_LINES,
    IP_CONFIG,
    MULTI_SEGMENT,
    MULTIPLE_INSTALLATIONS,
    UNASSIGNED_DEVICES,
    ImportLoss,
)
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Line, Project, Segment


def test_dumps_loads_roundtrip() -> None:
    original = [
        ImportLoss(MULTIPLE_INSTALLATIONS, 2, detail="a, b"),
        ImportLoss(UNASSIGNED_DEVICES, 3),
    ]
    assert notes.loads(notes.dumps(original)) == original


@pytest.mark.parametrize(
    "raw", [None, "", "not json", "{}", "[1, 2]", '[{"count": 1}]']
)
def test_loads_tolerates_bad_input(raw: str | None) -> None:
    # Missing "code", non-list, non-dict items and malformed JSON all yield [] instead of raising.
    assert notes.loads(raw) == []


def test_detect_losses_all_root_classes() -> None:
    root = ET.fromstring(
        """
        <Project>
          <Installation Name="one">
            <UnassignedDevices>
              <DeviceInstance Id="D-1" />
              <DeviceInstance Id="D-2" />
            </UnassignedDevices>
            <ComObjectInstanceRef RefId="O-1" Description="a note" />
            <ComObjectInstanceRef RefId="O-2" Text="custom" FunctionText="fx" />
            <ComObjectInstanceRef RefId="O-3" Description="second" />
            <IPConfig Assign="Auto" />
            <DeviceInstance Id="D-3">
              <AdditionalAddresses>
                <Address Address="200" />
              </AdditionalAddresses>
            </DeviceInstance>
          </Installation>
          <Installation Name="two" />
        </Project>
        """
    )
    extras = knxproj_import._RawExtras()
    losses = {
        loss.code: loss for loss in knxproj_import._detect_import_losses(root, extras)
    }

    assert losses[MULTIPLE_INSTALLATIONS].count == 2
    # IPConfig and @Text/@FunctionText/@Description are preserved through import/export, so they are
    # not reported. UnassignedDevices are detected from extras.unassigned_devices (empty here), not
    # from the root XML, so no note is raised for the <UnassignedDevices> block above.
    assert UNASSIGNED_DEVICES not in losses
    assert IP_CONFIG not in losses
    assert COM_OBJECT_TEXT_OVERRIDES not in losses
    assert MULTI_SEGMENT not in losses  # no multi-segment lines in extras


def test_detect_unassigned_devices_from_extras() -> None:
    """Unassigned devices round-trip verbatim but are not shown/editable, so their count is surfaced
    as an informational note. Detection is keyed on the captured extras, not the root XML."""
    root = ET.fromstring("<Project><Installation /></Project>")
    extras = knxproj_import._RawExtras()
    extras.unassigned_devices = [
        '<DeviceInstance Id="P-1-0_DI-1" />',
        '<DeviceInstance Id="P-1-0_DI-2" />',
    ]
    losses = {
        loss.code: loss for loss in knxproj_import._detect_import_losses(root, extras)
    }
    assert losses[UNASSIGNED_DEVICES].count == 2
    assert losses[UNASSIGNED_DEVICES].detail == ""

    # None captured -> no note.
    empty = knxproj_import._RawExtras()
    assert UNASSIGNED_DEVICES not in {
        loss.code for loss in knxproj_import._detect_import_losses(root, empty)
    }


def test_detect_multi_segment_from_extras() -> None:
    root = ET.fromstring("<Project><Installation /></Project>")
    extras = knxproj_import._RawExtras()
    extras.segments[(1, 1)] = [
        knxproj_import._SegmentDesc(0, None, 100, ("D-1",)),
        knxproj_import._SegmentDesc(1, None, 200, ("D-2",)),
    ]
    extras.segments[(1, 2)] = [knxproj_import._SegmentDesc(0, None, None, ("D-3",))]
    losses = {
        loss.code: loss for loss in knxproj_import._detect_import_losses(root, extras)
    }
    assert losses[MULTI_SEGMENT].count == 1  # only the two-segment line counts


def _fake_device(identifier: str, address: int) -> SimpleNamespace:
    return SimpleNamespace(
        address=address,
        identifier=identifier,
        individual_address=f"1.1.{address}",
        name=f"Dev{address}",
        product_ref="M-1_H-1_P-1",
        hardware_program_ref="M-1_H-1_HP-1",
        description="",
        order_number="ORD-1",
        hardware_name="HW",
        product_name="Prod",
        manufacturer_name="ACME",
        com_object_instance_refs=[],
        parameter_instance_refs={},
        module_instances=[],
        last_modified=None,
    )


def _import_with(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parser: SimpleNamespace,
    extras: knxproj_import._RawExtras,
) -> Path:
    def _fake(
        *_a: object, **_k: object
    ) -> tuple[SimpleNamespace, knxproj_import._RawExtras]:
        return parser, extras

    monkeypatch.setattr(knxproj_import, "_parse", _fake)
    dest = tmp_path / "imported.xknx"
    import_knxproj("unused.knxproj", dest, project_id="P-TEST")
    return dest


def _minimal_parser(areas: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(
        project_info=SimpleNamespace(
            name="Test",
            group_address_style=SimpleNamespace(value="ThreeLevel"),
            guid="GUID-1",
            created_by="ETS6",
            last_modified="2020-01-01",
            schema_version="23",
            tool_version="6.0.0",
        ),
        areas=areas,
        group_ranges=[],
        group_addresses=[],
        spaces=[],
        functions=[],
        # Flat list of every parsed device, as the real XMLParser exposes it (import inventory log).
        devices=[dev for area in areas for line in area.lines for dev in line.devices],
    )


def test_multi_segment_line_builds_two_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_a = _fake_device("DI-A", 1)
    dev_b = _fake_device("DI-B", 2)
    line = SimpleNamespace(
        address=1, name="Line 1", medium_type="MT-0", devices=[dev_a, dev_b]
    )
    area = SimpleNamespace(address=1, name="Area 1", lines=[line])
    extras = knxproj_import._RawExtras()
    extras.segments[(1, 1)] = [
        knxproj_import._SegmentDesc(0, None, 111, ("DI-A",)),
        knxproj_import._SegmentDesc(1, None, 222, ("DI-B",)),
    ]

    dest = _import_with(tmp_path, monkeypatch, _minimal_parser([area]), extras)

    with Session(make_engine(url_for(dest))) as s:
        segments = s.query(Segment).order_by(Segment.number).all()
        assert [(seg.number, seg.domain_address) for seg in segments] == [
            (0, 111),
            (1, 222),
        ]
        # Each device is placed on its own segment by @Id membership.
        by_number = {seg.number: seg for seg in segments}
        assert [d.address for d in by_number[0].devices] == [1]
        assert [d.address for d in by_number[1].devices] == [2]


def test_duplicate_area_merges_and_counts_dropped_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # xknxproject flattens two installations that both define area 1 / line 1 into duplicate
    # XMLArea objects; the importer must merge them and count the colliding line as dropped.
    line_a = SimpleNamespace(
        address=1, name="Line 1", medium_type="MT-0", devices=[_fake_device("DI-A", 1)]
    )
    line_b = SimpleNamespace(
        address=1,
        name="Line 1 dup",
        medium_type="MT-0",
        devices=[_fake_device("DI-B", 2)],
    )
    area_1 = SimpleNamespace(address=1, name="Area 1", lines=[line_a])
    area_dup = SimpleNamespace(address=1, name="Area 1 dup", lines=[line_b])
    extras = knxproj_import._RawExtras()

    dest = _import_with(
        tmp_path, monkeypatch, _minimal_parser([area_1, area_dup]), extras
    )

    svc = ProjectService()
    pid = svc.open(dest)
    # Must not raise MultipleResultsFound now that duplicates are merged. Coupler of area 1 / line 1
    # is individual address 1.1.0 == (1 << 12) | (1 << 8).
    svc.coupler_pass_through(pid, (1 << 12) | (1 << 8))
    svc.close(pid)

    with Session(make_engine(url_for(dest))) as s:
        assert s.query(Line).count() == 1  # colliding line dropped
        project = s.query(Project).one()
        loss_codes = {loss.code for loss in notes.loads(project.import_notes)}
        assert DROPPED_DUPLICATE_LINES in loss_codes


def test_export_result_echoes_import_notes(tmp_path: Path) -> None:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-ECHO")
    svc.close(pid)
    stored = [ImportLoss(UNASSIGNED_DEVICES, 4), ImportLoss(IP_CONFIG, 1)]
    with Session(make_engine(url_for(src))) as s:
        s.query(Project).one().import_notes = notes.dumps(stored)
        s.commit()

    out = tmp_path / "out.knxproj"
    result = export_knxproj(src, out, schema="20")
    assert result.import_notes == stored
