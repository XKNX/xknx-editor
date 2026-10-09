"""Build the group communication tables for the System B device model.

Same three tables as :mod:`.tables` (KNX Standard v3.0.0, Chapter 3/5/1
"Resources", sections 4.16 "Group Address Table (GrAT)", 4.17 "Group Object
Association Table (GrOAT)" and 4.18 "Group Object Table"), but in the System B
realisation - the mask 57B0h configuration procedure (Chapter 3/5/3
"Configuration Procedures", section 3.9.3) - which holds them in object relative
memory with a 2-octet count layout (validated byte-exact against real hardware):

- the group address table is ``[count:2][group address:2]*`` (big-endian), the
  count being the number of addresses. Unlike the memory-mapped variant it does
  *not* lead with the device's own individual address;
- the association table is ``[count:2]`` followed by one entry per link, each
  ``[group address index + 1 : 1][group object number : 1]`` (format 0) or with
  two octets per field (format 1, section 4.17.5.2.5). The device tells its format
  through the PID_TABLE description; :func:`association_table_wide` is the rule
  when it is not known;
- the group object table is ``[count:2 = highest object number]`` followed by one
  ``[flags:1][size code:1]`` record per object number ``1..highest``. Only linked
  objects carry their flags and size; every other slot stays ``00 00``.

All three live in relative memory (addressed through the object's table
reference), not at fixed addresses. See :mod:`.tables` for the memory-mapped
model and the shared :class:`~.tables.Association` and flag/size helpers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .errors import ImageError
from .tables import Association


def build_group_address_table_b(group_addresses: Sequence[int]) -> bytes:
    """Return the System B group address table bytes.

    Layout ``[count:2][group address:2]*`` (big-endian); ``group_addresses`` is
    sorted ascending and de-duplicated. The count is the number of addresses (no
    leading device-address entry). The resulting order defines the 1-based index
    the association table references.
    """
    addresses = sorted(set(group_addresses))
    if len(addresses) > 0xFFFF:
        raise ImageError(f"group address table has too many entries: {len(addresses)}")
    out = bytearray(_u16(len(addresses)))
    for address in addresses:
        out += _u16(address)
    return bytes(out)


def group_address_index_b(group_addresses: Sequence[int]) -> dict[int, int]:
    """Map each group address to its 1-based index in the System B address table."""
    return {address: i + 1 for i, address in enumerate(sorted(set(group_addresses)))}


def association_table_wide(address_count: int, highest_object_number: int) -> bool:
    """Whether the association table needs two octets per field (format 1) because a
    group address index or a group object number does not fit in one octet."""
    return address_count + 1 > 0xFF or highest_object_number > 0xFF


def build_association_table_b(
    associations: Sequence[Association], *, wide: bool = False
) -> bytes:
    """Return the System B association table bytes.

    Layout ``[count:2]`` then one entry per association. A narrow entry is
    ``[group address index + 1 : 1][group object number : 1]``; a wide entry uses
    two big-endian octets per field. Ordering matches the memory-mapped model:
    sending associations first, then by group object number, then by group
    address index.
    """
    ordered = sorted(
        associations,
        key=lambda a: (not a.sending, a.group_object_number, a.group_address_index),
    )
    if len(ordered) > 0xFFFF:
        raise ImageError(f"association table has too many entries: {len(ordered)}")
    out = bytearray(_u16(len(ordered)))
    for association in ordered:
        reference = association.group_address_index + 1
        number = association.group_object_number
        if wide:
            out += _u16(reference)
            out += _u16(number)
        else:
            _require_octet(reference, "group address index")
            _require_octet(number, "group object number")
            out.append(reference)
            out.append(number)
    return bytes(out)


def build_group_object_table_b(
    descriptors: Mapping[int, tuple[int, int]], highest_number: int
) -> bytes:
    """Return the System B group object table bytes.

    Layout ``[count:2 = highest_number]`` then one ``[flags:1][size code:1]``
    record for every object number ``1..highest_number``. ``descriptors`` maps a
    linked object's number to its ``(flags, size code)``; numbers absent from it
    (unlinked or undefined) stay ``00 00``; only linked objects carry flags, per
    the Group Object Table definition (Chapter 3/5/1 Resources, section 4.18).
    """
    if not 0 <= highest_number <= 0xFFFF:
        raise ImageError(
            f"group object count {highest_number} does not fit in two octets"
        )
    out = bytearray(_u16(highest_number))
    for number in range(1, highest_number + 1):
        descriptor = descriptors.get(number)
        if descriptor is None:
            out += b"\x00\x00"
            continue
        flags, size = descriptor
        _require_octet(flags, "group object flags")
        _require_octet(size, "group object size code")
        out.append(flags)
        out.append(size)
    return bytes(out)


def _u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ImageError(f"value {value} does not fit in two octets")
    return value.to_bytes(2, "big")


def _require_octet(value: int, what: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ImageError(f"{what} {value} does not fit in one octet")
