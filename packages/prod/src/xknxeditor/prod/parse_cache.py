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

import copyreg
import hashlib
import logging
import multiprocessing
import os
import pickle
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from enum import Enum
from functools import partial
from operator import attrgetter
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, cast

from xknxeditor.namespaces import (
    current_parse_progress,
    detect_version,
    parse_progress,
)
from xknxeditor.namespaces.intermediate.knx import Knx

from .data import to_ir
from .errors import ImportCancelled
from .gc_pause import gc_paused
from .resources import available_memory, usable_cpu_count

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
# "LegacyVarData"; 4: compact pickles, see _CompactPickler, and equal texts shared).
CACHE_VERSION = "4"
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


# Compact pickles. By default pickle writes an object of a class with slots as its class and a
# state dict of its slots, an object with a ``__dict__`` as its class and a copy of that dict; the
# unpickler keeps every such state container until the end of the load (the pickler memoizes
# them), and freeing them all at once leaves the heap fragmented: a loaded entry took about twice
# its live size. :class:`_CompactPickler` writes the objects of this package's classes as a call
# of :func:`_restore` with a :class:`_Layout` (written once per pickle) and the attribute values
# instead. Reading such a pickle needs nothing but :func:`pickle.load`.

# The packages whose classes _CompactPickler writes compactly (see _compact_class).
_PACKAGES = ("xknxeditor.",)
# The slot names of a class by class (see _slot_names).
_slots: dict[type[Any], tuple[str, ...]] = {}


def _slot_names(cls: type[Any]) -> tuple[str, ...]:
    """The names of the slots of ``cls`` and its bases, private ones as Python mangles them,
    those of the bases first."""
    names = _slots.get(cls)
    if names is None:
        found: list[str] = []
        for base in reversed(cls.__mro__):
            declared: str | Sequence[str] = base.__dict__.get("__slots__", ())
            for name in (declared,) if isinstance(declared, str) else declared:
                if name in ("__dict__", "__weakref__"):
                    continue
                if name.startswith("__") and not name.endswith("__"):
                    name = f"_{base.__name__.lstrip('_')}{name}"
                found.append(name)
        names = _slots[cls] = tuple(dict.fromkeys(found))
    return names


class _Layout:
    """The attributes a compact pickle writes for an object of ``cls``, in this order: its slots
    that have a value, then the keys of its ``__dict__``, if it has one; and all the slots
    ``cls`` had (see _slot_names), to know when it has changed."""

    __slots__ = ("cls", "names", "slots")

    def __init__(
        self, cls: type[Any], slots: tuple[str, ...], names: tuple[str, ...]
    ) -> None:
        self.cls = cls
        self.slots = slots
        self.names = names

    def __reduce__(self) -> tuple[Callable[..., _Layout], tuple[Any, ...]]:
        return (_layout, (self.cls, self.slots, self.names))


def _layout(cls: type[Any], slots: tuple[str, ...], names: tuple[str, ...]) -> _Layout:
    """The layout a compact pickle was written with, when ``cls`` still has the same slots
    (else the pickle is not read: a slot added since would be left without a value)."""
    if slots != _slot_names(cls):
        raise pickle.UnpicklingError(
            f"{cls.__qualname__} has changed since it was pickled"
        )
    return _Layout(cls, slots, names)


def _restore(layout: _Layout, *values: object) -> object:
    """An object written by :class:`_CompactPickler`: one of ``layout.cls`` with the attributes
    of ``layout`` set to ``values``, made without calling ``__init__``, as pickle does."""
    obj = object.__new__(layout.cls)
    # No strict zip: it takes a fifth of the load, and the pickler wrote one value per name.
    for name, value in zip(layout.names, values):  # noqa: B905
        object.__setattr__(obj, name, value)
    return obj


type _Getter = Callable[[object], tuple[object, ...]]


def _getter(names: tuple[str, ...]) -> _Getter:
    """A function giving the values of the attributes ``names`` of an object, in this order (an
    AttributeError when one has none)."""
    if len(names) > 1:
        return attrgetter(*names)
    if names:
        get = attrgetter(names[0])
        return lambda obj: (get(obj),)
    return lambda _obj: ()


# Per class, its slots and their getter when its objects are written compactly, else None (see
# _compact_class).
_compact: dict[type[Any], tuple[tuple[str, ...], _Getter] | None] = {}


def _compact_class(cls: type[Any]) -> tuple[tuple[str, ...], _Getter] | None:
    """The slots of ``cls`` and their getter when :class:`_CompactPickler` writes its objects,
    else None: a class of this package that pickles as any object does (no reduction or state
    of its own, no arguments for ``__new__``, not an enum)."""
    try:
        return _compact[cls]
    except KeyError:
        pass
    default = (
        cls.__module__.startswith(_PACKAGES)
        and cls.__new__ is object.__new__
        and not issubclass(cls, Enum)
        and cls not in copyreg.dispatch_table
        and cls.__reduce_ex__ is object.__reduce_ex__  # pyright: ignore[reportUnknownMemberType]
        and cls.__reduce__ is object.__reduce__
        and cls.__getstate__ is object.__getstate__  # pyright: ignore[reportUnknownMemberType]
        and not any(
            hasattr(cls, name)
            for name in ("__setstate__", "__getnewargs__", "__getnewargs_ex__")
        )
    )
    slots = _slot_names(cls)
    found = _compact[cls] = (slots, _getter(slots)) if default else None
    return found


