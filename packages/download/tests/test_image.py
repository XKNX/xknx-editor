"""Tests for download image assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.download.errors import ImageError
from xknxeditor.download.image import DownloadImage, MemorySegment, build_image
from xknxeditor.prod import load

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "prod"
    / "tests"
    / "fixtures"
    / "gira_2gang_button_interface.knxprod"
)


def test_read_within_segment() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x100, data=bytes(range(16))),),
        properties=(),
    )
    assert image.read(0x104, 4) == bytes([4, 5, 6, 7])


def test_read_outside_segment_raises() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x100, data=bytes(4)),),
        properties=(),
    )
    with pytest.raises(ImageError, match="no image data"):
        image.read(0x100, 8)


def test_memory_segment_end() -> None:
    assert MemorySegment(address=0x100, data=bytes(4)).end == 0x104


def test_masked_writes_within_segment() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x100, data=bytes(range(16))),),
        properties=(),
    )
    assert image.masked_writes(0x104, 4) == [(0x104, bytes([4, 5, 6, 7]))]


def test_masked_writes_missing_range_is_none() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x100, data=bytes(4)),),
        properties=(),
    )
    # No segment overlaps 0x900: the programming data is missing (distinct from an
    # empty list, which means covered-but-nothing-to-write). The write path turns
    # this into an ImageError.
    assert image.masked_writes(0x900, 4) is None


def test_masked_writes_allocation_larger_than_segment_clips() -> None:
    """An allocation larger than the image data that fills it writes only the data.

    Regression: a memory-mapped group-address / association table ``LdCtrlAbsSegment``
    declares the table's full capacity (here 513 octets) while the image segment holds
    only the few used entries. ``masked_writes`` must still return the (clipped) image
    run, not ``None`` - requiring full containment made it report the range as missing,
    so the write path (``_abs_segment``'s ``if runs``) and the preflight silently skipped
    the association table and the device kept its stale group-address links, so
    re-linked com-objects sent nothing on the bus.
    """
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=b"\x04\x11\x27\x11\x2a"),),
        properties=(),
    )
    runs = image.masked_writes(0x4000, 513)
    assert runs == [(0x4000, b"\x04\x11\x27\x11\x2a")]


def test_masked_writes_overlapping_segments_write_each_octet_once() -> None:
    """A group object table built on the code segment that also holds the parameters
    (BIM M112) covers the parameter segment: each octet is written once, not per segment."""
    parameters = MemorySegment(
        address=0x43FE,
        data=bytes([0, 0, 0xA1, 0xA2, 0, 0, 0xB1, 0]),
        mask=bytes([0, 0, 0xFF, 0xFF, 0, 0, 0xFF, 0]),
    )
    table = MemorySegment(
        address=0x43FE,
        data=bytes([0x08, 0x01, 0xA1, 0xA2, 0x30, 0x31, 0xB1, 0x32]),
        mask=bytes([0xFF, 0xFF, 0, 0, 0, 0, 0, 0]),
        initialized_mask=b"\xff" * 8,
    )
    image = DownloadImage(segments=(parameters, table), properties=())
    assert image.masked_writes(0x43FE, 8, include_initialized=True) == [
        (0x43FE, bytes([0x08, 0x01, 0xA1, 0xA2, 0x30, 0x31, 0xB1, 0x32]))
    ]
    # Without the seed only the masked octets of both are written, still once each.
    assert image.masked_writes(0x43FE, 8) == [
        (0x43FE, bytes([0x08, 0x01, 0xA1, 0xA2])),
        (0x4404, bytes([0xB1])),
    ]


def test_masked_writes_later_segment_wins_an_overlapping_octet() -> None:
    """Where overlapping segments disagree, the octet of the later one is written, as the
    device would hold it after both writes."""
    first = MemorySegment(address=0x100, data=b"\x01\x02\x03\x04")
    second = MemorySegment(address=0x102, data=b"\xaa\xbb\xcc")
    image = DownloadImage(segments=(first, second), properties=())
    assert image.masked_writes(0x100, 5) == [(0x100, b"\x01\x02\xaa\xbb\xcc")]
    assert image.masked_writes(0x101, 2) == [(0x101, b"\x02\xaa")]


def test_build_image_from_project_device() -> None:
    from types import SimpleNamespace
    from typing import cast

    from xknxeditor.download.project_data import SeedDevice

    registry = load(_FIXTURE)
    applications = [
        app for app in registry.applications.values() if app.dynamic_ui() is not None
    ]
    assert applications
    application = applications[0]

    # A device with no configured parameters/modules must yield the same image
    # as the plain default build (seeding with empty project data is a no-op).
    device = cast(
        "SeedDevice",
        SimpleNamespace(parameters=[], module_instances=[], com_objects=[]),
    )
    seeded = build_image(application, device=device)
    default = build_image(application)

    assert isinstance(seeded, DownloadImage)
    assert {s.address for s in seeded.segments} == {s.address for s in default.segments}


def test_build_image_from_fixture() -> None:
    registry = load(_FIXTURE)
    applications = [
        app for app in registry.applications.values() if app.dynamic_ui() is not None
    ]
    assert applications, "fixture should contain at least one application"

    image = build_image(applications[0])

    assert isinstance(image, DownloadImage)
    # every assembled segment carries data at a concrete address
    for segment in image.segments:
        assert segment.data
        assert segment.address >= 0


def test_build_image_without_a_dynamic_section() -> None:
    # An application without parameters (e.g. an interface) has nothing to encode but still loads.
    from copy import deepcopy

    from xknxeditor.prod import Application

    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    program.dynamic = None
    application = Application(program, original.version, original.manufacturer_id)
    assert application.dynamic_ui() is None
    image = build_image(application)
    assert isinstance(image, DownloadImage)
