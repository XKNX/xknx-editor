"""Management sessions for MDT custom symbol comparison and transfer."""

import contextlib

from xknx import XKNX
from xknx.telegram import IndividualAddress

from xknxeditor.download import DeviceProgrammer, DeviceSecurity
from xknxeditor.download.session import apdu_overhead, management_session
from xknxeditor.vendor.mdt import profile_for_application
from xknxeditor.vendor.mdt.transfer import (
    compare_symbols,
    program_symbols,
    synchronize_symbols,
    validate_symbols,
)


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
