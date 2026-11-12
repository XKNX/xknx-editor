"""The bytes a result writes otherwise than the device holds them are compared in blocks first:
the offsets found are those a byte by byte comparison finds, at the ends of blocks too."""

from __future__ import annotations

import pytest

from xknxeditor.recover.parameters import (
    _offsets_differing,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.parametrize(
    ("size", "changed", "other_size"),
    [
        (0, [], 0),
        (10, [], 10),
        (10, [0, 9], 10),
        (600, [255, 256, 257, 511, 599], 600),
        (600, [3, 300], 301),
        (300, [299], 900),
    ],
)
def test_the_offsets_are_those_compared_one_by_one(
    size: int, changed: list[int], other_size: int
) -> None:
    device = bytes(n % 251 for n in range(size))
    mine = bytearray(n % 251 for n in range(other_size))
    for offset in changed:
        if offset < other_size:
            mine[offset] ^= 0x5A
    expected = [n for n, (a, b) in enumerate(zip(device, mine, strict=False)) if a != b]
    assert _offsets_differing(device, bytes(mine)) == expected
