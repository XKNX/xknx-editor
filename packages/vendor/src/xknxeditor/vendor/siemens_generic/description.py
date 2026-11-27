"""Read parameter types, memory locations, object roles and dynamic pages from XML baggage."""

from __future__ import annotations

import re
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
    TIME_100MS = "time_100ms"
    TIME_10MS = "time_10ms"
    TIME_MS = "time_ms"
    TEXT = "text"
    UNKNOWN = "unknown"


# The milliseconds one unit of a time setting stands for: a time is stored as a whole number
# of its units.
TIME_UNIT_MS = {
    TypeKind.TIME_S: 1000,
    TypeKind.TIME_100MS: 100,
    TypeKind.TIME_10MS: 10,
    TypeKind.TIME_MS: 1,
}
TIME_KINDS = tuple(TIME_UNIT_MS)
# The kinds stored as a plain whole number.
INTEGER_KINDS = (TypeKind.NUMBER, *TIME_KINDS)


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
    minimum: int | None = None

    def enum_by_name(self, name: str) -> Enumeration | None:
        return next((e for e in self.enumerations if e.name == name), None)

    def enum_by_value(self, value: int) -> Enumeration | None:
        return next((e for e in self.enumerations if e.value == value), None)

    def enumeration(self, value: str) -> Enumeration | None:
        found = self.enum_by_name(value)
        if found is not None:
            return found
        try:
            return self.enum_by_value(integer(value))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class Parameter:
    id: str
    name: str
    type_id: str
    default: str
    text: str
    offset: int | None  # memory address, None when the parameter is not in memory
    format: str = ""  # e.g. "Percent"
    format_string: str = ""
    pattern: str = ""
    # A setting downloaded only while this ParameterRef's value is not 0: while it is, the
    # device keeps what it holds at the parameter's memory (see ``locked_parameters``).
    download_if: str = ""


@dataclass(frozen=True, slots=True)
class ParameterRef:
    id: str
    parameter_id: str
    default: str | None
    text: str
    global_variable: str = (
        ""  # e.g. "DeviceOrderNumber": the value comes from the device
    )
    hidden_enum_values: tuple[str, ...] = ()
    download_if: str = ""
    format: str = ""
    format_string: str = ""
    pattern: str = ""


class NodeKind(Enum):
    CHANNEL = "channel"
    INDEPENDENT = "independent"  # ChannelIndependentBlock
    BLOCK = "block"  # ParameterBlock: one page of settings
    CHOOSE = "choose"
    WHEN = "when"
    PARAMETER = "parameter"  # ParameterRefRef
    SEPARATOR = "separator"
    ASSIGN = "assign"
    OBJECT = "object"


@dataclass(frozen=True, slots=True)
class CommunicationObject:
    id: str
    number: int
    parent_value: str
    size: str
    text: str
    flags: dict[str, bool]


