"""The pre-flight classification must split each changed byte so a difference count is
never scary without context: a byte whose changed bit carries a real configured value is
a genuine change, while a byte only reset to the application default (device holds an older
value) or a device-managed runtime byte is benign.

The classification is pure (``editor_gui.programming``), shared by the GUI result window and
the MCP evaluate tool; these tests exercise it directly (no imgui context needed).
"""

from __future__ import annotations

from editor_gui.plugins.project.ui.preflight_result import (
    _CAT_CONFIG,
    _CAT_DEFAULT,
    _CAT_MATCH,
    _CAT_RUNTIME,
    PreflightResultWindow,
)
from editor_gui.programming import classify_preflight, preflight_segment_counts
from xknxeditor.download.preflight import PreflightReport, PropertyDiff, SegmentDiff


def _window(runtime: set[int] | None = None, driven: dict[int, int] | None = None):
    w = PreflightResultWindow()
    w._runtime = frozenset(runtime or set())
    w._driven = dict(driven or {})
    return w


def test_driven_bit_change_is_config() -> None:
    # bit 6 (0x40) differs and is driven by an active parameter -> real change.
    w = _window(driven={0x100: 0x40})
    seg = SegmentDiff(address=0x100, current=b"\x00", planned=b"\x40")
    assert w._segment_category(seg) == _CAT_CONFIG
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (1, 0, 0)


def test_stale_bit_on_written_byte_is_default() -> None:
    # The 1.1.9 case: byte written for a neighbour (bit 6 driven), but the only
    # differing bit is bit 5 (0x20), which no active parameter drives. The device
    # holds 0x74, our image writes 0x54 -> reset to the application default: benign.
    w = _window(driven={0x100: 0x40})
    seg = SegmentDiff(address=0x100, current=b"\x74", planned=b"\x54")
    assert w._segment_category(seg) == _CAT_DEFAULT
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (0, 1, 0)


def test_runtime_address_is_runtime() -> None:
    # A device-managed address stays runtime even if a bit differs.
    w = _window(runtime={0x100})
    seg = SegmentDiff(address=0x100, current=b"\x00", planned=b"\xff")
    assert w._segment_category(seg) == _CAT_RUNTIME
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (0, 0, 1)


def test_mixed_segment_reports_config() -> None:
    # One config byte + one default byte -> the segment is a real change overall,
    # but the counts break it down for the summary buckets.
    w = _window(driven={0x200: 0x40})
    seg = SegmentDiff(address=0x200, current=b"\x00\x74", planned=b"\x40\x54")
    assert w._segment_category(seg) == _CAT_CONFIG
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (1, 1, 0)


def test_unchanged_segment_is_match() -> None:
    w = _window()
    seg = SegmentDiff(address=0x300, current=b"\x11", planned=b"\x11")
    assert w._segment_category(seg) == _CAT_MATCH
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (0, 0, 0)


def test_classify_report_totals_and_locations() -> None:
    # A config segment, a benign default-only segment, and a property write (always
    # configured). config_locations counts the config segment plus the property, not the
    # default-only one.
    report = PreflightReport(
        segments=(
            SegmentDiff(address=0x100, current=b"\x00", planned=b"\x40"),  # config
            SegmentDiff(
                address=0x200, current=b"\x74", planned=b"\x54"
            ),  # default only
        ),
        properties=(
            PropertyDiff(
                object_index=0, property_id=7, current=b"\x00", planned=b"\x01"
            ),
        ),
    )
    classes = classify_preflight(report, frozenset(), {0x100: 0x40})
    assert classes.config_bytes == 2  # one driven byte + the property write
    assert classes.default_bytes == 1
    assert classes.runtime_bytes == 0
    assert classes.config_locations == 2  # config segment + property
    assert classes.actionable is True


def test_device_written_address_counts_as_runtime() -> None:
    # The 0x45c0 case: a byte the firmware rewrites at runtime (observed unstable during
    # the preview) is device-managed even though a driven bit differs -> it must not be
    # counted as config, so config_bytes==0 honestly means "in sync".
    report = PreflightReport(
        segments=(SegmentDiff(address=0x45C0, current=b"\x00", planned=b"\x40"),),
        properties=(),
        device_written=frozenset({0x45C0}),
    )
    classes = classify_preflight(report, frozenset(), {0x45C0: 0x40})
    assert classes.config_bytes == 0
    assert classes.runtime_bytes == 1
    assert classes.actionable is False