class _CompactPickler(pickle.Pickler):
    """Writes the objects of this package's classes (see _compact_class) compactly (see
    _restore), everything else as pickle does."""

    def __init__(self, file: IO[bytes]) -> None:
        super().__init__(file, protocol=pickle.HIGHEST_PROTOCOL)
        # The layouts written, by class and attribute names.
        self._layouts: dict[tuple[type[Any], tuple[str, ...]], _Layout] = {}

    def reducer_override(self, obj: object) -> Any:
        cls = type(obj)
        compact = _compact_class(cls)
        if compact is None:
            return NotImplemented
        slots, get = compact
        names = slots
        try:
            values = get(obj)
        except AttributeError:  # a slot without a value: left out
            names = tuple(name for name in slots if hasattr(obj, name))
            values = tuple(getattr(obj, name) for name in names)
        if cls.__dictoffset__:
            attributes: dict[str, object] = vars(obj)
            if attributes:
                names = (*names, *attributes)
                values = (*values, *attributes.values())
        layout = self._layouts.get((cls, names))
        if layout is None:
            layout = self._layouts[cls, names] = _Layout(cls, slots, names)
        return (_restore, (layout, *values))


def _write_entry(path: Path, entry: object) -> None:
    """Write ``entry`` compactly into the cache entry ``path``: aside and renamed, so a reader
    never sees a partly written entry, and to the file as it goes, so the pickled bytes are
    never in memory beside the parse."""
    partial = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with partial.open("wb") as file:
        try:
            _CompactPickler(file).dump(entry)
        except RecursionError:
            # A reference cycle through objects written compactly: each object is made from its
            # values, so one in a cycle would need itself first (the IR and the trees have
            # none). Written as pickle does, which makes an object before its values.
            file.seek(0)
            file.truncate()
            pickle.dump(entry, file, protocol=pickle.HIGHEST_PROTOCOL)
    partial.replace(path)


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
        _write_entry(path, (knx, code, trees))
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
        _write_entry(path, knx)
    except OSError as exc:  # a cache write failure must never break parsing
        logger.debug("could not write parse cache %s: %s", path.name, exc)
    return CacheHit(knx, version, len(xml_bytes), None, path)


def parse_workers(sizes: Sequence[int]) -> int:
    """How many worker processes parse application XMLs of ``sizes`` bytes; 1 parses them in this
    process. One worker per :data:`BYTES_PER_WORKER` of XML, or per the largest application's size
    when it is larger (more workers would wait for it), at most one per usable core but one (left
    to the user interface) and no more than the available memory holds."""
    total = sum(sizes)
    if len(sizes) < 2 or total - max(sizes) < PARALLEL_MIN_BYTES:
        return 1
    cores = usable_cpu_count()
    memory = available_memory() // _WORKER_MEMORY
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
        and available_memory() >= _WORKER_MEMORY
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
    running: set[Future[None]],
    pool: ProcessPoolExecutor,
    cancel: threading.Event | None,
    tick: Callable[[], None] | None = None,
) -> Future[None]:
    """Wait for any parse to finish, calling ``tick`` meanwhile. When cancelled, drop the
    parses not started yet and raise :class:`~.errors.ImportCancelled`."""
    while True:
        if cancel is not None and cancel.is_set():
            pool.shutdown(wait=False, cancel_futures=True)
            raise ImportCancelled
        finished, _ = wait(running, timeout=_CANCEL_POLL, return_when=FIRST_COMPLETED)
        if finished:
            return next(iter(finished))
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
            # Keep at most one full XML per worker in flight. Refill as soon as any worker
            # finishes so a slow parse does not leave other workers waiting for input.
            running = {
                pool.submit(_parse_into_cache, read(), cache_dir)
                for _size, read in todo[:workers]
            }
            remaining = iter(todo[workers:])
            for done in range(1, len(todo) + 1):
                finished = _wait(running, pool, cancel, tick)
                finished.result()
                running.remove(finished)
                if tick is not None:
                    tick()
                if progress is not None:
                    progress(cached + done, len(known))
                source = next(remaining, None)
                if source is not None:
                    _size, read = source
                    running.add(pool.submit(_parse_into_cache, read(), cache_dir))
    except ImportCancelled:
        raise
    except Exception as exc:  # e.g. processes cannot be started: parse in this process
        logger.warning("parallel application parsing failed: %s", exc)
