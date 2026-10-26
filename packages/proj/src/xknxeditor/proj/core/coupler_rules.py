"""Where a coupler may go: the checks made when a device is added, copied or moved to a segment,
or gets another application, in the order they are made (the first that fails is the reason).

A coupler is a device whose hardware is one and whose application's mask version is in the
table below: the medium above it (``NeededUpperLine``) follows from the mask. Whether it is a
line, area, backbone, segment or world coupler follows from where it is put, the media of its
hardware program and its ``SegmentCoupler`` capability. A world coupler is a KNXnet/IP router at
device number 0 on the backbone line.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from xknxeditor.proj.core.medium import (
    IP_ROUTER_MASKS,
    MAX_SEGMENTS,
    MEDIUM_IOT,
    MEDIUM_IP,
    MEDIUM_NAMES,
    MEDIUM_PL,
    MEDIUM_RF,
    MEDIUM_TP,
    LineView,
    MediumError,
    SegmentView,
    medium_name,
)

# The medium above a coupler, by the mask version of its application.
COUPLER_MASKS: dict[str, str] = {
    **{
        mask: MEDIUM_TP
        for mask in ("MV-0900", "MV-0910", "MV-0911", "MV-0912", "MV-1900", "MV-2920")
    },
    **dict.fromkeys(IP_ROUTER_MASKS, MEDIUM_IP),
}

# The directions a coupler couples in.
_UP, _DOWN, _UNKNOWN = 1, 2, 4


@dataclass(frozen=True)
class CouplerDevice:
    """What the catalog tells about a device that is put somewhere: the media of its hardware
    program, its application's mask version, whether its hardware is a coupler, whether it is
    a segment coupler (``SegmentCoupler`` capability) and whether it is an IP router (an IP
    device whose hardware is a coupler and whose IP configuration the tool sets, ``IPConfig``
    "Tool")."""

    media: frozenset[str]
    mask: str
    is_coupler: bool
    segment_coupler: bool = False
    ip_router: bool = False


def check_coupler(
    lines: Sequence[LineView],
    segment_id: int,
    device: CouplerDevice,
    label: str,
    *,
    ip_routers: Collection[int] = (),
    moving: Collection[int] = (),
    count: int = 1,
) -> None:
    """Raise a ``MediumError`` unless ``device`` (named ``label``) may go to the segment
    ``segment_id`` of the topology ``lines``. ``ip_routers`` are the project's KNXnet/IP
    routers (device ids), ``moving`` the devices being moved there (not counted where they
    were), ``count`` how many are added at once."""
    line = next(ln for ln in lines if any(s.id == segment_id for s in ln.segments))
    segment = next(s for s in line.segments if s.id == segment_id)
    backbone = next((ln for ln in lines if ln.area == 0 and ln.address == 0), None)
    others = [d for d in segment.devices if d[0] not in moving]
    is_ip_mask = device.mask in IP_ROUTER_MASKS

    if device.ip_router:
        if backbone is None:
            raise MediumError("ip_router_backbone")
        if backbone.main.medium != MEDIUM_IP and line is not backbone:
            raise MediumError("ip_router_backbone")
    if is_ip_mask and backbone is not None:
        world = [
            d
            for s in backbone.segments
            for d, number in s.devices
            if d in ip_routers and number == 0 and d not in moving
        ]
        if world:
            raise MediumError("world_coupler_present")
    needed = COUPLER_MASKS.get(device.mask)
    if not device.is_coupler or needed is None:
        return

    is_main = segment.number == line.main.number
    upper = _upper_line(lines, line, is_main, backbone)
    direction = _direction(line, segment, device, needed, is_main, upper, is_ip_mask)
    media = device.media
    if line is backbone:
        _check_backbone(
            lines,
            segment,
            device,
            label,
            needed,
            is_main,
            others,
            count,
            {d for d in ip_routers if d not in moving},
        )
    if line.address == 0 and line is not backbone and direction != _DOWN:
        if backbone is None:
            raise MediumError("area_coupler_backbone")
        if is_main and backbone.main.medium != needed:
            raise MediumError("coupler_main_line_medium", medium=medium_name(needed))
        if segment.medium not in media:
            raise MediumError("device_segment_medium", device=label)
    # Line couplers and segment couplers.
    if is_main and line.address == 0:
        return
    if not is_main and not device.segment_coupler and needed not in media:
        raise MediumError("coupler_no_segments")
    above = upper if is_main else line
    if above is None:
        raise MediumError("coupler_main_line")
    if (
        direction != _DOWN
        and is_main
        and above.main.medium != needed
        and not any(number == 0 for _d, number in others)
    ):
        raise MediumError("coupler_main_line_medium", medium=medium_name(needed))
    if direction != _DOWN and segment.medium not in media:
        raise MediumError("device_segment_medium", device=label)
    if direction == _DOWN and len(line.segments) >= MAX_SEGMENTS:
        raise MediumError("coupler_segments_full")


def _upper_line(
    lines: Sequence[LineView],
    line: LineView,
    is_main: bool,
    backbone: LineView | None,
) -> LineView | None:
    """The line above the main segment of ``line``: its area's main line, the backbone above a
    main line (and for the backbone itself); for a sub-segment its own line."""
    if not is_main:
        return line
    if line.address == 0:
        return backbone
    return next((ln for ln in lines if ln.area == line.area and ln.address == 0), None)


def _direction(
    line: LineView,
    segment: SegmentView,
    device: CouplerDevice,
    needed: str,
    is_main: bool,
    upper: LineView | None,
    is_ip_mask: bool,
) -> int:
    """Which way the coupler couples there (up, down, both or unknown); raises the reason it
    couples media the segments around it do not have."""
    medium = segment.medium
    media = device.media
    if needed not in media and not device.segment_coupler:
        return _UNKNOWN
    direction = 0
    if is_main:
        if upper is None:
            raise MediumError("coupler_main_line")
        if medium in media and upper.main.medium == needed:
            direction |= _UP
        if medium == needed:
            direction |= _DOWN
    elif medium in media and line.main.medium == needed:
        direction |= _UP
    if direction:
        return direction
    if is_main and medium == MEDIUM_TP and line.area == 0 and is_ip_mask:
        return _UP
    if not device.segment_coupler or is_main or medium in media:
        raise MediumError(
            "coupler_media_above",
            above=medium_name(needed),
            below=medium_name(medium),
            segment=medium_name(line.main.medium),
        )
    lower = next((m for m in MEDIUM_NAMES if m in media), needed)
    raise MediumError(
        "coupler_media_below",
        above=medium_name(needed),
        below=medium_name(lower),
        segment=medium_name(medium),
    )


def _check_backbone(
    lines: Sequence[LineView],
    segment: SegmentView,
    device: CouplerDevice,
    label: str,
    needed: str,
    is_main: bool,
    others: list[tuple[int, int | None]],
    count: int,
    ip_routers: Collection[int],
) -> None:
    """A coupler on the backbone: only KNXnet/IP routers on an IP backbone; a world coupler
    only on a TP backbone, and only one."""
    backbone = next(ln for ln in lines if ln.area == 0 and ln.address == 0)
    medium = segment.medium
    media = device.media
    is_ip_mask = device.mask in IP_ROUTER_MASKS
    segment_coupler = device.segment_coupler
    if (
        backbone.main.medium == MEDIUM_IP
        and is_main
        and not segment_coupler
        and not is_ip_mask
    ):
        raise MediumError("world_coupler_ip_router")
    if is_main and medium != MEDIUM_TP and not segment_coupler:
        raise MediumError("world_coupler_tp_backbone")
    if needed == MEDIUM_IP and is_main:
        if medium == MEDIUM_TP and MEDIUM_RF in media and segment_coupler:
            raise MediumError("device_line_medium", device=label)
        if MEDIUM_IOT in media:
            raise MediumError("device_line_medium", device=label)
        if count > 1 or any(d in ip_routers and number == 0 for d, number in others):
            raise MediumError("one_world_coupler")
        routers = {
            d
            for ln in lines
            for s in ln.segments
            for d, _n in s.devices
            if d in ip_routers
        }
        if routers:
            raise MediumError("world_coupler_ip_routers")
    elif medium == MEDIUM_TP and needed == medium:
        if MEDIUM_RF in media and not segment_coupler:
            raise MediumError("device_line_medium", device=label)
        if MEDIUM_PL in media:
            raise MediumError("device_line_medium", device=label)
