"""connection_evaluate_device lists the changed locations as the pre-flight window does."""

from __future__ import annotations

from editor_gui.plugins.mcp.tools.connection import preflight_locations
from xknxeditor.download.preflight import PreflightReport, PropertyDiff, SegmentDiff


def test_changed_locations_with_their_class_and_bytes() -> None:
    report = PreflightReport(
        segments=(
            SegmentDiff(
                address=0x4400, current=b"\x00\x11\x22\x33", planned=b"\x01\x11\x23\x34"
            ),
            SegmentDiff(address=0x4800, current=b"\x05", planned=b"\x05"),
            SegmentDiff(address=0x4900, current=b"\x00", planned=b"\x01"),
        ),
        properties=(
            PropertyDiff(
                object_index=-167772162,
                property_id=5,
                current=b"\x01",
                planned=b"\x04",
                object_type=40960,
                procedure_step=True,
            ),
            PropertyDiff(
                object_index=6, property_id=51, current=b"\x02", planned=b"\x01"
            ),
            PropertyDiff(
                object_index=7, property_id=52, current=b"\x01", planned=b"\x01"
            ),
        ),
        # The device rewrote 0x4900 itself during the preview.
        device_written=frozenset({0x4900}),
    )

    locations = preflight_locations(report, frozenset(), {0x4400: 0x01})

    assert locations == [
        {
            "memory": "0x4400",
            "size": 4,
            "changed_bytes": 3,
            "category": "config",
            "changes": [
                {"offset": 0, "current": "00", "planned": "01", "category": "config"},
                {
                    "offset": 2,
                    "current": "2233",
                    "planned": "2334",
                    "category": "default",
                },
            ],
        },
        {
            "memory": "0x4900",
            "size": 1,
            "changed_bytes": 1,
            "category": "runtime",
            "changes": [
                {"offset": 0, "current": "00", "planned": "01", "category": "runtime"}
            ],
        },
        {
            "object": "type 40960",
            "property": 5,
            "size": 1,
            "changed_bytes": 1,
            "category": "runtime",
            "changes": [
                {"offset": 0, "current": "01", "planned": "04", "category": "runtime"}
            ],
        },
        {
            "object": "6",
            "property": 51,
            "size": 1,
            "changed_bytes": 1,
            "category": "config",
            "changes": [
                {"offset": 0, "current": "02", "planned": "01", "category": "config"}
            ],
        },
    ]


def test_a_segment_only_reset_to_defaults() -> None:
    report = PreflightReport(
        segments=(SegmentDiff(address=0x4400, current=b"\x00", planned=b"\x02"),),
        properties=(),
    )
    # The only parameter at 0x4400 drives bit 0, which does not change.
    (location,) = preflight_locations(report, frozenset(), {0x4400: 0x01})
    assert location["category"] == "default"


def test_a_changed_range_is_split_by_class() -> None:
    report = PreflightReport(
        segments=(
            SegmentDiff(
                address=0x4400,
                current=b"\x00\x00\x00\x00",
                planned=b"\x01\x01\x01\x01",
            ),
        ),
        properties=(),
    )
    # A parameter drives 0x4400 and 0x4403; the device rewrites 0x4402 itself.
    (location,) = preflight_locations(
        report, frozenset({0x4402}), {0x4400: 0x01, 0x4403: 0x01}
    )
    assert location["category"] == "config"
    assert [
        (c["offset"], c["planned"], c["category"]) for c in location["changes"]
    ] == [
        (0, "01", "config"),
        (1, "01", "default"),
        (2, "01", "runtime"),
        (3, "01", "config"),
    ]


def test_a_procedure_step_is_runtime_throughout() -> None:
    report = PreflightReport(
        segments=(
            SegmentDiff(
                address=0x80,
                current=b"\x02\x00\x07",
                planned=b"\x01\x00\x08",
                procedure_step=True,
            ),
        ),
        properties=(),
    )
    (location,) = preflight_locations(report, frozenset(), {0x80: 0xFF})
    assert location["category"] == "runtime"
    assert [(c["offset"], c["category"]) for c in location["changes"]] == [
        (0, "runtime"),
        (2, "runtime"),
    ]
