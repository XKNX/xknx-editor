"""Discover devices on a KNX line by probing an individual-address range.

There is no line-enumeration service in the KNX Application Layer, so discovery
is done by opening a point-to-point connection to each candidate individual
address and reading its device descriptor (A_DeviceDescriptor_Read type 0, KNX
Standard v3.0.0 3/3/7 section 3.4.2.1). An address that answers hosts a device;
one that times out (or refuses the connection) does not. The scan is read-only.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError, XKNXException
from xknx.telegram import IndividualAddress

from xknxeditor.download import DeviceProgrammer
from xknxeditor.download.errors import DownloadError
from xknxeditor.download.session import connect

from .identify import AppId, read_application_id

# A probe fails (no device / unreachable) on a connection error, a generic xknx
# error, or a download-layer error such as VerificationError (no descriptor
# response). All three mean "nothing usable here", never "abort the scan".
_PROBE_ERRORS = (ManagementConnectionError, XKNXException, DownloadError)

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.address import IndividualAddressableType


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """A device that answered a probe: its address and mask version."""

    address: str
    mask_version: int


def iter_addresses(
    start: IndividualAddressableType, end: IndividualAddressableType
) -> Iterator[IndividualAddress]:
    """Yield every individual address from ``start`` to ``end`` inclusive.

    Walks the raw 16-bit address, so a range like ``1.1.1``-``1.2.10`` covers the
    whole of line ``1.1`` (through ``1.1.255``) before ``1.2``. ``start`` must not
    be after ``end``.
    """
    start_address = IndividualAddress(start)
    end_address = IndividualAddress(end)
    if start_address.raw > end_address.raw:
        raise ValueError(f"scan range start {start_address} is after end {end_address}")
    for raw in range(start_address.raw, end_address.raw + 1):
        yield IndividualAddress(raw)


async def probe_device(
    xknx: XKNX, address: IndividualAddressableType
) -> DiscoveredDevice | None:
    """Probe one address; return the device if it answers, else ``None``.

    Opens a fresh point-to-point connection, reads the device descriptor and
    always closes the connection again. A connection error or a missing response
    means no reachable device at that address and yields ``None``.
    """
    target = IndividualAddress(address)
    try:
        connection = await connect(xknx, target)
    except _PROBE_ERRORS:
        return None
    try:
        programmer = DeviceProgrammer(connection)
        mask_version = await programmer.read_device_descriptor()
    except _PROBE_ERRORS:
        return None
    finally:
        with contextlib.suppress(*_PROBE_ERRORS):
            await xknx.management.disconnect(target)
    return DiscoveredDevice(address=str(target), mask_version=mask_version)


async def probe_and_identify(
    xknx: XKNX, address: IndividualAddressableType
) -> tuple[DiscoveredDevice | None, AppId | None]:
    """Probe an address and read its application id over a single connection.

    Reading the descriptor and the application id on one connection (rather than
    reconnecting for each) avoids the reconnect race that makes rapid scans flaky:
    a fresh connect right after a disconnect can read stale or truncated data.
    Returns ``(None, None)`` when nothing answers, or ``(device, app_id)`` where
    ``app_id`` is ``None`` for an unprogrammed device.
    """
    target = IndividualAddress(address)
    try:
        connection = await connect(xknx, target)
    except _PROBE_ERRORS:
        return None, None
    try:
        programmer = DeviceProgrammer(connection)
        mask_version = await programmer.read_device_descriptor()
        app_id = await read_application_id(programmer)
    except _PROBE_ERRORS:
        return None, None
    finally:
        with contextlib.suppress(*_PROBE_ERRORS):
            await xknx.management.disconnect(target)
    return DiscoveredDevice(address=str(target), mask_version=mask_version), app_id


async def scan_bus(
    xknx: XKNX,
    start: IndividualAddressableType,
    end: IndividualAddressableType,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> list[DiscoveredDevice]:
    """Probe every address in ``[start, end]`` and return the responders.

    ``xknx`` has to be started. The probe is sequential (one open connection at a
    time, as a KNXnet/IP tunnel allows only one point-to-point connection); a
    ``progress(done, total)`` callback is invoked after each address. Read-only.
    """
    addresses = list(iter_addresses(start, end))
    total = len(addresses)
    found: list[DiscoveredDevice] = []
    for done, address in enumerate(addresses, start=1):
        device = await probe_device(xknx, address)
        if device is not None:
            found.append(device)
        if progress is not None:
            progress(done, total)
    return found
