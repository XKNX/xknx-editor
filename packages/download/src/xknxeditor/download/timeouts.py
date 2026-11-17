"""How long the bus exchanges wait for the interface and for the device.

The defaults suit a local connection. A slow one (a remote tunnel, a VPN) can take seconds to
confirm a frame or to pass a device's answer back, and needs longer waits. They apply to every
connection of the program: xknx reads its waits from module attributes at every exchange.
"""

from __future__ import annotations

from dataclasses import dataclass

from xknx.cemi import cemi_handler
from xknx.exceptions import ConfirmationError
from xknx.management import management

# xknx's defaults: the KNX transport layer's 3 s for the T_Ack, its 6 s connection timeout as
# the wait for an answer.
DEFAULT_CONFIRMATION = 3.0
DEFAULT_ACKNOWLEDGEMENT = 3.0
DEFAULT_RESPONSE = 6.0


@dataclass(frozen=True, slots=True)
class BusTimeouts:
    """The waits in seconds: ``confirmation`` for the interface's L_Data.con of every frame
    sent, ``acknowledgement`` for the device's T_Ack of a frame on a transport connection
    (repeated three times after that), ``response`` for the device's answer to a request."""

    confirmation: float = DEFAULT_CONFIRMATION
    acknowledgement: float = DEFAULT_ACKNOWLEDGEMENT
    response: float = DEFAULT_RESPONSE


def bus_timeouts() -> BusTimeouts:
    """The waits in use."""
    return BusTimeouts(
        confirmation=cemi_handler.REQUEST_TO_CONFIRMATION_TIMEOUT,
        acknowledgement=management.MANAGEMENT_ACK_TIMEOUT,
        response=management.MANAGEMENT_CONNECTION_TIMEOUT,
    )


def set_bus_timeouts(timeouts: BusTimeouts) -> None:
    """Use ``timeouts`` from the next exchange on."""
    cemi_handler.REQUEST_TO_CONFIRMATION_TIMEOUT = timeouts.confirmation  # pyright: ignore[reportConstantRedefinition]
    management.MANAGEMENT_ACK_TIMEOUT = timeouts.acknowledgement  # pyright: ignore[reportConstantRedefinition]
    management.MANAGEMENT_CONNECTION_TIMEOUT = timeouts.response  # pyright: ignore[reportConstantRedefinition]


def confirmation_timed_out(error: BaseException) -> bool:
    """Whether ``error`` comes from a frame the interface did not confirm in time (no
    L_Data.con), directly or through the errors it caused."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ConfirmationError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
