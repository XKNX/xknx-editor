"""The association table is read where the device's pointer to it says: a download with
dynamic table management packs it right after the address table."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.download.errors import PropertyAccessRejected
from xknxeditor.recover.read_config import (
    _association_table_address,  # pyright: ignore[reportPrivateUsage]
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
