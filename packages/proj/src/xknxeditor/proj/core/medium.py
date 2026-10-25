"""The media of lines and segments: which a line and a segment can have, which sub-segments a
line allows, when a medium may change and the domain addresses that follow from it.

The medium belongs to the segment; a line's medium is the one of its main segment (the segment
numbered 0). The line ``a.0`` is the main line of area ``a``, the line ``0.0`` the backbone; every
other line is a sub line, below its area's main line. The rules of a change are checked in a fixed
order and the first one that fails is the reason given (see :func:`check_medium_change`).
"""

from __future__ import annotations

import secrets
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

MEDIUM_TP = "MT-0"
MEDIUM_PL = "MT-1"
MEDIUM_RF = "MT-2"
MEDIUM_IP = "MT-5"
MEDIUM_IOT = "MT-6"

# Every medium, in the order a line offers them, with its short name.
MEDIUM_NAMES: dict[str, str] = {
    MEDIUM_TP: "TP",
    MEDIUM_PL: "PL",
    MEDIUM_RF: "RF",
    MEDIUM_IP: "IP",
    MEDIUM_IOT: "IoT",
}
# The media a sub-segment offers.
SEGMENT_MEDIA = (MEDIUM_TP, MEDIUM_RF, MEDIUM_IOT)
# The media a backbone line can have.
BACKBONE_MEDIA = (MEDIUM_IP, MEDIUM_TP)
# The most segments a line can have, its main segment included.
MAX_SEGMENTS = 128
# The mask versions of KNXnet/IP routers: couplers whose medium above is IP.
IP_ROUTER_MASKS = frozenset({"MV-091A", "MV-6900"})
# The media with a domain address, and its bits; a PL one is 1 to 255.
_DOMAIN_BITS = {MEDIUM_PL: 8, MEDIUM_RF: 48, MEDIUM_IOT: 32}


class MediumError(ValueError):
    """A change the media of the topology do not allow. ``reason`` names the rule (one of the
    keys of ``MEDIUM_ERRORS``), ``fields`` fill its text."""

    def __init__(self, reason: str, **fields: str) -> None:
        self.reason = reason
        self.fields = fields
        super().__init__(MEDIUM_ERRORS[reason].format(**fields))


# The English text of each rule a ``MediumError`` names; the editor shows them translated.
MEDIUM_ERRORS: dict[str, str] = {
    "segment_has_devices": (
        "The medium of a segment can only change while it holds no device."
    ),
    "coupler_below": (
        "As long as a coupler exists at a sub line or sub segment, a change of the medium "
        "type is not possible."
    ),
    "main_line_rf": "A main line of a project cannot have medium type RF.",
    "backbone_ip_or_tp": (
        "The operation could not be executed, because as medium type of the backbone only IP "
        "and TP are allowed."
    ),
    "sub_segment_ip_or_pl": "A sub-segment of KNX medium type IP or PL is not allowed.",
    "sub_segment_iot": (
        "The medium type of the segment '{segment}' cannot be changed to IoT, because its "
        "parent line '{line}' is not of medium type IP."
    ),
    "main_line_pl_below": (
        "The medium type of the main line '{line}' can no longer be changed to PL, because it "
        "already contains lines or segments that are not allowed in PL main lines."
    ),
    "main_line_iot_backbone": (
        "The medium type of the main line '{line}' cannot be changed to IoT, because the "
        "backbone line '{backbone}' is not of medium type IP."
    ),
    "main_line_iot_below": (
        "The medium type of the main line '{line}' can no longer be changed to IoT, because it "
        "already contains lines or segments that are not allowed in IoT main lines."
    ),
    "iot_below": (
        "The medium type of the line '{line}' cannot be changed, because it already contains "
        "lines or segments that require an IoT or IP main line."
    ),
    "rf_no_sub_segments": (
        "A sub-segment is not allowed below a main segment of KNX medium type RF."
    ),
    "pl_no_sub_segments": (
        "A sub-segment is not allowed below a main segment of KNX medium type PL."
    ),
    "iot_no_sub_segments": (
        "A sub-segment of KNX medium type IoT is not allowed below a main segment of the same "
        "type."
    ),
    "rf_main_line": (
        "The operation could not be executed, because a RF main line '{line}' requires all "
        "other lines in the area to be RF lines as well."
    ),
    "pl_main_line": (
        "The operation could not be executed, because a PL main line '{line}' requires all "
        "other lines in the area to be PL lines as well."
    ),
    "iot_main_line": (
        "The operation could not be executed, because a IoT main line '{line}' requires all "
        "other lines in the area to be IoT lines as well."
    ),
    "sub_line_iot": (
        "The medium type of the line '{line}' cannot be changed to IoT, because its main line "
        "'{main}' is not of medium type IP."
    ),
    "sub_line_iot_below": (
        "The medium type of the line '{line}' can no longer be changed to IoT, because it "
        "already contains segments that are not allowed in IoT lines."
    ),
    "ip_backbone_routers": (
        "The operation could not be executed, because the line is the IP backbone line and the "
        "project contains KNXnet/IP routers. KNXnet/IP routers require an IP backbone line."
    ),
    "ip_main_line_routers": (
        "The operation could not be executed, because the line is the IP main line and the "
        "project contains KNXnet/IP routers. KNXnet/IP routers require an IP main line."
    ),
    "ip_backbone_ip_lines": (
        "The operation could not be executed, because the line is the IP backbone line and the "
        "project contains IoT or IP lines."
    ),
    "ip_needs_ip_main_line": (
        "The operation could not be executed, because the medium type of the line can only be "
        "set to IP if an IP main line exists."
    ),
    "ip_needs_ip_backbone": (
        "The operation could not be executed, because the medium type of the line can only be "
        "set to IP if an IP backbone line exists."
    ),
    "below_tp": (
        "The operation could not be executed, because below a TP main segment only segments "
        "of KNX medium type TP, RF or PL can exist."
    ),
    "below_ip": (
        "Below a main segment of KNX medium type IP only segments of medium type TP or RF can "
        "exist."
    ),
    "pl_sub_segment": (
        "A sub-segment of KNX medium type PL is not allowed below a main segment."
    ),
    "too_many_segments": "The maximum number of segments has been reached.",
    "main_segment": "The main segment cannot be deleted.",
    "device_line_medium": "Device '{device}' is incompatible with line's medium type.",
    "device_segment_medium": "Device '{device}' is incompatible with segment's medium type.",
    "unknown_medium": "Unknown medium '{medium}'.",
}


