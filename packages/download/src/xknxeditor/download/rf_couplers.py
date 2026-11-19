"""The routing of system broadcasts in KNX TP1/RF Media Couplers, for an RF device behind one.

A new KNX RF device only receives system broadcasts. A TP1/RF Media Coupler routes them onto
RF only while its PID_RF_SBC_CONTROL says so (KNX v01.10.01 - Resources 03.05.01 - §4.5.15.1):
before the device gets its domain and individual address from a tool that is not on its RF
domain, the coupler towards it is set to "system broadcast" and every other one to "disabled",
so the broadcasts don't loop; afterwards the coupler is set to "disabled" again (KNX v02.01.01 -
Configuration Procedures 03.05.03 - §2.3.1.2 and §2.3.1.4). This does it as xknx's
``rf_sbc_control_write`` (XKNX/xknx#2039) does, through the editor's own programmer.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError

from .errors import DownloadError

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram import IndividualAddress

logger = logging.getLogger(__name__)

# The Router Object, and its instance for the Secondary Side (the RF one) of a TP1/RF Media
# Coupler, zero-based (KNX v01.01.01 - Mask 2920h 06.02.42 - §1.2).
_ROUTER_OBJECT = 6
_SECONDARY_SIDE = 1
# PID_RF_SBC_CONTROL of the Router Object.
_PID_RF_SBC_CONTROL = 112


class RFSBCMode(IntEnum):
    """Routing of system broadcast services onto KNX RF (03.05.01 §4.5.15.1, Table 27)."""

    DISABLED = 0
    BROADCAST = 1
    SYSTEM_BROADCAST = 2


class CouplerRoutingError(DownloadError):
    """A TP1/RF Media Coupler could not be prepared for the system broadcasts: ``coupler``,
    and ``reason`` why."""

    def __init__(self, coupler: IndividualAddress, reason: str) -> None:
        super().__init__(
            f"The media coupler {coupler} could not be set to route system broadcasts: "
            f"{reason}"
        )
        self.coupler = coupler
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RFCouplers:
    """The TP1/RF Media Couplers of the installation: the one towards the RF device and the
    others."""

    towards: IndividualAddress
    others: Sequence[IndividualAddress] = ()


async def set_rf_sbc_control(
    xknx: XKNX, coupler: IndividualAddress, mode: RFSBCMode
) -> RFSBCMode:
    """Set PID_RF_SBC_CONTROL of ``coupler`` and return the mode it has now. Raises
    :class:`CouplerRoutingError` when the coupler does not answer or refuses it."""
    from .programmer import DeviceProgrammer
    from .session import management_session

    manager = management_session(xknx, coupler, None)
    try:
        connection = await manager.open()
        programmer = DeviceProgrammer(connection)
        index = await programmer.locate_object(_ROUTER_OBJECT, _SECONDARY_SIDE)
        result = await programmer.function_property_raw(
            index, _PID_RF_SBC_CONTROL, bytes((mode,)), command=True
        )
    except (ManagementConnectionError, DownloadError) as exc:
        raise CouplerRoutingError(coupler, f"{mode.name.lower()}: {exc}") from exc
    finally:
        await manager.close()
    if not result or result[0] != 0:
        code = f"{result[0]:#04x}" if result else "none"
        raise CouplerRoutingError(
            coupler, f"{mode.name.lower()} refused with return code {code}"
        )
    current = RFSBCMode(result[1]) if len(result) > 1 else mode
    logger.info("Media coupler %s routes system broadcasts: %s", coupler, current.name)
    return current


@contextlib.asynccontextmanager
async def system_broadcast_routing(
    xknx: XKNX, couplers: RFCouplers | None
) -> AsyncGenerator[None]:
    """Let the coupler towards an RF device route system broadcasts while the context is open,
    and no other one. Without ``couplers`` nothing is set. When a coupler can't be prepared, the
    one already set is set back and :class:`CouplerRoutingError` names the coupler that failed."""
    if couplers is None:
        yield
        return
    for other in couplers.others:
        await set_rf_sbc_control(xknx, other, RFSBCMode.DISABLED)
    await set_rf_sbc_control(xknx, couplers.towards, RFSBCMode.SYSTEM_BROADCAST)
    try:
        yield
    finally:
        try:
            await set_rf_sbc_control(xknx, couplers.towards, RFSBCMode.DISABLED)
        except CouplerRoutingError as exc:
            # It ends by itself after 10 minutes (03.05.01 §4.5.15.1).
            logger.warning("%s", exc)
