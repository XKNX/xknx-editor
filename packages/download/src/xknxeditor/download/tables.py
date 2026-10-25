"""Build the group communication tables a device links group addresses through.

The formats follow KNX Standard v3.0.0, Chapter 3/5/1 "Resources": the Group
Address Table (section 4.16 "Group Address Table (GrAT)"), the Group Object
Association Table (section 4.17 "Group Object Association Table (GrOAT)") and the
Group Object Table (section 4.18). This module produces the Realisation Type
using a 1-octet count with the device's own individual address leading the group
address table:

- the group address table lists the addresses the device sends and receives on,
  led by the device's own individual address;
- the association table maps each configured link to a ``(group address, group
  object)`` pair, referencing the group address by its index in the address table.

These formats are validated byte-exact against real hardware; the 2-octet-count
System B realisation of the same tables is produced by :mod:`.tables_systemb`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .errors import ImageError


@dataclass(frozen=True, slots=True)
class Association:
    """One link: a group address (by address-table index) to a group object."""

    group_address_index: int
    group_object_number: int
    sending: bool = True


def build_group_address_table(
    device_address: int, group_addresses: Sequence[int]
) -> bytes:
    """Return the group address table bytes.

    Layout: ``[count:1][device address:2][group address:2]*`` (big-endian). The
    count includes the leading device-address entry. ``group_addresses`` is sorted
    ascending and de-duplicated; the resulting order defines the 1-based index the
    association table references (index 0 is the device address).
    """
    addresses = sorted(set(group_addresses))
    count = 1 + len(addresses)
    if count > 0xFF:
        raise ImageError(f"group address table has too many entries: {count}")
    out = bytearray()
    out.append(count)
    out += _u16(device_address)
    for address in addresses:
        out += _u16(address)
    return bytes(out)


def group_address_index(group_addresses: Sequence[int]) -> dict[int, int]:
    """Map each group address to its 1-based index in the address table."""
    return {address: i + 1 for i, address in enumerate(sorted(set(group_addresses)))}


def build_association_table(associations: Sequence[Association]) -> bytes:
    """Return the association table bytes.

    Layout: ``[count:1]`` then one ``[group address index:1][group object
    number:1]`` entry per association. Ordering (per the Group Object Association Table):
    sending associations first, then by group object number, then by group
    address index.
    """
    ordered = sorted(
        associations,
        key=lambda a: (
            not a.sending,
            a.group_object_number,
            a.group_address_index,
        ),
    )
    if len(ordered) > 0xFF:
        raise ImageError(f"association table has too many entries: {len(ordered)}")
    out = bytearray()
    out.append(len(ordered))
    for association in ordered:
        _require_octet(association.group_address_index, "group address index")
        _require_octet(association.group_object_number, "group object number")
        out.append(association.group_address_index)
        out.append(association.group_object_number)
    return bytes(out)


# --- com object table -------------------------------------------------------

# ComObjectSize value -> KNX size code (Chapter 3/5/1 Resources, Group Object Table).
_SIZE_CODE: dict[str, int] = {
    "1 Bit": 0,
    "2 Bit": 1,
    "3 Bit": 2,
    "4 Bit": 3,
    "5 Bit": 4,
    "6 Bit": 5,
    "7 Bit": 6,
    "1 Byte": 7,
    "2 Bytes": 8,
    "3 Bytes": 9,
    "4 Bytes": 10,
    "6 Bytes": 11,
    "8 Bytes": 12,
    "10 Bytes": 13,
    "14 Bytes": 14,
    "5 Bytes": 15,
    "7 Bytes": 16,
    "9 Bytes": 17,
    "11 Bytes": 18,
    "12 Bytes": 19,
    "13 Bytes": 20,
}

# Communication flag byte bit layout (bits 0-1 priority, then enables).
_PRIORITY_BITS: dict[str, int] = {"System": 0, "High": 1, "Alert": 2, "Low": 3}
# Bit 5 of the flag byte: "value read on initialisation" in a System B descriptor, but
# the segment selector of the value's data pointer in a memory-mapped record (BCU1,
# BCU2, M112; KNX Standard v3.0.0, 3/5/1 4.18.3.1.2.1). That one belongs to the product.
SEGMENT_SELECTOR_BIT = 0x20


def com_object_flag_byte(
    *,
    priority: str,
    communication: bool,
    read: bool,
    write: bool,
    transmit: bool,
    update: bool,
    read_on_init: bool,
) -> int:
    """Encode a com object's flags into the descriptor flag byte.

    ``read_on_init`` is bit 5 of a System B descriptor; a memory-mapped record keeps its
    own bit 5 (:data:`SEGMENT_SELECTOR_BIT`) instead.
    """
    value = _PRIORITY_BITS.get(priority, 3)
    if communication:
        value |= 0x04
    if read:
        value |= 0x08
    if write:
        value |= 0x10
    if read_on_init:
        value |= 0x20
    if transmit:
        value |= 0x40
    if update:
        value |= 0x80
    return value


def size_code(object_size: str) -> int:
    """Return the KNX size code for a ``ComObjectSize`` value string."""
    try:
        return _SIZE_CODE[object_size]
    except KeyError as exc:
        raise ImageError(
            f"com object size {object_size!r} is not implemented; known sizes are "
            f"{sorted(_SIZE_CODE)} (KNX Standard v3.0.0, 3/5/1 group object "
            f"descriptor). Please file a bug report with the product data so this "
            f"size can be added."
        ) from exc


# Inverse lookups, for decoding tables read back off a device.
_PRIORITY_NAME: dict[int, str] = {bits: name for name, bits in _PRIORITY_BITS.items()}
_SIZE_NAME: dict[int, str] = {code: name for name, code in _SIZE_CODE.items()}


def priority_from_bits(value: int) -> str:
    """Return the priority name for the low two bits of a flag byte (default Low)."""
    return _PRIORITY_NAME.get(value & 0x03, "Low")


def object_size_name(size_code_value: int) -> str | None:
    """Return the ``ComObjectSize`` string for a KNX size code, or ``None``."""
    return _SIZE_NAME.get(size_code_value)


@dataclass(frozen=True, slots=True)
class ComObjectDescriptor:
    """One com object's table entry: its number, flag byte and size code."""

    number: int
    flags: int
    size: int


