"""A segment at address 0: a device may report a zero table reference for a loaded part (the
PEI program of an MV-07B0 switch actuator). A load procedure uses the reference as it is, as
it judges an allocation by the load state; a preview compares a part without a segment with
no octets."""

from __future__ import annotations

import pytest
from xknx.telegram.apci import (
    MemoryRead,
    MemoryWrite,
    PropertyValueRead,
    PropertyValueResponse,
)

from xknxeditor.download.errors import LoadStateError, SegmentNotAllocated
from xknxeditor.download.image import build_image
from xknxeditor.download.load_state import PID_LOAD_STATE_CONTROL, LoadState
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import PID_TABLE_REFERENCE, DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
    LdCtrlCompareProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_rel_mem_t import (
    LdCtrlCompareRelMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_rel_mem_t import (
    LdCtrlLoadImageRelMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import (
    LdCtrlWriteRelMem,
)

from .conftest import FakeDevice
from .test_wire_audit import MAPPING, seeded_application

_DATA = b"BBBB"


def _load_state_reads(device: FakeDevice) -> list[int]:
    return [
        p.object_index
        for p in device.sent
        if isinstance(p, PropertyValueRead) and p.property_id == PID_LOAD_STATE_CONTROL
    ]


def _device(state: LoadState, reference: int = 0) -> FakeDevice:
    device = FakeDevice(dict(MAPPING), 0x07B0)
    device.table_references[5] = reference
    device.load_states[5] = state
    return device


async def test_a_loaded_part_at_address_zero() -> None:
    device = _device(LoadState.LOADED)
    assert await DeviceProgrammer(device).read_table_reference(5) == 0
    assert _load_state_reads(device) == [5]


@pytest.mark.parametrize(
    "state", [LoadState.UNLOADED, LoadState.LOADING, LoadState.ERROR]
)
async def test_a_zero_reference_outside_the_loaded_state_is_no_segment(
    state: LoadState,
) -> None:
    programmer = DeviceProgrammer(_device(state))
    with pytest.raises(SegmentNotAllocated, match=state.name.lower()) as raised:
        await programmer.read_table_reference(5)
    assert isinstance(raised.value, LoadStateError)


class _NoLoadState(FakeDevice):
    """A device that rejects a read of the load state (0 elements)."""

    def _handle_property_read(
        self, payload: PropertyValueRead
    ) -> PropertyValueResponse:
        if payload.property_id == PID_LOAD_STATE_CONTROL:
            return PropertyValueResponse(
                object_index=payload.object_index,
                property_id=payload.property_id,
                count=0,
                start_index=payload.start_index,
                data=b"",
            )
        return super()._handle_property_read(payload)


async def test_a_zero_reference_without_a_load_state_is_no_segment() -> None:
    device = _NoLoadState(dict(MAPPING), 0x07B0)
    with pytest.raises(SegmentNotAllocated, match="no load state"):
        await DeviceProgrammer(device).read_table_reference(5)


async def test_a_reference_other_than_zero_needs_no_load_state() -> None:
    device = _device(LoadState.UNLOADED, 0x4000)
    assert await DeviceProgrammer(device).read_table_reference(5) == 0x4000
    assert _load_state_reads(device) == []


async def test_the_relative_base_is_the_reference_as_reported() -> None:
    device = _device(LoadState.UNLOADED)
    assert await DeviceProgrammer(device).read_relative_base(5) == 0
    assert _load_state_reads(device) == []


def _runner(device: FakeDevice, controls: list[object]) -> LoadProcedureRunner:
    app, ui = seeded_application((5, 0, _DATA))
    return LoadProcedureRunner(
        app,
        build_image(app, ui=ui),
        DeviceProgrammer(device),
        controls=controls,
        object_types=MAPPING,
    )


def _parameters_procedure() -> list[object]:
    """The parts of the MV-07B0 parameters procedure that load the PEI program."""
    return [
        LdCtrlLoadImageProp(obj_idx=5, prop_id=PID_TABLE_REFERENCE),
        LdCtrlUnload(lsm_idx=5),
        LdCtrlLoad(lsm_idx=5),
        LdCtrlRelSegment(lsm_idx=5, size=4, mode=0, fill=0),
        LdCtrlCompareProp(obj_idx=5, prop_id=PID_TABLE_REFERENCE, inline_data=bytes(4)),
        LdCtrlWriteRelMem(obj_idx=5, offset=0, size=4, verify=True),
        LdCtrlLoadCompleted(lsm_idx=5),
    ]


async def test_a_download_writes_a_segment_at_address_zero() -> None:
    # The device keeps the segment at address 0 when it allocates it again.
    device = _device(LoadState.LOADED)
    await _runner(device, _parameters_procedure()).run()
    writes = [(p.address, p.data) for p in device.sent if isinstance(p, MemoryWrite)]
    assert writes == [(0, _DATA)]
    assert device.load_states[5] == LoadState.LOADED


async def test_a_preview_compares_a_segment_at_address_zero() -> None:
    device = _device(LoadState.LOADED)
    device.memory.update(dict(enumerate(b"BBAB")))
    report = await _runner(device, _parameters_procedure()).preflight()
    assert [(s.address, s.current, s.planned) for s in report.segments] == [
        (0, b"BBAB", _DATA)
    ]
    assert not [p for p in device.sent if isinstance(p, MemoryWrite)]
    assert device.load_states[5] == LoadState.LOADED


async def test_a_preview_compares_a_part_without_a_segment_with_nothing() -> None:
    # Unloaded: the download allocates the segment first, wherever the device places it.
    device = _device(LoadState.UNLOADED)
    device.memory.update(dict(enumerate(b"XXXX")))
    report = await _runner(
        device,
        [
            LdCtrlLoadImageRelMem(obj_idx=5, offset=0, size=4),
            LdCtrlWriteRelMem(obj_idx=5, offset=0, size=4, verify=True),
            LdCtrlCompareRelMem(obj_idx=5, offset=0, size=4, inline_data=_DATA),
        ],
    ).preflight()
    (segment,) = report.segments
    assert (segment.address, segment.current, segment.planned) == (0, b"", _DATA)
    assert segment.changed_bytes == 4
    # Nothing is read of memory the part does not hold.
    assert not [p for p in device.sent if isinstance(p, MemoryRead)]


async def test_a_download_reads_and_compares_a_segment_at_address_zero() -> None:
    device = _device(LoadState.LOADING)
    device.memory.update(dict(enumerate(_DATA)))
    runner = _runner(
        device,
        [
            LdCtrlLoadImageRelMem(obj_idx=5, offset=0, size=4),
            LdCtrlCompareRelMem(obj_idx=5, offset=0, size=4, inline_data=_DATA),
        ],
    )
    await runner.run()
    reads = [(p.address, p.count) for p in device.sent if isinstance(p, MemoryRead)]
    assert reads[0] == (0, 4)
    assert all(address < 4 for address, _ in reads)
    # The load state does not decide where a load procedure reads.
    assert _load_state_reads(device) == []