def medium_name(medium: str) -> str:
    """The short name of a medium ("TP"); an unknown id as it is."""
    return MEDIUM_NAMES.get(medium, medium)


@dataclass(frozen=True)
class SegmentView:
    """A segment as the rules see it: its medium, name, domain address and devices (their ids
    and device numbers)."""

    id: int
    number: int
    medium: str
    name: str = ""
    domain_address: int | None = None
    devices: tuple[tuple[int, int | None], ...] = ()


@dataclass(frozen=True)
class LineView:
    """A line as the rules see it: its area and address, its name and its segments, the main
    segment first."""

    id: int
    area: int
    address: int
    name: str
    segments: tuple[SegmentView, ...]

    @property
    def main(self) -> SegmentView:
        return self.segments[0]

    @property
    def sub_segments(self) -> tuple[SegmentView, ...]:
        return self.segments[1:]

    @property
    def device_ids(self) -> set[int]:
        return {device for s in self.segments for device, _number in s.devices}


def sub_segment_media(main: str) -> tuple[str, ...]:
    """The media a new sub-segment of a line whose main segment has ``main`` can have: TP or
    RF below TP or IP; none below RF, PL or IoT."""
    return (MEDIUM_TP, MEDIUM_RF) if main in (MEDIUM_TP, MEDIUM_IP) else ()


def check_new_sub_segment(main: str, medium: str) -> None:
    """Raise a ``MediumError`` unless a line whose main segment has ``main`` can get a new
    sub-segment of ``medium``."""
    if main == MEDIUM_RF:
        raise MediumError("rf_no_sub_segments")
    if main == MEDIUM_PL:
        raise MediumError("pl_no_sub_segments")
    if main == MEDIUM_IOT:
        raise MediumError("iot_no_sub_segments")
    if medium in sub_segment_media(main):
        return
    if medium == MEDIUM_PL:
        raise MediumError("pl_sub_segment")
    raise MediumError("below_tp" if main == MEDIUM_TP else "below_ip")


