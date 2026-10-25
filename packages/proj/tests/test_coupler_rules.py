"""Where a coupler or a KNXnet/IP router may go."""

from __future__ import annotations

import pytest

from xknxeditor.proj.core.coupler_rules import CouplerDevice, check_coupler
from xknxeditor.proj.core.medium import (
    MEDIUM_IP,
    MEDIUM_RF,
    MEDIUM_TP,
    LineView,
    MediumError,
    SegmentView,
)

IP_ROUTER = CouplerDevice(
    media=frozenset({MEDIUM_TP, MEDIUM_IP}),
    mask="MV-091A",
    is_coupler=True,
    ip_router=True,
)
LINE_COUPLER = CouplerDevice(
    media=frozenset({MEDIUM_TP}), mask="MV-0912", is_coupler=True
)
SENSOR = CouplerDevice(media=frozenset({MEDIUM_TP}), mask="MV-0705", is_coupler=False)


def _line(
    area: int,
    address: int,
    *media: str,
    devices: dict[int, tuple[tuple[int, int | None], ...]] | None = None,
) -> LineView:
    return LineView(
        id=area * 10 + address,
        area=area,
        address=address,
        name=f"L{area}{address}",
        segments=tuple(
            SegmentView(
                id=area * 100 + address * 10 + number,
                number=number,
                medium=medium,
                devices=(devices or {}).get(number, ()),
            )
            for number, medium in enumerate(media)
        ),
    )


def _reason(
    lines: list[LineView], segment_id: int, device: CouplerDevice, **kwargs: object
) -> str | None:
    try:
        check_coupler(lines, segment_id, device, "Router", **kwargs)  # type: ignore[arg-type]
    except MediumError as error:
        return error.reason
    return None


IP_PROJECT = [_line(0, 0, MEDIUM_IP), _line(1, 0, MEDIUM_IP), _line(1, 1, MEDIUM_TP)]


def test_an_ip_router_couples_an_ip_main_line_to_a_tp_line() -> None:
    assert _reason(IP_PROJECT, 110, IP_ROUTER) is None
    # Not on the IP backbone itself.
    assert _reason(IP_PROJECT, 0, IP_ROUTER) == "world_coupler_tp_backbone"
    # It needs a backbone, an IP one.
    assert _reason(IP_PROJECT[1:], 110, IP_ROUTER) == "ip_router_backbone"
    tp_backbone = [_line(0, 0, MEDIUM_TP), *IP_PROJECT[1:]]
    assert _reason(tp_backbone, 110, IP_ROUTER) == "ip_router_backbone"


def test_world_couplers() -> None:
    # An IP router at 0.0.0 of a TP backbone is a world coupler: one, and no other router.
    tp_project = [_line(0, 0, MEDIUM_TP), _line(1, 0, MEDIUM_TP)]
    world = CouplerDevice(
        media=frozenset({MEDIUM_TP, MEDIUM_IP}), mask="MV-091A", is_coupler=True
    )
    assert _reason(tp_project, 0, world) is None
    assert _reason(tp_project, 0, world, count=2) == "one_world_coupler"
    with_router = [
        _line(0, 0, MEDIUM_TP),
        _line(1, 0, MEDIUM_TP, devices={0: ((9, 0),)}),
    ]
    assert _reason(with_router, 0, world, ip_routers={9}) == "world_coupler_ip_routers"
    # Being moved, it is not another router.
    assert _reason(with_router, 0, world, ip_routers={9}, moving={9}) is None
    # With a world coupler there, no other device with an IP router's mask goes below it.
    with_world = [
        _line(0, 0, MEDIUM_TP, devices={0: ((9, 0),)}),
        *IP_PROJECT[1:],
    ]
    assert _reason(with_world, 110, world, ip_routers={9}) == "world_coupler_present"
    # An IP router needs an IP backbone first.
    assert _reason(with_world, 110, IP_ROUTER, ip_routers={9}) == "ip_router_backbone"


def test_line_couplers_and_segments() -> None:
    tp_project = [
        _line(0, 0, MEDIUM_TP),
        _line(1, 0, MEDIUM_TP),
        _line(1, 1, MEDIUM_TP),
    ]
    assert _reason(tp_project, 110, LINE_COUPLER) is None
    # Not on an IP backbone: it couples TP above to TP below there.
    assert _reason(IP_PROJECT, 0, LINE_COUPLER) == "coupler_media_above"
    # Only an IP router may couple an IP backbone (one whose media lack the medium above).
    ip_only = CouplerDevice(
        media=frozenset({MEDIUM_IP}), mask="MV-0912", is_coupler=True
    )
    assert _reason(IP_PROJECT, 0, ip_only) == "world_coupler_ip_router"
    # A coupler without segment coupling in an RF sub-segment of a TP line.
    rf_sub = [*tp_project[:2], _line(1, 1, MEDIUM_TP, MEDIUM_RF)]
    assert _reason(rf_sub, 111, LINE_COUPLER) == "coupler_media_above"
    # Without a main line, a line coupler has nothing above it.
    assert _reason(
        [_line(0, 0, MEDIUM_TP), _line(1, 1, MEDIUM_TP)], 110, LINE_COUPLER
    ) == ("coupler_main_line")
    # Any other device is no coupler: nothing to check here.
    assert _reason(IP_PROJECT, 0, SENSOR) is None


def test_messages() -> None:
    with pytest.raises(MediumError) as error:
        check_coupler(IP_PROJECT[1:], 110, IP_ROUTER, "Router")
    assert str(error.value) == "An IP router requires an IP backbone line."
