"""Decode group communication tables read off a device back into project data.

This is the inverse of :mod:`xknxeditor.download.tables` (memory-mapped BCU/System 7,
1-octet counts) and :mod:`xknxeditor.download.tables_systemb` (System B, 2-octet
counts). The byte layouts are fixed and documented there; here they are parsed
back into group addresses, links and per-object flags. All formats are the ones
validated byte-exact against real hardware.

The association tables carry no explicit "sending" flag - KNX derives the sending
group address by convention. Since the encoders order the sending association of
each group object first, this module marks the first association seen for a group
object number as the sending one; the rest are receiving.
"""

from __future__ import annotations

from dataclasses import dataclass

# The flag/size bit layout lives in the encoder module; use its public inverse
# helpers so the decode never drifts from the forward direction.
from xknxeditor.download.tables import object_size_name, priority_from_bits


@dataclass(frozen=True, slots=True)
class DecodedGroupObject:
    """A group object's programmed flags and size, decoded from a table record."""

    number: int
    priority: str
    communication: bool
    read: bool
    write: bool
    transmit: bool
    update: bool
    # ``None`` from a memory-mapped record, whose bit 5 is the segment selector.
    read_on_init: bool | None
    size_code: int
    object_size: str | None


@dataclass(frozen=True, slots=True)
class DecodedLink:
    """One decoded association: a group address linked to a group object."""

    group_address: int
    group_object_number: int
    sending: bool


class TableDecodeError(ValueError):
    """A table's bytes do not match the expected layout."""


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def decode_flag_byte(value: int) -> tuple[str, bool, bool, bool, bool, bool, bool]:
    """Decode a communication flag byte into ``(priority, C, R, W, T, U, roi)``.

    Inverse of :func:`xknxeditor.download.tables.com_object_flag_byte`.
    """
    return (
        priority_from_bits(value),
        bool(value & 0x04),
        bool(value & 0x08),
        bool(value & 0x10),
        bool(value & 0x40),
        bool(value & 0x80),
        bool(value & 0x20),
    )


def _group_object(
    number: int, flags: int, size: int, *, system_b: bool
) -> DecodedGroupObject:
    priority, communication, read, write, transmit, update, read_on_init = (
        decode_flag_byte(flags)
    )
    size_code = size & 0x3F
    return DecodedGroupObject(
        number=number,
        priority=priority,
        communication=communication,
        read=read,
        write=write,
        transmit=transmit,
        update=update,
        read_on_init=read_on_init if system_b else None,
        size_code=size_code,
        object_size=object_size_name(size_code),
    )


def _apply_sending(pairs: list[tuple[int, int]]) -> list[DecodedLink]:
    """Turn ``(group address, group object number)`` pairs into links.

    Marks the first association of each group object number (table order) as
    sending, matching the sending-first ordering the encoders write.
    """
    seen: set[int] = set()
    links: list[DecodedLink] = []
    for group_address, number in pairs:
        sending = number not in seen
        seen.add(number)
        links.append(
            DecodedLink(
                group_address=group_address,
                group_object_number=number,
                sending=sending,
            )
        )
    return links


# --- memory-mapped (BCU / System 7, 1-octet counts) -------------------------


def decode_group_address_table(data: bytes) -> tuple[int, list[int]]:
    """Decode a memory-mapped group address table.

    Layout ``[count:1][device address:2][group address:2]*``. Returns
    ``(device_address, group_addresses)`` in table order (the address at 1-based
    index ``i`` is ``group_addresses[i-1]``).
    """
    if len(data) < 1:
        raise TableDecodeError("empty group address table")
    count = data[0]
    if count < 1 or len(data) < 1 + count * 2:
        raise TableDecodeError(
            f"group address table too short for count {count}: {len(data)} octets"
        )
    device_address = _u16(data, 1)
    group_addresses = [_u16(data, 3 + 2 * i) for i in range(count - 1)]
    return device_address, group_addresses


