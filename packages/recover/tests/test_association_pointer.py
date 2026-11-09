"""The association table is read where the device's pointer to it says: a download with
dynamic table management packs it right after the address table."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.download.errors import PropertyAccessRejected
from xknxeditor.recover.read_config import (
    _association_table_address,  # pyright: ignore[reportPrivateUsage]
    _read_memory_mapped,  # pyright: ignore[reportPrivateUsage]
)


class _Programmer:
    def __init__(
        self,
        properties: dict[tuple[int, int], bytes] | None = None,
        memory: dict[int, int] | None = None,
    ) -> None:
        self.properties = properties or {}
        self.memory = memory or {}

    async def read_property(self, object_index: int, property_id: int) -> bytes:
        if (object_index, property_id) not in self.properties:
            raise PropertyAccessRejected("no such property")
        return self.properties[object_index, property_id]

    async def read_memory(self, address: int, count: int) -> bytes:
        return bytes(self.memory.get(address + n, 0) for n in range(count))


def _application(mask: str) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            program=SimpleNamespace(mask_version=mask), load_procedures=None
        ),
    )


async def test_bim_m112_reports_it_as_the_table_reference_of_object_2() -> None:
    programmer = cast(Any, _Programmer(properties={(2, 7): b"\x40\x13"}))
    assert (
        await _association_table_address(programmer, _application("MV-0705")) == 0x4013
    )
    # No answer, or 0: the declared place.
    assert (
        await _association_table_address(
            cast(Any, _Programmer()), _application("MV-0705")
        )
        is None
    )
    zero = cast(Any, _Programmer(properties={(2, 7): b"\x00\x00"}))
    assert await _association_table_address(zero, _application("MV-0705")) is None


async def test_bcu1_points_to_it_from_0111h_above_100h() -> None:
    programmer = cast(Any, _Programmer(memory={0x0111: 0x3A}))
    assert (
        await _association_table_address(programmer, _application("MV-0012")) == 0x013A
    )


async def test_a_mask_with_a_fixed_association_table_has_no_pointer() -> None:
    # System B keeps it in relative memory: no pointer to read.
    programmer = cast(Any, _Programmer(properties={(2, 7): b"\x40\x13"}))
    assert await _association_table_address(programmer, _application("MV-07B0")) is None


def _m112_application(association_offset: int) -> Any:
    """BIM M112 with the address table at 4000h and the association table in a segment
    of its own at 5000h, ``association_offset`` octets into it."""

    def table(segment: str, offset: int) -> SimpleNamespace:
        return SimpleNamespace(code_segment=segment, offset=offset)

    return cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(
                segment_base_addrs=lambda: {"AS-4000": 0x4000, "AS-5000": 0x5000},
                segment_sizes=lambda: {"AS-4000": 0x100, "AS-5000": 0x100},
            ),
            load_procedures=None,
            program=SimpleNamespace(
                mask_version="MV-0705",
                static=SimpleNamespace(
                    address_table=table("AS-4000", 0),
                    association_table=table("AS-5000", association_offset),
                    com_object_table=None,
                ),
            ),
        ),
    )


# Two group addresses (1/0/1, 1/0/2) after the device address 1.1.1.
_ADDRESS_TABLE = bytes.fromhex("03" + "1101" + "0801" + "0802")
# Object 4 on the first group address, object 7 on the second.
_ASSOCIATIONS = bytes.fromhex("02" + "0104" + "0207")


async def test_a_pointer_to_the_segment_start_reads_the_table_at_its_offset() -> None:
    # The table reference of the association table's load state machine is where its
    # segment starts; the table lies 3 octets into it, where the download writes it.
    memory = {0x4000 + n: octet for n, octet in enumerate(_ADDRESS_TABLE)}
    memory.update({0x5003 + n: octet for n, octet in enumerate(_ASSOCIATIONS)})
    programmer = cast(Any, _Programmer(properties={(2, 7): b"\x50\x00"}, memory=memory))
    read = await _read_memory_mapped(programmer, _m112_application(3))
    assert sorted(
        (link.group_address, link.group_object_number) for link in read.links
    ) == [
        (0x0801, 4),
        (0x0802, 7),
    ]


async def test_a_pointer_elsewhere_reads_the_table_where_it_points() -> None:
    # A packed download moves the table: the pointer, not the declared place, holds it.
    memory = {0x4000 + n: octet for n, octet in enumerate(_ADDRESS_TABLE)}
    memory.update({0x4007 + n: octet for n, octet in enumerate(_ASSOCIATIONS)})
    programmer = cast(Any, _Programmer(properties={(2, 7): b"\x40\x07"}, memory=memory))
    read = await _read_memory_mapped(programmer, _m112_application(3))
    assert sorted(link.group_object_number for link in read.links) == [4, 7]
