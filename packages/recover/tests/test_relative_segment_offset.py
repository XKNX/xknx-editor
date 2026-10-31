"""A System B parameter segment is read where the download writes it: its object's table
reference plus the segment's own offset (a Siemens 5WG1 510 segment starts at 4096)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.recover.read_config import read_parameter_memory


class _Programmer:
    def __init__(self) -> None:
        self.reads: list[tuple[int, int]] = []

    async def read_table_reference(self, object_index: int) -> int:
        return {4: 0x10000}[object_index]

    async def read_memory(self, address: int, size: int) -> bytes:
        self.reads.append((address, size))
        return bytes(size)


async def test_a_relative_segment_is_read_at_its_offset() -> None:
    segments = {
        "M-0001_A-9838-01-3974_RS-04-01000": 256,
        "M-0001_A-9838-01-3974_RS-04-00000": 8,
    }
    application = cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(segment_sizes=lambda: segments),
            program=SimpleNamespace(
                static=SimpleNamespace(
                    address_table=None,
                    code=SimpleNamespace(
                        relative_segment=[
                            SimpleNamespace(
                                id="M-0001_A-9838-01-3974_RS-04-01000", offset=4096
                            ),
                            SimpleNamespace(
                                id="M-0001_A-9838-01-3974_RS-04-00000", offset=None
                            ),
                        ]
                    ),
                )
            ),
        ),
    )
    programmer = _Programmer()
    read = await read_parameter_memory(cast(Any, programmer), application)
    assert set(read) == set(segments)
    assert programmer.reads == [(0x10000 + 4096, 256), (0x10000, 8)]
