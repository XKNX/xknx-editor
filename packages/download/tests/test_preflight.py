"""Tests for the read-only pre-flight (dry-run) check."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram.apci import MemoryRead, MemoryWrite, PropertyValueWrite

from xknxeditor.download.errors import ImageError, VerificationError
from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.preflight import (
    ByteRange,
    PreflightReport,
    PropertyDiff,
    SegmentDiff,
)
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)

from .conftest import FakeDevice

if True:  # keep import used for typing without a runtime dependency cycle
    from xknxeditor.prod import Application

_ADDRESS_TABLE_TYPE = 1


def _application(*controls: object) -> Application:
    """Wrap Load Controls into a minimal application stand-in."""
    procedure = LoadProceduresLoadProcedure(choice=list(controls))  # type: ignore[arg-type]
    load_procedures = LoadProcedures(load_procedure=[procedure])
    fake = SimpleNamespace(
        load_procedures=load_procedures,
        load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
        manufacturer_id="M-0072",
        program=SimpleNamespace(
            pei_type=1,
            application_number=1,
            application_version=1,
            mask_version="MV-0705",
        ),
    )
    return cast("Application", fake)


def _runner(
    application: Application, image: DownloadImage
) -> tuple[LoadProcedureRunner, FakeDevice]:
    device = FakeDevice(object_types={0: 0x0000, 1: _ADDRESS_TABLE_TYPE})
    programmer = DeviceProgrammer(device)
    return LoadProcedureRunner(application, image, programmer), device


# --- report dataclass units ------------------------------------------------


def test_changed_ranges_finds_contiguous_runs() -> None:
    diff = SegmentDiff(
        address=0x100,
        current=b"\x00\x00\x00\x00\x00",
        planned=b"\x00\xff\xff\x00\xff",
    )
    assert diff.changed_ranges == (ByteRange(1, 2), ByteRange(4, 1))
    assert diff.changed_bytes == 3
    assert diff.changed


def test_report_totals_and_summary() -> None:
    report = PreflightReport(
        segments=(
            SegmentDiff(address=0x10, current=b"\x00\x00", planned=b"\x01\x02"),
            SegmentDiff(address=0x20, current=b"\xaa", planned=b"\xaa"),
        ),
        properties=(
            PropertyDiff(
                object_index=3, property_id=5, current=b"\x00", planned=b"\x09"
            ),
        ),
    )
    assert report.total_changed_bytes == 3
    assert report.has_changes
    assert len(report.changed_segments) == 1
    assert len(report.changed_properties) == 1
    assert "3 byte(s) would change" in report.summary()


# --- runner preflight behaviour ---------------------------------------------


async def test_preflight_reports_memory_change_without_writing() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=b"\x01\x02\x03\x04"),),
        properties=(),
    )
    application = _application(
        LdCtrlWriteMem(address=0x4000, size=4, verify=False, inline_data=None)
    )
    runner, device = _runner(application, image)
    # device currently holds different bytes at two positions
    device.memory.update({0x4000: 0x01, 0x4001: 0xFF, 0x4002: 0x03, 0x4003: 0xFF})

    report = await runner.preflight()

    assert not any(isinstance(p, MemoryWrite) for p in device.sent)  # nothing written
    assert any(isinstance(p, MemoryRead) for p in device.sent)  # only read
    (segment,) = report.segments
    assert segment.address == 0x4000
    assert segment.current == b"\x01\xff\x03\xff"
    assert segment.planned == b"\x01\x02\x03\x04"
    assert segment.changed_ranges == (ByteRange(1, 1), ByteRange(3, 1))
    assert report.total_changed_bytes == 2


async def test_preflight_reports_no_change_when_matching() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=b"\xde\xad"),),
        properties=(),
    )
    application = _application(
        LdCtrlWriteMem(address=0x4000, size=2, verify=False, inline_data=None)
    )
    runner, device = _runner(application, image)
    device.memory.update({0x4000: 0xDE, 0x4001: 0xAD})

    report = await runner.preflight()

    assert not report.has_changes
    assert report.segments[0].changed is False


async def test_preflight_marks_memory_written_with_different_values_as_a_step() -> None:
    # A download handshake flag: 01 at the start, 02 at the end; a value written once is a
    # setting.
    image = DownloadImage(segments=(), properties=())
    application = _application(
        LdCtrlWriteMem(address=0x80, size=1, verify=False, inline_data=b"\x01"),
        LdCtrlWriteMem(address=0x90, size=1, verify=False, inline_data=b"\x05"),
        LdCtrlWriteMem(address=0x80, size=1, verify=False, inline_data=b"\x02"),
    )
    runner, device = _runner(application, image)
    device.memory.update({0x80: 0x02, 0x90: 0x00})

    report = await runner.preflight()

    assert [(s.address, s.planned, s.procedure_step) for s in report.segments] == [
        (0x80, b"\x01", True),
        (0x90, b"\x05", False),
        (0x80, b"\x02", True),
    ]


async def test_preflight_previews_abs_segment_without_allocating() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x4400, data=b"\x11\x22\x33"),),
        properties=(),
    )
    application = _application(
        LdCtrlAbsSegment(
            obj_type=_ADDRESS_TABLE_TYPE,
            occurrence=0,
            seg_type=0,
            address=0x4400,
            size=3,
            access=0xFF,
            mem_type=3,
            seg_flags=0x80,
        )
    )
    runner, device = _runner(application, image)

    report = await runner.preflight()

    # no allocation (no load state changed) and no memory written
    assert device.load_states == {}
    assert not any(isinstance(p, MemoryWrite) for p in device.sent)
    (segment,) = report.segments
    assert segment.address == 0x4400
    assert segment.planned == b"\x11\x22\x33"
    assert segment.changed_bytes == 3  # device is all zeros


async def test_preflight_abs_segment_uncovered_range_is_clean_not_error() -> None:
    """A RAM/system AbsSegment the image never fills must preview as allocate-only, not fail.

    Regression: a real device (Interra ITR524, schema 20) allocates a RAM segment at 0x0700 that
    the download image does not cover. ``_abs_segment`` allocates it and writes nothing (``if
    runs:``). The preflight must mirror that - a missing image slice for an *AbsSegment* is a clean
    allocate-only preview, not the ``ImageError`` that a missing *WriteMem* slice raises. Sharing
    ``_diff_masked``'s raise-on-None across both once made this device fail its read-only test even
    though a real download of the same scope succeeded.
    """
    image = DownloadImage(
        segments=(MemorySegment(address=0x4400, data=b"\x11\x22\x33"),),
        properties=(),
    )
    application = _application(
        LdCtrlAbsSegment(
            obj_type=_ADDRESS_TABLE_TYPE,
            occurrence=0,
            seg_type=0,
            address=0x0700,  # RAM; no image segment covers it
            size=538,
            access=0xFF,
            mem_type=2,
            seg_flags=0x80,
        )
    )
    runner, device = _runner(application, image)

    report = await runner.preflight()  # must not raise ImageError

    assert not any(
        isinstance(p, MemoryWrite) for p in device.sent
    )  # allocate-only, read-only
    assert report.segments == ()  # nothing to diff for the uncovered range
    assert not report.has_changes


async def test_preflight_previews_property_without_writing() -> None:
    application = _application(
        LdCtrlWriteProp(
            obj_idx=5,
            prop_id=0x33,
            start_element=1,
            count=1,
            verify=False,
            inline_data=b"\xaa\xbb",
        )
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))
    device.properties[(5, 0x33)] = b"\xaa\x00"

    report = await runner.preflight()

    assert not any(isinstance(p, PropertyValueWrite) for p in device.sent)
    (prop,) = report.properties
    assert prop.object_index == 5
    assert prop.property_id == 0x33
    assert prop.current == b"\xaa\x00"
    assert prop.planned == b"\xaa\xbb"
    assert prop.changed_ranges == (ByteRange(1, 1),)


async def test_preflight_compare_prop_gate_raises_on_mismatch() -> None:
    device = FakeDevice()
    device.properties[(0, 78)] = b"\x00\x00\x00\x00\x09\x99"
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=78,
            start_element=1,
            count=1,
            inline_data=b"\x00\x00\x00\x00\x02\x27",
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    with pytest.raises(VerificationError, match="compare failed"):
        await runner.preflight()


async def test_preflight_ignores_inline_property_source() -> None:
    # inline data is used directly as the planned value (no image lookup needed)
    application = _application(
        LdCtrlWriteProp(
            obj_idx=5,
            prop_id=0x33,
            start_element=1,
            count=1,
            verify=False,
            inline_data=b"\x01",
        )
    )
    runner, _device = _runner(application, DownloadImage(segments=(), properties=()))
    _device.property_element_sizes[(5, 0x33)] = 1

    report = await runner.preflight()

    assert report.properties[0].planned == b"\x01"
    assert report.properties[0].current == b""  # unset property reads empty


def test_property_trailing_zero_padding_is_not_a_change() -> None:
    # Device stores 8 octets; the planned value is the same 8 octets padded with
    # two trailing zeros (property carried at its maximum element size). The pad
    # must not count as a change.
    current = bytes.fromhex("000040000000a611")
    planned = bytes.fromhex("000040000000a6110000")
    diff = PropertyDiff(
        object_index=3, property_id=204, current=current, planned=planned
    )
    assert diff.changed_bytes == 0
    assert not diff.changed


def test_property_nonzero_extension_still_counts() -> None:
    # A non-zero octet beyond the device length is a real change, still reported.
    diff = PropertyDiff(
        object_index=3,
        property_id=204,
        current=bytes.fromhex("0000"),
        planned=bytes.fromhex("000001"),
    )
    assert diff.changed_bytes == 1


# --- missing programming data must not preview as "no change" ----------------


async def test_preflight_rejects_missing_image_data() -> None:
    """A range no image segment covers is a fatal ImageError in the download, so the preview
    must not report it as nothing-to-change.

    ``DownloadImage.masked_writes`` deliberately distinguishes ``None`` (no segment covers the
    range - the programming data is missing) from ``[]`` (covered, but nothing to write there).
    Collapsing them let a preflight report a clean, no-change preview for a download that then
    aborts with ImageError partway through, after the Load State Machine had been unloaded.
    """
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=b"\x01\x02\x03\x04"),),
        properties=(),
    )
    # The control targets 0x9000, which no segment covers.
    application = _application(
        LdCtrlWriteMem(address=0x9000, size=4, verify=False, inline_data=None)
    )
    runner, device = _runner(application, image)

    with pytest.raises(ImageError, match="no image data for address range"):
        await runner.preflight()

    assert not any(isinstance(p, MemoryWrite) for p in device.sent)  # still read-only


async def test_preflight_still_allows_a_covered_range_that_writes_nothing() -> None:
    """The empty-list case (segment covers the range, mask writes nothing) stays a clean preview."""
    image = DownloadImage(
        segments=(
            MemorySegment(address=0x4000, data=b"\x01\x02\x03\x04", mask=b"\x00" * 4),
        ),
        properties=(),
    )
    application = _application(
        LdCtrlWriteMem(address=0x4000, size=4, verify=False, inline_data=None)
    )
    runner, _device = _runner(application, image)

    report = await runner.preflight()

    assert not report.has_changes


async def test_only_an_eeprom_segment_gets_its_data_written() -> None:
    """An allocation writes the image data only into EEPROM (memory type 3); a RAM segment
    is allocated, and neither written nor previewed, although the image covers it."""
    image = DownloadImage(
        segments=(
            MemorySegment(address=0x0700, data=b"\x01\x02"),
            MemorySegment(address=0x4400, data=b"\x11\x22"),
        ),
        properties=(),
    )

    def segment(address: int, mem_type: int) -> LdCtrlAbsSegment:
        return LdCtrlAbsSegment(
            obj_type=_ADDRESS_TABLE_TYPE,
            occurrence=0,
            seg_type=0,
            address=address,
            size=2,
            access=0xFF,
            mem_type=mem_type,
            seg_flags=0x80,
        )

    application = _application(segment(0x0700, 2), segment(0x4400, 3))
    runner, _device = _runner(application, image)
    report = await runner.preflight()
    assert [s.address for s in report.segments] == [0x4400]

    runner, device = _runner(application, image)
    await runner.run()
    writes = [p for p in device.sent if isinstance(p, MemoryWrite)]
    assert [w.address for w in writes] == [0x4400]
    assert device.memory.get(0x0700) is None