def decode_association_table(
    data: bytes, group_addresses: list[int]
) -> list[DecodedLink]:
    """Decode a memory-mapped association table into links.

    Layout ``[count:1]`` then ``[group address index:1][group object number:1]``
    per entry, where index ``i`` references ``group_addresses[i-1]`` (index 0 is
    the device address). Entries referencing an out-of-range index are skipped.
    """
    if len(data) < 1:
        raise TableDecodeError("empty association table")
    count = data[0]
    if len(data) < 1 + count * 2:
        raise TableDecodeError(
            f"association table too short for count {count}: {len(data)} octets"
        )
    pairs: list[tuple[int, int]] = []
    for i in range(count):
        index = data[1 + 2 * i]
        number = data[2 + 2 * i]
        if 1 <= index <= len(group_addresses):
            pairs.append((group_addresses[index - 1], number))
    return _apply_sending(pairs)


def decode_com_object_table(
    data: bytes, *, header_size: int = 5, record_size: int = 4
) -> dict[int, DecodedGroupObject]:
    """Decode a memory-mapped com object table into ``{number: group object}``.

    Each record is ``[flags:1][size code:1][data pointer:2]`` at
    ``header_size + number * record_size``. Every record present in the segment is
    decoded; the caller selects the numbers it needs (linked / defined objects).
    """
    result: dict[int, DecodedGroupObject] = {}
    number = 0
    while header_size + number * record_size + 2 <= len(data):
        offset = header_size + number * record_size
        result[number] = _group_object(
            number, data[offset], data[offset + 1], system_b=False
        )
        number += 1
    return result


# --- System B (2-octet counts, relative memory) -----------------------------


def decode_group_address_table_b(data: bytes) -> list[int]:
    """Decode a System B group address table.

    Layout ``[count:2][group address:2]*`` with no leading device address. The
    address at 1-based index ``i`` is ``group_addresses[i-1]``.
    """
    if len(data) < 2:
        raise TableDecodeError("empty System B group address table")
    count = _u16(data, 0)
    if len(data) < 2 + count * 2:
        raise TableDecodeError(
            f"System B group address table too short for count {count}: "
            f"{len(data)} octets"
        )
    return [_u16(data, 2 + 2 * i) for i in range(count)]


def decode_association_table_b(
    data: bytes, group_addresses: list[int]
) -> list[DecodedLink]:
    """Decode a System B association table into links.

    Layout ``[count:2]`` then per entry ``[group address index + 1][group object
    number]`` - narrow (1 octet each) or wide (2 octets each), detected from the
    byte length. The stored reference is one-based into the address table (the
    encoder writes ``sorted position + 1``), so ``r`` maps to
    ``group_addresses[r-1]``.
    """
    if len(data) < 2:
        raise TableDecodeError("empty System B association table")
    count = _u16(data, 0)
    body = len(data) - 2
    if count and body == count * 2:
        wide = False
    elif count and body == count * 4:
        wide = True
    elif count == 0:
        return []
    else:
        raise TableDecodeError(
            f"System B association table length {len(data)} matches neither narrow "
            f"nor wide layout for count {count}"
        )
    pairs: list[tuple[int, int]] = []
    step = 4 if wide else 2
    for i in range(count):
        offset = 2 + i * step
        if wide:
            reference = _u16(data, offset)
            number = _u16(data, offset + 2)
        else:
            reference = data[offset]
            number = data[offset + 1]
        position = reference - 1
        if 0 <= position < len(group_addresses):
            pairs.append((group_addresses[position], number))
    return _apply_sending(pairs)


def decode_group_object_table_b(data: bytes) -> dict[int, DecodedGroupObject]:
    """Decode a System B group object table into ``{number: group object}``.

    Layout ``[count:2 = highest number]`` then a ``[flags:1][size code:1]`` record
    for every number ``1..highest``. ``00 00`` slots (unlinked) are omitted.
    """
    if len(data) < 2:
        raise TableDecodeError("empty System B group object table")
    highest = _u16(data, 0)
    return decode_group_object_records_b(1, data[2 : 2 + highest * 2])


def decode_group_object_records_b(
    first_number: int, data: bytes
) -> dict[int, DecodedGroupObject]:
    """Decode consecutive System B ``[flags:1][size code:1]`` records from ``first_number`` on.

    ``00 00`` slots (unlinked) are omitted.
    """
    result: dict[int, DecodedGroupObject] = {}
    for position in range(len(data) // 2):
        flags = data[position * 2]
        size = data[position * 2 + 1]
        if flags == 0 and size == 0:
            continue
        number = first_number + position
        result[number] = _group_object(number, flags, size, system_b=True)
    return result
