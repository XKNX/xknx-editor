"""Build the group communication tables of a System 300 device (mask 0300, property based).

The device holds the three tables as interface object properties, not in memory:

- the group address table is ``PID_TABLE`` (23) of the address table object (type 1): one
  big-endian group address per element, sorted ascending, without a count element and without
  the device's own address;
- the association table is ``PID_TABLE`` of the association table object (type 2): one element
  per link, ``[group address index + 1 : 1][group object number : 1]``, or two octets per field
  when the property's elements are four octets wide (the device tells it through the
  description of the property). Sending links first, then by group object number, then by group
  address index;
- the group object table is ``PID_GRPOBJTABLE`` (51) of the group object table object (type 9):
  one 6-octet element per group object number ``1..highest``. Octet 1 is the object's flags
  with bit 5 cleared; the other octets are the application's parameters (its datapoint type).

These are the System B tables (see :mod:`.tables_systemb`) without their count, written by the
product's own ``WriteProp`` controls (see :func:`.group_communication.materialize_group_communication_controls`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .errors import ImageError
from .tables import Association
from .tables_systemb import build_association_table_b, build_group_address_table_b

# The objects and properties the tables are written to.
ADDRESS_TABLE = (1, 23)
ASSOCIATION_TABLE = (2, 23)
GROUP_OBJECT_TABLE = (9, 51)
GROUP_OBJECT_REFERENCES = (9, 52)
GROUP_OBJECT_ELEMENT_SIZE = 6
GROUP_OBJECT_REFERENCE_SIZE = 8
# The octet of a group object element that holds its flags, and the flag it leaves out.
_FLAGS_OCTET = 1
_READ_ON_INIT = 0x20


@dataclass(frozen=True, slots=True)
class PropertyTables:
    """The group communication tables of a System 300 device, written as properties."""

    group_addresses: tuple[int, ...]
    associations: tuple[Association, ...]
    # The flag octet of each group object ``1..highest`` (bit 5 already cleared).
    object_flags: bytes

    @property
    def address_data(self) -> bytes:
        """The group address table elements, two octets each."""
        return build_group_address_table_b(self.group_addresses)[2:]

    def association_data(self, width: int) -> bytes:
        """The association table elements for elements of ``width`` octets (2 or 4)."""
        if width not in (2, 4):
            raise ImageError(
                f"association table elements of {width} octets are not supported"
            )
        return build_association_table_b(self.associations, wide=width == 4)[2:]

    def element_count(self, target: tuple[int, int]) -> int:
        """How many elements the table written to ``target`` (object type, PID) has."""
        if target == ADDRESS_TABLE:
            return len(self.group_addresses)
        if target == ASSOCIATION_TABLE:
            return len(self.associations)
        if target in (GROUP_OBJECT_TABLE, GROUP_OBJECT_REFERENCES):
            return len(self.object_flags)
        return 0

    def lay_object_flags(
        self, data: bytearray, covered: bytearray, start_element: int
    ) -> None:
        """Lay each object's flag octet over ``data`` (group object elements from
        ``start_element``) and mark it covered."""
        for at in range(_FLAGS_OCTET, len(data), GROUP_OBJECT_ELEMENT_SIZE):
            number = start_element + at // GROUP_OBJECT_ELEMENT_SIZE
            if 1 <= number <= len(self.object_flags):
                data[at] = self.object_flags[number - 1]
                covered[at] = 0xFF


def property_tables(
    group_addresses: Sequence[int],
    associations: Sequence[Association],
    flags_by_number: dict[int, int],
    highest_number: int,
) -> PropertyTables:
    """The tables from the group addresses (sorted here), the links (by 0-based address
    table index) and the flag octet of every group object number ``1..highest_number``."""
    return PropertyTables(
        group_addresses=tuple(sorted(set(group_addresses))),
        associations=tuple(associations),
        object_flags=bytes(
            flags_by_number.get(number, 0) & ~_READ_ON_INIT
            for number in range(1, highest_number + 1)
        ),
    )
