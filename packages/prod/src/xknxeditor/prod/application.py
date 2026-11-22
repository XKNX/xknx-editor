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
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

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

    from .parser_v2.dynamic import DynamicTreeBuilder, DynamicUI


# The memory the reloadable applications whose parsed program and evaluation tree stay in
# memory may take together, estimated (see set_resident_budget): the least recently used
# beyond it are parsed again when used. The one used last always stays.
_DEFAULT_BUDGET = 600_000_000
# A parsed program with its evaluation tree takes about this much per octet of its XML (four
# to six times across applications from 0.1 to 34 MB of XML), and an application whose XML
# was not seen this much.
_MEMORY_PER_XML_OCTET = 5
_UNKNOWN_MEMORY = 20_000_000

_resident_lock = threading.RLock()
_resident: OrderedDict[int, Application] = OrderedDict()
_budget = [_DEFAULT_BUDGET]
# Application id -> its estimated memory, from the size of its XML when it was parsed.
_memory: dict[str, int] = {}


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


def _evict() -> None:
    """Drop the least recently used parsed programs beyond the budget, all but the last one
    (under ``_resident_lock``)."""
    total = sum(_memory.get(app.id, _UNKNOWN_MEMORY) for app in _resident.values())
    while len(_resident) > 1 and total > _budget[0]:
        _key, oldest = _resident.popitem(last=False)
        total -= _memory.get(oldest.id, _UNKNOWN_MEMORY)
        oldest.unload()


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
        self, program: ApplicationProgram, version: str, manufacturer_id: str
    ) -> None:
        self._program: ApplicationProgram | None = program
        self._id = program.id
        self._name = program.name or program.id
        self.version = version
        self.manufacturer_id = manufacturer_id
        self._tree_builder: DynamicTreeBuilder | None = None
        self._reloader: Callable[[], ApplicationProgram] | None = None
        self._load_lock = threading.Lock()

    @classmethod
    def unloaded(
        cls,
        application_id: str,
        name: str,
        version: str,
        manufacturer_id: str,
        loader: Callable[[], ApplicationProgram],
    ) -> Application:
        """An application known by its id, name and versions only; ``loader`` parses its program
        when it is first used."""
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
        return self._program is not None and self._tree_builder is not None

    def __repr__(self) -> str:
        return f"Application(id={self._id!r}, version={self.version!r})"

    def set_reloader(self, reloader: Callable[[], ApplicationProgram]) -> None:
        """Allow dropping the parsed program while unused; ``reloader`` parses it again."""
        with _resident_lock:
            self._reloader = reloader
            if self._program is not None:
                self._mark_used()

    def unload(self) -> None:
        """Drop the parsed program and its tree, parsed again when used (an application that
        cannot be parsed again keeps them)."""
        if self._reloader is None:
            return
        self._program = None
        self._tree_builder = None

    def _mark_used(self) -> None:
        if self._reloader is None:
            return
        _resident[id(self)] = self
        _resident.move_to_end(id(self))
        _evict()

    def _resolve(
        self, *, with_tree: bool
    ) -> tuple[ApplicationProgram, DynamicTreeBuilder | None]:
        # Loading holds only this application's lock, so other applications stay usable meanwhile
        # (e.g. while one loads in the background).
        with self._load_lock:
            program = self._program
            if program is None:
                assert self._reloader is not None
                with gc_paused():
                    program = self._reloader()
            tree_builder = self._tree_builder
            if with_tree and tree_builder is None:
                from .parser_v2.dynamic import DynamicTreeBuilder as _DynamicTreeBuilder

                with gc_paused():
                    tree_builder = _DynamicTreeBuilder(program)
            # Publish the program and its tree together under the LRU lock, which eviction also
            # holds: otherwise a concurrent eviction during the (slow) tree build could null the
            # fields between the two assignments and leave a half-loaded state - a program without
            # its tree, or a tree paired with a later-reloaded (possibly updated) program. Marking
            # used in the same section keeps this application resident past that eviction pass.
            with _resident_lock:
                self._program = program
                if tree_builder is not None:
                    self._tree_builder = tree_builder
                self._mark_used()
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
) -> list[Application]:
    """Parse one application XML on its own (no hardware/catalog context).

    With ``language`` (e.g. "de-DE" or "de") the .knxprod translations are overlaid for localized
    labels. ``cache_dir`` turns on a content-addressed disk cache for the parse (see :mod:`parse_cache`)."""
    from .parse_cache import cached_to_ir

    version = detect_version(xml_bytes)
    knx = cached_to_ir(xml_bytes, version, cache_dir)
    memory = len(xml_bytes) * _MEMORY_PER_XML_OCTET
    if language:
        from .translate import apply_translations

        apply_translations(knx, language)
    programs = list(_programs(knx))
    for program in programs:
        # The programs of one XML share it (rare): each counts all of it.
        _memory[program.id] = memory
    return [
        Application(program=p, version=version, manufacturer_id=manufacturer_id)
        for p in programs
    ]
