"""Per-program facade that resolves one IR application program on demand.

The raw `intermediate.ApplicationProgram` sits on `.program`; the methods surface the derived
views: code, load procedures, dynamic UI.

A parsed program and its evaluation tree take tens of megabytes. An application that can be
reloaded (see :meth:`Application.set_reloader`) therefore keeps them only while it is among the
most recently used ones, and reloads them when it is used again.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from xknxeditor.namespaces import detect_version

from .gc_pause import gc_paused

if TYPE_CHECKING:
    from pathlib import Path

    from xknxeditor.namespaces.intermediate.application_program_static_t_code import (
        ApplicationProgramStaticCode,
    )
    from xknxeditor.namespaces.intermediate.application_program_t import (
        ApplicationProgram,
    )
    from xknxeditor.namespaces.intermediate.knx import Knx
    from xknxeditor.namespaces.intermediate.load_procedure_style_t import (
        LoadProcedureStyle,
    )
    from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures

    from .parse_cache import CacheHit
    from .parser_v2.dynamic import DynamicTreeBuilder, DynamicUI


# The memory the reloadable applications whose parsed program and evaluation tree stay in
# memory may take together, estimated (see set_resident_budget): the least recently used
# beyond it are parsed again when used. The one used last and those being evaluated stay.
_DEFAULT_BUDGET = 600_000_000
# Approximate RSS cost per octet of XML for a parsed program and its tree. A fresh-process
# load from the compact cache used about 3.5 to 5.1 times the XML size for 50 to 73 MB programs.
_MEMORY_PER_XML_OCTET = 5
_UNKNOWN_MEMORY = 20_000_000

_resident_lock = threading.RLock()
_resident: OrderedDict[int, Application] = OrderedDict()
_loading: dict[int, str] = {}
_budget = [_DEFAULT_BUDGET]
# Application id -> its estimated memory, from the size of its XML.
_memory: dict[str, int] = {}


class _EvaluationPin:
    """Shared by the evaluators of one parsed program."""


_evaluated: WeakValueDictionary[int, _EvaluationPin] = WeakValueDictionary()


def pin_program(program: ApplicationProgram) -> _EvaluationPin:
    """Keep a program resident for as long as a DynamicUI holds this pin and the program."""
    with _resident_lock:
        return _evaluated.setdefault(id(program), _EvaluationPin())


def set_resident_budget(octets: int) -> None:
    """Let the applications kept in memory take about ``octets`` together (a host with little
    memory keeps few, one with much keeps all of a project's); those beyond it are dropped
    now."""
    with _resident_lock:
        _budget[0] = max(0, octets)
        _evict()


def resident_budget() -> int:
    """The memory the applications kept in memory may take together (see set_resident_budget)."""
    return _budget[0]


def _evict(*, keep_last: bool = True) -> None:
    """Make room for loads in progress, dropping idle programs in LRU order (with the lock held).

    A new load can replace the last resident application. Once it is published it becomes
    the last one, which stays even when its estimate alone exceeds the budget.
    """
    total = sum(_memory.get(app.id, _UNKNOWN_MEMORY) for app in _resident.values())
    total += sum(
        _memory.get(app_id, _UNKNOWN_MEMORY)
        for key, app_id in _loading.items()
        if key not in _resident
    )
    if total <= _budget[0]:
        return
    last = next(reversed(_resident), None) if keep_last else None
    for key, app in tuple(_resident.items()):
        if total <= _budget[0] or key == last:
            break
        app.unload()
        if key not in _resident:
            total -= _memory.get(app.id, _UNKNOWN_MEMORY)


@contextmanager
def reserve_application_memory(
    application_id: str, *, xml_size: int
) -> Generator[None]:
    """Make room for an application loaded directly from the catalog."""
    load = object()
    key = id(load)
    with _resident_lock:
        _memory[application_id] = xml_size * _MEMORY_PER_XML_OCTET
        _loading[key] = application_id
        _evict(keep_last=False)
    try:
        yield
    finally:
        with _resident_lock:
            del _loading[key]


class Application:
    """A lazily resolved application program: `.program` is raw IR, methods yield computed types."""

    __slots__ = (
        "_id",
        "_load_lock",
        "_name",
        "_program",
        "_reloader",
        "_tree_builder",
        "manufacturer_id",
        "version",
    )

    def __init__(
        self,
        program: ApplicationProgram,
        version: str,
        manufacturer_id: str,
        tree: DynamicTreeBuilder | None = None,
    ) -> None:
        self._program: ApplicationProgram | None = program
        self._id = program.id
        self._name = program.name or program.id
        self.version = version
        self.manufacturer_id = manufacturer_id
        # The evaluation tree, when the program came with it (see parse_cache).
        self._tree_builder: DynamicTreeBuilder | None = tree
        self._reloader: Callable[[], ApplicationProgram | Application] | None = None
        self._load_lock = threading.Lock()

    @classmethod
    def unloaded(
        cls,
        application_id: str,
        name: str,
        version: str,
        manufacturer_id: str,
        loader: Callable[[], ApplicationProgram | Application],
    ) -> Application:
        """An application known by its id, name and versions only; ``loader`` parses its program
        (or gives the application loaded, with its tree) when it is first used."""
        app = cls.__new__(cls)
        app._program = None
        app._id = application_id
        app._name = name
        app.version = version
        app.manufacturer_id = manufacturer_id
        app._tree_builder = None
        app._reloader = loader
        app._load_lock = threading.Lock()
        return app

    @property
    def loaded(self) -> bool:
        """Whether the parsed program and its evaluation tree are in memory."""
        with _resident_lock:
            return self._program is not None and self._tree_builder is not None

    def __repr__(self) -> str:
        return f"Application(id={self._id!r}, version={self.version!r})"

    def set_reloader(
        self, reloader: Callable[[], ApplicationProgram | Application]
    ) -> None:
        """Allow dropping the parsed program while unused; ``reloader`` parses it again (or
        gives the application loaded again, with its tree)."""
        with _resident_lock:
            self._reloader = reloader
            if self._program is not None:
                self._mark_used()

    def unload(self) -> None:
        """Drop the idle program and its tree if they can be loaded again."""
        with _resident_lock:
            if self._can_unload():
                _resident.pop(id(self), None)
                self._program = None
                self._tree_builder = None

    def _can_unload(self) -> bool:
        return (
            self._reloader is not None
            and id(self) not in _loading
            and id(self._program) not in _evaluated
        )

    def _mark_used(self) -> None:
        if self._reloader is None:
            return
        _resident[id(self)] = self
        _resident.move_to_end(id(self))
        _evict()

    def _resolve(
        self, *, with_tree: bool
    ) -> tuple[ApplicationProgram, DynamicTreeBuilder | None]:
        with self._load_lock:
            with _resident_lock:
                program, tree_builder = self._program, self._tree_builder
                reloader = self._reloader
                if program is not None and (tree_builder is not None or not with_tree):
                    self._mark_used()
                    return program, tree_builder
                if reloader is not None:
                    _loading[id(self)] = self.id
                    _evict(keep_last=False)
            # The resident lock is free while decoding or building the tree. Other callers
            # can use their applications, and eviction accounts for all concurrent loads.
            try:
                if program is None:
                    assert reloader is not None
                    with gc_paused(long_lived=True):
                        loaded = reloader()
                    if isinstance(loaded, Application):
                        program, tree_builder = loaded._resolve(with_tree=False)
                    else:
                        program, tree_builder = loaded, None
                if with_tree and tree_builder is None:
                    from .parser_v2.dynamic import (
                        DynamicTreeBuilder as _DynamicTreeBuilder,
                    )

                    with gc_paused(long_lived=True):
                        tree_builder = _DynamicTreeBuilder(program)
                # Publish both fields and update the LRU in the same critical section.
                with _resident_lock:
                    self._program = program
                    self._tree_builder = tree_builder
                    self._mark_used()
            finally:
                with _resident_lock:
                    _loading.pop(id(self), None)
        return program, tree_builder

    @property
    def program(self) -> ApplicationProgram:
        return self._resolve(with_tree=False)[0]

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def code(self) -> ApplicationProgramStaticCode | None:
        program = self.program
        return program.static.code if program.static else None

    @property
    def load_procedures(self) -> LoadProcedures | None:
        program = self.program
        return program.static.load_procedures if program.static else None

    @property
    def load_procedure_style(self) -> LoadProcedureStyle | None:
        return self.program.load_procedure_style

    def resolved(self) -> tuple[ApplicationProgram, DynamicTreeBuilder]:
        """The program together with the evaluation tree built from that same program."""
        program, tree_builder = self._resolve(with_tree=True)
        assert tree_builder is not None
        return program, tree_builder

    def tree_builder(self) -> DynamicTreeBuilder:
        """Cached eval-tree builder (indexer + node tree) for this program.

        It depends only on the program, not on any device's parameter/com-object instances, so it is
        built once and shared read-only across every device of this application. Building it per
        device dominates large-project import (~86% of per-device cost)."""
        return self.resolved()[1]

    def dynamic_ui(self) -> DynamicUI | None:
        """Build a new caller-owned DynamicUI (with its own GlobalState), or None if no dynamic section."""
        if self.program.dynamic is None:
            return None
        program, tree_builder = self.resolved()
        from .parser_v2.dynamic import DynamicUI as _DynamicUI

        return _DynamicUI(program, tree_builder=tree_builder)


def _programs(knx: Knx) -> Iterator[ApplicationProgram]:
    if knx.manufacturer_data is None:
        return
    for manufacturer in knx.manufacturer_data.manufacturer:
        if manufacturer.application_programs is not None:
            yield from manufacturer.application_programs.application_program


def parse_application_xml(
    xml_bytes: bytes,
    manufacturer_id: str,
    language: str | None = None,
    cache_dir: Path | None = None,
    source: str | None = None,
) -> list[Application]:
    """Parse one application XML on its own (no hardware/catalog context).

    With ``language`` (e.g. "de-DE" or "de") the .knxprod translations are overlaid for localized
    labels. ``cache_dir`` turns on a content-addressed disk cache for the parse (see :mod:`parse_cache`);
    with ``source`` (where the XML was read from, see :func:`cached_applications`) a later parse of
    it finds the cache entry without reading the XML."""
    from .parse_cache import cached_parse, remember_source

    version = detect_version(xml_bytes)
    hit = cached_parse(xml_bytes, version, cache_dir)
    if cache_dir is not None and source is not None:
        remember_source(source, xml_bytes, version, cache_dir)
    return _applications(hit, manufacturer_id, language)


def cached_applications(
    source: str,
    manufacturer_id: str,
    language: str | None = None,
    cache_dir: Path | None = None,
) -> list[Application] | None:
    """What :func:`parse_application_xml` gave for the XML of ``source`` (a stored product file
    and an application in it, named so that another content has another name), from the parse
    cache, without reading the XML: opening a large product file and unpacking its XML take
    longer than the cached parse. None when the cache has no entry for it."""
    from .parse_cache import cached_for_source

    if cache_dir is None:
        return None
    found = cached_for_source(source, cache_dir)
    if found is None:
        return None
    return _applications(found, manufacturer_id, language)


def _applications(
    hit: CacheHit, manufacturer_id: str, language: str | None
) -> list[Application]:
    from .parse_cache import store_trees, tree_code

    knx = hit.knx
    memory = hit.size * _MEMORY_PER_XML_OCTET
    programs = list(_programs(knx))
    trees = hit.trees
    if trees is None and hit.path is not None and tree_code() is not None:
        # Before the translations: the cache keeps the program as its XML has it, and a tree
        # is the same in every language.
        from .parser_v2.dynamic import DynamicTreeBuilder

        with gc_paused(long_lived=True):
            trees = {p.id: DynamicTreeBuilder(p) for p in programs}
        store_trees(hit.path, knx, trees)
    if language:
        from .translate import apply_translations

        apply_translations(knx, language)
    with _resident_lock:
        for program in programs:
            # The programs of one XML share it (rare): each counts all of it.
            _memory[program.id] = memory
    return [
        Application(
            program=p,
            version=hit.version,
            manufacturer_id=manufacturer_id,
            tree=trees.get(p.id) if trees is not None else None,
        )
        for p in programs
    ]
