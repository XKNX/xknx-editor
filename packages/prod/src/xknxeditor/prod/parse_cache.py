"""Content-addressed on-disk cache for the expensive application-XML parse (:func:`to_ir`).

``to_ir(xml_bytes, version)`` is a pure function of the raw application XML, so the cache key is the
SHA-256 of those bytes (plus the schema version). That makes the cache self-invalidating: a changed
or re-imported ``.knxprod`` has different bytes -> a different key -> a fresh parse; the same app
reused across projects hits the same entry. A ``CACHE_VERSION`` prefix and a defensive load guard
mean a Python/xsdata upgrade (or a corrupt file) simply misses and re-parses, never returns a wrong
result. Language translations are applied by the caller after parsing (cheap), so one cached IR
serves every language.
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
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from xknxeditor.namespaces import detect_version
from xknxeditor.namespaces.intermediate.knx import Knx

from .data import to_ir
from .errors import ImportCancelled

if TYPE_CHECKING:
    import threading

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

# Parsing in worker processes pays off from about this much application XML besides the largest
# application, which takes its own time either way. A worker needs about 0.5 s to start and 0.7 s
# for its first parse, a parse about 0.15 s per MB; below this, the applications (e.g. of a project
# with applications of up to a few MB each) are parsed faster in one process.
PARALLEL_MIN_BYTES = 15_000_000
# Application XML per worker: more workers than that only add their start-up time.
BYTES_PER_WORKER = 8_000_000
# Memory one worker may need for a large application: the XML, its parse and the pickled copy.
_WORKER_MEMORY = 1 << 30


def _cache_path(xml_bytes: bytes, version: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(xml_bytes).hexdigest()
    return cache_dir / f"ir_{CACHE_VERSION}_{version}_{digest}.pkl"


def _remove_other_versions(cache_dir: Path) -> None:
    """Remove the entries of other cache versions from ``cache_dir`` (once per process): they
    are never read again."""
    if cache_dir in _SWEPT:
        return
    _SWEPT.add(cache_dir)
    for entry in cache_dir.glob("ir_*.pkl"):
        if not entry.name.startswith(f"ir_{CACHE_VERSION}_"):
            try:
                entry.unlink()
            except OSError as exc:  # another process may hold or remove it
                logger.debug("could not remove parse cache %s: %s", entry.name, exc)


def cached_to_ir(xml_bytes: bytes, version: str, cache_dir: Path | None) -> Knx:
    """``to_ir`` with an optional on-disk cache keyed by the XML content hash.

    With ``cache_dir=None`` this is a plain ``to_ir`` (no caching)."""
    if cache_dir is None:
        return to_ir(xml_bytes, version)
    path = _cache_path(xml_bytes, version, cache_dir)
    if path.exists():
        try:
            # Read from the file as it goes, so the pickled bytes are never in memory beside
            # the parse.
            with path.open("rb") as file:
                return pickle.load(file)
        except (
            Exception
        ) as exc:  # corrupt / incompatible pickle -> reparse and overwrite
            logger.debug("parse cache miss (unreadable %s): %s", path.name, exc)
    knx = to_ir(xml_bytes, version)
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
    return knx


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


def _parse_into_cache(xml_bytes: bytes, cache_dir: Path) -> None:
    """Worker process: parse one application XML into the cache."""
    cached_to_ir(xml_bytes, detect_version(xml_bytes), cache_dir)


def _wait(
    future: Future[None], pool: ProcessPoolExecutor, cancel: threading.Event | None
) -> None:
    """Wait for ``future``; when ``cancel`` is set meanwhile, drop the parses not started yet and
    raise :class:`~.errors.ImportCancelled`."""
    while True:
        try:
            future.result(timeout=_CANCEL_POLL)
            return
        except FuturesTimeout:
            if cancel is not None and cancel.is_set():
                pool.shutdown(wait=False, cancel_futures=True)
                raise ImportCancelled from None


def fill_cache(
    xmls: Sequence[XmlSource],
    cache_dir: Path,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Parse the application XMLs not cached yet in worker processes, each into the cache, so the
    caller then reads every parse from the cache.

    ``xmls`` gives each application XML's size and a function that reads it: the XMLs are read
    one by one when needed, and only those handed to the workers are in memory at the same time.
    How many workers, if any, :func:`parse_workers` decides; when no workers would be started
    even if nothing were cached, no XML is read at all. When they cannot run, the caller's own
    :func:`cached_to_ir` parses what is still missing. Setting ``cancel`` drops the parses not
    started yet and raises :class:`~.errors.ImportCancelled` (the started ones finish).
    ``progress`` hears how many of the applications are parsed (cached ones count as parsed) after
    each one."""
    if parse_workers([size for size, _read in xmls]) < 2:
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
    workers = parse_workers([size for size, _read in todo])
    if workers < 2:
        return
    cached = len(known) - len(todo)
    logger.debug("parsing %d applications in %d processes", len(todo), workers)
    try:
        # "spawn" starts every worker the same way on every operating system.
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            # A few more XMLs handed over than there are workers, so none of them waits for the
            # next one; the others are read only when one of these is done.
            running: deque[Future[None]] = deque()
            done = 0
            for _size, read in todo:
                if len(running) >= 2 * workers:
                    _wait(running.popleft(), pool, cancel)
                    done += 1
                    if progress is not None:
                        progress(cached + done, len(known))
                running.append(pool.submit(_parse_into_cache, read(), cache_dir))
            while running:
                _wait(running.popleft(), pool, cancel)
                done += 1
                if progress is not None:
                    progress(cached + done, len(known))
    except ImportCancelled:
        raise
    except Exception as exc:  # e.g. processes cannot be started: parse in this process
        logger.warning("parallel application parsing failed: %s", exc)
