"""Management sessions for MDT custom symbol comparison and transfer."""

import contextlib
from dataclasses import dataclass

from xknx import XKNX
from xknx.exceptions import ManagementConnectionError, XKNXException
from xknx.telegram import IndividualAddress

from xknxeditor.download import DeviceProgrammer, DeviceSecurity, negotiated_apdu_length
from xknxeditor.download.session import apdu_overhead, management_session
from xknxeditor.vendor.mdt import profile_for_application
from xknxeditor.vendor.mdt.transfer import (
    compare_symbols,
    program_symbols,
    recover_symbols,
    synchronize_symbols,
    validate_symbols,
)


@dataclass(frozen=True)
class SymbolReadTarget:
    generation: int
    node_id: int
    address: str
    application_id: str
    entries: tuple[tuple[str, bytes], ...]


async def read_symbols(
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    application_id: str,
    *,
    interface_max_apdu_length: int | None = None,
) -> dict[int, bytes]:
    """Read the complete library with an application check and a short-frame retry."""
    from xknxeditor.recover.identify import AppId, read_application_id

    profile = profile_for_application(application_id)
    if profile is None or profile.kind != "symbols":
        raise ValueError("Device has no MDT symbol configuration")
    manufacturer, program = application_id.split("_A-", 1)
    number, version, *_ = program.split("-")
    expected = AppId(manufacturer, int(number, 16), int(version, 16))
    for attempt in range(2):
        manager = management_session(xknx, IndividualAddress(address), security)
        try:
            connection = await manager.open()
            programmer = DeviceProgrammer(
                connection, apdu_overhead=apdu_overhead(security)
            )
            if attempt == 0:
                programmer.max_apdu_length = negotiated_apdu_length(
                    await programmer.read_max_apdu_length(), interface_max_apdu_length
                )
            if await read_application_id(programmer) != expected:
                raise ValueError("Device application does not match the project")
            return await recover_symbols(programmer, profile)
        except (ManagementConnectionError, XKNXException):
            if attempt:
                raise
        finally:
            with contextlib.suppress(Exception):
                await manager.close()
    raise RuntimeError("Symbol read did not complete")


async def run_symbols(
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    application_id: str,
    symbols: dict[int, bytes],
    *,
    writes: bool,
    synchronize: bool = False,
) -> dict[int, bool] | int:
    profile = profile_for_application(application_id)
    if profile is None:
        raise ValueError("Device has no MDT symbol configuration")
    if writes and synchronize:
        raise ValueError("Cannot program and synchronize in the same operation")
    validate_symbols(profile, symbols, writes=writes)
    if synchronize and set(symbols) != set(range(profile.slots)):
        raise ValueError("Synchronization requires the complete symbol library")
    manager = management_session(xknx, IndividualAddress(address), security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        programmer.max_apdu_length = min(55, await programmer.read_max_apdu_length())
        if synchronize:
            return await synchronize_symbols(programmer, profile, symbols)
        if writes:
            return await program_symbols(programmer, profile, symbols)
        return await compare_symbols(programmer, profile, symbols)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()
