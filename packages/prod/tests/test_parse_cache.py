"""Parsing application XMLs into the parse cache, in this process or in worker processes."""

from __future__ import annotations

import pickle
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

import psutil
import pytest

from xknxeditor.namespaces import detect_version
from xknxeditor.prod import parse_cache
from xknxeditor.prod.data import to_ir

_FIXTURES = Path(__file__).parent / "fixtures"
_APPLICATIONS = [
    _FIXTURES / "calculations_application.xml",
    _FIXTURES / "download_invisible_application.xml",
    _FIXTURES / "inactive_choose_application.xml",
]
_MB = 1_000_000


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with 8 physical cores and 32 GiB of free memory."""
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 8)
    monkeypatch.setattr(
        psutil.Process, "cpu_affinity", lambda _self: list(range(8)), raising=False
    )
    monkeypatch.setattr("xknxeditor.prod.resources._PROC_SELF", tmp_path)

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


@pytest.mark.usefixtures("machine")
def test_parse_workers_within_container_memory(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup fs"
    container = cgroup / "container"
    container.mkdir(parents=True)
    (tmp_path / "cgroup").write_text("0::/container\n")
    encoded_mount = cgroup.as_posix().replace(" ", r"\040")
    (tmp_path / "mountinfo").write_text(
        f"1 0 0:1 / {encoded_mount} rw - cgroup2 cgroup rw\n"
    )
    (container / "memory.max").write_text(str(2 << 30))
    sizes = [25 * _MB] * 10

    (container / "memory.current").write_text(str(512 << 20))
    assert parse_cache.parse_workers(sizes) == 1
    assert parse_cache.worker_processes(sizes) == 1

    (container / "memory.current").write_text(str(1536 << 20))
    assert parse_cache.worker_processes(sizes) == 0

    (container / "memory.stat").write_text(f"inactive_file {1024 << 20}\n")
    assert parse_cache.worker_processes(sizes) == 1


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


class _RecordingFuture(Future[None]):
    """A finished parse that tells when it is waited for."""

    def __init__(self, events: list[str], index: int) -> None:
        super().__init__()
        self._events = events
        self._index = index

    def result(self, timeout: float | None = None) -> None:
        self._events.append(f"wait {self._index}")
        assert self.done()
        super().result(timeout)


class _RecordingPool:
    """A worker pool that records which XMLs it is handed and when they are waited for."""

    def __init__(self, events: list[str], xmls: list[bytes]) -> None:
        self._events = events
        self._xmls = xmls
        self._futures: list[_RecordingFuture] = []

    def __call__(self, **_kwargs: object) -> _RecordingPool:
        return self

    def __enter__(self) -> _RecordingPool:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def submit(self, _fn: object, xml: bytes, _cache_dir: Path) -> Future[None]:
        index = self._xmls.index(xml)
        self._events.append(f"submit {index}")
        future = _RecordingFuture(self._events, index)
        self._futures.append(future)
        # The second worker finishes first. The first one stays busy until the pool feeds the
        # second one another XML.
        if index != 0:
            future.set_result(None)
        if index == 2:
            self._futures[0].set_result(None)
        return future


def test_only_a_few_xmls_are_handed_over_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The XMLs are read one by one, first to see which are cached, then each right before it is
    handed to a worker. Each worker gets one XML at a time and a cached XML is not handed over."""
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
    assert events[:15] == [
        *(f"read {index}" for index in range(10)),
        "read 0",
        "submit 0",
        "read 1",
        "submit 1",
        "wait 1",
    ]
    assert [event for event in events if event.startswith("submit")] == [
        f"submit {index}" for index in todo
    ]
    pending = 0
    for event in events[10:]:
        if event.startswith("wait"):
            pending -= 1
        elif event.startswith("submit"):
            pending += 1
        else:
            assert pending < 2
    assert pending == 0
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


