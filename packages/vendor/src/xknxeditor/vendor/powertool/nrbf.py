"""Read a bounded NRBF object graph as data without constructing runtime classes."""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Reference:
    id: int


@dataclass
class Record:
    type_name: str
    members: dict[str, Value]


type Value = bool | int | float | str | bytes | Reference | Record | list[Value] | None


@dataclass(frozen=True)
class TypeInfo:
    kind: int
    detail: int | str | None = None
    library: int | None = None


@dataclass(frozen=True)
class ClassInfo:
    name: str
    members: tuple[str, ...]
    types: tuple[TypeInfo, ...]
    library: int | None = None


@dataclass(frozen=True)
class ArrayInfo:
    tag: int
    element: TypeInfo
    shape: int = 0


@dataclass(frozen=True)
class Document:
    root: Reference
    objects: dict[int, Value]
    length: int
    libraries: dict[int, str] = field(default_factory=lambda: {})
    classes: dict[int, ClassInfo] = field(default_factory=lambda: {})
    arrays: dict[int, ArrayInfo] = field(default_factory=lambda: {})
    primitives: dict[tuple[int, str | int], int] = field(default_factory=lambda: {})

    def resolve(self, value: Value) -> Value:
        return self.objects[value.id] if isinstance(value, Reference) else value


@dataclass(frozen=True)
class _Primitive:
    kind: int
    value: Value


@dataclass(frozen=True)
class _Nulls:
    count: int


