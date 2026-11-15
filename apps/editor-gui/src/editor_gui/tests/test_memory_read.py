"""Memory readback uses the selected address space and never writes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from xknx.telegram.apci import MemoryRead, PropertyValueRead, UserMemoryRead

from editor_gui import memory_read
from editor_gui.memory_read import MemoryReadRequest, preview_segments
from packages.download.tests.conftest import FakeDevice
from xknxeditor.download.errors import LoadStateError, VerificationError


@pytest.mark.parametrize("relative", [False, True])
async def test_readback_has_exact_bytes_and_only_read_services(monkeypatch, relative):
    bus = FakeDevice()
    for i in range(300):
        bus.memory[0x4020 + i] = i % 256
    bus.table_references[4] = 0x4000
    manager = SimpleNamespace(open=AsyncMock(return_value=bus), close=AsyncMock())
    monkeypatch.setattr(memory_read, "management_session", lambda *_args: manager)
    request = MemoryReadRequest(
        "segment", 0x20 if relative else 0x4020, 300, 4 if relative else None
    )
    progress = []
    result = await memory_read.read_memory(
        None, "1.1.1", request, progress=lambda *p: progress.append(p)
    )
    assert result.address == 0x4020
    assert result.data == bytes(i % 256 for i in range(300))
    assert progress[0] == (0, 300) and progress[-1] == (300, 300)
    assert all(isinstance(p, (MemoryRead, PropertyValueRead)) for p in bus.sent)
    assert bool([p for p in bus.sent if isinstance(p, PropertyValueRead)]) == relative
    manager.close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["refused", "zero_reference", "cancel"])
async def test_incomplete_read_closes_connection_without_a_result(monkeypatch, failure):
    bus = FakeDevice()
    bus.readable_end = 0x4010
    manager = SimpleNamespace(open=AsyncMock(return_value=bus), close=AsyncMock())
    monkeypatch.setattr(memory_read, "management_session", lambda *_args: manager)
    request = MemoryReadRequest("segment", 0x4000, 50)
    if failure == "zero_reference":
        bus.properties[(4, 7)] = bytes(4)
        request = MemoryReadRequest("segment", 0, 50, 4)
    elif failure == "cancel":
        monkeypatch.setattr(
            bus, "request", AsyncMock(side_effect=asyncio.CancelledError)
        )
    with pytest.raises((VerificationError, LoadStateError, asyncio.CancelledError)):
        await memory_read.read_memory(None, "1.1.1", request)
    manager.close.assert_awaited_once()
    assert all(isinstance(p, (MemoryRead, PropertyValueRead)) for p in bus.sent)


async def test_user_memory_uses_its_own_read_service(monkeypatch):
    bus = FakeDevice()
    manager = SimpleNamespace(open=AsyncMock(return_value=bus), close=AsyncMock())
    monkeypatch.setattr(memory_read, "management_session", lambda *_args: manager)
    result = await memory_read.read_memory(
        None, "1.1.1", MemoryReadRequest("user", 0x10, 4, user_memory=True)
    )
    assert len(result.data) == 4
    assert bus.sent and all(isinstance(p, UserMemoryRead) for p in bus.sent)


async def test_prepared_preview_reads_relative_and_absolute_resources(monkeypatch):
    from xknxeditor.download.image import (
        DownloadImage,
        MemorySegment,
        ObjectMemorySegment,
    )
    from xknxeditor.download.prepared import PreparedDownload

    bus = FakeDevice()
    bus.table_references[3] = 0x4000
    for base, data in ((0x4000, b"abc"), (0x8C00, b"xyz")):
        bus.memory.update({base + i: value for i, value in enumerate(data)})
    manager = SimpleNamespace(open=AsyncMock(return_value=bus), close=AsyncMock())
    monkeypatch.setattr(memory_read, "management_session", lambda *_args: manager)
    prepared = PreparedDownload(
        image=DownloadImage(
            segments=(MemorySegment(0x8C00, b"xyz"),),
            properties=(),
            application_segments=(ObjectMemorySegment(3, MemorySegment(0, b"abc")),),
            object_segments={3: b"abc", 4: b"xyz"},
        ),
        resource_sizes={3: 3, 4: 3},
        absolute_resources={4: 0x8C00},
    )
    segments = preview_segments(cast(Any, object()), prepared)
    relative = await memory_read.read_memory(
        None, "1.1.1", segments["Object 3"].request
    )
    absolute = await memory_read.read_memory(None, "1.1.1", segments["0x8C00"].request)
    assert (relative.address, relative.data) == (0x4000, b"abc")
    assert (absolute.address, absolute.data) == (0x8C00, b"xyz")
    refs = [p.object_index for p in bus.sent if isinstance(p, PropertyValueRead)]
    assert refs == [3]
    assert all(isinstance(p, (MemoryRead, PropertyValueRead)) for p in bus.sent)


def test_preview_keeps_absolute_relative_and_user_memory_distinct():
    absolute = SimpleNamespace(id="absolute", address=0x5000, user_memory=False)
    user = SimpleNamespace(id="user", address=0x10, user_memory=True)
    relative = SimpleNamespace(id="relative", offset=0x20, load_state_machine=4)
    code = SimpleNamespace(
        absolute_segment=[absolute, user], relative_segment=[relative]
    )
    device = SimpleNamespace(
        app=SimpleNamespace(program=SimpleNamespace(static=SimpleNamespace(code=code))),
        encode_to_memory=lambda: {
            "absolute": b"abc",
            "relative": b"def",
            "user": b"ghi",
        },
    )
    segments = preview_segments(cast(Any, device), None)
    assert segments["absolute"].request == MemoryReadRequest("absolute", 0x5000, 3)
    assert segments["relative"].request == MemoryReadRequest("relative", 0x20, 3, 4)
    assert segments["user"].request == MemoryReadRequest(
        "user", 0x10, 3, user_memory=True
    )
    device.app.program.static.code = None
    assert preview_segments(cast(Any, device), None) == {}


def test_invalid_range_fails_before_connection():
    with pytest.raises(ValueError, match="length"):
        MemoryReadRequest("x", 0xFFFFF0, 100)
    with pytest.raises(ValueError, match="object"):
        MemoryReadRequest("x", 0, 1, 4, True)


async def test_secure_read_retains_security_and_interface_limit(monkeypatch):
    bus = FakeDevice()
    bus.properties[(0, 56)] = bytes([55])
    security = object()
    manager = SimpleNamespace(open=AsyncMock(return_value=bus), close=AsyncMock())
    passed = []
    monkeypatch.setattr(
        memory_read, "management_session", lambda *args: passed.append(args) or manager
    )
    result = await memory_read.read_memory(
        None,
        "1.1.1",
        MemoryReadRequest("x", 0x100, 48),
        security=security,
        interface_max_apdu=31,
    )
    assert passed[0][2] is security
    assert len(result.data) == 48
    assert max(p.count for p in bus.sent if isinstance(p, MemoryRead)) <= 15
    assert all(isinstance(p, (MemoryRead, PropertyValueRead)) for p in bus.sent)


async def test_connection_service_reads_when_bus_writes_are_blocked(monkeypatch):
    from editor_gui.plugins.connection.service import ConnectionService

    service = ConnectionService()
    service.block_bus_writes = True
    service._xknx = cast(Any, object())
    service._loop = asyncio.get_running_loop()
    monkeypatch.setattr(service, "_security_for", lambda _device: None)
    monkeypatch.setattr(memory_read, "read_memory", AsyncMock(return_value="read"))
    device = SimpleNamespace(individual_address="1.1.1", app=None)
    future = service.read_memory(cast(Any, device), MemoryReadRequest("x", 0x100, 5))
    assert future is not None
    assert await asyncio.wrap_future(future) == "read"
    assert service.busy_operation is None
