"""Read the memory ranges shown in the editor's image preview."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.telegram import IndividualAddress

from xknxeditor.download.errors import VerificationError
from xknxeditor.download.procedure import MemoryServices
from xknxeditor.download.programmer import DeviceProgrammer, negotiated_apdu_length
from xknxeditor.download.session import apdu_overhead, management_session

if TYPE_CHECKING:
    from xknx import XKNX

    from editor_gui.device import Device
    from xknxeditor.download.data_secure import DeviceSecurity
    from xknxeditor.download.prepared import PreparedDownload


@dataclass(frozen=True)
class MemoryReadRequest:
    segment: str
    address: int
    size: int
    object_index: int | None = None
    user_memory: bool = False

    def __post_init__(self) -> None:
        if not self.segment or not 0 <= self.address < 0x1000000:
            raise ValueError("Invalid memory address")
        if not 0 < self.size <= 0x1000000 - self.address:
            raise ValueError("Invalid memory length")
        if self.object_index is not None and (
            not 0 <= self.object_index <= 255 or self.user_memory
        ):
            raise ValueError("Invalid relative memory object")


@dataclass(frozen=True)
class PreviewSegment:
    request: MemoryReadRequest
    data: bytes


@dataclass(frozen=True)
class MemoryReadResult:
    request: MemoryReadRequest
    address: int
    data: bytes


def preview_segments(
    device: Device, prepared: PreparedDownload | None
) -> dict[str, PreviewSegment]:
    """Keep preview bytes and their address spaces together."""
    result: dict[str, PreviewSegment] = {}

    def add(
        key: str,
        address: int,
        data: bytes,
        object_index: int | None = None,
        user_memory: bool = False,
    ) -> None:
        if data:
            result[key] = PreviewSegment(
                MemoryReadRequest(key, address, len(data), object_index, user_memory),
                data,
            )

    if prepared is not None:
        for relative in prepared.image.application_segments:
            add(
                f"Object {relative.object_index}",
                relative.memory.address,
                relative.memory.data,
                relative.object_index,
            )
        for absolute in prepared.image.segments:
            add(f"0x{absolute.address:04X}", absolute.address, absolute.data)
        return result
    if device.app is None:
        return result
    code = device.app.program.static.code
    if code is None:
        return result
    absolute_segments = {s.id: s for s in code.absolute_segment}
    relative_segments = {s.id: s for s in code.relative_segment}
    for key, data in device.encode_to_memory().items():
        if key in relative_segments:
            relative = relative_segments[key]
            add(key, relative.offset, data, relative.load_state_machine)
        elif key in absolute_segments:
            absolute = absolute_segments[key]
            add(key, absolute.address, data, user_memory=bool(absolute.user_memory))
        else:
            raise ValueError(f"Unknown memory segment {key}")
    return result


async def read_memory(
    xknx: XKNX,
    address: str,
    request: MemoryReadRequest,
    *,
    security: DeviceSecurity | None = None,
    services: MemoryServices | None = None,
    interface_max_apdu: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> MemoryReadResult:
    """Read a whole selected range; a refusal or short read never produces a dump."""
    manager = management_session(xknx, IndividualAddress(address), security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        if services is not None:
            services.apply(programmer)
        if security is not None:
            programmer.max_apdu_length = negotiated_apdu_length(
                await programmer.read_max_apdu_length(), interface_max_apdu
            )
        base = request.address
        if request.object_index is not None:
            base += await programmer.read_table_reference(request.object_index)
        if base + request.size > 0x1000000:
            raise ValueError("Memory range exceeds the supported address space")
        data = bytearray()
        if progress is not None:
            progress(0, request.size)
        while len(data) < request.size:
            count = min(256, request.size - len(data))
            part = await programmer.read_memory(
                base + len(data), count, user=request.user_memory
            )
            if len(part) != count:
                raise VerificationError("Incomplete device memory read")
            data.extend(part)
            if progress is not None:
                progress(len(data), request.size)
        return MemoryReadResult(request, base, bytes(data))
    finally:
        await manager.close()