def build_com_object_table(
    seed: bytes,
    descriptors: Sequence[ComObjectDescriptor],
    *,
    header_size: int = 5,
    record_size: int = 4,
) -> tuple[bytes, bytes]:
    """Overlay com object descriptors onto the segment seed.

    Each record is ``[flag byte][size code][data pointer:2]`` at
    ``header_size + number * record_size``. Only the flag and size bytes are
    written (from ``descriptors``); the header and the manufacturer's data
    pointers are kept from ``seed``. Returns ``(data, mask)`` where the mask marks
    the two written bytes of each descriptor, so a download touches nothing else.
    """
    data = bytearray(seed)
    mask = bytearray(len(seed))
    for descriptor in descriptors:
        offset = header_size + descriptor.number * record_size
        if offset + 2 > len(data):
            continue
        _require_octet(descriptor.flags, "com object flags")
        _require_octet(descriptor.size, "com object size code")
        # Read-modify-write per the Group Object Table descriptor: the flag byte keeps
        # bit 5 (the segment selector) from the seed, and the size byte keeps its
        # top two bits (the size code occupies only the low six bits).
        data[offset] = (data[offset] & SEGMENT_SELECTOR_BIT) | (
            descriptor.flags & ~SEGMENT_SELECTOR_BIT
        )
        data[offset + 1] = (data[offset + 1] & 0xC0) | (descriptor.size & 0x3F)
        mask[offset] = 0xFF
        mask[offset + 1] = 0xFF
    return bytes(data), bytes(mask)


def _u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ImageError(f"value {value} does not fit in two octets")
    return value.to_bytes(2, "big")


def _require_octet(value: int, what: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ImageError(f"{what} {value} does not fit in one octet")
