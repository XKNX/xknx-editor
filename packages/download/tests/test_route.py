"""The couplers between the interface and a device, and the APDU length they pass."""

from __future__ import annotations

import pytest
from xknx.exceptions import ManagementConnectionTimeout
from xknx.telegram import IndividualAddress

from xknxeditor.download.route import route_couplers, route_max_apdu_length

from .conftest import FakeDevice


def _addresses(*texts: str) -> list[IndividualAddress]:
    return [IndividualAddress(text) for text in texts]


@pytest.mark.parametrize(
    ("interface", "device", "couplers"),
    [
        # Same line: no coupler.
        ("1.3.5", "1.3.39", []),
        # Main line to a line (issue #242/#245: interface 1.0.58, device 1.3.39).
        ("1.0.58", "1.3.39", ["1.3.0"]),
        # Line to line in one area: both line couplers.
        ("1.2.7", "1.3.39", ["1.2.0", "1.3.0"]),
        # Line to main line.
        ("1.3.39", "1.0.58", ["1.3.0"]),
        # Line in one area to a line in another: line, area, area, line coupler.
        ("1.2.7", "2.4.1", ["1.2.0", "1.0.0", "2.0.0", "2.4.0"]),
        # Backbone to a line.
        ("0.0.5", "2.4.1", ["2.0.0", "2.4.0"]),
        # A coupler as target is reached on its primary side: it is no coupler on the route.
        ("1.0.58", "1.3.0", []),
        ("0.0.5", "2.4.0", ["2.0.0"]),
    ],
)
def test_couplers_on_the_route(
    interface: str, device: str, couplers: list[str]
) -> None:
    assert route_couplers(IndividualAddress(interface), IndividualAddress(device)) == (
        _addresses(*couplers)
    )


class _Manager:
    def __init__(self, device: FakeDevice | None) -> None:
        self.device = device
        self.closed = False

    async def open(self) -> FakeDevice:
        if self.device is None:
            raise ManagementConnectionTimeout("no answer")
        return self.device

    async def close(self) -> None:
        self.closed = True


def _coupler(max_apdu_length: int | None) -> FakeDevice:
    coupler = FakeDevice()
    if max_apdu_length is not None:
        coupler.properties[(0, 56)] = max_apdu_length.to_bytes(2, "big")
    return coupler


async def _route_limit(
    couplers: dict[str, FakeDevice | None],
) -> tuple[int | None, dict[str, _Manager]]:
    managers = {address: _Manager(device) for address, device in couplers.items()}
    limit = await route_max_apdu_length(
        _addresses(*couplers), lambda address: managers[str(address)]
    )
    return limit, managers


async def test_the_coupler_limits_the_route() -> None:
    limit, managers = await _route_limit({"1.3.0": _coupler(55)})
    assert limit == 55
    assert managers["1.3.0"].closed


async def test_the_narrowest_coupler_limits_the_route() -> None:
    limit, _ = await _route_limit(
        {"1.2.0": _coupler(210), "1.0.0": _coupler(55), "2.0.0": _coupler(248)}
    )
    assert limit == 55


async def test_a_coupler_without_an_answer_does_not_limit_the_route() -> None:
    # An old coupler (mask 0910/0911) has no PID_MAX_APDU_LENGTH; the memory-service
    # fallback still finds the block size such a line takes.
    limit, managers = await _route_limit(
        {"1.2.0": _coupler(None), "1.0.0": None, "1.3.0": _coupler(55)}
    )
    assert limit == 55
    assert managers["1.2.0"].closed
    assert (await _route_limit({"1.2.0": _coupler(None), "1.0.0": None}))[0] is None


async def test_no_coupler_no_limit() -> None:
    assert (await _route_limit({}))[0] is None
