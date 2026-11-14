"""KNX line/backbone coupler filter table: the routing bitmap, its computation, and read-back.

A coupler's filter table is a 65536-bit bitmap (8192 bytes) over the whole 16-bit group-address raw
value space (KNX Standard v3.0.0, coupler/router group-address filtering): a set bit means "route
this group address across the coupler", a clear bit means "block". It is indexed strictly by the
flat raw value, LSB-first within each byte::

    byte offset = ga >> 3          bit mask = 1 << (ga & 7)

Which group addresses a coupler must route (``routed_group_addresses``): a group address crosses a
coupler iff at least one device *behind* the coupler and at least one device *outside* it link that
address, plus any address flagged "unfiltered" (route regardless) and any manually added
pass-through address.

Short/older couplers store only the first N bytes their filter resource holds (BCU1 coupler MV-0900
= 3584 bytes = the first 28672 group addresses); System-B couplers hold the full 8192.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Bytes in a full filter table: 65536 group addresses, one bit each.
FILTER_TABLE_SIZE = 8192


def build_filter_table(
    group_addresses: Iterable[int], *, length: int = FILTER_TABLE_SIZE
) -> bytes:
    """Build a coupler filter-table bitmap routing exactly ``group_addresses`` (raw 16-bit values).

    ``length`` truncates the bitmap to the coupler's filter-table resource size (default full 8192).
    Addresses whose byte falls beyond the truncated range are dropped - they cannot be represented -
    matching genuine implementations, which store only ``min(resource_length, 8192)`` bytes.
    """
    if length < 0:
        raise ValueError(f"filter table length must be non-negative: {length}")
    # A filter table never exceeds the full 16-bit group-address space; genuine implementations store
    # min(resource_length, 8192) bytes. Clamp so an oversized resource length can never
    # produce a table that decodes group-address values above 0xFFFF.
    length = min(length, FILTER_TABLE_SIZE)
    table = bytearray(length)
    for ga in group_addresses:
        if not 0 <= ga <= 0xFFFF:
            raise ValueError(f"group address raw value out of range: {ga}")
        byte_index = ga >> 3
        if byte_index < length:
            table[byte_index] |= 1 << (ga & 7)
    return bytes(table)


def addresses_in_filter_table(table: bytes) -> list[int]:
    """The routed group-address raw values in ``table`` (inverse of :func:`build_filter_table`).

    Mirrors the genuine read-back, used to verify a coupler after programming. Group-address raw values
    are 16-bit, so an over-length external table is only decoded up to 0xFFFF."""
    count = min(8 * len(table), 0x10000)
    return [i for i in range(count) if table[i >> 3] & (1 << (i & 7))]


def is_coupler_address(individual_address: int) -> bool:
    """Whether a raw individual address is a coupler's (device octet 0 -> ``x.y.0``).

    A device gets a filter table iff its mask advertises one AND its address low byte is 0
    (line/area/backbone coupler) or it is a segment coupler. This is the address side of that rule;
    the mask capability and segment-coupler case are decided by the caller."""
    return (individual_address & 0xFF) == 0


def routed_group_addresses(
    inside: Iterable[int],
    outside: Iterable[int],
    *,
    unfiltered: Iterable[int] = (),
    additional: Iterable[int] = (),
    partner_required: bool = True,
) -> list[int]:
    """The set of group addresses a coupler must route.

    - ``inside``  - group addresses linked by devices *behind* the coupler.
    - ``outside`` - group addresses linked by devices *in front of* the coupler.
    - ``unfiltered`` - addresses flagged "route regardless" (pass through unconditionally).
    - ``additional`` - manually added pass-through addresses.

    A plain address is routed iff it is linked on *both* sides (it genuinely crosses the boundary);
    unfiltered and additional addresses are always routed. Returned sorted and de-duplicated.

    With ``partner_required=False`` every address linked behind the coupler is routed, partner in
    front of it or not, as ETS builds a line coupler's table. That keeps addresses whose only
    partners are not in the project (a visualisation or gateway reached over KNXnet/IP) passing;
    the default drops them.
    """
    inside_set = set(inside)
    crossing = inside_set & set(outside) if partner_required else inside_set
    return sorted(crossing | set(unfiltered) | set(additional))


def _is_behind(device_ia: int, coupler_ia: int) -> bool:
    """Whether ``device_ia`` sits behind the coupler at ``coupler_ia`` (a.l.0 / a.0.0).

    A line coupler (``a.l.0``, line != 0) is behind = same area and line. An area/backbone
    coupler (``a.0.0``, line == 0) is behind = same area (all its lines)."""
    c_area, c_line = (coupler_ia >> 12) & 0xF, (coupler_ia >> 8) & 0xF
    d_area, d_line = (device_ia >> 12) & 0xF, (device_ia >> 8) & 0xF
    if c_line == 0:  # area/backbone coupler: whole area is behind it
        return d_area == c_area
    return d_area == c_area and d_line == c_line  # line coupler: its line


def compute_coupler_filter_table(
    coupler_ia: int,
    device_group_addresses: Mapping[int, Iterable[int]],
    *,
    length: int = FILTER_TABLE_SIZE,
    unfiltered: Iterable[int] = (),
    additional: Iterable[int] = (),
    partner_required: bool = True,
) -> bytes:
    """Build the filter-table bitmap for the coupler at ``coupler_ia`` from the project topology.

    ``device_group_addresses`` maps each device's raw individual address to the raw group-address
    values it links (send or receive). A group address is routed across the coupler iff at least one
    device behind the coupler and at least one device in front of it link it (the standard
    filter-table computation), plus any ``unfiltered``/``additional`` pass-through addresses.
    ``length`` is the coupler's filter-table resource size (BCU1 = 3584, System B = 8192).
    ``partner_required=False`` routes every address linked behind the coupler (ETS's rule, see
    :func:`routed_group_addresses`).
    """
    inside: set[int] = set()
    outside: set[int] = set()
    for device_ia, gas in device_group_addresses.items():
        target = inside if _is_behind(device_ia, coupler_ia) else outside
        target.update(gas)
    routed = routed_group_addresses(
        inside,
        outside,
        unfiltered=unfiltered,
        additional=additional,
        partner_required=partner_required,
    )
    return build_filter_table(routed, length=length)
