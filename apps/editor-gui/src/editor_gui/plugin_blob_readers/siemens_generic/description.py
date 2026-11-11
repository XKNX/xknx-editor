"""Parser for the application description the Siemens ETS3 Generic Plug-in runs on.

Siemens shipped many System B devices of around 2009 (e.g. the N 512/11 switching actuator) with
an ETS application that holds no real settings: its parameters only switch group objects on and
off, and an ``<Extension>`` hands the user interface, download and data handling to the "ETS3
Generic Plug-In". The real application is described in a plain XML baggage next to it, in the
plug-in's own pre-ETS4 dialect (``GenericPlugIn_4.xsd``, no KNX namespace): parameter types,
parameters with memory offsets, parameter refs with per-context defaults and a ``Dynamic`` tree of
channels, parameter blocks and ``choose``/``when``. Translation baggages (``<Languages>``) carry
the texts per language.

Only these XML files are read; the plug-in program itself is never needed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum

# The plug-in writes the text attributes with escaped line breaks ("\\r\\n").
_ESCAPED_BREAKS = ("\\r\\n", "\\n", "\\r")


def clean_text(text: str) -> str:
    """A description text as one line: escaped and real line breaks become spaces."""
    for brk in _ESCAPED_BREAKS:
        text = text.replace(brk, " ")
    return " ".join(text.split())


class TypeKind(Enum):
    ENUM = "enum"
    NUMBER = "number"
    TIME_S = "time_s"
    TIME_MS = "time_ms"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Enumeration:
    id: str
    name: str  # the symbolic value stored in the plug-in settings, e.g. "yn_Yes"
    text: str
    value: int  # the value written to memory


@dataclass(frozen=True, slots=True)
class ParameterType:
    id: str
    kind: TypeKind
    size_bits: int
    little_endian: bool = False
    signed: bool = False
    enumerations: tuple[Enumeration, ...] = ()
    maximum: int | None = None  # maxInclusive of a number type

    @property
    def full_scale(self) -> int | None:
        """The raw value that stands for 100 % of a "Percent" number: its maxInclusive, else
        the largest number its size holds."""
        if self.maximum is not None and self.maximum > 0:
            return self.maximum
        if 0 < self.size_bits <= 32:
            return (1 << self.size_bits) - 1
        return None

    def enum_by_name(self, name: str) -> Enumeration | None:
        return next((e for e in self.enumerations if e.name == name), None)

    def enum_by_value(self, value: int) -> Enumeration | None:
        return next((e for e in self.enumerations if e.value == value), None)


@dataclass(frozen=True, slots=True)
class Parameter:
    id: str
    name: str
    type_id: str
    default: str
    text: str
    offset: int | None  # memory address, None when the parameter is not in memory
    format: str = ""  # e.g. "Percent"


@dataclass(frozen=True, slots=True)
class ParameterRef:
    id: str
    parameter_id: str
    default: str | None
    text: str
    global_variable: str = (
        ""  # e.g. "DeviceOrderNumber": the value comes from the device
    )


class NodeKind(Enum):
    CHANNEL = "channel"
    INDEPENDENT = "independent"  # ChannelIndependentBlock
    BLOCK = "block"  # ParameterBlock: one page of settings
    CHOOSE = "choose"
    WHEN = "when"
    PARAMETER = "parameter"  # ParameterRefRef
    SEPARATOR = "separator"
    ASSIGN = "assign"


@dataclass(slots=True)
class Node:
    """One element of the ``Dynamic`` tree, reduced to what a read-only view needs."""

    kind: NodeKind
    id: str = (
        ""  # block/separator/channel id; ParamRefId of a choose; RefId of a parameter
    )
    text: str = ""
    tests: tuple[str, ...] = ()  # a when's test values; empty with ``default``
    default: bool = False  # a when marked default="true"
    search_active: bool = False  # a choose with SearchForActiveReferenceValue="true"
    target: str = ""  # an assign's TargetParamRefRef
    source: str = ""  # an assign's SourceParamRefRef
    value: str | None = None  # an assign's Value
    children: list[Node] = field(default_factory=list["Node"])


@dataclass(slots=True)
class Description:
    """A parsed Generic Plug-in application description."""

    id: str
    types: dict[str, ParameterType]
    parameters: dict[str, Parameter]
    refs: dict[str, ParameterRef]
    dynamic: list[Node]
    # Texts of blocks, separators and parameters by id, as in the description itself.
    texts: dict[str, str]
    refs_by_parameter: dict[str, list[str]] = field(
        default_factory=dict[str, "list[str]"]
    )

    @property
    def memory_base(self) -> int | None:
        """The lowest parameter memory address: byte 0 of a stored memory image."""
        offsets = [p.offset for p in self.parameters.values() if p.offset is not None]
        return min(offsets) if offsets else None


def is_description(root: ET.Element) -> bool:
    """Whether ``root`` is a Generic Plug-in application description."""
    return (
        root.tag == "ApplicationProgram"
        and root.find("Static/Parameters") is not None
        and root.find("Dynamic") is not None
    )


def parse_description(data: bytes) -> Description | None:
    """The description in ``data``, or None when it is not one (or not XML)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if not is_description(root):
        return None
    types = {t.id: t for t in (_parse_type(e) for e in root.iter("ParameterType"))}
    parameters: dict[str, Parameter] = {}
    for e in root.iterfind("Static/Parameters/Parameter"):
        memory = e.find("Memory")
        offset_text = memory.get("Offset") if memory is not None else None
        pid = e.get("Id", "")
        parameters[pid] = Parameter(
            id=pid,
            name=e.get("Name", ""),
            type_id=e.get("RefId", ""),
            default=e.get("Value", ""),
            text=clean_text(e.get("Text", "")),
            offset=_int(offset_text),
            format=e.get("Format", ""),
        )
    refs: dict[str, ParameterRef] = {}
    by_parameter: dict[str, list[str]] = {}
    for e in root.iterfind("Static/ParameterRefs/ParameterRef"):
        rid = e.get("Id", "")
        ref = ParameterRef(
            id=rid,
            parameter_id=e.get("RefId", ""),
            default=e.get("Value"),
            text=clean_text(e.get("Text", "")),
            global_variable=e.get("GlobalVariableReference", ""),
        )
        refs[rid] = ref
        by_parameter.setdefault(ref.parameter_id, []).append(rid)
    texts: dict[str, str] = {}
    dynamic_root = root.find("Dynamic")
    dynamic = (
        [n for n in (_parse_node(c, texts) for c in dynamic_root) if n is not None]
        if dynamic_root is not None
        else []
    )
    return Description(
        id=root.get("Id", ""),
        types=types,
        parameters=parameters,
        refs=refs,
        dynamic=dynamic,
        texts=texts,
        refs_by_parameter=by_parameter,
    )