def check_medium_change(
    lines: Sequence[LineView],
    segment_id: int,
    medium: str,
    couplers: Collection[int] = (),
    ip_routers: Collection[int] = (),
) -> None:
    """Raise a ``MediumError`` unless the segment ``segment_id`` of the topology ``lines`` may
    change to ``medium``. ``couplers`` are the devices whose hardware is a coupler,
    ``ip_routers`` those whose application is a KNXnet/IP router (counted at device number 0)."""
    line = next(ln for ln in lines if any(s.id == segment_id for s in ln.segments))
    segment = next(s for s in line.segments if s.id == segment_id)
    current = segment.medium
    area_lines = [ln for ln in lines if ln.area == line.area]
    area_main = next((ln for ln in area_lines if ln.address == 0), None)
    backbone = next((ln for ln in lines if ln.area == 0 and ln.address == 0), None)
    sub_lines = [ln for ln in area_lines if ln.address != 0]
    is_main = segment.number == line.main.number
    is_main_line = line.address == 0
    is_backbone = is_main_line and line.area == 0

    if segment.devices:
        raise MediumError("segment_has_devices")
    if is_main:
        _check_couplers(lines, line, sub_lines, is_main_line, is_backbone, couplers)
        if is_main_line and medium == MEDIUM_RF:
            raise MediumError("main_line_rf")

    if not is_main:
        if medium in (MEDIUM_PL, MEDIUM_IP):
            raise MediumError("sub_segment_ip_or_pl")
        if medium == MEDIUM_IOT and line.main.medium not in (MEDIUM_IP, MEDIUM_IOT):
            raise MediumError("sub_segment_iot", segment=line.main.name, line=line.name)
    elif is_backbone:
        if medium in (MEDIUM_PL, MEDIUM_RF, MEDIUM_IOT):
            raise MediumError("backbone_ip_or_tp")
    elif is_main_line:
        _check_main_line(line, sub_lines, backbone, medium)
    else:
        _check_sub_line(line, area_main, medium)

    _check_ip(
        lines,
        line,
        segment,
        area_lines,
        area_main,
        backbone,
        current,
        medium,
        ip_routers,
    )


def _check_couplers(
    lines: Sequence[LineView],
    line: LineView,
    sub_lines: list[LineView],
    is_main_line: bool,
    is_backbone: bool,
    couplers: Collection[int],
) -> None:
    def has_coupler(segments: Iterable[SegmentView]) -> bool:
        return any(d in couplers for s in segments for d, _n in s.devices)

    if has_coupler(line.sub_segments):
        raise MediumError("coupler_below")
    if is_backbone:
        if has_coupler(
            s for ln in lines if ln.address == 0 or ln.area == 0 for s in ln.segments
        ):
            raise MediumError("coupler_below")
    elif is_main_line and has_coupler(ln.main for ln in sub_lines):
        raise MediumError("coupler_below")


def _check_main_line(
    line: LineView, sub_lines: list[LineView], backbone: LineView | None, medium: str
) -> None:
    def below(allowed: str) -> bool:
        return bool(line.sub_segments) or any(
            ln.main.medium != allowed for ln in sub_lines
        )

    if medium == MEDIUM_PL:
        if below(MEDIUM_PL):
            raise MediumError("main_line_pl_below", line=line.name)
    elif medium == MEDIUM_IOT:
        if backbone is not None and backbone.main.medium != MEDIUM_IP:
            raise MediumError(
                "main_line_iot_backbone", line=line.name, backbone=backbone.name
            )
        if below(MEDIUM_IOT):
            raise MediumError("main_line_iot_below", line=line.name)
        return
    if medium == MEDIUM_IP:
        return
    if any(s.medium == MEDIUM_IOT for s in line.sub_segments) or any(
        ln.main.medium == MEDIUM_IOT for ln in sub_lines
    ):
        raise MediumError("iot_below", line=line.name)


def _check_sub_line(line: LineView, area_main: LineView | None, medium: str) -> None:
    main_medium = area_main.main.medium if area_main is not None else None
    main_text = (
        f"{area_main.area}.{area_main.address} {area_main.name}".strip()
        if area_main is not None
        else ""
    )
    if medium == MEDIUM_RF and line.sub_segments:
        raise MediumError("rf_no_sub_segments")
    if medium != MEDIUM_RF and main_medium == MEDIUM_RF:
        raise MediumError("rf_main_line", line=main_text)
    if medium == MEDIUM_PL and line.sub_segments:
        raise MediumError("pl_no_sub_segments")
    if medium != MEDIUM_PL and main_medium == MEDIUM_PL:
        raise MediumError("pl_main_line", line=main_text)
    if medium != MEDIUM_IOT and main_medium == MEDIUM_IOT:
        raise MediumError("iot_main_line", line=main_text)
    if (
        medium == MEDIUM_IOT
        and main_medium is not None
        and main_medium not in (MEDIUM_IP, MEDIUM_IOT)
    ):
        raise MediumError(
            "sub_line_iot",
            line=line.name,
            main=area_main.name if area_main is not None else "",
        )
    if medium != MEDIUM_IOT and any(s.medium == MEDIUM_IOT for s in line.sub_segments):
        raise MediumError("iot_below", line=line.name)
    if medium == MEDIUM_IOT and line.sub_segments:
        raise MediumError("sub_line_iot_below", line=line.name)