@pytest.mark.parametrize("old", ["0", "3"])
def test_entries_of_other_cache_versions_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: str
) -> None:
    """A parse written by another cache version (another conversion, or 3: pickled as pickle
    does) is never read again: the next parse into the folder removes it, and the parse is made
    anew."""
    monkeypatch.setattr(parse_cache, "_SWEPT", set())
    xml = _xmls()[0]
    version = detect_version(xml)
    stale = parse_cache._cache_path(xml, version, tmp_path).name.replace(  # pyright: ignore[reportPrivateUsage]
        f"ir_{parse_cache.CACHE_VERSION}_", f"ir_{old}_"
    )
    assert stale != parse_cache._cache_path(xml, version, tmp_path).name  # pyright: ignore[reportPrivateUsage]
    (tmp_path / stale).write_bytes(
        pickle.dumps(to_ir(xml, version)) if old == "3" else b"old conversion"
    )
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
    """The entry written is a pickle of the parse, and it is read back without parsing."""
    xml = _xmls()[0]
    version = detect_version(xml)
    knx = parse_cache.cached_to_ir(xml, version, tmp_path)
    assert pickle.loads(_cached(xml, tmp_path)) == knx
    assert not list(tmp_path.glob("*.tmp"))

    def no_parse(*_args: object) -> None:
        raise AssertionError("parsed again")

    monkeypatch.setattr(parse_cache, "to_ir", no_parse)
    assert parse_cache.cached_to_ir(xml, version, tmp_path) == knx


@pytest.mark.usefixtures("machine")
@pytest.mark.parametrize(
    ("sizes", "processes"),
    [
        ([1 * _MB] * 9, 0),  # parsed in this process
        ([19 * _MB], 0),
        ([20 * _MB], 1),  # a large application alone: one worker
        ([80 * _MB, 1 * _MB], 1),
        ([2 * _MB] * 10, 3),  # as many as parse_workers
        ([], 0),
    ],
)
def test_worker_processes(sizes: list[int], processes: int) -> None:
    assert parse_cache.worker_processes(sizes) == processes


def test_no_lone_worker_without_the_memory_for_it(
    machine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Memory:
        available = 512 << 20

    monkeypatch.setattr(psutil, "virtual_memory", lambda: _Memory())
    assert parse_cache.worker_processes([80 * _MB]) == 0


def test_workers_count_the_bytes_they_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_cache, "parse_workers", _one_per_application)
    xmls = _xmls()
    heard: list[int] = []
    parse_cache.fill_cache(_sources(xmls), tmp_path, parsed_bytes=heard.append)
    assert heard[-1] == sum(len(xml) for xml in xmls)
    assert heard == sorted(heard)


def test_a_cached_application_counts_as_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_cache, "parse_workers", _one_per_application)
    xmls = _xmls()
    parse_cache.cached_to_ir(xmls[0], detect_version(xmls[0]), tmp_path)
    heard: list[int] = []
    parse_cache.fill_cache(_sources(xmls), tmp_path, parsed_bytes=heard.append)
    assert heard[0] >= len(xmls[0])
    assert heard[-1] == sum(len(xml) for xml in xmls)


def test_a_parse_tells_how_far_it_has_got(tmp_path: Path) -> None:
    """Reading the XML counts for READ_SHARE of its bytes, converting it up to CONVERTED_SHARE,
    storing it the rest; a parse read back from the cache is done at once."""
    from xknxeditor.namespaces import parse_progress

    xml = _xmls()[0]
    size = len(xml)
    heard: list[int] = []
    with parse_progress(heard.append):
        parse_cache.cached_parse(xml, detect_version(xml), tmp_path)
    assert heard[-2:] == [int(size * parse_cache.CONVERTED_SHARE), size]
    assert all(0 < position <= size * parse_cache.READ_SHARE for position in heard[:-2])
    assert heard == sorted(heard)

    heard.clear()
    with parse_progress(heard.append):
        parse_cache.cached_parse(xml, detect_version(xml), tmp_path)
    assert heard == [size]
