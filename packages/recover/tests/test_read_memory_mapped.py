"""A memory-mapped group object table is read where and how the download writes it: at its
offset in the segment, in 3-octet records on BCU1/BCU2 and 4-octet records on M112."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from xknxeditor.recover import read_config
from xknxeditor.recover.read_config import (
    _read_memory_mapped,  # pyright: ignore[reportPrivateUsage]
)

_SEGMENT = "AS-0100"
_BASE = 0x0100


class _Programmer:
    def __init__(self, memory: dict[int, int]) -> None:
        self.memory = memory

    async def read_memory(self, address: int, count: int) -> bytes:
        return bytes(self.memory.get(address + n, 0) for n in range(count))

    async def read_property(self, object_index: int, property_id: int) -> bytes:
        return b"\x00\x00"


def _application(mask: str, *, table_offset: int, size: int) -> Any:
    def table(offset: int) -> SimpleNamespace:
        return SimpleNamespace(code_segment=_SEGMENT, offset=offset)

    return cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(
                segment_base_addrs=lambda: {_SEGMENT: _BASE},
                segment_sizes=lambda: {_SEGMENT: size},
            ),
            load_procedures=None,
            program=SimpleNamespace(
                mask_version=mask,
                static=SimpleNamespace(
                    address_table=table(0x16),
                    association_table=table(0x20),
                    com_object_table=table(table_offset),
                ),
            ),
        ),
    )


def _memory(table_address: int, table: bytes) -> dict[int, int]:
    memory = {_BASE + 0x16: 0x01, _BASE + 0x17: 0x11, _BASE + 0x18: 0x01}
    memory.update({table_address + n: octet for n, octet in enumerate(table)})
    return memory


@pytest.fixture(autouse=True)
def _three_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_config, "_highest_com_object_number", lambda _app: 2)


@pytest.mark.parametrize("mask", ["MV-0020", "MV-0012"])
async def test_bcu_table_is_3_octet_records_at_its_offset(mask: str) -> None:
    # Object 0 transmits 1 bit, object 1 is written (1 bit), object 2 is 2 octets.
    table = bytes.fromhex("03d1" + "0b6300" + "0cf700" + "11eb48")
    application = _application(mask, table_offset=0x40, size=0x80)
    programmer = cast(Any, _Programmer(_memory(_BASE + 0x40, table + b"\xaa" * 8)))
    read = await _read_memory_mapped(programmer, application)
    assert sorted(read.group_objects) == [0, 1, 2]
    sizes = {n: go.object_size for n, go in read.group_objects.items()}
    assert sizes == {0: "1 Bit", 1: "1 Bit", 2: "2 Bytes"}
    assert read.group_objects[1].write is True
    assert read.group_objects[0].write is False


async def test_m112_table_is_4_octet_records_at_its_offset() -> None:
    # Count, RAM flags pointer (2), then value pointer (2), flags and type per object.
    table = bytes.fromhex("034000" + "4100d700" + "4101df07" + "4102d708")
    application = _application("MV-0705", table_offset=0x40, size=0x80)
    programmer = cast(Any, _Programmer(_memory(_BASE + 0x40, table)))
    read = await _read_memory_mapped(programmer, application)
    sizes = {n: go.object_size for n, go in read.group_objects.items()}
    assert sizes == {0: "1 Bit", 1: "1 Byte", 2: "2 Bytes"}
    assert read.group_objects[1].read is True


async def test_table_is_not_read_past_its_segment() -> None:
    # The segment ends one record after the table start: only that record is decoded.
    table = bytes.fromhex("03d1" + "0b6300" + "0cf700")
    application = _application("MV-0020", table_offset=0x40, size=0x45)
    programmer = cast(Any, _Programmer(_memory(_BASE + 0x40, table)))
    read = await _read_memory_mapped(programmer, application)
    assert sorted(read.group_objects) == [0]
