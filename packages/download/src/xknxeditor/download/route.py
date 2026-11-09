"""The couplers between the interface and a device, and the APDU length they pass.

A coupler forwards a frame only up to its own maximum APDU length, so a download over a
coupler that passes less than the interface and the device take loses its long frames
(issue #245: a line coupler that passes APDU 55). The route follows the KNX topology of
the individual addresses: a line ``a.l`` hangs on its line coupler ``a.l.0``, an area
``a`` on its area coupler ``a.0.0`` on the backbone.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable

from xknx.exceptions import XKNXException
from xknx.telegram import IndividualAddress

from .errors import DownloadError
from .programmer import PID_MAX_APDU_LENGTH, ConnectionManager, DeviceProgrammer

logger = logging.getLogger(__name__)


def _couplers_up(
    address: IndividualAddress, shared_area: bool
) -> list[IndividualAddress]:
    """The couplers from ``address``'s line up to the backbone, or to its area's main line
    when the other end of the route is in the same area."""
    area, line = address.area, address.main
    couplers: list[IndividualAddress] = []
    if line:
        couplers.append(IndividualAddress(f"{area}.{line}.0"))
    if area and not shared_area:
        couplers.append(IndividualAddress(f"{area}.0.0"))
    return couplers


def route_couplers(
    interface: IndividualAddress, device: IndividualAddress
) -> list[IndividualAddress]:
    """The couplers a frame from ``interface`` to ``device`` passes, in that order.

    A coupler at either end is reached on its own side and is not part of the route.
    """
    if (interface.area, interface.main) == (device.area, device.main):
        return []
    shared_area = interface.area == device.area
    route = [
        *_couplers_up(interface, shared_area),
        *reversed(_couplers_up(device, shared_area)),
    ]
    return [coupler for coupler in route if coupler not in (interface, device)]


async def route_max_apdu_length(
    couplers: Iterable[IndividualAddress],
    connect: Callable[[IndividualAddress], ConnectionManager],
) -> int | None:
    """The largest APDU all ``couplers`` pass: the smallest PID_MAX_APDU_LENGTH they report.

    ``connect`` gives the connection manager for a coupler. A coupler that does not answer
    or has no such property does not limit the route (``None`` when none does): the
    memory-service fallback of the download still finds the size such a line takes.
    """
    limits: list[int] = []
    for coupler in couplers:
        manager = connect(coupler)
        data = b""
        try:
            with contextlib.suppress(DownloadError, XKNXException):
                connection = await manager.open()
                data = await DeviceProgrammer(connection).read_property(
                    0, PID_MAX_APDU_LENGTH
                )
        finally:
            with contextlib.suppress(DownloadError, XKNXException):
                await manager.close()
        if data:
            limits.append(int.from_bytes(data, "big"))
        logger.debug(
            "route coupler %s: max APDU length %s",
            coupler,
            limits[-1] if data else "unknown",
        )
    return min(limits, default=None)
