"""Transport for the MDT DALI commissioning protocol over KNX Interface-Object Properties.

Wraps a ``Programmer`` (from :mod:`xknxeditor.download`) whose ``read_property``/``write_property``
speak A_PropertyValue_Read/Write. All DALI properties live on object ``6 + channel`` (firmware on
object 0). Property arrays are 1-based; element 0 is the size descriptor. Commands (property 218) are
fire-and-poll: write the 3-byte command, then read it back until byte 0 is 0 (done) or 0xFF (error).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from xknxeditor.dali.properties import (
    DEFAULT_OBJECT_INDEX_BASE,
    FIRMWARE_OBJECT_INDEX,
    POLL_DONE,
    POLL_ERROR,
    DeviceCommand,
    PropertyType,
    element_size_for,
    object_index,
)
from xknxeditor.dali.structs import Command, ExtCommand

if TYPE_CHECKING:
    from xknxeditor.dali.profiles import DaliProfile

# A_PropertyValue_Read/Write encode the element count in 4 bits -> at most 15 elements per telegram.
_MAX_PROPERTY_ELEMENTS = 15


class DaliCommissioningError(Exception):
    """A DALI commissioning operation failed (transport error, or a command reported 0xFF)."""


class Programmer(Protocol):
    """The subset of ``xknxeditor.download.Programmer`` this package needs."""

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes: ...

    async def write_property(
        self,
        object_index: int,
        property_id: int,
        data: bytes,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes: ...


class MdtDaliConnection:
    """Property-level access to one DALI channel of a Type-A gateway (MDT / Theben / IPAS).

    ``object_index_base`` is the KNX object index of channel 0 (vendor-specific: MDT = 6,
    Theben/IPAS = 7); see :mod:`xknxeditor.dali.profiles`. ``profile`` carries vendor
    overrides (e.g. Theben's 10-byte Group_Status) used by :func:`element_size_for`; pass
    the active ``DaliProfile`` so reads/writes decode the right record size.
    """

    def __init__(
        self,
        programmer: Programmer,
        channel: int = 0,
        object_index_base: int = DEFAULT_OBJECT_INDEX_BASE,
        profile: DaliProfile | None = None,
    ) -> None:
        self._prog = programmer
        self.channel = channel
        self.object_index = object_index(channel, object_index_base)
        self.profile = profile

    async def read_firmware(self) -> tuple[int, int, int] | None:
        """Read the 3-byte Hawk firmware version (object 0, property 25); None if unavailable."""
        data = await self._prog.read_property(
            FIRMWARE_OBJECT_INDEX, PropertyType.HAWK_FIRMWARE, count=1, start_index=1
        )
        if len(data) >= 3:
            return (data[0], data[1], data[2])
        return None

    async def read_table(self, prop: PropertyType, count: int) -> bytes:
        """Read ``count`` elements of ``prop``, chunking across APDUs.

        A_PropertyValue_Read encodes the element count in 4 bits, so at most 15 elements
        can be requested per telegram; a 64-element table needs several reads. Also stops
        early if the device caps a response below the requested count. Element size comes
        from :func:`element_size_for` so vendor overrides (e.g. Theben's 10-byte
        Group_Status) apply.
        """
        size = element_size_for(prop, self.profile)
        out = b""
        start = 1
        remaining = count
        while remaining > 0:
            request = min(remaining, _MAX_PROPERTY_ELEMENTS)
            chunk = await self._prog.read_property(
                self.object_index, prop, count=request, start_index=start
            )
            got = len(chunk) // size
            if got == 0:
                # Device returned less than one element: the response is a short/partial
                # read and continuing would mis-align subsequent elements. Raise so
                # callers (notably the commissioner) treat the table as incomplete rather
                # than silently filling with unassigned slots.
                if remaining == count:
                    raise DaliCommissioningError(
                        f"empty response for property {int(prop)} on object "
                        f"{self.object_index}"
                    )
                raise DaliCommissioningError(
                    f"short read for property {int(prop)}: expected {remaining} more "
                    f"elements of {size} bytes, got {len(chunk)} bytes"
                )
            out += chunk[: got * size]
            start += got
            remaining -= got
        return out

    async def write_table(self, prop: PropertyType, data: bytes, count: int) -> None:
        """Write ``count`` elements of ``prop`` (the programmer fragments across APDUs).

        Validates ``data`` against the per-profile element size up front so a payload
        built from the wrong record width (e.g. MDT's 8-byte slot into a Theben P64's
        10-byte Group_Status) fails here instead of being silently misaligned on-wire.
        """
        size = element_size_for(prop, self.profile)
        if len(data) != count * size:
            raise DaliCommissioningError(
                f"write payload size mismatch for property {int(prop)}: expected "
                f"{count * size} bytes ({count} elements of {size}), got {len(data)}"
            )
        await self._prog.write_property(
            self.object_index, prop, data, count=count, start_index=1
        )

    async def read_table_size(self, prop: PropertyType) -> int | None:
        """Read the property's element-count descriptor (array element 0).

        Returns ``None`` when the property is absent/unsupported (a short or empty response) — which
        must be distinguished from a real size of 0, so callers don't then try to write it.
        """
        data = await self._prog.read_property(
            self.object_index, prop, count=1, start_index=0
        )
        if len(data) < 2:
            return None
        return (data[0] << 8) | data[1]

    async def write_table_size(self, prop: PropertyType, size: int) -> None:
        """Write the property's element-count descriptor (array element 0), 2 bytes big-endian."""
        await self._prog.write_property(
            self.object_index,
            prop,
            bytes(((size >> 8) & 0xFF, size & 0xFF)),
            count=1,
            start_index=0,
        )

    async def send_command(
        self, cmd: DeviceCommand, param1: int = 0, param2: int = 0
    ) -> None:
        """Write a 3-byte command to property 218 (no poll)."""
        await self.write_table(
            PropertyType.COMMAND, Command(cmd, param1, param2).encode(), count=1
        )

    async def send_ext_command(self, ext: ExtCommand) -> None:
        """Write an 8-byte extended (colour) command to property 227."""
        await self.write_table(PropertyType.EXT_COMMAND, ext.encode(), count=1)

    async def poll_result(
        self,
        timeout: float = 300.0,
        interval: float = 1.0,
        on_progress: Callable[[Command], None] | None = None,
    ) -> Command:
        """Poll property 218 until byte 0 is 0 (done) or 0xFF (error), or ``timeout`` elapses.

        Returns the final 3-byte command readback (bytes 1-2 are progress counters). ``on_progress``
        is called with each readback while still running (for live found/removed counters). Raises on
        timeout or on the 0xFF error sentinel.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            data = await self._prog.read_property(
                self.object_index, PropertyType.COMMAND, count=1, start_index=1
            )
            result = Command.decode(data) if len(data) >= 3 else Command(0xFE)
            if result.cmd == POLL_DONE:
                return result
            if result.cmd == POLL_ERROR:
                raise DaliCommissioningError("device reported command error (0xFF)")
            if on_progress is not None:
                on_progress(result)
            if loop.time() >= deadline:
                raise DaliCommissioningError(f"command timed out after {timeout:.0f}s")
            await asyncio.sleep(interval)

    async def run_command(
        self,
        cmd: DeviceCommand,
        param1: int = 0,
        param2: int = 0,
        *,
        timeout: float = 300.0,
        interval: float = 1.0,
        on_progress: Callable[[Command], None] | None = None,
    ) -> Command:
        """Send a command and poll to completion; returns the final readback."""
        await self.send_command(cmd, param1, param2)
        return await self.poll_result(
            timeout=timeout, interval=interval, on_progress=on_progress
        )
