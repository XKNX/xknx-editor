"""Glue between the GUI/connection stack and the MDT DALI commissioning library.

Opens a (optionally Tool-Key secured) point-to-point connection to a DALI gateway, builds an
``MdtDaliCommissioner`` on the same ``DeviceProgrammer`` the download flows use, runs one caller
supplied coroutine against it, and always closes the connection. Kept out of the plugins so the
connection service stays thin and this is unit-testable.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable

from xknx import XKNX
from xknx.telegram import IndividualAddress

from xknxeditor.dali import MdtDaliCommissioner, MdtDaliConnection, profile_for
from xknxeditor.download import DeviceProgrammer, DeviceSecurity

# Reuse the download package's own connection builders (plain or Tool-Key secured) so a DALI session
# gets the same secure handling as a normal download. Repo-internal; intentional cross-module use.
from xknxeditor.download.session import apdu_overhead, management_session


def is_dali_dca_app(app_id: str | None) -> bool:
    """Whether ``app_id`` is a Type-A DALI gateway (MDT / Theben) — gates the commissioning tab."""
    return profile_for(app_id) is not None


def dali_object_index_base(app_id: str | None) -> int:
    """The KNX object-index base for ``app_id``'s DALI channel 0 (vendor-specific); 6 if unknown."""
    profile = profile_for(app_id)
    return profile.object_index_base if profile is not None else 6


async def run_dali_operation[T](
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    channel: int,
    op: Callable[[MdtDaliCommissioner], Awaitable[T]],
    *,
    object_index_base: int = 6,
) -> T:
    """Open a connection to ``address``, run ``op`` against an ``MdtDaliCommissioner``, then close.

    ``security`` (from the keyring) enables a Tool-Key secured session, matching the download flows.
    ``object_index_base`` is the vendor's DALI object index for channel 0 (see dali profiles).
    """
    target = IndividualAddress(address)
    manager = management_session(xknx, target, security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        # Negotiate the device's real APDU length (defaults to the mandatory 15). Multi-byte DALI
        # property writes (and a secure session's overhead) would not fit the 15-octet default.
        programmer.max_apdu_length = await programmer.read_max_apdu_length()
        commissioner = MdtDaliCommissioner(
            MdtDaliConnection(programmer, channel, object_index_base)
        )
        return await op(commissioner)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()
