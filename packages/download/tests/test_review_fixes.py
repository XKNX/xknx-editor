"""Tests for the download hardening fixes from the code review.

Each test pins a behaviour that was corrected against the KNX Standard (see the
cited comments in the source).
"""

from __future__ import annotations

import pytest
from xknx.telegram.apci import UserMemoryRead, UserMemoryWrite

from xknxeditor.download.crc import expected_mcb_table, segment_crc
from xknxeditor.download.errors import (
    InsufficientApduLength,
    LoadStateError,
    UnsupportedProcedureError,
    VerificationError,
)
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import ApduRestriction, DeviceProgrammer

from .conftest import FakeDevice


def _mcb_entry(size: int, *, protected: bool) -> bytes:
    flags = 0x00 if protected else 0x01
    return size.to_bytes(4, "big") + bytes([flags, 0x33, 0, 0])


def test_mcb_crc_single_entry_covers_whole_segment() -> None:
    segment = bytes(range(16))
    table = _mcb_entry(16, protected=True)
    out = expected_mcb_table(table, segment)
    crc = segment_crc(segment)
    assert out[6:8] == bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def test_mcb_crc_per_entry_partitions_the_segment() -> None:
    # Two entries: 4 protected octets, then 1 mutable octet (like the Gira
    # fixture the review cited). The protected CRC must cover only the first 4.
    segment = bytes(range(5))
    table = _mcb_entry(4, protected=True) + _mcb_entry(1, protected=False)
    out = expected_mcb_table(table, segment)
    assert out[6:8] == bytes.fromhex("6f21")
    # The unprotected entry is left untouched (placeholder CRC 00 00).
    assert out[14:16] == b"\x00\x00"


def test_mcb_crc_rejects_sizes_that_do_not_tile() -> None:
    from xknxeditor.download.errors import ImageError

    with pytest.raises(ImageError, match="tile"):
        expected_mcb_table(_mcb_entry(99, protected=True), bytes(16))


async def test_write_memory_splits_at_64_kib_boundary() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=55)
    # A write straddling 0x10000 must split into a Memory part below and a
    # UserMemory part at/above the boundary.
    await programmer.write_memory(0xFFFE, bytes(range(8)))
    memory_writes = [p for p in device.sent if type(p).__name__ == "MemoryWrite"]
    user_writes = [p for p in device.sent if isinstance(p, UserMemoryWrite)]
    assert memory_writes and user_writes
    assert max(p.address for p in memory_writes) < 0x10000
    assert min(p.address for p in user_writes) == 0x10000
    # Bytes land contiguously regardless of the split.
    assert bytes(device.memory[0xFFFE + i] for i in range(8)) == bytes(range(8))


async def test_read_memory_above_boundary_uses_user_memory() -> None:
    device = FakeDevice()
    device.memory.update({0x10000 + i: i for i in range(4)})
    programmer = DeviceProgrammer(device, max_apdu_length=55)
    assert await programmer.read_memory(0x10000, 4) == bytes(range(4))
    assert any(isinstance(p, UserMemoryRead) for p in device.sent)


async def test_write_property_rejects_zero_count() -> None:
    programmer = DeviceProgrammer(FakeDevice(), max_apdu_length=55)
    with pytest.raises(VerificationError, match="non-positive element count"):
        await programmer.write_property(0, 5, b"\x01\x02", count=0)


async def test_write_property_rejects_indivisible_data() -> None:
    programmer = DeviceProgrammer(FakeDevice(), max_apdu_length=55)
    with pytest.raises(VerificationError, match="not divisible"):
        await programmer.write_property(0, 5, b"\x01\x02\x03", count=2)


async def test_write_property_rejects_oversized_element() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=15)
    with pytest.raises(
        InsufficientApduLength,
        match=r"restricted to 15 by communication settings, required: 69\)",
    ):
        await programmer.write_property(0, 5, bytes(64), count=1)
    assert not device.sent


async def test_oversized_element_counts_the_secure_and_extended_headers() -> None:
    programmer = DeviceProgrammer(FakeDevice(), max_apdu_length=40, apdu_overhead=13)
    programmer.apdu_restricted_by = ApduRestriction.INTERFACE
    # 40 - 13 - 5 leaves 22 octets for the elements of a standard frame.
    assert programmer.property_chunk_size(22) == 1
    with pytest.raises(InsufficientApduLength) as raised:
        programmer.property_chunk_size(23)
    assert (raised.value.usable, raised.value.required) == (40, 41)
    assert raised.value.restricted_by == "capabilities of the bus interface"
    # The extended services add 4 octets of header.
    assert programmer.extended_property_chunk_size(18) == 1
    with pytest.raises(InsufficientApduLength, match="required: 41"):
        programmer.extended_property_chunk_size(19)
    with pytest.raises(VerificationError, match="invalid property element size"):
        programmer.property_chunk_size(-1)


async def test_read_table_reference_rejects_zero() -> None:
    device = FakeDevice()
    device.table_references[2] = 0
    programmer = DeviceProgrammer(device, max_apdu_length=55)
    with pytest.raises(LoadStateError, match="zero table reference"):
        await programmer.read_table_reference(2)


class _UnknownControl:
    """A load control this engine does not implement."""


class _RecordingManager:
    def __init__(self, device: FakeDevice) -> None:
        self._device = device
        self.opened = 0

    async def open(self) -> FakeDevice:
        self.opened += 1
        return self._device

    async def close(self) -> None:
        pass


async def test_prevalidate_rejects_unsupported_control_before_connecting() -> None:
    device = FakeDevice()
    manager = _RecordingManager(device)
    runner = LoadProcedureRunner(
        object(),  # type: ignore[arg-type]
        DownloadImage(segments=(), properties=()),
        connection_manager=manager,
        controls=[_UnknownControl()],
    )
    with pytest.raises(UnsupportedProcedureError):
        await runner.run()
    # The connection is never opened - the device is left untouched.
    assert manager.opened == 0
