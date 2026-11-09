"""Parsing application XMLs into the parse cache, in this process or in worker processes."""

from __future__ import annotations

import pickle
from collections.abc import Callable
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


def _sources(
    xmls: list[bytes], reads: list[str] | None = None
) -> list[parse_cache.XmlSource]:
    """``xmls`` as sources for ``fill_cache``; ``reads`` hears "read <index>" of each one read."""

    def reader(index: int) -> Callable[[], bytes]:
        def read() -> bytes:
            if reads is not None:
                reads.append(f"read {index}")
            return xmls[index]

        return read

    return [(len(xml), reader(index)) for index, xml in enumerate(xmls)]


def _cached(xml: bytes, cache_dir: Path) -> bytes:
    return parse_cache._cache_path(xml, detect_version(xml), cache_dir).read_bytes()  # pyright: ignore[reportPrivateUsage]


def test_workers_parse_into_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_cache, "parse_workers", _one_per_application)
    parse_cache.fill_cache(_sources(_xmls()), tmp_path / "parallel")

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
    reads: list[str] = []
    parse_cache.fill_cache(_sources(_xmls(), reads), tmp_path)
    assert list(tmp_path.iterdir()) == []
    # Their sizes alone show that no workers pay off: none of them is read.
    assert reads == []


class _Done:
    """A finished parse that tells when it is waited for."""

    def __init__(self, events: list[str], index: int) -> None:
        self._events = events
        self._index = index

    def result(self, timeout: float | None = None) -> None:
        self._events.append(f"wait {self._index}")


class _RecordingPool:
    """A worker pool that records which XMLs it is handed and when they are waited for."""

    def __init__(self, events: list[str], xmls: list[bytes]) -> None:
        self._events = events
        self._xmls = xmls

    def __call__(self, **_kwargs: object) -> _RecordingPool:
        return self

    def __enter__(self) -> _RecordingPool:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def submit(self, _fn: object, xml: bytes, _cache_dir: Path) -> _Done:
        index = self._xmls.index(xml)
        self._events.append(f"submit {index}")
        return _Done(self._events, index)


def test_only_a_few_xmls_are_handed_over_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The XMLs are read one by one, first to see which are cached, then each right before it is
    handed to a worker, and only twice as many as there are workers are handed over before the
    first of them is done; a cached XML is not handed over."""
    base = _xmls()[0]
    # Ten different applications of falling size (the largest are handed over first).
    xmls = [base + b"<!--" + b"x" * (10 - index) + b"-->" for index in range(10)]
    parse_cache.cached_to_ir(xmls[3], detect_version(xmls[3]), tmp_path)
    events: list[str] = []
    monkeypatch.setattr(
        parse_cache, "ProcessPoolExecutor", _RecordingPool(events, xmls)
    )
    monkeypatch.setattr(parse_cache, "parse_workers", lambda _sizes: 2)
    progress: list[tuple[int, int]] = []

    parse_cache.fill_cache(
        _sources(xmls, events),
        tmp_path,
        progress=lambda done, total: progress.append((done, total)),
    )

    todo = [0, 1, 2, 4, 5, 6, 7, 8, 9]
    assert events == [
        *(f"read {index}" for index in range(10)),
        *(step for index in todo[:4] for step in (f"read {index}", f"submit {index}")),
        *(
            step
            for waited, submitted in zip(todo, todo[4:], strict=False)
            for step in (f"wait {waited}", f"read {submitted}", f"submit {submitted}")
        ),
        *(f"wait {index}" for index in todo[-4:]),
    ]
    assert progress == [(done, 10) for done in range(2, 11)]


def test_without_workers_the_caller_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_pool(*_args: object, **_kwargs: object) -> None:
        raise OSError("no processes")

    monkeypatch.setattr(parse_cache, "parse_workers", _one_per_application)
    monkeypatch.setattr(parse_cache, "ProcessPoolExecutor", no_pool)
    parse_cache.fill_cache(_sources(_xmls()), tmp_path)
    assert "parallel application parsing failed" in caplog.text

    for xml in _xmls():
        knx = parse_cache.cached_to_ir(xml, detect_version(xml), tmp_path)
        assert knx.manufacturer_data is not None


def test_entries_of_other_cache_versions_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parse written by another cache version (another conversion) is never read again: the
    next parse into the folder removes it, and the parse is made anew."""
    monkeypatch.setattr(parse_cache, "_SWEPT", set())
    xml = _xmls()[0]
    version = detect_version(xml)
    stale = parse_cache._cache_path(xml, version, tmp_path).name.replace(  # pyright: ignore[reportPrivateUsage]
        f"ir_{parse_cache.CACHE_VERSION}_", "ir_0_"
    )
    (tmp_path / stale).write_bytes(b"old conversion")
    other = tmp_path / "unrelated.txt"
    other.write_text("kept")

    knx = parse_cache.cached_to_ir(xml, version, tmp_path)

    assert knx.manufacturer_data is not None
    assert not (tmp_path / stale).exists()
    assert other.exists()
    assert pickle.loads(_cached(xml, tmp_path)) == knx


def test_a_cache_entry_is_the_pickled_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry written is the pickle of the parse, and it is read back without parsing."""
    xml = _xmls()[0]
    version = detect_version(xml)
    knx = parse_cache.cached_to_ir(xml, version, tmp_path)
    assert _cached(xml, tmp_path) == pickle.dumps(knx, protocol=pickle.HIGHEST_PROTOCOL)
    assert not list(tmp_path.glob("*.tmp"))

    def no_parse(*_args: object) -> None:
        raise AssertionError("parsed again")

    monkeypatch.setattr(parse_cache, "to_ir", no_parse)
    assert parse_cache.cached_to_ir(xml, version, tmp_path) == knx
