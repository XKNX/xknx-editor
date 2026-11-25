"""Binds a KNX XML document straight into the generated ``files.vXX`` dataclasses.

The result is the one xsdata's parser gives, built several times faster: what each element and
attribute becomes is worked out once per class (from xsdata's own metadata) instead of once per
element, and attribute values convert through xsdata's converters, once per distinct value. A
document with anything this binding does not cover (an unknown element, a repeated single element,
a missing required attribute, ``xsi:type``, text between elements, …) raises :class:`Unsupported`,
and the caller parses it with xsdata, which then also reports what is wrong with it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any, cast
from xml.etree import ElementTree

from xsdata.formats.dataclass.context import XmlContext
from xsdata.formats.dataclass.models.elements import XmlMeta, XmlVar
from xsdata.formats.dataclass.parsers.config import ParserConfig
from xsdata.formats.dataclass.parsers.utils import ParserUtils

# These attributes change how xsdata binds an element.
_XSI_BINDING = frozenset(
    {
        "{http://www.w3.org/2001/XMLSchema-instance}type",
        "{http://www.w3.org/2001/XMLSchema-instance}nil",
    }
)
# How much of the document the XML parser gets at a time.
CHUNK = 64 * 1024
# Converted values kept per attribute, before the cache starts over.
_CACHE_SIZE = 4096


class Unsupported(Exception):
    """The document holds something this binding leaves to xsdata."""


Convert = Callable[[str], Any]

# xsdata's conversion of a field's value (its signature is only partly typed).
_parse_var = cast("Callable[..., Any]", ParserUtils.parse_var)  # pyright: ignore[reportUnknownMemberType]


def _any_text(text: str) -> str:
    """The value of an element of any type that has neither attributes nor children: its text."""
    return text


class _Child:
    """How a child element is bound: into ``name`` of its parent, as an object of ``plan`` (a
    class) or converted from its text (a simple type), into a list when ``many``."""

    __slots__ = ("convert", "empty", "many", "name", "plan", "target")

    def __init__(
        self,
        name: str,
        many: bool,
        *,
        target: tuple[type, str | None] | None = None,
        convert: Convert | None = None,
        empty: Callable[[], Any] | None = None,
    ) -> None:
        self.name = name
        self.many = many
        # The class and namespace of a class element; its plan once it is needed.
        self.target = target
        self.plan: _Plan | None = None
        self.convert = convert
        self.empty = empty


class _Plan:
    """How the elements of one class are bound."""

    __slots__ = ("attributes", "children", "clazz", "qname", "text")

    def __init__(
        self,
        clazz: type,
        qname: str,
        attributes: dict[str, tuple[str, Convert]],
        children: dict[str, _Child],
        text: tuple[str, Convert] | None,
    ) -> None:
        self.clazz = clazz
        self.qname = qname
        self.attributes = attributes
        self.children = children
        self.text = text


class Binder:
    """Binds documents into the generated classes; the plans are shared by every parse."""

    def __init__(self) -> None:
        self._context = XmlContext()
        self._config = ParserConfig()
        self._plans: dict[tuple[type, str | None], _Plan] = {}
        self._lock = threading.Lock()

    def _convert(self, meta: XmlMeta, var: XmlVar) -> Convert:
        """The conversion xsdata gives a value of ``var``; a plain string stays as it is."""
        config = self._config
        tokens = var.tokens_factory is not None  # pyright: ignore[reportUnknownMemberType]
        if not tokens and not var.format and tuple(var.types) == (str,):
            return str

        def parse(value: str) -> Any:
            return _parse_var(
                meta=meta, var=var, config=config, value=value, ns_map=None
            )

        if tokens:
            # A list is made anew for every value.
            return parse
        cache: dict[str, Any] = {}

        def convert(value: str) -> Any:
            try:
                return cache[value]
            except KeyError:
                if len(cache) >= _CACHE_SIZE:
                    cache.clear()
                result = cache[value] = parse(value)
                return result

        return convert

    def _empty(self, meta: XmlMeta, var: XmlVar) -> Callable[[], Any]:
        """The value of an element of a simple type without text."""
        config = self._config
        blank: Any = b"" if bytes in var.types else ""

        def empty() -> Any:
            value = _parse_var(
                meta=meta, var=var, config=config, value=None, ns_map=None
            )
            return blank if value is None else value

        return empty

    def plan(self, clazz: type, namespace: str | None) -> _Plan:
        """The plan of ``clazz`` as an element in ``namespace`` (its parent's)."""
        key = (clazz, namespace)
        plan = self._plans.get(key)
        if plan is None:
            with self._lock:
                plan = self._plans.get(key)
                if plan is None:
                    plan = self._plans[key] = self._build(clazz, namespace)
        return plan

    def _build(self, clazz: type, namespace: str | None) -> _Plan:
        meta = self._context.fetch(clazz, namespace, None)
        if meta.wildcards or meta.any_attributes or meta.wrappers or meta.nillable:
            raise Unsupported(f"{clazz.__qualname__}: wildcard, wrapper or nil")
        attributes: dict[str, tuple[str, Convert]] = {}
        for qname, var in meta.attributes.items():
            if not var.init:
                raise Unsupported(f"{clazz.__qualname__}.{var.name}: fixed value")
            attributes[qname] = (var.name, self._convert(meta, var))
        candidates = [
            (qname, var) for qname, vars_ in meta.elements.items() for var in vars_
        ]
        candidates += [
            (qname, var)
            for choice in meta.choices
            for qname, var in choice.elements.items()
        ]
        children: dict[str, _Child] = {}
        for qname, var in candidates:
            if (
                qname in children
                or not var.init
                or var.nillable
                or var.is_wildcard
                or var.is_clazz_union
            ):
                raise Unsupported(f"{clazz.__qualname__}.{var.name}: ambiguous element")
            if var.clazz is not None:
                child = _Child(
                    var.name, var.list_element, target=(var.clazz, meta.namespace)
                )
            elif var.any_type:
                # xsdata binds it to the class registered for its name, if there is one.
                if var.process_contents != "skip" and self._context.find_type(qname):
                    raise Unsupported(f"{clazz.__qualname__}.{var.name}: typed by name")
                child = _Child(var.name, var.list_element, convert=_any_text, empty=str)
            else:
                child = _Child(
                    var.name,
                    var.list_element,
                    convert=self._convert(meta, var),
                    empty=self._empty(meta, var),
                )
            children[qname] = child
        text: tuple[str, Convert] | None = None
        if meta.text is not None:
            if not meta.text.init:
                raise Unsupported(f"{clazz.__qualname__}: fixed text")
            text = (meta.text.name, self._convert(meta, meta.text))
        return _Plan(clazz, meta.qname, attributes, children, text)

    def _child_plan(self, child: _Child) -> _Plan:
        plan = child.plan
        if plan is None:
            assert child.target is not None
            plan = child.plan = self.plan(*child.target)
        return plan

    def parse(
        self, source: bytes, clazz: type, progress: Callable[[int], None] | None = None
    ) -> Any:
        """``source`` bound into ``clazz``, the class of its root element. ``progress`` hears how
        many bytes of it are read, after each chunk."""
        root = self.plan(clazz, None)
        parser: ElementTree.XMLPullParser[Any] = ElementTree.XMLPullParser(
            events=("start", "end")
        )
        # Per open element: its plan (None for a simple type), how it is bound into its
        # parent, the element and the values of its fields.
        stack: list[
            tuple[_Plan | None, _Child | None, ElementTree.Element, dict[str, Any]]
        ] = []
        result: Any = None
        position = 0
        size = len(source)
        while True:
            if position < size:
                parser.feed(source[position : position + CHUNK])
                position = min(position + CHUNK, size)
                end = False
            else:
                parser.close()
                end = True
            events = cast(
                "Iterator[tuple[str, ElementTree.Element]]", parser.read_events()
            )
            for event, element in events:
                if event == "start":
                    if not stack:
                        if element.tag != root.qname:
                            raise Unsupported(f"root element {element.tag}")
                        stack.append((root, None, element, {}))
                        continue
                    parent = stack[-1][0]
                    if parent is None:
                        raise Unsupported(
                            f"{element.tag} in an element of a simple type"
                        )
                    child = parent.children.get(element.tag)
                    if child is None:
                        raise Unsupported(f"unknown element {element.tag}")
                    plan = self._child_plan(child) if child.convert is None else None
                    stack.append((plan, child, element, {}))
                    continue
                result = self._end(stack, element)
            if progress is not None and not end:
                progress(position)
            if end:
                return result

    @staticmethod
    def _end(
        stack: list[
            tuple[_Plan | None, _Child | None, ElementTree.Element, dict[str, Any]]
        ],
        element: ElementTree.Element,
    ) -> Any:
        """Bind the element that ends into its parent's fields; the root's object."""
        plan, child, _element, params = stack.pop()
        tail = element.tail
        if tail and not tail.isspace():
            raise Unsupported(f"text after {element.tag}")
        attrib = element.attrib
        if plan is None:
            assert (
                child is not None
                and child.convert is not None
                and child.empty is not None
            )
            if attrib and child.convert is _any_text:
                raise Unsupported(f"attributes of {element.tag}")
            text = element.text
            value = child.empty() if text is None else child.convert(text)
            if value is None:
                value = child.empty()
        else:
            if attrib:
                attributes = plan.attributes
                for qname, raw in attrib.items():
                    target = attributes.get(qname)
                    if target is not None:
                        params[target[0]] = target[1](raw)
                    elif qname in _XSI_BINDING:
                        raise Unsupported(f"{qname} on {element.tag}")
            if plan.text is not None and element.text is not None:
                params[plan.text[0]] = plan.text[1](element.text)
            try:
                value = plan.clazz(**params)
            except TypeError as exc:  # a required field missing: xsdata reports it
                raise Unsupported(f"{element.tag}: {exc}") from None
        if not stack:
            return value
        # The element is the last child of its parent so far: dropped, it leaves no memory.
        _parent_plan, _parent_child, parent_element, parent_params = stack[-1]
        del parent_element[-1]
        assert child is not None
        name = child.name
        if child.many:
            items = parent_params.get(name)
            if items is None:
                parent_params[name] = [value]
            else:
                items.append(value)
        elif name in parent_params:
            raise Unsupported(f"{element.tag} twice")
        else:
            parent_params[name] = value
        return None
