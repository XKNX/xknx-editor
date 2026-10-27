"""The extended memory services (A_MemoryExtended_Read/Write, 24-bit addresses) for a device
whose application takes them, and the choice between them and the standard ones."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from xknx.telegram.apci import (
    MemoryExtendedRead,
    MemoryExtendedWrite,
    MemoryRead,
    UserMemoryWrite,
)

from xknxeditor.download import programmer as programmer_module
from xknxeditor.download.errors import CompareMismatch, VerificationError
from xknxeditor.download.procedure import MemoryServices, memory_services
from xknxeditor.download.programmer import DeviceProgrammer

from .conftest import FakeDevice


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(programmer_module, "_TRANSIENT_RETRY_DELAY", 0.0)


def _programmer(
    device: FakeDevice, *, max_apdu: int = 15, overhead: int = 0
) -> DeviceProgrammer:
    programmer = DeviceProgrammer(
        device, max_apdu_length=max_apdu, apdu_overhead=overhead
    )
    programmer.extended_memory = True
    programmer.skip_property_verify_probe()
    return programmer


async def test_memory_above_one_mebibyte_is_written_and_read() -> None:
    device = FakeDevice()
    programmer = _programmer(device)
    data = bytes(range(30))
    await programmer.write_memory(0x123456, data)
    assert await programmer.read_memory(0x123456, len(data)) == data
    assert all(
        isinstance(p, MemoryExtendedWrite | MemoryExtendedRead) for p in device.sent
    )
    # A standard frame: 10 octets per extended frame.
    writes = [p for p in device.sent if isinstance(p, MemoryExtendedWrite)]
    assert [len(w.data) for w in writes] == [10, 10, 10]


async def test_a_short_read_below_64_kib_stays_standard_on_a_standard_frame() -> None:
    device = FakeDevice()
    device.memory.update({0x4000: 0x11, 0x4001: 0x22})
    programmer = _programmer(device)
    assert await programmer.read_memory(0x4000, 2) == b"\x11\x22"
    assert isinstance(device.sent[-1], MemoryRead)
    # Writes always take the extended service.
    await programmer.write_memory(0x4000, b"\x33")
    assert isinstance(device.sent[-1], MemoryExtendedWrite)


async def test_a_long_frame_reads_extended_too() -> None:
    device = FakeDevice()
    programmer = _programmer(device, max_apdu=55)
    await programmer.read_memory(0x4000, 120)
    reads = [p for p in device.sent if isinstance(p, MemoryExtendedRead)]
    assert [r.count for r in reads] == [50, 50, 20]


async def test_the_secure_overhead_shrinks_the_frame() -> None:
    device = FakeDevice()
    programmer = _programmer(device, max_apdu=55, overhead=13)
    await programmer.write_memory(0x4000, bytes(40))
    writes = [p for p in device.sent if isinstance(p, MemoryExtendedWrite)]
    assert [len(w.data) for w in writes] == [37, 3]


async def test_a_confirmation_with_a_crc_is_verified() -> None:
    device = FakeDevice()
    device.extended_write_code = 1
    programmer = _programmer(device)
    await programmer.write_memory(0x10000, b"\x01\x02\x03", verify=True)
    device.extended_crc = 0x1234
    with pytest.raises(CompareMismatch, match="CRC"):
        await programmer.write_memory(0x10000, b"\x01\x02\x03", verify=True)
    # Without verification the CRC is not checked.
    await programmer.write_memory(0x10000, b"\x01\x02\x03")


async def test_an_error_return_code_fails() -> None:
    device = FakeDevice()
    device.extended_write_code = 0xF1
    programmer = _programmer(device)
    with pytest.raises(VerificationError, match="return code 0xf1"):
        await programmer.write_memory(0x10000, b"\x01")


async def test_a_failed_extended_service_falls_back_when_the_device_asks_for_it() -> (
    None
):
    device = FakeDevice()
    device.extended_memory_unanswered = True
    programmer = _programmer(device)
    programmer.extended_memory_fallback = True
    await programmer.write_memory(0x12000, b"\x05\x06")
    assert isinstance(device.sent[-1], UserMemoryWrite)
    assert device.memory[0x12000] == 5
    # The rest of the connection uses the standard services.
    assert not programmer.extended_memory


async def test_without_fallback_a_failed_extended_service_fails() -> None:
    device = FakeDevice()
    device.extended_memory_unanswered = True
    programmer = _programmer(device)
    with pytest.raises(Exception, match="extended service"):
        await programmer.write_memory(0x12000, b"\x05")


async def test_the_standard_services_end_at_one_mebibyte() -> None:
    programmer = DeviceProgrammer(FakeDevice())
    programmer.skip_property_verify_probe()
    with pytest.raises(VerificationError, match="extended memory services"):
        await programmer.read_memory(0x123456, 4)
    extended = _programmer(FakeDevice())
    with pytest.raises(VerificationError, match="24-bit"):
        await extended.read_memory(0xFFFFFF, 4)


def _application(app_id: str, *, memory: bool = False, secure: bool = False) -> Any:
    options = SimpleNamespace(supports_extended_memory_services=memory)
    program = SimpleNamespace(
        id=app_id,
        static=SimpleNamespace(options=options),
        is_secure_enabled=secure,
        application_number=int(app_id.split("_A-")[1][:4], 16),
    )
    return SimpleNamespace(id=app_id, manufacturer_id=app_id[:6], program=program)


@pytest.mark.parametrize(
    ("app_id", "memory", "secure", "expected"),
    [
        ("M-0071_A-4381-12-8943", True, False, MemoryServices(True, True)),
        ("M-0083_A-00B0-32-0DFC", True, False, MemoryServices(True, False)),
        ("M-0002_A-1234-10-0000", False, True, MemoryServices(True, False)),
        ("M-0002_A-1234-10-0000", False, False, MemoryServices()),
        # MDT presence detectors take at most 12 octets per standard memory service.
        ("M-0083_A-0064-34-D10D", False, False, MemoryServices(max_count=12)),
        ("M-0083_A-006B-34-27BA", False, False, MemoryServices(max_count=12)),
        ("M-0002_A-0064-10-0000", False, False, MemoryServices()),
    ],
)
def test_the_application_decides_the_memory_services(
    app_id: str, memory: bool, secure: bool, expected: MemoryServices
) -> None:
    assert memory_services(_application(app_id, memory=memory, secure=secure)) == (
        expected
    )


async def test_short_memory_services_cap_standard_and_user_memory() -> None:
    device = FakeDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=55)
    programmer.skip_property_verify_probe()
    MemoryServices(max_count=12).apply(programmer)
    await programmer.write_memory(0x4000, bytes(30))
    await programmer.write_memory(0x10000, bytes(20))
    sizes = [len(p.data) for p in device.sent if hasattr(p, "data")]
    assert sizes == [12, 12, 6, 12, 8]


def test_a_fallback_cap_stays_within_short_memory_services() -> None:
    short = MemoryServices(max_count=12)
    assert [short.cap(given) for given in (None, 15, 8)] == [12, 12, 8]
    assert [MemoryServices().cap(given) for given in (None, 15)] == [None, 15]
