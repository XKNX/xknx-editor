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


# How many reloadable applications keep their parsed program and evaluation tree in memory.
RESIDENT_APPLICATIONS = 6

_resident_lock = threading.RLock()
_resident: OrderedDict[int, Application] = OrderedDict()


class Application:
    """A lazily resolved application program: `.program` is raw IR, methods yield computed types."""

    __slots__ = (
        "_id",
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

    def __repr__(self) -> str:
        return f"Application(id={self._id!r}, version={self.version!r})"

    def set_reloader(self, reloader: Callable[[], ApplicationProgram]) -> None:
        """Allow dropping the parsed program while unused; ``reloader`` parses it again."""
        with _resident_lock:
            self._reloader = reloader
            if self._program is not None:
                self._mark_used()

    def _mark_used(self) -> None:
        if self._reloader is None:
            return
        _resident[id(self)] = self
        _resident.move_to_end(id(self))
        while len(_resident) > RESIDENT_APPLICATIONS:
            _key, oldest = _resident.popitem(last=False)
            oldest._program = None
            oldest._tree_builder = None

    def _resolve(
        self, *, with_tree: bool
    ) -> tuple[ApplicationProgram, DynamicTreeBuilder | None]:
        with _resident_lock:
            program = self._program
            if program is None:
                assert self._reloader is not None
                with gc_paused():
                    program = self._reloader()
                self._program = program
            if with_tree and self._tree_builder is None:
                from .parser_v2.dynamic import DynamicTreeBuilder as _DynamicTreeBuilder

                with gc_paused():
                    self._tree_builder = _DynamicTreeBuilder(program)
            self._mark_used()
            return program, self._tree_builder

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
    if language:
        from .translate import apply_translations

        apply_translations(knx, language)
    return [
        Application(program=p, version=version, manufacturer_id=manufacturer_id)
        for p in _programs(knx)
    ]
