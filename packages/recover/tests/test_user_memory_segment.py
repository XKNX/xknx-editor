"""A code segment in the device's user memory is not read with the standard memory services:
at its address the standard memory holds another segment (an ABB A018 user-memory octet at
0x4676 lies inside the segment at 0x4600)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.recover.read_config import read_parameter_memory


class _Programmer:
    def __init__(self) -> None:
        self.reads: list[tuple[int, int]] = []

    async def read_memory(self, address: int, size: int) -> bytes:
        self.reads.append((address, size))
        return bytes(size)


async def test_a_user_memory_segment_is_not_read() -> None:
    sizes = {"AS-4600": 160, "AS-U04676": 1}
    bases = {"AS-4600": 0x4600, "AS-U04676": 0x4676}
    application = cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(
                segment_sizes=lambda: sizes, segment_base_addrs=lambda: bases
            ),
            program=SimpleNamespace(
                static=SimpleNamespace(
                    address_table=SimpleNamespace(code_segment="AS-4000"),
                    code=SimpleNamespace(
                        absolute_segment=[
                            SimpleNamespace(id="AS-4600", user_memory=False),
                            SimpleNamespace(id="AS-U04676", user_memory=True),
                        ],
                        relative_segment=[],
                    ),
                )
            ),
        ),
    )
    programmer = _Programmer()
    read = await read_parameter_memory(cast(Any, programmer), application)
    assert set(read) == {"AS-4600"}
    assert programmer.reads == [(0x4600, 160)]
