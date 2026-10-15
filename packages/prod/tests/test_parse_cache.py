"""Parsing application XMLs into the parse cache, in this process or in worker processes."""

from __future__ import annotations

import pickle
from pathlib import Path

import psutil
import pytest

from xknxeditor.namespaces import detect_version
from xknxeditor.prod import parse_cache

_FIXTURES = Path(__file__).parent / "fixtures"
_APPLICATIONS = [
    _FIXTURES / "calculations_application.xml",
    _FIXTURES / "download_invisible_application.xml",
    _FIXTURES / "inactive_choose_application.xml",
]
_MB = 1_000_000


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with 8 physical cores and 32 GiB of free memory."""
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 8)

    class _Memory:
        available = 32 << 30

    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Memory())


@pytest.mark.usefixtures("machine")
@pytest.mark.parametrize(
    ("sizes", "workers"),
    [
        ([1 * _MB] * 9, 1),  # a few MB: starting workers costs more than it saves
        ([_MB // 2] * 30, 1),
        ([80 * _MB], 1),  # one application cannot be split
        ([80 * _MB, 1 * _MB], 1),  # the largest one takes its time either way
        ([2 * _MB] * 10, 3),  # one worker per 8 MB
        ([5 * _MB] * 10, 7),  # at most one per physical core but one
        ([80 * _MB] + [1 * _MB] * 20, 2),  # no more than finish beside the largest one
        ([14 * _MB] * 59, 7),
    ],
)
def test_parse_workers(sizes: list[int], workers: int) -> None:
    assert parse_cache.parse_workers(sizes) == workers


def test_parse_workers_within_free_memory(
    machine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Memory:
        available = 3 << 30

    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Memory())
    assert parse_cache.parse_workers([14 * _MB] * 59) == 3


def _one_per_application(sizes: list[int]) -> int:
    return len(sizes)


def _xmls() -> list[bytes]:
    return [path.read_bytes() for path in _APPLICATIONS]


def _cached(xml: bytes, cache_dir: Path) -> bytes:
    return parse_cache._cache_path(xml, detect_version(xml), cache_dir).read_bytes()  # pyright: ignore[reportPrivateUsage]


def test_workers_parse_into_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_cache, "parse_workers", _one_per_application)
    parse_cache.fill_cache(_xmls(), tmp_path / "parallel")

    for xml in _xmls():
        parsed = parse_cache.cached_to_ir(xml, detect_version(xml), tmp_path / "own")
        # The worker processes wrote the same parse this process makes.
        assert pickle.loads(_cached(xml, tmp_path / "parallel")) == parsed


def test_small_applications_stay_in_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_pool(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("worker processes were started")

    monkeypatch.setattr(parse_cache, "ProcessPoolExecutor", no_pool)
    parse_cache.fill_cache(_xmls(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_without_workers_the_caller_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_pool(*_args: object, **_kwargs: object) -> None:
        raise OSError("no processes")

    monkeypatch.setattr(parse_cache, "parse_workers", _one_per_application)
    monkeypatch.setattr(parse_cache, "ProcessPoolExecutor", no_pool)
    parse_cache.fill_cache(_xmls(), tmp_path)
    assert "parallel application parsing failed" in caplog.text

    for xml in _xmls():
        knx = parse_cache.cached_to_ir(xml, detect_version(xml), tmp_path)
        assert knx.manufacturer_data is not None
