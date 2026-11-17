"""Short texts naming what an edit changed, recorded with the edit so the history can name it.

A text is made when the edit is made, from what the project holds then: the history still names
an element after it is renamed or removed. It holds only addresses and names, no wording, so the
history words it in the language it is shown in.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga
from xknxeditor.proj.models import (
    Area,
    Base,
    ComObjectLink,
    Device,
    Function,
    FunctionGroupAddress,
    GroupAddress,
    GroupRange,
    Line,
    Project,
    Segment,
    Space,
    Trade,
    TradeDevice,
)


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def _address(segment: Segment | None, number: int | None) -> str:
    if segment is None:
        return ""
    line = segment.line
    return f"{line.area.address}.{line.address}.{'-' if number is None else number}"


def address_on(session: Session, segment_id: int | None, number: int | None) -> str:
    """The individual address a device gets with the device number ``number`` on the segment
    ``segment_id`` ("1.1.5", "1.1.-" without a number, "" on no line)."""
    segment = None if segment_id is None else session.get(Segment, segment_id)
    return _address(segment, number)


def device_text(device: Device) -> str:
    """The device as the history names it: its individual address ("1.1.-" without a device
    number, none on no line) and its name, else its product's."""
    return _join(
        _address(device.segment, device.address), device.name or device.product_name
    )


def _style(session: Session) -> GroupAddressStyle:
    style = session.query(Project.group_address_style).limit(1).scalar()
    return GroupAddressStyle(style or GroupAddressStyle.THREE_LEVEL)


def group_address_text(session: Session, group_address: GroupAddress) -> str:
    """The group address as the history names it: its address and its name."""
    return _join(format_ga(group_address.address, _style(session)), group_address.name)


def _group_range_text(session: Session, group_range: GroupRange) -> str:
    style = _style(session)
    printed = format_ga(group_range.range_start, style)
    if style is GroupAddressStyle.THREE_LEVEL:
        # A main group (2048 addresses) as "1/", a middle group as "1/2/".
        levels = 1 if group_range.range_end - group_range.range_start >= 0x7FF else 2
        printed = "/".join(printed.split("/")[:levels]) + "/"
    elif style is GroupAddressStyle.TWO_LEVEL:
        printed = printed.split("/")[0] + "/"
    else:
        printed = f"{group_range.range_start}-{group_range.range_end}"
    return _join(printed, group_range.name)


def removed_texts(session: Session, removed: Base) -> tuple[str, str]:
    """What a removal names: the removed element, and for a link of two elements the second
    one (the group address of a function, the device of a trade, the group address of a group
    object's link); ``""`` where there is nothing to name."""
    if isinstance(removed, Device):
        return device_text(removed), ""
    if isinstance(removed, Area):
        return _join(str(removed.address), removed.name), ""
    if isinstance(removed, Line):
        return _join(f"{removed.area.address}.{removed.address}", removed.name), ""
    if isinstance(removed, Segment):
        line = removed.line
        return _join(f"{line.area.address}.{line.address}", removed.name), ""
    if isinstance(removed, GroupAddress):
        return group_address_text(session, removed), ""
    if isinstance(removed, GroupRange):
        return _group_range_text(session, removed), ""
    if isinstance(removed, Space | Function | Trade):
        return removed.name, ""
    if isinstance(removed, FunctionGroupAddress):
        return removed.function.name, group_address_text(session, removed.group_address)
    if isinstance(removed, TradeDevice):
        return removed.trade.name, device_text(removed.device)
    if isinstance(removed, ComObjectLink):
        return "", group_address_text(session, removed.group_address)
    return "", ""


def element_text(session: Session, element: Base) -> str:
    """An element as the history names it (a device, area, line, segment, group address or
    range by its address and name; a building part, function or trade by its name)."""
    return removed_texts(session, element)[0]
