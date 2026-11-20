"""Siemens DALI-2 discovery, identification and physical address changes."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from xknxeditor.dali.connection import DaliCommissioningError

_LOG = logging.getLogger(__name__)
_IMAGE_LIMIT = 1 << 20
_APP = re.compile(r"M-0001_A-9A1([BC])-(02|03|30)-[0-9A-F]{4}(?:-.*)?$")


def line_count(app_id: str | None) -> int:
    """Number of DALI lines for supported application versions, zero for others."""
    match = _APP.fullmatch(app_id.upper()) if app_id else None
    return (2 if match[1] == "C" else 1) if match else 0


class Programmer(Protocol):
    """Transport used by the DALI-2 commissioning interface."""

    async def function_property_raw(
        self, object_index: int, property_id: int, data: bytes, *, command: bool
    ) -> bytes: ...

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes: ...

    async def read_user_memory(self, address: int, size: int) -> bytes: ...


@dataclass(frozen=True)
class Instance:
    """One discovered input instance."""

    number: int
    type: int
    resolution: int


@dataclass(frozen=True)
class Device:
    """Physical identity and capabilities from the discovery cache."""

    short_address: int
    random_address: int
    flags: int
    version: int
    types: tuple[int, ...] = ()
    min_level: int = 0
    instances: tuple[Instance, ...] = ()


@dataclass(frozen=True)
class Scan:
    """A complete inventory image; raw bytes preserve all additional fields."""

    line: int
    sensors: bool
    interface_id: int
    devices: tuple[Device, ...]
    raw: bytes


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    number = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        value = data[offset]
        offset += 1
        if shift == 63 and value > 1:
            break
        number |= (value & 127) << shift
        if not value & 128:
            return number, offset
    raise DaliCommissioningError("Invalid DALI-2 discovery integer")


@dataclass
class _Message:
    fields: dict[int, list[int | bytes]]

    @classmethod
    def parse(cls, data: bytes) -> _Message:
        fields: dict[int, list[int | bytes]] = {}
        offset = 0
        while offset < len(data):
            tag, offset = _varint(data, offset)
            field, wire = tag >> 3, tag & 7
            if not 0 < field < 1 << 29:
                raise DaliCommissioningError("Invalid DALI-2 discovery field")
            if wire == 0:
                value, offset = _varint(data, offset)
                fields.setdefault(field, []).append(value)
                continue
            if wire == 2:
                size, offset = _varint(data, offset)
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
            else:
                raise DaliCommissioningError("Unsupported DALI-2 discovery field")
            if offset + size > len(data):
                raise DaliCommissioningError("Truncated DALI-2 discovery field")
            if wire == 2:
                fields.setdefault(field, []).append(data[offset : offset + size])
            else:
                fields.setdefault(field, []).append(-1)
            offset += size
        return cls(fields)

    def uint(self, field: int, maximum: int = 0xFFFFFFFF) -> int:
        value = self.fields.get(field, [0])[-1]
        if type(value) is not int or not 0 <= value <= maximum:
            raise DaliCommissioningError("Invalid DALI-2 discovery value")
        return value

    def children(self, field: int) -> list[_Message]:
        result: list[_Message] = []
        for value in self.fields.get(field, []):
            if not isinstance(value, bytes):
                raise DaliCommissioningError("Invalid DALI-2 discovery message")
            result.append(self.parse(value))
        return result

    def types(self) -> tuple[int, ...]:
        result: list[int] = []
        for value in self.fields.get(5, []):
            if isinstance(value, int):
                result.append(value)
            else:
                offset = 0
                while offset < len(value):
                    number, offset = _varint(value, offset)
                    result.append(number)
        if any(not 0 <= value <= 255 for value in result):
            raise DaliCommissioningError("Invalid DALI-2 device type")
        return tuple(result)


def decode_scan(data: bytes, line: int = 0, *, sensors: bool = False) -> Scan:
    """Decode a complete protobuf discovery result, rejecting incomplete scans."""
    _line(line)
    message = _Message.parse(data)
    devices = message.children(2)
    error = message.uint(3)
    if error not in (0, 3) or (error == 3 and devices) or len(devices) > 64:
        raise DaliCommissioningError(f"DALI-2 discovery failed with code {error}")
    result: list[Device] = []
    seen: set[int] = set()
    for row in devices:
        address = row.uint(2, 63)
        if address in seen:
            raise DaliCommissioningError("Duplicate DALI-2 discovery address")
        seen.add(address)
        instances: dict[int, Instance] = {}
        if sensors:
            for child in row.children(6):
                number = child.uint(1, 255)
                if number == 255 or number in instances:
                    _LOG.warning(
                        "Ignoring duplicate or invalid DALI-2 instance %s", number
                    )
                    continue
                instances[number] = Instance(number, child.uint(2, 255), child.uint(3))
        result.append(
            Device(
                address,
                row.uint(3, 0xFFFFFF),
                row.uint(1, 255),
                row.uint(4),
                () if sensors else row.types(),
                0 if sensors else row.uint(6, 255),
                tuple(instances.values()),
            )
        )
    return Scan(line, sensors, message.uint(1), tuple(result), data)


def _line(line: int) -> None:
    if type(line) is not int or line not in (0, 1):
        raise ValueError("DALI-2 line must be 0 or 1")


def _address(address: int) -> None:
    if type(address) is not int or not 0 <= address < 64:
        raise ValueError("DALI short address must be between 0 and 63")


def _duration(value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Timeout must be finite and positive")


class SiemensDali2Commissioner:
    """Commission one DALI-2 line through an exclusively held programmer connection."""

    def __init__(
        self, programmer: Programmer, line: int = 0, *, poll_interval: float = 0.2
    ) -> None:
        _line(line)
        _duration(poll_interval)
        self._programmer = programmer
        self.line = line
        self.object_index = 16 + line
        self.poll_interval = poll_interval
        self._paused = False
        self._renew_at = 0.0
        self._lock = asyncio.Lock()

    async def _raw(self, data: bytes, *, command: bool) -> bytes:
        if (
            self._paused
            and data[1] != 80
            and asyncio.get_running_loop().time() >= self._renew_at
        ):
            await self._polling(True)
        raw = await self._programmer.function_property_raw(
            self.object_index, 52, data, command=command
        )
        if not raw or raw[0] != 0:
            raise DaliCommissioningError(
                f"DALI-2 command {data[1]:02x} rejected: {raw.hex()}"
            )
        return raw

    @staticmethod
    def _state(raw: bytes, service: int) -> int:
        if len(raw) < 3 or raw[1] != service or raw[2] not in range(6):
            raise DaliCommissioningError(
                f"Invalid DALI-2 command response: {raw.hex()}"
            )
        return raw[2]

    async def _run(self, payload: bytes) -> bytes:
        service = payload[1]
        while True:
            raw = await self._raw(payload[:2], command=False)
            state = self._state(raw, service)
            if state not in (1, 5):
                break
            await asyncio.sleep(self.poll_interval)
        raw = await self._raw(payload, command=True)
        if len(raw) >= 3 and self._state(raw, service) in (3, 5):
            raise DaliCommissioningError(
                f"DALI-2 command {service:02x} failed: {raw.hex()}"
            )
        while True:
            raw = await self._raw(payload[:2], command=False)
            state = self._state(raw, service)
            if state == 2 or (service == 65 and state in (0, 4)):
                return raw
            if state not in (1, 4, 5):
                raise DaliCommissioningError(
                    f"DALI-2 command {service:02x} failed: {raw.hex()}"
                )
            await asyncio.sleep(self.poll_interval)

    async def _polling(self, paused: bool) -> None:
        raw = await self._raw(bytes((0, 80, 0 if paused else 1)), command=True)
        if len(raw) > 1 and raw[1] != 80:
            raise DaliCommissioningError("Invalid DALI-2 polling response")
        status = await self._programmer.read_property(self.object_index, 54)
        if status != bytes((0 if paused else 1,)):
            raise DaliCommissioningError("DALI-2 polling state did not change")
        self._renew_at = asyncio.get_running_loop().time() + 5

    @asynccontextmanager
    async def _pause(self) -> AsyncGenerator[None]:
        status = await self._programmer.read_property(self.object_index, 54)
        if status not in (b"\x00", b"\x01"):
            raise DaliCommissioningError("Invalid DALI-2 polling status")
        restore = status == b"\x01"
        try:
            self._paused = True
            await self._polling(True)
            yield
        finally:
            self._paused = False
            if restore:
                async with asyncio.timeout(5):
                    await self._polling(False)

    async def _read(self, sensors: bool) -> Scan | None:
        pid = 56 if sensors else 55
        pointer = await self._programmer.read_property(self.object_index, pid)
        if len(pointer) != 4:
            raise DaliCommissioningError("Invalid DALI-2 discovery pointer")
        address = int.from_bytes(pointer, "big")
        if address == 0:
            return None
        if address + 4 > _IMAGE_LIMIT:
            raise DaliCommissioningError("DALI-2 discovery address outside user memory")
        size_raw = await self._programmer.read_user_memory(address, 4)
        if len(size_raw) != 4:
            raise DaliCommissioningError("Incomplete DALI-2 discovery size")
        size = int.from_bytes(size_raw, "little")
        if not size or address + 4 + size > _IMAGE_LIMIT:
            raise DaliCommissioningError("Invalid DALI-2 discovery size")
        data = await self._programmer.read_user_memory(address + 4, size)
        if (
            len(data) != size
            or await self._programmer.read_property(self.object_index, pid) != pointer
        ):
            raise DaliCommissioningError(
                "DALI-2 discovery image changed or is incomplete"
            )
        return decode_scan(data, self.line, sensors=sensors)

    async def read_cached(
        self, *, sensors: bool = False, timeout: float = 60
    ) -> Scan | None:
        """Read the stored inventory without a DALI command; None means unavailable."""
        _duration(timeout)
        async with self._lock, asyncio.timeout(timeout):
            return await self._read(sensors)

    async def _discover(self, sensors: bool, options: int) -> Scan:
        async with self._pause():
            await self._run(bytes((0, 33 if sensors else 17, options)))
        result = await self._read(sensors)
        if result is None:
            raise DaliCommissioningError(
                "DALI-2 scan completed without a discovery image"
            )
        return result

    async def discover(
        self, *, sensors: bool = False, options: int = 0, timeout: float = 300
    ) -> Scan:
        """Start discovery and read its completed image; discovery may assign addresses."""
        if type(options) is not int or not 0 <= options <= 7:
            raise ValueError("DALI-2 scan options must be between 0 and 7")
        _duration(timeout)
        async with self._lock, asyncio.timeout(timeout):
            return await self._discover(sensors, options)

    async def identify(
        self,
        address: int,
        *,
        sensors: bool = False,
        start: bool = True,
        mode: int = 0,
        interval_ms: int = 2000,
        timeout: float = 15,
    ) -> None:
        """Identify a device; mode 0 flashes, 1 turns on, 2 turns off; start=False stops."""
        _address(address)
        if type(mode) is not int or mode not in (0, 1, 2):
            raise ValueError("DALI-2 identification mode must be 0, 1 or 2")
        if (
            type(interval_ms) is not int
            or not 100 <= interval_ms <= 25500
            or interval_ms % 100
        ):
            raise ValueError(
                "Identification interval must be 100 to 25500 ms in steps of 100"
            )
        _duration(timeout)
        action = (18 if sensors else 16) if start else 17
        payload = bytes((0, 96, action, address, mode))
        if start:
            payload += bytes((interval_ms // 100,))
        async with self._lock, asyncio.timeout(timeout), self._pause():
            raw = await self._raw(payload, command=True)
            if len(raw) > 1 and raw[1] != 96:
                raise DaliCommissioningError("Invalid DALI-2 identification response")

    @staticmethod
    def _frame(data: bytes, flags: int) -> bytes:
        return (
            bytes((0, 65, flags, 3))
            + data.rjust(4, b"\x00")
            + bytes((8 * len(data), 1))
        )

    async def _send(self, data: bytes, flags: int) -> None:
        result = await self._run(self._frame(data, flags))
        if len(result) >= 10 and result[9] not in (0, 1):
            raise DaliCommissioningError(
                f"DALI-2 frame was not confirmed: {result.hex()}"
            )

    async def change_short_address(
        self,
        old_address: int,
        new_address: int,
        random_address: int,
        *,
        sensors: bool = False,
        timeout: float = 120,
    ) -> Scan:
        """Move a physical device to a free address, verify identity and return a fresh scan."""
        _address(old_address)
        _address(new_address)
        if old_address == new_address:
            raise ValueError("DALI short addresses must differ")
        if type(random_address) is not int or not 0 < random_address <= 0xFFFFFF:
            raise ValueError("DALI random address must be between 1 and 0xFFFFFF")
        _duration(timeout)
        async with self._lock, asyncio.timeout(timeout):
            inventory = await self._discover(sensors, 0)
            matches = [d for d in inventory.devices if d.short_address == old_address]
            if (
                len(matches) != 1
                or matches[0].random_address != random_address
                or matches[0].flags & 12
            ):
                raise DaliCommissioningError(
                    "DALI-2 identity changed or has conflicting addresses"
                )
            if any(
                d.short_address == new_address
                or (
                    d.short_address != old_address
                    and d.random_address == random_address
                )
                for d in inventory.devices
            ):
                raise DaliCommissioningError(
                    "DALI-2 target or random address is already used"
                )
            async with self._pause():
                try:
                    if sensors:
                        await self._send(bytes((193, 48, 255)), 1)
                        await self._send(bytes(((old_address << 1) | 1, 254, 20)), 6)
                        await self._send(bytes((193, 1, 255)), 5)
                        for command, shift in ((5, 16), (6, 8), (7, 0)):
                            await self._send(
                                bytes((193, command, (random_address >> shift) & 255)),
                                0,
                            )
                        await self._send(bytes((193, 8, new_address)), 0)
                    else:
                        await self._send(bytes((163, 255)), 1)
                        await self._send(bytes(((old_address << 1) | 1, 128)), 6)
                        await self._send(bytes((165, 0)), 5)
                        for command, shift in ((177, 16), (179, 8), (181, 0)):
                            await self._send(
                                bytes((command, (random_address >> shift) & 255)), 0
                            )
                        await self._send(bytes((183, (new_address << 1) | 1)), 0)
                finally:
                    async with asyncio.timeout(5):
                        await self._send(
                            bytes((193, 0, 0)) if sensors else bytes((161, 0)), 2
                        )
            result = await self._discover(sensors, 0)
            if not any(
                d.short_address == new_address and d.random_address == random_address
                for d in result.devices
            ) or any(d.short_address == old_address for d in result.devices):
                raise DaliCommissioningError(
                    "DALI-2 address change failed verification"
                )
            return result
