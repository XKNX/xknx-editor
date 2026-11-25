from __future__ import annotations

import importlib
import io
import logging
import re
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from types import ModuleType
from typing import Any, cast
from xml.etree import ElementTree

from xsdata.formats.dataclass.parsers import XmlParser
from xsdata.formats.dataclass.parsers.handlers.native import EVENTS, XmlEventHandler
from xsdata.formats.dataclass.serializers import XmlSerializer
from xsdata.formats.dataclass.serializers.config import SerializerConfig

from xknxeditor.namespaces.binding import Binder, Unsupported

logger = logging.getLogger(__name__)

# Hears how many bytes of the document being parsed are read so far (see parse_progress).
_progress: ContextVar[Callable[[int], None] | None] = ContextVar(
    "xknxeditor_parse_progress", default=None
)


@contextmanager
def parse_progress(report: Callable[[int], None]) -> Generator[None]:
    """While in the block, ``report`` hears how many bytes of each document :func:`load_xml`
    parses are read so far, after each chunk. A document xsdata parses after the faster binding
    gave up on it is reported again from its start."""
    token = _progress.set(report)
    try:
        yield
    finally:
        _progress.reset(token)


def current_parse_progress() -> Callable[[int], None] | None:
    """The ``report`` of the :func:`parse_progress` block this runs in, if any."""
    return _progress.get()


SUPPORTED_VERSIONS = frozenset({"10", "11", "12", "13", "14", "20", "21", "22", "23"})

VERSION_PATTERN = re.compile(rb'xmlns="http://knx\.org/xml/project/(\d+)"')

VERSION_MODULES: dict[str, ModuleType | None] = {v: None for v in SUPPORTED_VERSIONS}


def _load_version(version: str) -> ModuleType:
    mod = VERSION_MODULES[version]
    if mod is None:
        mod = importlib.import_module(f"xknxeditor.namespaces.files.v{version}")
        VERSION_MODULES[version] = mod
    return mod


VERSION_NAMESPACES = {v: f"http://knx.org/xml/project/{v}" for v in SUPPORTED_VERSIONS}


class _PullEventHandler(XmlEventHandler):
    """xsdata's handler of the standard library's XML parser, reading the parser's events
    itself. ``ElementTree.iterparse`` makes a class for every document it reads, a reference
    cycle that keeps the document until the garbage collector runs, and an application's parse
    is left out of the collections (see xknxeditor.prod.gc_pause)."""

    def parse(self, source: Any, ns_map: dict[str | None, str]) -> Any:
        if not isinstance(source, io.IOBase):
            return super().parse(source, ns_map)
        return self.process_context(_pull_events(source), ns_map)


def _pull_events(source: io.IOBase) -> Iterator[tuple[str, Any]]:
    """The events ``ElementTree.iterparse`` gives for ``source``, read in the same chunks."""
    parser: ElementTree.XMLPullParser[Any] = ElementTree.XMLPullParser(events=EVENTS)
    report = _progress.get()
    while chunk := source.read(16 * 1024):
        parser.feed(chunk)
        yield from cast("Iterator[tuple[str, Any]]", parser.read_events())
        if report is not None:
            report(source.tell())
    parser.close()
    yield from cast("Iterator[tuple[str, Any]]", parser.read_events())


_parser = XmlParser(handler=_PullEventHandler)
_binder = Binder()
_serializer = XmlSerializer(
    config=SerializerConfig(
        xml_declaration=True,
        encoding="UTF-8",
        indent="  ",
    )
)


class VersionError(Exception):
    pass


def detect_version(xml_bytes: bytes) -> str:
    match = VERSION_PATTERN.search(xml_bytes[:2000])
    if match:
        return match.group(1).decode("ascii")
    raise VersionError("Could not detect KNX namespace version")


def get_model_class(version: str) -> type[Any]:
    if version not in SUPPORTED_VERSIONS:
        raise VersionError(
            f"Unsupported KNX version: {version}. Supported: {', '.join(sorted(SUPPORTED_VERSIONS))}"
        )
    return _load_version(version).Knx


def load_xml(source: bytes, version: str | None = None) -> Any:
    """``source`` bound into the classes of its KNX schema version (of ``version``, detected when
    not given). The binding of :mod:`.binding` builds it; a document it leaves to xsdata, xsdata's
    parser, which also raises what is wrong with it."""
    if version is None:
        version = detect_version(source)
    model_class = get_model_class(version)
    try:
        return _binder.parse(source, model_class, _progress.get())
    except Unsupported as exc:
        logger.debug("parsed by xsdata: %s", exc)
    return _parser.from_bytes(source, model_class)


def serialize_xml(obj: object, version: str) -> bytes:
    ns_map = {"": VERSION_NAMESPACES[version]}
    return _serializer.render(obj, ns_map=ns_map).encode("utf-8")  # type: ignore[reportUnknownMemberType]
