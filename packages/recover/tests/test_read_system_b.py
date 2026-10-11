"""Reading System B group communication tables from a device memory image."""

from __future__ import annotations

from typing import Any, cast

from xknxeditor.download.tables import Association, com_object_flag_byte
from xknxeditor.download.tables_systemb import (
    build_association_table_b,
    build_group_address_table_b,
    build_group_object_table_b,
    group_address_index_b,
)
from xknxeditor.recover import read_group_communication

_BASES = {1: 0x1000, 2: 0x2000, 9: 0x3000}  # object type -> table address


class _Device:
    """Interface object N is of type N; its table lives at ``_BASES[N]``."""

    memory_chunk_size = 12

    def __init__(self) -> None:
        gas = [0x0B02, 0x0C00]
        index = group_address_index_b(gas)
        flags = com_object_flag_byte(
            priority="Low",
            communication=True,
            read=True,
            write=False,
            transmit=True,
            update=False,
            read_on_init=False,
        )
        self.memory = bytearray(0x4000)
        self._put(1, build_group_address_table_b(gas))
        self._put(
            2,
            build_association_table_b(
                [
                    Association(
                        index[0x0B02] - 1, group_object_number=20, sending=True
                    ),
                    Association(
                        index[0x0C00] - 1, group_object_number=300, sending=False
                    ),
                ],
                wide=True,
            ),
        )
        self._put(9, build_group_object_table_b({20: (flags, 0), 300: (flags, 7)}, 400))
        self.reads: list[tuple[int, int]] = []

    def _put(self, object_type: int, data: bytes) -> None:
        base = _BASES[object_type]
        self.memory[base : base + len(data)] = data

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        return object_type

    async def read_table_reference(self, object_index: int) -> int:
        return _BASES[object_index]

    async def read_association_table_wide(self) -> bool | None:
        return None

    async def read_memory(self, address: int, size: int) -> bytes:
        self.reads.append((address, size))
        return bytes(self.memory[address : address + size])


async def test_system_b_without_application_reads_only_linked_records() -> None:
    device = _Device()
    tables = await read_group_communication(
        cast(Any, device), None, linked_objects_only=True
    )
    assert tables.group_addresses == [0x0B02, 0x0C00]
    assert {
        (link.group_object_number, link.group_address) for link in tables.links
    } == {
        (20, 0x0B02),
        (300, 0x0C00),
    }
    assert set(tables.group_objects) == {20, 300}
    assert tables.group_objects[300].size_code == 7
    group_object_reads = [read for read in device.reads if read[0] >= 0x3000]
    # The count, then one record each, instead of all 400 records.
    assert group_object_reads == [(0x3000, 2), (0x3000, 2), (0x3028, 2), (0x3258, 2)]
