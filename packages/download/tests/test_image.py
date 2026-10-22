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