def _check_ip(
    lines: Sequence[LineView],
    line: LineView,
    segment: SegmentView,
    area_lines: list[LineView],
    area_main: LineView | None,
    backbone: LineView | None,
    current: str,
    medium: str,
    ip_routers: Collection[int],
) -> None:
    """The rules of IP and IoT lines and of KNXnet/IP routers (at device number 0)."""

    def has_router(devices: Iterable[tuple[int, int | None]]) -> bool:
        return any(d in ip_routers and number == 0 for d, number in devices)

    is_main = segment.number == line.main.number
    is_main_line = line.address == 0
    ip_or_iot = (MEDIUM_IP, MEDIUM_IOT)
    if is_main_line and line.area == 0 and is_main and current == MEDIUM_IP:
        if has_router(d for ln in lines for s in ln.segments for d in s.devices):
            raise MediumError("ip_backbone_routers")
        if any(
            s.medium in ip_or_iot
            for ln in lines
            for s in ln.segments
            if s.id != segment.id
        ):
            raise MediumError("ip_backbone_ip_lines")
    if area_main is not None and is_main_line and is_main and current == MEDIUM_IP:
        for other in area_lines:
            for s in other.segments:
                if (
                    medium not in ip_or_iot
                    and other.address != 0
                    and s.medium in ip_or_iot
                ):
                    raise MediumError("ip_needs_ip_main_line")
                if has_router(d for seg in other.segments for d in seg.devices):
                    raise MediumError("ip_main_line_routers")
    if medium != MEDIUM_IP:
        return
    if not is_main_line:
        if area_main is not None and area_main.main.medium != MEDIUM_IP:
            raise MediumError(
                "ip_needs_ip_backbone" if line.area == 0 else "ip_needs_ip_main_line"
            )
    elif (
        line.area != 0
        and current not in ip_or_iot
        and backbone is not None
        and backbone.main.medium != MEDIUM_IP
    ):
        raise MediumError("ip_needs_ip_backbone")


def domain_addresses(
    lines: Sequence[LineView], segment_id: int, medium: str
) -> dict[int, int | None]:
    """The domain addresses that change when the segment ``segment_id`` becomes ``medium``,
    by segment id. Without a domain address for the medium the segment has none. A segment of
    a sub line takes the one of its area's main line when that has the same medium; the main
    segment of a main line gets a new one that every segment of the area with that medium
    takes as well; any other segment gets a new one. A main line that changes from IoT to IP
    gives each IoT line of its area a new one of its own."""
    line = next(ln for ln in lines if any(s.id == segment_id for s in ln.segments))
    segment = next(s for s in line.segments if s.id == segment_id)
    area_lines = [ln for ln in lines if ln.area == line.area]
    area_main = next((ln for ln in area_lines if ln.address == 0), None)
    used = {
        s.domain_address
        for ln in lines
        for s in ln.segments
        if s.domain_address is not None and s.id != segment_id
    }
    is_main_of_main_line = line.address == 0 and segment.number == line.main.number
    changes: dict[int, int | None] = {}
    if medium not in _DOMAIN_BITS:
        changes[segment_id] = None
    elif (
        line.address != 0 and area_main is not None and area_main.main.medium == medium
    ):
        changes[segment_id] = area_main.main.domain_address
    else:
        address = new_domain_address(medium, used)
        changes[segment_id] = address
        if is_main_of_main_line:
            for ln in area_lines:
                for s in ln.segments:
                    if s.medium == medium and s.id != segment_id:
                        changes[s.id] = address
    if medium == MEDIUM_IP and segment.medium == MEDIUM_IOT and is_main_of_main_line:
        taken = used | {a for a in changes.values() if a is not None}
        for ln in area_lines:
            if ln.address != 0 and ln.main.medium == MEDIUM_IOT:
                address = new_domain_address(MEDIUM_IOT, taken)
                changes[ln.main.id] = address
                if address is not None:
                    taken.add(address)
    return changes


def new_domain_address(medium: str, used: Iterable[int]) -> int | None:
    """A domain address for a segment that becomes ``medium``, none of ``used``: the lowest free
    one for PL, a random one for RF and IoT; None for a medium without one (or no PL address
    left)."""
    taken = set(used)
    if medium == MEDIUM_PL:
        return next((a for a in range(1, 256) if a not in taken), None)
    bits = _DOMAIN_BITS.get(medium)
    if bits is None:
        return None
    while True:
        address = secrets.randbits(bits)
        if address and address not in taken:
            return address


def device_fits(medium: str, device_media: Iterable[str]) -> bool:
    """Whether a device whose hardware supports ``device_media`` can be on a ``medium`` segment."""
    return medium in set(device_media)
