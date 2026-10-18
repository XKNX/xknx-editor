"""Read from Device reads the group communication tables again in standard frames when a read in
long frames gets no response (issue #95: an interface that drops frames that long)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from editor_gui import device_info
from xknxeditor.download import DeviceProgrammer
from xknxeditor.download.errors import MemoryReadNotAnswered


class _Manager:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0

    async def open(self) -> object:
        self.opened += 1
        return object()

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The APDU length of every table read; a read above the standard frame gets no response."""
    lengths: list[int] = []

    async def read_group_communication(
        programmer: DeviceProgrammer, application: object, **_: object
    ) -> str:
        lengths.append(programmer.max_apdu_length)
        if programmer.max_apdu_length > 15:
            raise MemoryReadNotAnswered("no response to a 63-octet read")
        return "tables"

    monkeypatch.setattr(
        "xknxeditor.recover.read_group_communication", read_group_communication
    )
    return lengths


async def test_a_dropped_long_read_is_read_again_in_standard_frames(
    reads: list[int],
) -> None:
    manager = _Manager()
    programmer = DeviceProgrammer(cast(Any, object()), max_apdu_length=239)
    tables = await device_info._group_communication_tables(  # pyright: ignore[reportPrivateUsage]
        cast(Any, manager), programmer, None, 0
    )
    assert tables == "tables"
    assert reads == [239, 15]
    assert (manager.closed, manager.opened) == (1, 1)


async def test_standard_frames_need_no_second_read(reads: list[int]) -> None:
    manager = _Manager()
    programmer = DeviceProgrammer(cast(Any, object()), max_apdu_length=15)
    assert (
        await device_info._group_communication_tables(  # pyright: ignore[reportPrivateUsage]
            cast(Any, manager), programmer, None, 0
        )
        == "tables"
    )
    assert reads == [15]
    assert manager.opened == 0