def parse_translations(data: bytes) -> dict[str, dict[str, str]]:
    """The ``Text`` translations of a translation file: {language: {ref id: text}}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for language in root.iterfind("Languages/Language"):
        code = language.get("LanguageId", "")
        if not code:
            continue
        texts = out.setdefault(code, {})
        for item in language.iterfind("Data"):
            ref = item.get("RefId", "")
            for tr in item.iterfind("Translation"):
                if tr.get("AttributeName") == "Text" and ref:
                    texts[ref] = clean_text(tr.get("Text", ""))
    return out


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text, 0) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return None


_KINDS = {
    "TypeRestriction": TypeKind.ENUM,
    "TypeNumber": TypeKind.NUMBER,
    "TypeTimeSec": TypeKind.TIME_S,
    "TypeTimeMSec": TypeKind.TIME_MS,
    "TypeText": TypeKind.TEXT,
}


def _parse_type(e: ET.Element) -> ParameterType:
    tid = e.get("Id", "")
    body = next(iter(e), None)
    if body is None:
        return ParameterType(id=tid, kind=TypeKind.UNKNOWN, size_bits=0)
    kind = _KINDS.get(body.tag, TypeKind.UNKNOWN)
    enums: list[Enumeration] = []
    for en in body.iterfind("Enumeration"):
        value = _int(en.get("Value"))
        if value is None:
            continue
        enums.append(
            Enumeration(
                id=en.get("Id", ""),
                name=en.get("Name", ""),
                text=clean_text(en.get("Text", "") or en.get("Name", "")),
                value=value,
            )
        )
    return ParameterType(
        id=tid,
        kind=kind,
        size_bits=_int(body.get("Size")) or 0,
        little_endian=body.get("IntelOrderSwap") == "true",
        signed=body.get("Type") == "signed",
        enumerations=tuple(enums),
        maximum=_int(body.get("maxInclusive")),
    )


_TAGS = {
    "Channel": NodeKind.CHANNEL,
    "ChannelIndependentBlock": NodeKind.INDEPENDENT,
    "ParameterBlock": NodeKind.BLOCK,
    "choose": NodeKind.CHOOSE,
    "when": NodeKind.WHEN,
    "ParameterRefRef": NodeKind.PARAMETER,
    "ParameterSeparator": NodeKind.SEPARATOR,
    "Assign": NodeKind.ASSIGN,
}


def _parse_node(e: ET.Element, texts: dict[str, str]) -> Node | None:
    kind = _TAGS.get(e.tag)
    if kind is None:
        return None  # ComObjectRefRef and anything else a parameter view does not need
    node = Node(kind=kind)
    if kind is NodeKind.CHOOSE:
        node.id = e.get("ParamRefId", "")
        node.search_active = e.get("SearchForActiveReferenceValue") == "true"
    elif kind is NodeKind.WHEN:
        test = e.get("test")
        node.tests = tuple(test.split(";")) if test is not None else ()
        node.default = e.get("default") == "true"
    elif kind is NodeKind.PARAMETER:
        node.id = e.get("RefId", "")
    elif kind is NodeKind.ASSIGN:
        node.target = e.get("TargetParamRefRef", "")
        node.source = e.get("SourceParamRefRef", "")
        node.value = e.get("Value")
    else:
        node.id = e.get("Id", "")
        node.text = clean_text(e.get("Text", ""))
        if node.id and node.text:
            texts[node.id] = node.text
    node.children = [n for n in (_parse_node(c, texts) for c in e) if n is not None]
    return node
