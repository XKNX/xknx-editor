"""Memory and CPUs available to this process, including Linux cgroup limits."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path, PurePosixPath

import psutil

_PROC_SELF = Path("/proc/self")


def _unescape_mount_path(path: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), path)


def _cgroups(proc_self: Path, controller: str) -> Iterator[tuple[Path, bool]]:
    """Yield the process's cgroup and visible ancestors, with their v2 flag."""
    groups: dict[str, PurePosixPath] = {}
    for line in (proc_self / "cgroup").read_text(errors="surrogateescape").splitlines():
        _, controllers, group = line.split(":", 2)
        for name in controllers.split(","):
            groups[name] = PurePosixPath(group)

    # A controller may be mounted at a nonstandard path or expose only a subtree.
    for line in (
        (proc_self / "mountinfo").read_text(errors="surrogateescape").splitlines()
    ):
        mount, _, filesystem = line.partition(" - ")
        fs, _, options = filesystem.split()
        unified = fs == "cgroup2"
        if unified:
            group = groups.get("")
        elif fs == "cgroup" and controller in options.split(","):
            group = groups.get(controller)
        else:
            continue
        root, point = map(_unescape_mount_path, mount.split()[3:5])
        mount_root = PurePosixPath(root)
        if group is None or ".." in group.parts or not group.is_relative_to(mount_root):
            continue
        mountpoint = Path(point)
        directory = mountpoint.joinpath(*group.relative_to(mount_root).parts)
        while True:
            yield directory, unified
            if directory == mountpoint:
                break
            directory = directory.parent


def _inactive_file_cache(directory: Path, key: str) -> int:
    with suppress(OSError, ValueError):
        for line in (directory / "memory.stat").read_text().splitlines():
            name, _, value = line.partition(" ")
            if name == key:
                return int(value)
    return 0


def available_memory(*, proc_self: Path | None = None) -> int:
    """Return available memory in bytes, capped by cgroup headroom after reclaimable file cache."""
    available = psutil.virtual_memory().available
    headroom = available
    try:
        for directory, unified in _cgroups(proc_self or _PROC_SELF, "memory"):
            limit_name, usage_name, cache_key = (
                ("memory.max", "memory.current", "inactive_file")
                if unified
                else (
                    "memory.limit_in_bytes",
                    "memory.usage_in_bytes",
                    "total_inactive_file",
                )
            )
            # The file can be absent where the controller is disabled or at the hierarchy root.
            try:
                raw_limit = (directory / limit_name).read_text().strip()
            except FileNotFoundError:
                continue
            if raw_limit == "max":
                continue
            limit = int(raw_limit)
            # cgroup v1 can report a page-aligned value near LONG_MAX for an unlimited group.
            if 0 <= limit < 1 << 60:
                usage = int((directory / usage_name).read_text())
                cache = _inactive_file_cache(directory, cache_key)
                if 0 < cache <= usage:
                    usage -= cache
                headroom = min(headroom, max(0, limit - usage))
    except (OSError, ValueError):
        return available
    return headroom


def usable_cpu_count(*, proc_self: Path | None = None) -> int:
    """Return physical cores capped by CPU affinity and rounded-up cgroup quotas."""
    cores = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    with suppress(OSError, psutil.Error):
        affinity: Callable[[], list[int]] | None = getattr(
            psutil.Process(), "cpu_affinity", None
        )
        if affinity is not None:
            cores = min(cores, len(affinity()))

    usable = cores
    try:
        for directory, unified in _cgroups(proc_self or _PROC_SELF, "cpu"):
            try:
                if unified:
                    quota, period = (directory / "cpu.max").read_text().split()
                else:
                    quota = (directory / "cpu.cfs_quota_us").read_text().strip()
                    period = (directory / "cpu.cfs_period_us").read_text().strip()
            except FileNotFoundError:
                continue
            if quota == "max":
                continue
            quota_us, period_us = int(quota), int(period)
            if quota_us > 0 and period_us > 0:
                usable = min(usable, -(-quota_us // period_us))
    except (OSError, ValueError):
        return cores
    return usable
