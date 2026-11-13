"""Read a bounded NRBF object graph as data without constructing runtime classes."""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Reference:
    id: int


@dataclass
class Record:
    type_name: str
    members: dict[str, Value]


type Value = bool | int | float | str | bytes | Reference | Record | list[Value] | None


@dataclass(frozen=True)
class Document:
    root: Reference
    objects: dict[int, Value]
    length: int

    def resolve(self, value: Value) -> Value:
        return self.objects[value.id] if isinstance(value, Reference) else value


@dataclass(frozen=True)
class _Class:
    name: str
    members: tuple[str, ...]
    types: tuple[tuple[int, int | None], ...]


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
        self.classes: dict[int, _Class] = {}
        self.libraries: set[int] = set()
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

    def type_info(self, kind: int) -> tuple[int, int | None]:
        primitive = None
        if kind in (0, 7):
            primitive = self.byte()
        elif kind == 3:
            self.string()
        elif kind == 4:
            self.string()
            self.library_refs.add(self.integer())
        elif kind not in (1, 2, 5, 6):
            raise ValueError(f"Unsupported PowerTool member type {kind}")
        return kind, primitive

    def add(self, identity: int, value: Value) -> Reference:
        if identity == 0 or identity in self.objects:
            raise ValueError("Duplicate or zero PowerTool object ID")
        self.objects[identity] = value
        return Reference(identity)

    def value(self, depth: int) -> Value | _Nulls:
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
            self.libraries.add(identity)
            self.string()
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
                if tag == 5:
                    self.library_refs.add(self.integer())
                metadata = _Class(name, members, types)
                self.classes[identity] = metadata
            record = Record(metadata.name, {})
            reference = self.add(identity, record)
            for name, (kind, primitive) in zip(
                metadata.members, metadata.types, strict=True
            ):
                value = (
                    self.primitive(primitive)
                    if kind == 0 and primitive is not None
                    else self.value(depth + 1)
                )
                if isinstance(value, _Nulls):
                    raise ValueError("Null run outside PowerTool array")
                record.members[name] = value
            return reference
        if tag == 6:
            identity = self.integer()
            return self.add(identity, self.string())
        if tag == 8:
            return self.primitive(self.byte())
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
                kind, primitive = self.type_info(self.byte())
            else:
                count = self.count(self.integer())
                kind, primitive = (0, self.byte()) if tag == 15 else (2, None)
            if kind == 0 and primitive == 2:
                return self.add(identity, self.take(count))
            values: list[Value] = []
            reference = self.add(identity, values)
            while len(values) < count:
                value = (
                    self.primitive(primitive)
                    if kind == 0 and primitive is not None
                    else self.value(depth + 1)
                )
                if isinstance(value, _Nulls):
                    if not 1 <= value.count <= count - len(values):
                        raise ValueError("Invalid PowerTool array null run")
                    values.extend([None] * value.count)
                else:
                    values.append(value)
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
        return Document(Reference(root), self.objects, self.offset)


def read_document(data: bytes) -> Document:
    """Parse supported records and retain references, including cycles, without executing them."""
    return _Reader(data).read()
