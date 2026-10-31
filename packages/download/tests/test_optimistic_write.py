"""A verified memory write compares the device first and writes only what differs."""

from __future__ import annotations

from typing import Any

import pytest
from xknx.telegram.apci import MemoryRead, MemoryWrite, UserMemoryRead

from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
    LdCtrlControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
    LdCtrlSetControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

from .conftest import FakeDevice
from .test_procedure import _application


def _device(address: int, data: bytes) -> FakeDevice:
    device = FakeDevice()
    device.memory.update({address + i: value for i, value in enumerate(data)})
    return device


def _writes(device: FakeDevice) -> list[tuple[int, bytes]]:
    """The memory writes, without those of the load state record at 0x0104 (BIM M112)."""
    return [
        (p.address, p.data)
        for p in device.sent
        if isinstance(p, MemoryWrite) and p.address >= 0x4000
    ]


def _reads(device: FakeDevice) -> list[tuple[int, int]]:
    return [(p.address, p.count) for p in device.sent if isinstance(p, MemoryRead)]


def _memory(device: FakeDevice, address: int, size: int) -> bytes:
    return bytes(device.memory.get(address + i, 0) for i in range(size))


async def test_only_the_differing_octets_of_a_block_are_written() -> None:
    planned = bytes(range(100))
    current = bytearray(planned)
    current[10:12] = b"\xee\xee"  # block 1: octets 10 and 11
    current[40] = 0xEE  # block 2 (37..73): octet 40 ...
    current[45] = 0xEE  # ... to octet 45
    device = _device(0x4000, bytes(current))

    optimistic = await DeviceProgrammer(
        device, max_apdu_length=40
    ).write_memory_optimistic(0x4000, planned)

    assert optimistic
    assert _memory(device, 0x4000, 100) == planned
    # Read in blocks of max APDU - 3 octets; a block is written from its first to its last
    # differing octet, a matching block not at all.
    assert _writes(device) == [(0x400A, planned[10:12]), (0x4028, planned[40:46])]
    assert [a for a, _ in _reads(device)][:2] == [0x4000, 0x400A]  # block, then verify


async def test_a_matching_memory_is_only_read() -> None:
    planned = bytes(range(80))
    device = _device(0x4000, planned)

    assert await DeviceProgrammer(device, max_apdu_length=40).write_memory_optimistic(
        0x4000, planned
    )

    assert _writes(device) == []
    assert _reads(device) == [(0x4000, 37), (0x4025, 37), (0x404A, 6)]


async def test_a_mostly_different_memory_is_written_whole_after_the_first_block() -> (
    None
):
    planned = bytes(range(1, 101))
    device = _device(0x4000, bytes(100))

    optimistic = await DeviceProgrammer(
        device, max_apdu_length=40
    ).write_memory_optimistic(0x4000, planned)

    # 37 octets compared, all different: the rest is written as a whole, not read first.
    assert not optimistic
    assert _memory(device, 0x4000, 100) == planned
    assert _writes(device)[0] == (0x4000, planned[:37])
    assert b"".join(d for _, d in _writes(device)[1:]) == planned[37:]
    # One block read, then one verify read per write.
    assert len(_reads(device)) == 1 + len(_writes(device))


async def test_four_differing_blocks_in_a_row_write_the_rest_whole() -> None:
    planned = bytes(range(12 * 6))
    current = bytearray(planned)
    for block in range(4):
        current[block * 12] ^= 0xFF  # one differing octet per block: few differences
    device = _device(0x4000, bytes(current))

    # APDU 15: blocks of 12 octets.
    optimistic = await DeviceProgrammer(device).write_memory_optimistic(0x4000, planned)

    assert not optimistic
    assert _memory(device, 0x4000, len(planned)) == planned
    assert [a for a, _ in _writes(device)][:4] == [0x4000, 0x400C, 0x4018, 0x4024]
    assert b"".join(d for _, d in _writes(device)[4:]) == planned[48:]


async def test_a_matching_block_restarts_the_count_of_differing_blocks() -> None:
    planned = bytes(range(12 * 8))
    current = bytearray(planned)
    for block in (0, 1, 2, 4, 5, 6):
        current[block * 12] ^= 0xFF
    device = _device(0x4000, bytes(current))

    assert await DeviceProgrammer(device).write_memory_optimistic(0x4000, planned)
    assert len(_writes(device)) == 6


