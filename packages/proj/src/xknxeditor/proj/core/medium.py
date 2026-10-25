"""The media of lines and segments: which a line and a segment can have, which sub-segments a
line allows, and the domain address an RF or PL segment needs.

The medium belongs to the segment; a line's medium is the one of its main segment (the segment
numbered 0). A segment holding devices keeps its medium.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

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
# The most segments a line can have, its main segment included.
MAX_SEGMENTS = 128
# A PL domain address is 1 to 255; an RF one has 48 bits.
_PL_DOMAIN_MAX = 255
_RF_DOMAIN_BITS = 48


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
    "no_sub_segments": (
        "A sub-segment is not allowed below a main segment of KNX medium type {medium}."
    ),
    "sub_segment_iot": (
        "The medium type of the segment '{segment}' cannot be changed to IoT, because its "
        "parent line '{line}' is not of medium type IP."
    ),
    "sub_segment_medium": "A sub-segment of KNX medium type {medium} is not allowed.",
    "line_has_sub_segments": (
        "The medium type of the line '{line}' can no longer be changed to {medium}, because it "
        "already contains segments that are not allowed below it."
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


def sub_segment_media(main: str) -> tuple[str, ...]:
    """The media a sub-segment of a line whose main segment has ``main`` can have: TP or RF
    below TP, and also IoT below IP; none below RF, PL or IoT."""
    if main == MEDIUM_TP:
        return (MEDIUM_TP, MEDIUM_RF)
    if main == MEDIUM_IP:
        return (MEDIUM_TP, MEDIUM_RF, MEDIUM_IOT)
    return ()


def check_sub_segment(main: str, medium: str, segment: str, line: str) -> None:
    """Raise a ``MediumError`` unless a line whose main segment has ``main`` can have a
    sub-segment (named ``segment``) of ``medium``."""
    allowed = sub_segment_media(main)
    if not allowed:
        raise MediumError("no_sub_segments", medium=medium_name(main))
    if medium in allowed:
        return
    if medium == MEDIUM_IOT:
        raise MediumError("sub_segment_iot", segment=segment, line=line)
    raise MediumError("sub_segment_medium", medium=medium_name(medium))


def needs_domain_address(medium: str) -> bool:
    return medium in (MEDIUM_RF, MEDIUM_PL)


def new_domain_address(medium: str, used: Iterable[int]) -> int | None:
    """A domain address for a segment that becomes ``medium``, none of ``used``: the lowest free
    one for PL, a random one for RF; None for a medium without one (or no PL address left)."""
    taken = set(used)
    if medium == MEDIUM_PL:
        return next((a for a in range(1, _PL_DOMAIN_MAX + 1) if a not in taken), None)
    if medium == MEDIUM_RF:
        while True:
            address = secrets.randbits(_RF_DOMAIN_BITS)
            if address and address not in taken:
                return address
    return None


def device_fits(medium: str, device_media: Iterable[str]) -> bool:
    """Whether a device whose hardware supports ``device_media`` can be on a ``medium`` segment."""
    return medium in set(device_media)