class _Reader:
    def __init__(self, data: bytes) -> None:
        if not 17 <= len(data) <= 32 * 1024 * 1024:
            raise ValueError("Invalid or oversized PowerTool blob")
        self.data = data
        self.offset = 0
        self.objects: dict[int, Value] = {}
        self.classes: dict[int, ClassInfo] = {}
        self.arrays: dict[int, ArrayInfo] = {}
        self.primitives: dict[tuple[int, str | int], int] = {}
        self.libraries: dict[int, str] = {}
        self.library_refs: set[int] = set()
        self.references: set[int] = set()
        self.cells = 0
        self.records = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("Truncated PowerTool blob")
        data = self.data[self.offset : end]
        self.offset = end
        return data

    def byte(self) -> int:
        return self.take(1)[0]

    def integer(self) -> int:
        return int.from_bytes(self.take(4), "little", signed=True)

    def count(self, value: int) -> int:
        self.cells += value
        if value < 0 or self.cells > 1_000_000:
            raise ValueError("PowerTool blob collection limit exceeded")
        return value

    def string(self) -> str:
        length = 0
        for shift in range(0, 35, 7):
            byte = self.byte()
            length |= (byte & 127) << shift
            if not byte & 128:
                if length > 1024 * 1024:
                    raise ValueError("PowerTool blob string limit exceeded")
                return self.take(length).decode("utf-8", errors="strict")
        raise ValueError("Invalid PowerTool string length")

    def primitive(self, kind: int) -> Value:
        formats = {
            2: "B",
            6: "d",
            7: "h",
            8: "i",
            9: "q",
            10: "b",
            11: "f",
            12: "q",
            13: "Q",
            14: "H",
            15: "I",
            16: "Q",
        }
        if kind == 1:
            value = self.byte()
            if value not in (0, 1):
                raise ValueError("Invalid PowerTool Boolean")
            return bool(value)
        if kind == 3:
            first = self.byte()
            size = 1 if first < 128 else 2 if 0xC2 <= first <= 0xDF else 3
            return (bytes([first]) + self.take(size - 1)).decode("utf-8")
        if kind == 5 or kind == 18:
            return self.string()
        if kind == 17:
            return None
        if kind not in formats:
            raise ValueError(f"Unsupported PowerTool primitive {kind}")
        fmt = "<" + formats[kind]
        value: int | float = struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]
        return value

    def type_info(self, kind: int) -> TypeInfo:
        detail: int | str | None = None
        library = None
        if kind in (0, 7):
            detail = self.byte()
        elif kind in (3, 4):
            detail = self.string()
            if kind == 4:
                library = self.integer()
                self.library_refs.add(library)
        elif kind not in (1, 2, 5, 6):
            raise ValueError(f"Unsupported PowerTool member type {kind}")
        return TypeInfo(kind, detail, library)

    def unbox(self, value: Value | _Primitive, identity: int, slot: str | int) -> Value:
        if isinstance(value, _Primitive):
            self.primitives[identity, slot] = value.kind
            return value.value
        return value

    def add(self, identity: int, value: Value) -> Reference:
        if identity == 0 or identity in self.objects:
            raise ValueError("Duplicate or zero PowerTool object ID")
        self.objects[identity] = value
        return Reference(identity)

    def value(self, depth: int) -> Value | _Nulls | _Primitive:
        if depth > 128:
            raise ValueError("PowerTool blob nesting limit exceeded")
        while True:
            self.records += 1
            if self.records > 200_000:
                raise ValueError("PowerTool blob record limit exceeded")
            tag = self.byte()
            if tag != 12:
                break
            identity = self.integer()
            if identity <= 0 or identity in self.libraries:
                raise ValueError("Invalid PowerTool library ID")
            self.libraries[identity] = self.string()
        if tag in (1, 4, 5):
            identity = self.integer()
            if tag == 1:
                metadata_id = self.integer()
                if metadata_id not in self.classes:
                    raise ValueError("Missing PowerTool class metadata")
                metadata = self.classes[metadata_id]
                self.count(len(metadata.members))
            else:
                name = self.string()
                count = self.count(self.integer())
                members = tuple(self.string() for _ in range(count))
                if len(set(members)) != count:
                    raise ValueError("Duplicate PowerTool member")
                kinds = self.take(count)
                types = tuple(self.type_info(k) for k in kinds)
                library = self.integer() if tag == 5 else None
                if library is not None:
                    self.library_refs.add(library)
                metadata = ClassInfo(name, members, types, library)
            self.classes[identity] = metadata
            record = Record(metadata.name, {})
            reference = self.add(identity, record)
            for name, info in zip(metadata.members, metadata.types, strict=True):
                value = (
                    self.primitive(info.detail)
                    if info.kind == 0 and isinstance(info.detail, int)
                    else self.value(depth + 1)
                )
                if isinstance(value, _Nulls):
                    raise ValueError("Null run outside PowerTool array")
                record.members[name] = self.unbox(value, identity, name)
            return reference
        if tag == 6:
            identity = self.integer()
            return self.add(identity, self.string())
        if tag == 8:
            kind = self.byte()
            return _Primitive(kind, self.primitive(kind))
        if tag == 9:
            identity = self.integer()
            self.references.add(identity)
            return Reference(identity)
        if tag == 10:
            return None
        if tag in (13, 14):
            return _Nulls(self.byte() if tag == 13 else self.integer())
        if tag in (7, 15, 16, 17):
            identity = self.integer()
            if tag == 7:
                shape = self.byte()
                rank = self.integer()
                if shape not in (0, 1) or rank != 1:
                    raise ValueError("Unsupported PowerTool array shape")
                count = self.count(self.integer())
                info = self.type_info(self.byte())
            else:
                count = self.count(self.integer())
                shape = 0
                info = (
                    TypeInfo(0, self.byte())
                    if tag == 15
                    else TypeInfo(1 if tag == 17 else 2)
                )
            self.arrays[identity] = ArrayInfo(tag, info, shape)
            if info.kind == 0 and info.detail == 2:
                return self.add(identity, self.take(count))
            values: list[Value] = []
            reference = self.add(identity, values)
            while len(values) < count:
                value = (
                    self.primitive(info.detail)
                    if info.kind == 0 and isinstance(info.detail, int)
                    else self.value(depth + 1)
                )
                if isinstance(value, _Nulls):
                    if not 1 <= value.count <= count - len(values):
                        raise ValueError("Invalid PowerTool array null run")
                    values.extend([None] * value.count)
                else:
                    values.append(self.unbox(value, identity, len(values)))
            return reference
        raise ValueError(f"Unsupported PowerTool NRBF record {tag}")

    def read(self) -> Document:
        if self.byte() != 0:
            raise ValueError("Invalid PowerTool blob header")
        root = self.integer()
        self.integer()
        if self.integer() != 1 or self.integer() != 0:
            raise ValueError("Unsupported PowerTool blob version")
        while self.offset < len(self.data) and self.data[self.offset] != 11:
            if isinstance(self.value(0), _Nulls):
                raise ValueError("Null run outside PowerTool array")
        if self.byte() != 11 or any(self.data[self.offset :]):
            raise ValueError("Invalid PowerTool blob end or padding")
        if root not in self.objects or not self.references.issubset(self.objects):
            raise ValueError("Missing PowerTool object reference")
        if not self.library_refs.issubset(self.libraries):
            raise ValueError("Missing PowerTool library reference")
        return Document(
            Reference(root),
            self.objects,
            self.offset,
            self.libraries,
            self.classes,
            self.arrays,
            self.primitives,
        )


def read_document(data: bytes) -> Document:
    """Parse supported records and retain references, including cycles, without executing them."""
    return _Reader(data).read()


