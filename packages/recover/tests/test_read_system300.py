"""Reading the group communication of a System 300 device, which keeps its tables as
properties, and of a device without group objects, which has none."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import xknxeditor.recover.read_config as read_config
from xknxeditor.recover.read_config import (
    _read_system_300,  # pyright: ignore[reportPrivateUsage]
    read_group_communication,
)


class _Programmer:
    """Objects by type, array properties by (object index, PID) with an element width."""

    def __init__(self, arrays: dict[tuple[int, int], tuple[int, bytes]]) -> None:
        self.arrays = arrays
        self.reads: list[tuple[int, int, int, int]] = []

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        return {1: 1, 2: 2}[object_type]

    async def read_property_element_size(self, index: int, pid: int) -> int:
        return self.arrays[index, pid][0]

    def property_chunk_size(self, width: int) -> int:
        return 2

    async def read_property(
        self, index: int, pid: int, *, count: int = 1, start_index: int = 1
    ) -> bytes:
        self.reads.append((index, pid, start_index, count))
        width, data = self.arrays[index, pid]
        if start_index == 0:
            return (len(data) // width).to_bytes(2, "big")
        begin = (start_index - 1) * width
        return data[begin : begin + count * width]


async def test_a_system_300_device_reports_its_links_through_properties() -> None:
    programmer = _Programmer(
        {
            # Group addresses 1/0/0, 1/0/1, 1/0/2; links [index + 1][object number].
            (1, 23): (2, bytes.fromhex("080008010802")),
            (2, 23): (2, bytes.fromhex("010502070307")),
        }
    )
    read = await _read_system_300(cast(Any, programmer))
    assert read.group_addresses == [0x0800, 0x0801, 0x0802]
    assert [(link.group_address, link.group_object_number) for link in read.links] == [
        (0x0800, 5),
        (0x0801, 7),
        (0x0802, 7),
    ]
    # The count first, then the elements in frames of two.
    assert programmer.reads[:3] == [(1, 23, 0, 1), (1, 23, 1, 2), (1, 23, 3, 1)]


async def test_wide_association_elements_are_read_as_such() -> None:
    programmer = _Programmer(
        {
            (1, 23): (2, bytes.fromhex("0800")),
            (2, 23): (4, bytes.fromhex("00010120")),
        }
    )
    read = await _read_system_300(cast(Any, programmer))
    assert [(link.group_address, link.group_object_number) for link in read.links] == [
        (0x0800, 0x0120)
    ]


async def test_an_application_without_group_objects_reads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(read_config, "_has_com_objects", lambda _app: False)
    programmer = _Programmer({})
    application = cast(Any, SimpleNamespace())
    read = await read_group_communication(cast(Any, programmer), application)
    assert (read.group_addresses, read.links, read.group_objects) == ([], [], {})
    assert not programmer.reads
