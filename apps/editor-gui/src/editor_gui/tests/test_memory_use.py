"""What Settings > General > Memory Use keeps in memory to switch between devices quickly."""

from __future__ import annotations

import pytest

from editor_gui.memory_use import MemoryUse, memory_use, normalized

_MB = 1_000_000


def test_low_and_high_do_not_depend_on_the_free_memory() -> None:
    assert memory_use("low", 64_000 * _MB) == MemoryUse(256 * _MB, 1)
    assert memory_use("high", 100 * _MB) == MemoryUse(16_000 * _MB, 10)


@pytest.mark.parametrize(
    ("free_mb", "applications_mb", "devices"),
    [
        # A Raspberry Pi with a little free: the floor, one device.
        (600, 256, 1),
        # A quarter of what is free.
        (1_600, 400, 1),
        (4_000, 1_000, 3),
        (10_000, 2_500, 6),
        # A large computer: the ceiling.
        (64_000, 4_000, 6),
    ],
)
def test_automatic_follows_the_free_memory(
    free_mb: int, applications_mb: int, devices: int
) -> None:
    use = memory_use("automatic", free_mb * _MB)
    assert use == MemoryUse(applications_mb * _MB, devices)


def test_automatic_without_knowing_the_free_memory_keeps_a_moderate_amount() -> None:
    assert memory_use("automatic", None) == MemoryUse(1_000 * _MB, 3)


@pytest.mark.parametrize("value", [None, "", "huge", 3])
def test_an_unknown_setting_is_the_automatic_one(value: object) -> None:
    assert normalized(value) == "automatic"
    assert normalized("low") == "low"