class _Writer:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.data = bytearray()
        self.emitted: set[int] = set()
        self.metadata: dict[ClassInfo, int] = {}
        self.pending: deque[int] = deque()
        self.queued: set[int] = set()

    def integer(self, value: int) -> None:
        self.data.extend(struct.pack("<i", value))

    def string(self, value: str) -> None:
        encoded = value.encode("utf-8")
        length = len(encoded)
        while length >= 128:
            self.data.append((length & 127) | 128)
            length >>= 7
        self.data.append(length)
        self.data.extend(encoded)

    def primitive(self, kind: int, value: Value) -> None:
        formats = {
            1: "?",
            2: "B",
            6: "d",
            7: "h",
            8: "i",
            9: "q",
            10: "b",
            11: "f",
            12: "q",
            13: "Q",
            14: "H",
            15: "I",
            16: "Q",
        }
        if kind == 17 and value is None:
            return
        if kind in (3, 5, 18) and isinstance(value, str):
            if kind == 3:
                self.data.extend(value.encode("utf-8"))
            else:
                self.string(value)
        elif kind in formats and isinstance(value, (int, float)):
            self.data.extend(struct.pack("<" + formats[kind], value))
        else:
            raise ValueError("Invalid PowerTool primitive value")

    def type_info(self, info: TypeInfo) -> None:
        if info.kind in (0, 7) and isinstance(info.detail, int):
            self.data.append(info.detail)
        elif info.kind in (3, 4) and isinstance(info.detail, str):
            self.string(info.detail)
            if info.kind == 4 and info.library is not None:
                self.integer(info.library)

    def value(self, value: Value, primitive: int | None = None, depth: int = 0) -> None:
        if depth > 128 or len(self.data) > 32 * 1024 * 1024:
            raise ValueError("PowerTool blob serialization limit exceeded")
        if primitive is not None:
            self.data.extend((8, primitive))
            self.primitive(primitive, value)
        elif value is None:
            self.data.append(10)
        elif isinstance(value, Reference):
            if value.id > 0 or value.id in self.emitted:
                if value.id not in self.emitted and value.id not in self.queued:
                    self.pending.append(value.id)
                    self.queued.add(value.id)
                self.data.append(9)
                self.integer(value.id)
            else:
                self.object(value.id, depth + 1)
        else:
            raise ValueError("PowerTool object requires a typed reference")

    def object(self, identity: int, depth: int) -> None:
        self.emitted.add(identity)
        value = self.document.objects[identity]
        if isinstance(value, str):
            self.data.append(6)
            self.integer(identity)
            self.string(value)
        elif isinstance(value, Record):
            info = self.document.classes[identity]
            if tuple(value.members) != info.members:
                raise ValueError("PowerTool class members do not match metadata")
            previous = self.metadata.get(info)
            if previous is not None:
                self.data.append(1)
                self.integer(identity)
                self.integer(previous)
            else:
                self.metadata[info] = identity
                self.data.append(4 if info.library is None else 5)
                self.integer(identity)
                self.string(info.name)
                self.integer(len(info.members))
                for member in info.members:
                    self.string(member)
                self.data.extend(t.kind for t in info.types)
                for member_type in info.types:
                    self.type_info(member_type)
                if info.library is not None:
                    self.integer(info.library)
            for name, member_type in zip(info.members, info.types, strict=True):
                member = value.members[name]
                if member_type.kind == 0 and isinstance(member_type.detail, int):
                    self.primitive(member_type.detail, member)
                else:
                    self.value(
                        member, self.document.primitives.get((identity, name)), depth
                    )
        elif isinstance(value, (list, bytes)):
            array = self.document.arrays[identity]
            self.data.append(array.tag)
            self.integer(identity)
            if array.tag == 7:
                self.data.append(array.shape)
                self.integer(1)
            self.integer(len(value))
            if array.tag == 7:
                self.data.append(array.element.kind)
                self.type_info(array.element)
            elif array.tag == 15:
                assert isinstance(array.element.detail, int)
                self.data.append(array.element.detail)
            if isinstance(value, bytes):
                self.data.extend(value)
            else:
                for index, item in enumerate(value):
                    if array.element.kind == 0 and isinstance(
                        array.element.detail, int
                    ):
                        self.primitive(array.element.detail, item)
                    else:
                        self.value(
                            item, self.document.primitives.get((identity, index)), depth
                        )
        else:
            raise ValueError("Unsupported PowerTool serialized object")


def write_document(document: Document) -> bytes:
    """Serialize a typed graph without loading or instantiating any runtime classes."""
    writer = _Writer(document)
    writer.data.append(0)
    for number in (document.root.id, -1, 1, 0):
        writer.integer(number)
    for identity, library in document.libraries.items():
        writer.data.append(12)
        writer.integer(identity)
        writer.string(library)
    try:
        writer.object(document.root.id, 0)
        while writer.pending:
            identity = writer.pending.popleft()
            if identity not in writer.emitted:
                writer.object(identity, 0)
    except (KeyError, OverflowError, struct.error) as exc:
        raise ValueError("Invalid PowerTool serialization graph") from exc
    writer.data.append(11)
    data = bytes(writer.data)
    read_document(data)
    return data
