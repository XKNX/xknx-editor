"""Resource limits as seen from the process's own cgroup and mount namespaces."""

from __future__ import annotations

from pathlib import Path

import psutil
import pytest

from xknxeditor.prod.resources import available_memory, usable_cpu_count

_GIB = 1 << 30


@pytest.fixture(autouse=True)
def host(monkeypatch: pytest.MonkeyPatch) -> None:
    class Memory:
        available = 8 * _GIB

    monkeypatch.setattr(psutil, "virtual_memory", Memory)
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(
        "os.sched_getaffinity", lambda _pid: set(range(4)), raising=False
    )


@pytest.fixture
def unified(tmp_path: Path) -> Path:
    mount = tmp_path / "cgroup fs"
    group = mount / "slice" / "editor"
    group.mkdir(parents=True)
    encoded_mount = mount.as_posix().replace(" ", r"\040")
    (tmp_path / "mountinfo").write_text(
        f"1 0 0:1 / {encoded_mount} rw - cgroup2 cgroup rw\n"
    )
    (tmp_path / "cgroup").write_text("0::/slice/editor\n")
    return group


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    # Only /docker/container is mounted, under a name unrelated to the controller list.
    mount = tmp_path / "legacy cgroup"
    group = mount / "editor"
    group.mkdir(parents=True)
    encoded_mount = mount.as_posix().replace(" ", r"\040")
    (tmp_path / "mountinfo").write_text(
        f"2 0 0:1 /docker/container {encoded_mount} ro - cgroup cgroup rw,cpu,memory\n"
    )
    (tmp_path / "cgroup").write_text("8:cpu,memory:/docker/container/editor\n")
    return group


@pytest.mark.parametrize(
    ("limit", "usage", "expected"),
    [
        (3 * _GIB, _GIB, 2 * _GIB),
        (32 * _GIB, _GIB, 8 * _GIB),
        (_GIB, 2 * _GIB, 0),
        (0, 0, 0),
        ("max", _GIB, 8 * _GIB),
    ],
)
def test_v2_memory(
    tmp_path: Path, unified: Path, limit: int | str, usage: int, expected: int
) -> None:
    (unified / "memory.max").write_text(str(limit))
    (unified / "memory.current").write_text(str(usage))
    assert available_memory(proc_self=tmp_path) == expected


def test_parent_cgroup_accounts_for_siblings(tmp_path: Path, unified: Path) -> None:
    (unified / "memory.max").write_text(str(2 * _GIB))
    (unified / "memory.current").write_text(str(_GIB))
    (unified.parent / "memory.max").write_text(str(4 * _GIB))
    (unified.parent / "memory.current").write_text(str(4 * _GIB - 4096))
    assert available_memory(proc_self=tmp_path) == 4096


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(3 * _GIB, 2 * _GIB), (9223372036854771712, 8 * _GIB), (-1, 8 * _GIB)],
)
def test_v1_memory(tmp_path: Path, legacy: Path, limit: int, expected: int) -> None:
    (legacy / "memory.limit_in_bytes").write_text(str(limit))
    (legacy / "memory.usage_in_bytes").write_text(str(_GIB))
    assert available_memory(proc_self=tmp_path) == expected


def test_unreadable_usage_falls_back_to_host(tmp_path: Path, unified: Path) -> None:
    (unified / "memory.max").write_text(str(_GIB))
    (unified / "memory.current").mkdir()
    assert available_memory(proc_self=tmp_path) == 8 * _GIB


def test_without_proc_or_affinity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr("os.sched_getaffinity")
    assert available_memory(proc_self=tmp_path) == 8 * _GIB
    assert usable_cpu_count(proc_self=tmp_path) == 8


@pytest.mark.parametrize(
    ("quota", "expected"),
    [
        ("max 100000", 4),
        ("150000 100000", 2),
        ("40000 100000", 1),
        ("800000 100000", 4),
    ],
)
def test_v2_cpu_quota(tmp_path: Path, unified: Path, quota: str, expected: int) -> None:
    (unified / "cpu.max").write_text(quota)
    assert usable_cpu_count(proc_self=tmp_path) == expected


def test_parent_cpu_quota(tmp_path: Path, unified: Path) -> None:
    (unified / "cpu.max").write_text("max 100000")
    (unified.parent / "cpu.max").write_text("250000 100000")
    assert usable_cpu_count(proc_self=tmp_path) == 3


def test_v1_cpu_quota(tmp_path: Path, legacy: Path) -> None:
    (legacy / "cpu.cfs_quota_us").write_text("150000")
    (legacy / "cpu.cfs_period_us").write_text("100000")
    assert usable_cpu_count(proc_self=tmp_path) == 2
