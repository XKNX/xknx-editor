"""How much the editor keeps in memory to switch between devices quickly (Settings > General:
Memory Use): the parsed applications (see :func:`xknxeditor.prod.application.set_resident_budget`)
and the evaluated parameters of the devices viewed last.

A device whose application is still in memory opens at once; one whose application was dropped
parses it again (seconds for a large one). A computer with much memory keeps all of a project's
applications, a small one (a Raspberry Pi, a NAS) few. ``automatic`` sizes it from the memory that
is free at the start; ``low`` and ``high`` choose it."""

from __future__ import annotations

from dataclasses import dataclass

from xknxeditor.prod.resources import available_memory as _available_memory

MODES = ("automatic", "low", "high")
DEFAULT_MODE = "automatic"

_MB = 1_000_000
# Automatic: this share of the memory free at the start, within these bounds.
_SHARE = 0.25
_LOW = 256 * _MB
_CEILING = 4_000 * _MB
_HIGH = 16_000 * _MB


@dataclass(frozen=True, slots=True)
class MemoryUse:
    """What a mode keeps: the estimated memory the parsed applications may take together, and
    how many devices keep their evaluated parameters."""

    applications: int
    devices: int


def memory_use(mode: str, available: int | None) -> MemoryUse:
    """What ``mode`` keeps on a computer with ``available`` octets of free memory (None:
    unknown, as much as on one with a few GB free)."""
    if mode == "low":
        return MemoryUse(_LOW, 1)
    if mode == "high":
        return MemoryUse(_HIGH, 10)
    free = available if available is not None else 4_000 * _MB
    applications = min(max(int(free * _SHARE), _LOW), _CEILING)
    devices = 1 if applications < 512 * _MB else 3 if applications < 2_000 * _MB else 6
    return MemoryUse(applications, devices)


def available_memory() -> int | None:
    """The memory free to the editor now (octets), None when it cannot be read."""
    try:
        return _available_memory()
    except Exception:
        return None


def normalized(mode: object) -> str:
    """``mode`` when it is one of :data:`MODES`, else the default."""
    return mode if isinstance(mode, str) and mode in MODES else DEFAULT_MODE