@pytest.mark.parametrize(
    ("max_apdu", "extended", "address", "block"),
    [
        (15, False, 0x4000, 12),
        (40, False, 0x4000, 37),
        (254, False, 0x4000, 63),
        (254, True, 0x4000, 249),
        (254, False, 0x10000, 12),
    ],
)
def test_block_size(max_apdu: int, extended: bool, address: int, block: int) -> None:
    programmer = DeviceProgrammer(FakeDevice(), max_apdu_length=max_apdu)
    programmer.extended_memory = extended
    assert programmer._optimistic_block(address) == block


async def test_user_memory_is_compared_with_the_user_memory_services() -> None:
    planned = bytes(range(30))
    device = _device(0x10000, planned)

    assert await DeviceProgrammer(device).write_memory_optimistic(0x10000, planned)
    reads = [p for p in device.sent if isinstance(p, UserMemoryRead)]
    assert sum(p.count for p in reads) == len(planned)
    assert not [p for p in device.sent if isinstance(p, MemoryRead | MemoryWrite)]


def _segment(address: int, size: int) -> LdCtrlAbsSegment:
    return LdCtrlAbsSegment(
        obj_type=1,
        occurrence=0,
        seg_type=0,
        address=address,
        size=size,
        access=0xFF,
        mem_type=3,
        seg_flags=0x80,
    )


async def _run(
    *controls: object, image: DownloadImage, device: FakeDevice, **kwargs: Any
) -> None:
    device.object_types = {0: 0, 1: 1}
    application = _application(*controls)
    application.program.mask_version = "MV-0701"
    await LoadProcedureRunner(
        application, image, DeviceProgrammer(device, max_apdu_length=40), **kwargs
    ).run()


async def test_a_segment_writes_only_what_the_device_lacks() -> None:
    planned = bytes(range(60))
    current = bytearray(planned)
    current[50] = 0
    device = _device(0x4400, bytes(current))

    await _run(
        _segment(0x4400, 60),
        image=DownloadImage((MemorySegment(0x4400, planned),), ()),
        device=device,
    )

    assert _memory(device, 0x4400, 60) == planned
    assert _writes(device) == [(0x4432, planned[50:51])]


async def test_a_write_mem_writes_only_what_the_device_lacks() -> None:
    planned = bytes(range(20))
    device = _device(0x4400, planned[:10] + bytes(10))

    await _run(
        LdCtrlWriteMem(address=0x4400, size=20, verify=True),
        image=DownloadImage((MemorySegment(0x4400, planned),), ()),
        device=device,
    )

    assert _writes(device) == [(0x440A, planned[10:])]


@pytest.mark.parametrize(
    "control",
    [
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_OPTIMISTIC_WRITE, value=False
        ),
        None,
    ],
    ids=["optimistic write off", "unverified"],
)
async def test_without_optimistic_write_the_data_is_written_whole(
    control: object | None,
) -> None:
    planned = bytes(range(20))
    device = _device(0x4400, planned[:10] + bytes(10))
    write = LdCtrlWriteMem(address=0x4400, size=20, verify=control is not None)

    await _run(
        *([control] if control is not None else []),
        write,
        image=DownloadImage((MemorySegment(0x4400, planned),), ()),
        device=device,
    )

    assert _writes(device) == [(0x4400, planned)]
    # No read before the write; a verified write is read back.
    assert _reads(device) == ([] if control is None else [(0x4400, 20)])


async def test_memory_auto_verify_writes_the_data_whole() -> None:
    planned = bytes(range(20))
    device = _device(0x4400, planned[:10] + bytes(10))
    device.properties[(0, 14)] = b"\x01"

    await _run(
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_MEMORY_AUTO_VERIFY, value=True
        ),
        LdCtrlWriteMem(address=0x4400, size=20, verify=True),
        image=DownloadImage((MemorySegment(0x4400, planned),), ()),
        device=device,
    )

    assert b"".join(d for _, d in _writes(device)) == planned
    assert device.properties[(0, 14)] == b"\x05"  # verify mode switched on


async def test_after_a_mostly_different_run_the_later_runs_are_written_whole() -> None:
    first = bytes(range(1, 41))
    second = bytes(range(41, 51))
    device = _device(0x4400, bytes(0x30) + second)

    await _run(
        _segment(0x4400, 0x3A),
        image=DownloadImage(
            (
                MemorySegment(
                    0x4400,
                    first + bytes(8) + second,
                    mask=b"\xff" * 40 + bytes(8) + b"\xff" * 10,
                ),
            ),
            (),
        ),
        device=device,
    )

    assert _memory(device, 0x4430, 10) == second
    # The second run matches the device but is written, because the first was mostly new.
    assert (0x4430, second) in _writes(device)
