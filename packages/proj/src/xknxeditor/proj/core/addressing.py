"""Group addresses: one 16-bit integer rendered three ways.

A KNX group address is just a 16-bit value; :class:`GroupAddressStyle` picks how it splits into a
string and how deep the named ``GroupRange`` tree goes. The integer is canonical and
style-independent — format/parse/range are pure functions of (value, style).
"""

from enum import StrEnum


class GroupAddressStyle(StrEnum):
    FREE = "Free"
    TWO_LEVEL = "TwoLevel"
    THREE_LEVEL = "ThreeLevel"


def format_ga(address: int, style: GroupAddressStyle) -> str:
    """Format a group-address value to a string for ``style``."""
    if style is GroupAddressStyle.FREE:
        return str(address)
    if style is GroupAddressStyle.TWO_LEVEL:
        return f"{address >> 11}/{address & 0x7FF}"
    return f"{address >> 11}/{(address >> 8) & 0x7}/{address & 0xFF}"


def parse_ga(text: str, style: GroupAddressStyle) -> int:
    """Turn a ``style`` group-address string back into its 16-bit value."""
    parts = [int(p) for p in text.split("/")]
    if style is GroupAddressStyle.FREE:
        return parts[0]
    if style is GroupAddressStyle.TWO_LEVEL:
        main, sub = parts
        return (main << 11) | sub
    main, middle, sub = parts
    return (main << 11) | (middle << 8) | sub


def format_ia(area: int, line: int, device: int) -> str:
    """Join topology parts into an ``area.line.device`` string (e.g. ``1.1.5``)."""
    return f"{area}.{line}.{device}"


def parse_ia(text: str) -> tuple[int, int, int]:
    """Split an ``area.line.device`` string into its three numbers."""
    area, line, device = text.split(".")
    return int(area), int(line), int(device)


def format_group_range(range_start: int, style: GroupAddressStyle, level: int) -> str:
    """The number shown for a ``GroupRange`` folder: its main group (``level`` 0) or main/middle
    group (``level`` 1), derived from ``range_start``. Empty for Free, which has no numbered
    folders."""
    if style is GroupAddressStyle.FREE:
        return ""
    main = range_start >> 11
    if style is GroupAddressStyle.TWO_LEVEL or level <= 0:
        return str(main)
    middle = (range_start >> 8) & 0x7
    return f"{main}/{middle}"


def ranges_for(address: int, style: GroupAddressStyle) -> list[tuple[int, int, str]]:
    """The ``GroupRange`` chain (top → leaf) ``address`` belongs under in this style.

    Entries are ``(range_start, range_end, name)``: ThreeLevel gives main + middle, TwoLevel a lone
    main, Free one catch-all (a group address always sits under some range). Since address 0 is
    reserved, the first range begins at 1."""
    main = address >> 11
    if style is GroupAddressStyle.FREE:
        return [(1, 0xFFFF, "Group addresses")]
    if style is GroupAddressStyle.TWO_LEVEL:
        main_base = main << 11
        return [(max(1, main_base), main_base + 0x7FF, f"Main group {main}")]
    middle = (address >> 8) & 0x7
    main_base = main << 11
    middle_base = main_base + (middle << 8)
    return [
        (max(1, main_base), main_base + 0x7FF, f"Main group {main}"),
        (max(1, middle_base), middle_base + 0xFF, f"Middle group {main}/{middle}"),
    ]