@dataclass(slots=True)
class Node:
    """One element of the parameter and communication-object tree."""

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
    channel: int = 0


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
    objects: dict[str, CommunicationObject] = field(
        default_factory=dict[str, CommunicationObject]
    )
    compact_blob: bool = False
    unsupported: tuple[str, ...] = ()
    allocation_size: int = 0
    hardware_types: tuple[bytes, ...] = ()
    application_ids: tuple[str, ...] = ()
    image_unsupported: tuple[str, ...] = ()
    # The ApplicationProgram's own HardwareType: the one hardware a plug-in of version 1
    # downloads to (later ones take ``hardware_types``).
    root_hardware_type: bytes | None = None
    # UseRelDownloadSubThisOffset of a CodeSegment: the parameter memory is written relative
    # to the application's segment, at an address less this offset.
    relative_offset: int | None = None
    # The version of the plug-in the product ships (its setup in the baggage), e.g. (2, 0, 37).
    plugin_version: tuple[int, int, int] | None = None

    def plugin_from(self, *version: int) -> bool:
        """Whether the product's plug-in is ``version`` or later (an unknown one is the
        latest)."""
        return self.plugin_version is None or self.plugin_version >= version

    @property
    def compatible_hardware(self) -> tuple[bytes, ...]:
        """The hardware types (PID 78 of the device object) a download is allowed to."""
        if not self.plugin_from(2):
            return (self.root_hardware_type,) if self.root_hardware_type else ()
        return self.hardware_types

    @property
    def image_origin(self) -> int | None:
        """The address byte 0 of a stored memory image stands for: 0 for a plug-in of
        version 1 (its image holds the whole address space), else the memory base."""
        if not self.plugin_from(2):
            return 0 if self.memory_base is not None else None
        return self.memory_base

    @property
    def download_offset(self) -> int | None:
        """What a download subtracts from a parameter's address to write it relative to the
        application's segment; None when it writes the addresses themselves (a plug-in
        before version 2.0.24 knows no relative download)."""
        return self.relative_offset if self.plugin_from(2, 0, 24) else None

    @property
    def memory_base(self) -> int | None:
        """The lowest parameter memory address (byte 0 of a stored memory image from plug-in
        version 2 on, see ``image_origin``)."""
        offsets = [
            p.offset
            for p in self.parameters.values()
            if p.offset is not None and p.offset > 0
        ]
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
    schema = root.get(
        "{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation", ""
    )
    legacy_order = bool(re.search(r"GenericPlugIn_[123]\.xsd$", schema, re.IGNORECASE))
    types = {
        t.id: t
        for t in (_parse_type(e, legacy_order) for e in root.iter("ParameterType"))
    }
    segments = {
        e.get("Id", ""): _int(e.get("Address")) or 0
        for e in root.iterfind("Static/CodeSegments/CodeSegment")
    }
    allocation_lengths = [
        _int(e.get("AllocLength")) or 0
        for e in root.iterfind("Static/CodeSegments/CodeSegment")
    ]
    parameters: dict[str, Parameter] = {}
    parent_by_child = {child: parent for parent in root.iter() for child in parent}
    for e in root.iterfind("Static/Parameters//Parameter"):
        parent = parent_by_child[e]
        memory = parent if parent.tag == "Union" else e.find("Memory")
        offset_text = memory.get("Offset") if memory is not None else None
        offset = _int(offset_text)
        segment_id = memory.get("CodeSegment", "") if memory is not None else ""
        if offset is not None:
            if segment_id and segment_id not in segments:
                offset = None
            else:
                offset += segments.get(segment_id, 0)
        pid = e.get("Id", "")
        parameters[pid] = Parameter(
            id=pid,
            name=e.get("Name", ""),
            type_id=e.get("RefId", ""),
            default=e.get("Value", ""),
            text=clean_text(e.get("Text", "")),
            offset=offset,
            format=e.get("Format", ""),
            format_string=e.get("FormatString", ""),
            pattern=e.get("RegEx", ""),
            download_if=e.get("DownloadIfParameterRefId", ""),
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
            hidden_enum_values=tuple(
                filter(None, re.split(r"[;,]\s*", e.get("HiddenEnumValues", "")))
            ),
            download_if=e.get("DownloadIfParameterRefId", ""),
            format=e.get("Format", ""),
            format_string=e.get("FormatString", ""),
            pattern=e.get("RegEx", ""),
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

    def assign_channels(nodes: list[Node], next_channel: int = 0) -> int:
        for node in nodes:
            if node.kind is NodeKind.CHANNEL:
                next_channel += 1
                node.channel = next_channel
            next_channel = assign_channels(node.children, next_channel)
        return next_channel

    assign_channels(dynamic)
    objects: dict[str, CommunicationObject] = {}
    definitions = {
        e.get("Id", ""): e for e in root.iterfind("Static/ComObjectTable/ComObject")
    }
    for ref in root.iterfind("Static/ComObjectRefs/ComObjectRef"):
        obj = definitions.get(ref.get("RefId", ""))
        if obj is None:
            continue
        attributes = {**obj.attrib, **{k: v for k, v in ref.attrib.items() if v}}
        object_id = ref.get("Id", "")
        size = attributes.get("Size", "")
        parent_value = attributes.get("ParentValue", "0")
        if parent_value == "0":
            quantity, unit = size.split(maxsplit=1)
            parent_value = str(
                int(quantity) * (8 if unit.lower().startswith("byte") else 1)
            )
        objects[object_id] = CommunicationObject(
            object_id,
            _int(attributes.get("Number")) or 0,
            parent_value,
            size,
            clean_text(attributes.get("Text", "")),
            {
                name: attributes.get(attribute) == "Enabled"
                for name, attribute in {
                    "communication": "CommunicationFlag",
                    "read": "ReadFlag",
                    "write": "WriteFlag",
                    "transmit": "TransmitFlag",
                    "update": "UpdateFlag",
                    "read_on_init": "ReadOnInitFlag",
                }.items()
            },
        )
    return Description(
        id=root.get("Id", ""),
        types=types,
        parameters=parameters,
        refs=refs,
        dynamic=dynamic,
        texts=texts,
        refs_by_parameter=by_parameter,
        objects=objects,
        compact_blob=bool(re.search(r"GenericPlugIn_4\.xsd$", schema, re.IGNORECASE)),
        allocation_size=(
            allocation_lengths[0]
            if legacy_order and allocation_lengths
            else max(allocation_lengths, default=0)
        ),
        hardware_types=tuple(
            bytes.fromhex(e.get("Data", ""))
            for e in root.iterfind(
                "Static/DeviceDownloadCompatibility/HardwareTypes/HwType"
            )
        ),
        application_ids=tuple(
            e.get("AppId", "")
            for e in root.iterfind(
                "Static/DeviceDownloadCompatibility/ApplicationIDs/ApplicationID"
            )
        ),
        root_hardware_type=_hex(root.get("HardwareType")),
        relative_offset=next(
            (
                offset
                for e in root.iterfind("Static/CodeSegments/CodeSegment")
                if (offset := _int(e.get("UseRelDownloadSubThisOffset"))) is not None
            ),
            None,
        ),
        image_unsupported=tuple(
            sorted(
                {
                    e.tag
                    for e in root.iter()
                    if e.tag in {"Union", "Property"}
                    or (
                        "DownloadIfParameterRefId" in e.attrib
                        and e.tag not in {"Parameter", "ParameterRef"}
                    )
                    or (e.tag == "CodeSegment" and e.get("Data"))
                    or (e.tag == "Memory" and e.get("CodeSegment", "") not in segments)
                }
            )
        ),
        unsupported=tuple(
            sorted(
                {
                    e.tag
                    for e in dynamic_root.iter()
                    if e is not dynamic_root and e.tag not in _TAGS
                }
            )
        )
        if dynamic_root is not None
        else (),
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


def _hex(text: str | None) -> bytes | None:
    try:
        return bytes.fromhex(text) if text else None
    except ValueError:
        return None


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return integer(text)
    except ValueError:
        return None


def integer(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") else int(text)


_KINDS = {
    "TypeRestriction": TypeKind.ENUM,
    "TypeNumber": TypeKind.NUMBER,
    "TypeTimeSec": TypeKind.TIME_S,
    "TypeTimeSec10": TypeKind.TIME_100MS,
    "TypeTimeSec100": TypeKind.TIME_10MS,
    "TypeTimeMSec": TypeKind.TIME_MS,
    "TypeText": TypeKind.TEXT,
}


def _parse_type(e: ET.Element, legacy_order: bool = False) -> ParameterType:
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
        little_endian=legacy_order or body.get("IntelOrderSwap") in ("true", "1"),
        signed=body.get("Type") == "signed",
        enumerations=tuple(enums),
        maximum=_int(body.get("maxInclusive")),
        minimum=_int(body.get("minInclusive")),
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
    "ComObjectRefRef": NodeKind.OBJECT,
}


def _parse_node(e: ET.Element, texts: dict[str, str]) -> Node | None:
    kind = _TAGS.get(e.tag)
    if kind is None:
        return None
    node = Node(kind=kind)
    if kind is NodeKind.CHOOSE:
        node.id = e.get("ParamRefId", "")
        node.search_active = e.get("SearchForActiveReferenceValue") == "true"
    elif kind is NodeKind.WHEN:
        test = e.get("test")
        node.tests = (
            tuple(t.strip() for t in re.split(r"[;,]", test) if t.strip())
            if test is not None
            else ()
        )
        node.default = e.get("default") == "true"
    elif kind in (NodeKind.PARAMETER, NodeKind.OBJECT):
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


def dynamic_refs(nodes: list[Node]) -> list[str]:
    """The ParameterRefs of the Dynamic tree, in every branch: the settings it shows and the
    targets of its Assigns."""
    found: list[str] = []
    for node in nodes:
        if node.kind is NodeKind.PARAMETER and node.id:
            found.append(node.id)
        elif node.kind is NodeKind.ASSIGN and node.target:
            found.append(node.target)
        found.extend(dynamic_refs(node.children))
    return found


def locked_parameters(
    description: Description, values: dict[str, str], active: list[str]
) -> set[str]:
    """The parameters a download leaves as the device holds them: those whose download
    condition (``DownloadIfParameterRefId``, of the ParameterRef or else of the parameter)
    names a setting of value 0 in ``values``. A plug-in from version 2.0.24 on checks every
    setting of the Dynamic tree, an earlier one only the ``active`` ones and only their
    ParameterRef's condition; one of version 1 knows no condition."""
    if not description.plugin_from(2):
        return set()
    every = description.plugin_from(2, 0, 24)
    locked: set[str] = set()
    for ref_id in dynamic_refs(description.dynamic) if every else active:
        ref = description.refs.get(ref_id)
        if ref is None:
            continue
        parameter = description.parameters.get(ref.parameter_id)
        if parameter is None:
            continue
        condition = ref.download_if or (parameter.download_if if every else "")
        if condition and _is_zero(description, condition, values.get(condition)):
            locked.add(parameter.id)
    return locked


def _is_zero(description: Description, ref_id: str, value: str | None) -> bool:
    """Whether a setting holds 0 (an enumeration by the value of its choice); a setting
    without a value is not 0."""
    if value is None:
        return False
    ref = description.refs.get(ref_id)
    parameter = description.parameters.get(ref.parameter_id) if ref else None
    ptype = description.types.get(parameter.type_id) if parameter else None
    enum = ptype.enum_by_name(value) if ptype is not None else None
    return (str(enum.value) if enum is not None else value) == "0"
