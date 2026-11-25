"""Content-addressed on-disk cache for the expensive application-XML parse (:func:`to_ir`).

``to_ir(xml_bytes, version)`` is a pure function of the raw application XML, so the cache key is the
SHA-256 of those bytes (plus the schema version). That makes the cache self-invalidating: a changed
or re-imported ``.knxprod`` has different bytes -> a different key -> a fresh parse; the same app
reused across projects hits the same entry. A ``CACHE_VERSION`` prefix and a defensive load guard
mean a Python/xsdata upgrade (or a corrupt file) simply misses and re-parses, never returns a wrong
result. Language translations are applied by the caller after parsing (cheap), so one cached IR
serves every language.

An entry also keeps the evaluation trees of its application programs (see
:class:`~xknxeditor.prod.parser_v2.dynamic.DynamicTreeBuilder`): building one takes about twice as
long as loading the program, and loading it with the program half as long again. A tree is the same
in every language (it reads the texts when it evaluates). It is kept with the identity of the code
that built it (see :func:`set_tree_code`), and built and stored again by other code.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import pickle
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import psutil

from xknxeditor.namespaces import (
    current_parse_progress,
    detect_version,
    parse_progress,
)
from xknxeditor.namespaces.intermediate.knx import Knx

from .data import to_ir
from .errors import ImportCancelled
from .gc_pause import gc_paused

if TYPE_CHECKING:
    import threading
    from multiprocessing.sharedctypes import Synchronized

    from .parser_v2.dynamic import DynamicTreeBuilder

logger = logging.getLogger(__name__)

# An application XML for :func:`fill_cache`: its size in bytes and a function that reads it.
type XmlSource = tuple[int, Callable[[], bytes]]

# How often a wait for a worker looks whether the import was cancelled, in seconds.
_CANCEL_POLL = 0.2

# Bump when the IR shape, the conversion into it or pickle compatibility changes, to abandon old
# cache entries (2: a v10/v11 colour text is a TypeColor; 3: an object size "Variable" is
# "LegacyVarData").
CACHE_VERSION = "3"
# The cache folders whose entries of other cache versions this process removed.
_SWEPT: set[Path] = set()
# The identity of the code that builds the evaluation trees (see set_tree_code); None: no trees
# are kept.
_tree_code: list[str | None] = [None]

# The evaluation trees of an entry's programs by program id.
type Trees = dict[str, DynamicTreeBuilder]


def set_tree_code(code: str | None) -> None:
    """Keep the evaluation trees of the programs in the cache, as built by the code ``code``
    identifies (it changes with every change of that code, e.g. the hash of its sources); a tree
    another code built is built again. None keeps no trees (the default)."""
    _tree_code[0] = code


def tree_code() -> str | None:
    """The identity of the code whose evaluation trees the cache keeps (see set_tree_code)."""
    return _tree_code[0]


class CacheHit:
    """A parse from the cache: the IR, its schema version, the size of its XML, the evaluation
    trees kept with it by the current code (None: none), and the entry (to store trees in, see
    store_trees)."""

    __slots__ = ("knx", "path", "size", "trees", "version")

    def __init__(
        self, knx: Knx, version: str, size: int, trees: Trees | None, path: Path | None
    ) -> None:
        self.knx = knx
        self.version = version
        self.size = size
        self.trees = trees
        self.path = path


def _entry(loaded: Any) -> tuple[Knx, Trees | None] | None:
    """The IR and the trees of the current code in a loaded entry: an IR alone (as stored before
    trees were kept, or by a parse in another process), or ``(IR, code, trees)``."""
    if isinstance(loaded, Knx):
        return loaded, None
    if (
        isinstance(loaded, tuple)
        and len(loaded) == 3  # pyright: ignore[reportUnknownArgumentType]
        and isinstance(loaded[0], Knx)
    ):
        knx, code, trees = cast("tuple[Knx, object, Trees]", loaded)
        current = _tree_code[0]
        return knx, trees if current is not None and code == current else None
    return None


def store_trees(path: Path, knx: Knx, trees: Trees) -> None:
    """Keep ``trees`` (built by the current code from the programs of ``knx``, not translated)
    with ``knx`` in the cache entry ``path``."""
    code = _tree_code[0]
    if code is None:
        return
    try:
        partial = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with partial.open("wb") as file:
            pickle.dump((knx, code, trees), file, protocol=pickle.HIGHEST_PROTOCOL)
        partial.replace(path)
    except OSError as exc:  # a cache write failure must never break loading
        logger.debug("could not store the trees in %s: %s", path.name, exc)


# Parsing in worker processes pays off from about this much application XML besides the largest
# application, which takes its own time either way. A worker needs about 0.5 s to start and 0.7 s
# for its first parse, a parse about 0.15 s per MB; below this, the applications (e.g. of a project
# with applications of up to a few MB each) are parsed faster in one process.
PARALLEL_MIN_BYTES = 15_000_000
# Application XML per worker: more workers than that only add their start-up time.
BYTES_PER_WORKER = 8_000_000
# How far an application's parse has got after reading its XML, and after converting it (the
# rest is storing it in the cache): measured on large applications, where healing the XML takes
# 10 %, reading it 32 %, converting it 24 % and storing the result 35 % of the time.
READ_SHARE = 0.4
CONVERTED_SHARE = 0.65
# An application this large is parsed in a worker process even alone: there it has a core and
# the interpreter to itself, while in this process the user interface takes turns with it.
LONE_WORKER_MIN_BYTES = 20_000_000
# Memory one worker may need for a large application: the XML, its parse and the pickled copy.
_WORKER_MEMORY = 1 << 30


def _cache_path(xml_bytes: bytes, version: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(xml_bytes).hexdigest()
    return cache_dir / f"ir_{CACHE_VERSION}_{version}_{digest}.pkl"


def _source_path(source: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(source.encode()).hexdigest()
    return cache_dir / f"src_{CACHE_VERSION}_{digest}.txt"


def remember_source(
    source: str, xml_bytes: bytes, version: str, cache_dir: Path
) -> None:
    """Note which cache entry the XML read from ``source`` (a stored product file and an
    application in it, named so that another content has another name) has, and its size: a
    later parse of the same source takes it without reading the XML (see cached_for_source)."""
    digest = hashlib.sha256(xml_bytes).hexdigest()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _source_path(source, cache_dir)
        partial = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        partial.write_text(f"{version} {digest} {len(xml_bytes)}", encoding="ascii")
        partial.replace(path)
    except OSError as exc:  # a cache write failure must never break parsing
        logger.debug("could not note parse cache source %s: %s", source, exc)


def cached_for_source(source: str, cache_dir: Path) -> CacheHit | None:
    """The parse of the XML of ``source`` (see remember_source) without reading the XML; None
    when the cache has no entry for it (the XML is read and parsed then)."""
    try:
        version, digest, size = (
            _source_path(source, cache_dir).read_text(encoding="ascii").split()
        )
        path = cache_dir / f"ir_{CACHE_VERSION}_{version}_{digest}.pkl"
        with path.open("rb") as file:
            entry = _entry(pickle.load(file))
    except (OSError, ValueError):
        return None
    except Exception as exc:  # corrupt / incompatible pickle: read and parse again
        logger.debug("parse cache miss for %s: %s", source, exc)
        return None
    if entry is None:
        return None
    return CacheHit(entry[0], version, int(size), entry[1], path)


def _remove_other_versions(cache_dir: Path) -> None:
    """Remove the entries of other cache versions from ``cache_dir`` (once per process): they
    are never read again."""
    if cache_dir in _SWEPT:
        return
    _SWEPT.add(cache_dir)
    for entry in (*cache_dir.glob("ir_*.pkl"), *cache_dir.glob("src_*.txt")):
        if not entry.name.startswith((f"ir_{CACHE_VERSION}_", f"src_{CACHE_VERSION}_")):
            try:
                entry.unlink()
            except OSError as exc:  # another process may hold or remove it
                logger.debug("could not remove parse cache %s: %s", entry.name, exc)


def cached_to_ir(xml_bytes: bytes, version: str, cache_dir: Path | None) -> Knx:
    """``to_ir`` with an optional on-disk cache keyed by the XML content hash.

    With ``cache_dir=None`` this is a plain ``to_ir`` (no caching)."""
    return cached_parse(xml_bytes, version, cache_dir).knx


def _staged_to_ir(xml_bytes: bytes, version: str) -> Knx:
    """:func:`to_ir`, telling a :func:`~xknxeditor.namespaces.parse_progress` block around it how
    far the parse has got in bytes of the XML: reading it counts for :data:`READ_SHARE` of them,
    converting it up to :data:`CONVERTED_SHARE` (storing it does the rest)."""
    report = current_parse_progress()
    if report is None:
        return to_ir(xml_bytes, version)
    size = len(xml_bytes)
    with parse_progress(lambda position: report(int(min(position, size) * READ_SHARE))):
        knx = to_ir(xml_bytes, version)
    report(int(size * CONVERTED_SHARE))
    return knx


def cached_parse(xml_bytes: bytes, version: str, cache_dir: Path | None) -> CacheHit:
    """:func:`cached_to_ir` with the trees kept in the entry (see CacheHit). A
    :func:`~xknxeditor.namespaces.parse_progress` block around it hears how far the parse has
    got, in bytes of the XML (all of them once it is done)."""
    report = current_parse_progress()
    hit = _cached_parse(xml_bytes, version, cache_dir)
    if report is not None:
        report(len(xml_bytes))
    return hit


def _cached_parse(xml_bytes: bytes, version: str, cache_dir: Path | None) -> CacheHit:
    if cache_dir is None:
        return CacheHit(
            _staged_to_ir(xml_bytes, version), version, len(xml_bytes), None, None
        )
    path = _cache_path(xml_bytes, version, cache_dir)
    if path.exists():
        try:
            # Read from the file as it goes, so the pickled bytes are never in memory beside
            # the parse.
            with path.open("rb") as file:
                entry = _entry(pickle.load(file))
            if entry is not None:
                return CacheHit(entry[0], version, len(xml_bytes), entry[1], path)
        except (
            Exception
        ) as exc:  # corrupt / incompatible pickle -> reparse and overwrite
            logger.debug("parse cache miss (unreadable %s): %s", path.name, exc)
    knx = _staged_to_ir(xml_bytes, version)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _remove_other_versions(cache_dir)
        # Written aside and renamed, so a reader never sees a partly written entry. Written to
        # the file as it goes, so the pickled bytes are never in memory beside the parse.
        partial = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with partial.open("wb") as file:
            pickle.dump(knx, file, protocol=pickle.HIGHEST_PROTOCOL)
        partial.replace(path)
    except OSError as exc:  # a cache write failure must never break parsing
        logger.debug("could not write parse cache %s: %s", path.name, exc)
    return CacheHit(knx, version, len(xml_bytes), None, path)


def parse_workers(sizes: Sequence[int]) -> int:
    """How many worker processes parse application XMLs of ``sizes`` bytes; 1 parses them in this
    process. One worker per :data:`BYTES_PER_WORKER` of XML, or per the largest application's size
    when it is larger (more workers would wait for it), at most one per physical core but one (left
    to the user interface) and no more than the free memory holds."""
    total = sum(sizes)
    if len(sizes) < 2 or total - max(sizes) < PARALLEL_MIN_BYTES:
        return 1
    cores = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    memory = psutil.virtual_memory().available // _WORKER_MEMORY
    per_worker = max(BYTES_PER_WORKER, max(sizes))
    return max(1, min(len(sizes), -(-total // per_worker), cores - 1, memory))


def worker_processes(sizes: Sequence[int]) -> int:
    """How many worker processes parse application XMLs of ``sizes`` bytes; 0 parses them in this
    process. Those of :func:`parse_workers`, or one for an application of at least
    :data:`LONE_WORKER_MIN_BYTES` that no more workers would help, when the free memory holds it."""
    workers = parse_workers(sizes)
    if workers >= 2:
        return workers
    if (
        sizes
        and max(sizes) >= LONE_WORKER_MIN_BYTES
        and psutil.virtual_memory().available >= _WORKER_MEMORY
    ):
        return 1
    return 0


# In a worker process: the bytes of application XML all workers have parsed (see fill_cache).
_parsed_bytes: list[Synchronized[int] | None] = [None]


def _start_worker(parsed: Synchronized[int] | None) -> None:
    """Worker process: count what it parses in ``parsed``."""
    _parsed_bytes[0] = parsed


def _parse_into_cache(xml_bytes: bytes, cache_dir: Path) -> None:
    """Worker process: parse one application XML into the cache, counting its bytes as they are
    parsed (at the end all of them, also when the parse was cached). The collector is paused, as
    for a parse in the editor's process: the parse holds no reference cycles, and collecting
    while its objects are created took about as long as the parse."""
    with gc_paused():
        _counted_parse(xml_bytes, cache_dir)


def _counted_parse(xml_bytes: bytes, cache_dir: Path) -> None:
    parsed = _parsed_bytes[0]
    if parsed is None:
        cached_to_ir(xml_bytes, detect_version(xml_bytes), cache_dir)
        return
    counted = 0

    def count(position: int) -> None:
        nonlocal counted
        with parsed.get_lock():
            parsed.value += position - counted
        counted = position

    with parse_progress(count):
        cached_to_ir(xml_bytes, detect_version(xml_bytes), cache_dir)
    count(len(xml_bytes))


def _report_parsed(
    report: Callable[[int], None], cached: int, parsed: Synchronized[int]
) -> None:
    """Tell ``report`` the bytes parsed: ``cached`` ones and what the workers counted."""
    report(cached + parsed.value)


def _wait(
    future: Future[None],
    pool: ProcessPoolExecutor,
    cancel: threading.Event | None,
    tick: Callable[[], None] | None = None,
) -> None:
    """Wait for ``future``, calling ``tick`` meanwhile; when ``cancel`` is set meanwhile, drop the
    parses not started yet and raise :class:`~.errors.ImportCancelled`."""
    while True:
        try:
            future.result(timeout=_CANCEL_POLL)
            return
        except FuturesTimeout:
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                raise ImportCancelled from None
            if tick is not None:
                tick()


def fill_cache(
    xmls: Sequence[XmlSource],
    cache_dir: Path,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
    parsed_bytes: Callable[[int], None] | None = None,
) -> None:
    """Parse the application XMLs not cached yet in worker processes, each into the cache, so the
    caller then reads every parse from the cache.

    ``xmls`` gives each application XML's size and a function that reads it: the XMLs are read
    one by one when needed, and only those handed to the workers are in memory at the same time.
    How many workers, if any, :func:`worker_processes` decides; when no workers would be started
    even if nothing were cached, no XML is read at all. When they cannot run, the caller's own
    :func:`cached_to_ir` parses what is still missing. Setting ``cancel`` drops the parses not
    started yet and raises :class:`~.errors.ImportCancelled` (the started ones finish).
    ``progress`` hears how many of the applications are parsed (cached ones count as parsed) after
    each one, ``parsed_bytes`` how many bytes of them, while they are parsed."""
    if not worker_processes([size for size, _read in xmls]):
        return
    known: set[Path] = set()
    missing: dict[Path, XmlSource] = {}
    for size, read in xmls:
        xml = read()
        path = _cache_path(xml, detect_version(xml), cache_dir)
        known.add(path)
        if path not in missing and not path.exists():
            missing[path] = (size, read)
        del xml
    # The largest first, so the workers finish close together.
    todo = sorted(missing.values(), key=lambda source: source[0], reverse=True)
    workers = worker_processes([size for size, _read in todo])
    if not workers:
        return
    cached = len(known) - len(todo)
    cached_bytes = sum(size for size, _read in xmls) - sum(size for size, _read in todo)
    logger.debug("parsing %d applications in %d processes", len(todo), workers)
    # "spawn" starts every worker the same way on every operating system.
    context = multiprocessing.get_context("spawn")
    counter: Synchronized[int] | None = None
    tick: Callable[[], None] | None = None
    if parsed_bytes is not None:
        counter = context.Value("q", 0)
        tick = partial(_report_parsed, parsed_bytes, cached_bytes, counter)
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=context,
            initializer=_start_worker,
            initargs=(counter,),
        ) as pool:
            # A few more XMLs handed over than there are workers, so none of them waits for the
            # next one; the others are read only when one of these is done.
            running: deque[Future[None]] = deque()
            done = 0
            for _size, read in todo:
                if len(running) >= 2 * workers:
                    _wait(running.popleft(), pool, cancel, tick)
                    done += 1
                    if tick is not None:
                        tick()
                    if progress is not None:
                        progress(cached + done, len(known))
                running.append(pool.submit(_parse_into_cache, read(), cache_dir))
            while running:
                _wait(running.popleft(), pool, cancel, tick)
                done += 1
                if tick is not None:
                    tick()
                if progress is not None:
                    progress(cached + done, len(known))
    except ImportCancelled:
        raise
    except Exception as exc:  # e.g. processes cannot be started: parse in this process
        logger.warning("parallel application parsing failed: %s", exc)
