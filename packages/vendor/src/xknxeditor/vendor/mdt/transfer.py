"""Symbol transfer through the device's property interface."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from typing import Protocol

from .profiles import DcaProfile
from .symbols import decode_symbol


class SymbolConnection(Protocol):
    async def write_property(
        self,
        object_index: int,
        property_id: int,
        data: bytes,
        *,
        count: int = 1,
        start_index: int = 1,
        verify: bool = True,
    ) -> bytes: ...
    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes: ...
    async def restart(self) -> None: ...


_PROTECTED_DEFAULTS = {
    0: {
        "a0d9512935864058f8353f7d383628e8bb6c88a8f0bc071a74fd0b2be74a7a17",
        "a9f73767bd1bb8d1344553c51638075ff85ac7119cb9ab185b8dee2eba550d65",
    },
    1: {
        "6904937b4ca5fb30644ad0338a3820b1a41664fefe18685de7a665adf94823fb",
        "a69c3aac31a5638b644426098c27ead7f3da6559952c327feb880b8c3e52054a",
    },
}


def validate_symbols(
    profile: DcaProfile, symbols: Mapping[int, bytes], *, writes: bool = True
) -> None:
    if profile.kind != "symbols" or not symbols:
        raise ValueError("No symbols to transfer")
    for slot, data in symbols.items():
        if type(slot) is not int or not 0 <= slot < profile.slots:
            raise ValueError("Symbol slot is outside this device's library")
        decode_symbol(data)
        if (
            writes
            and slot < profile.first_custom_slot
            and (
                profile.app_id != "M0083-A0041"
                or sha256(data).hexdigest() not in _PROTECTED_DEFAULTS.get(slot, set())
            )
        ):
            raise ValueError("Protected symbol slot accepts only original defaults")


async def compare_symbols(
    connection: SymbolConnection, profile: DcaProfile, symbols: Mapping[int, bytes]
) -> dict[int, bool]:
    """Read slot checksums without entering write mode or changing the device."""
    validate_symbols(profile, symbols, writes=False)
    return {
        slot: await connection.read_property(5, 151, start_index=slot + 1)
        == data[512:][::-1]
        for slot, data in sorted(symbols.items())
    }


async def synchronize_symbols(
    connection: SymbolConnection,
    profile: DcaProfile,
    symbols: Mapping[int, bytes],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[int, bool]:
    """Refresh Smart checksums and compare the complete library without writing pixels."""
    validate_symbols(profile, symbols, writes=False)
    if set(symbols) != set(range(profile.slots)):
        raise ValueError("Synchronization requires the complete symbol library")
    if profile.app_id == "M0083-A0041":
        failure: BaseException | None = None
        try:
            await connection.write_property(5, 153, b"\x02")
            await sleep(0.1)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                await connection.write_property(5, 153, b"\x00")
            except Exception as exc:
                if failure is None:
                    raise
                failure.add_note(f"Resetting symbol checksum mode failed: {exc}")
        await sleep(0.1)
    return await compare_symbols(connection, profile, symbols)


async def program_symbols(
    connection: SymbolConnection,
    profile: DcaProfile,
    symbols: Mapping[int, bytes],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Validate all slots before writing; reset write mode and restart after transfer."""
    validate_symbols(profile, symbols)
    count = 0
    failure: BaseException | None = None
    try:
        items = sorted(symbols.items())
        batches = [items] if len(items) == profile.slots else [[item] for item in items]
        for batch in batches:
            slot_failure: BaseException | None = None
            idle = False
            try:
                await connection.write_property(5, 153, b"\x01")
                for slot, data in batch:
                    await connection.write_property(
                        5, 150, data[:512], count=64, start_index=slot * 64 + 1
                    )
                await connection.write_property(5, 153, b"\x02")
                await sleep(0.2)
                if await connection.read_property(5, 152) != b"\x02":
                    raise ValueError(
                        f"Device rejected symbol {batch[0][0] + 1}"
                        if len(batch) == 1
                        else "Device rejected symbol library"
                    )
                if len(batch) != profile.slots:
                    await connection.write_property(5, 153, b"\x00")
                    idle = True
                for slot, data in batch:
                    actual = await connection.read_property(
                        5, 151, start_index=slot + 1
                    )
                    if actual != data[512:][::-1]:
                        raise ValueError(
                            f"Symbol {slot + 1} checksum differs after transfer"
                        )
                    count += 1
            except BaseException as exc:
                slot_failure = exc
                raise
            finally:
                try:
                    if not idle:
                        await connection.write_property(5, 153, b"\x00")
                except Exception as exc:
                    if slot_failure is None:
                        raise
                    slot_failure.add_note(
                        f"Resetting symbol transfer mode failed: {exc}"
                    )
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await connection.restart()
        except Exception as exc:
            if failure is None:
                raise
            failure.add_note(f"Restart after symbol transfer failed: {exc}")
    return count
