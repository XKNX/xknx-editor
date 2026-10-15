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
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import psutil

from xknxeditor.namespaces import detect_version
from xknxeditor.namespaces.intermediate.knx import Knx

from .data import to_ir

logger = logging.getLogger(__name__)

# Bump when the IR shape or pickle compatibility changes, to abandon old cache entries.
CACHE_VERSION = "1"

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


def cached_to_ir(xml_bytes: bytes, version: str, cache_dir: Path | None) -> Knx:
    """``to_ir`` with an optional on-disk cache keyed by the XML content hash.

    With ``cache_dir=None`` this is a plain ``to_ir`` (no caching)."""
    if cache_dir is None:
        return to_ir(xml_bytes, version)
    path = _cache_path(xml_bytes, version, cache_dir)
    if path.exists():
        try:
            return pickle.loads(path.read_bytes())
        except (
            Exception
        ) as exc:  # corrupt / incompatible pickle -> reparse and overwrite
            logger.debug("parse cache miss (unreadable %s): %s", path.name, exc)
    knx = to_ir(xml_bytes, version)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Written aside and renamed, so a reader never sees a partly written entry.
        partial = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        partial.write_bytes(pickle.dumps(knx, protocol=pickle.HIGHEST_PROTOCOL))
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


def fill_cache(xmls: Iterable[bytes], cache_dir: Path) -> None:
    """Parse the application XMLs not cached yet in worker processes, each into the cache, so the
    caller then reads every parse from the cache.

    How many workers, if any, :func:`parse_workers` decides. When they cannot run, the caller's own
    :func:`cached_to_ir` parses what is still missing."""
    pending = {_cache_path(xml, detect_version(xml), cache_dir): xml for xml in xmls}
    todo = sorted(
        (xml for path, xml in pending.items() if not path.exists()),
        key=len,
        reverse=True,  # the largest first, so the workers finish close together
    )
    workers = parse_workers([len(xml) for xml in todo])
    if workers < 2:
        return
    logger.debug("parsing %d applications in %d processes", len(todo), workers)
    try:
        # "spawn" starts every worker the same way on every operating system.
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            for future in [
                pool.submit(_parse_into_cache, xml, cache_dir) for xml in todo
            ]:
                future.result()
    except Exception as exc:  # e.g. processes cannot be started: parse in this process
        logger.warning("parallel application parsing failed: %s", exc)
