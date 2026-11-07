"""What the description of a project takes from an application program, read once per program.

A parsed program takes tens of megabytes and only a few stay in memory (see
``xknxeditor.prod.application``), so the description of a project keeps just this: the group
objects and their references, the channels and the module arguments, with the attribute values
the program holds (texts in the product language the program was read in).
"""

from __future__ import annotations

import dataclasses
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from xknxproject.util import parse_semantics_dpas, parse_semantics_functional_blocks

from editor_gui.languages import product_text_languages
from xknxeditor.namespaces.intermediate.application_program_channel_t import (
    ApplicationProgramChannel,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_com_objects_com_object import (
    ModuleDefStaticComObjectsComObject,
)
from xknxeditor.namespaces.intermediate.module_t_numeric_arg import ModuleNumericArg

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.application_program_t import (
        ApplicationProgram,
    )
    from xknxeditor.namespaces.intermediate.com_object_ref_t import ComObjectRef
    from xknxeditor.namespaces.intermediate.com_object_t import ComObject
    from xknxeditor.namespaces.intermediate.module_def_t import ModuleDef
    from xknxeditor.prod.application import Application

# The flags in the order of ObjectDef.flags.
FLAG_NAMES = (
    "read",
    "write",
    "communication",
    "transmit",
    "update",
    "read_on_init",
)
# How many digests stay in memory: far more programs than a project uses.
_CACHE_SIZE = 256
# The names in a field's type, and those of plain values.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLAIN_TYPES = frozenset(
    {"None", "str", "int", "bool", "float", "bytes", "Decimal", "object"}
)
_ELEMENT_FIELDS: dict[type, tuple[str, ...]] = {}


@dataclass(frozen=True, slots=True)
class ObjectDef:
    """A ``ComObject`` or ``ComObjectRef``: what it sets, None where it sets nothing."""

    name: str | None
    text: str | None
    function_text: str | None
    object_size: str | None
    # read, write, communication, transmit, update, read on init (see FLAG_NAMES)
    flags: tuple[bool | None, ...]
    # its DatapointType, space-separated, "" for none
    datapoint_types: str
    # ComObjectRef only: the ComObject it refers to, its text parameter and its semantics
    ref_id: str | None = None
    text_parameter_ref_id: str | None = None
    dpas: tuple[str, ...] | None = None
    # ComObject only: its number, and the module argument its number is based on
    number: int | None = None
    base_number: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelDef:
    """An ``ApplicationProgramChannel``."""

    name: str
    text: str | None
    text_parameter_ref_id: str | None
    functional_blocks: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class ArgumentDef:
    """A module argument: its name, and whether the program computes its value (from an
    allocator or a base value), which then has no literal value."""

    name: str
    allocates: int | None
    computed: bool


@dataclass(frozen=True, slots=True)
class ApplicationDigest:
    """The parts of one application program, by their full ids."""

    application_id: str
    objects: dict[str, ObjectDef]
    refs: dict[str, ObjectDef]
    channels: dict[str, ChannelDef]
    arguments: dict[str, ArgumentDef]


_cache: dict[tuple[str, str, str], ApplicationDigest] = {}
_cache_lock = threading.Lock()


def application_digest(app: Application) -> ApplicationDigest:
    """The digest of ``app``, read from its program the first time (which loads the program
    when it is not in memory). Its texts are in the product languages the program was read in,
    so a digest is kept per those languages."""
    key = (app.id, app.version, product_text_languages())
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    digest = digest_program(app.program)
    with _cache_lock:
        if len(_cache) >= _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = digest
    return digest


def digest_program(program: ApplicationProgram) -> ApplicationDigest:
    """Read the digest of a parsed application program."""
    objects: dict[str, ObjectDef] = {}
    refs: dict[str, ObjectDef] = {}
    channels: dict[str, ChannelDef] = {}
    arguments: dict[str, tuple[str, int | None]] = {}
    computed: set[str] = set()

    static = program.static
    if static.com_object_table is not None:
        for co in static.com_object_table.com_object:
            objects[co.id] = _object(co)
    if static.com_object_refs is not None:
        for ref in static.com_object_refs.com_object_ref:
            refs[ref.id] = _ref(ref)
    _walk(program.dynamic, channels, computed)

    def module_def(md: ModuleDef) -> None:
        if md.arguments is not None:
            for arg in md.arguments.argument:
                arguments[arg.id] = (arg.name, arg.allocates)
        if md.static.com_objects is not None:
            for co in md.static.com_objects.com_object:
                objects[co.id] = _object(co)
        if md.static.com_object_refs is not None:
            for ref in md.static.com_object_refs.com_object_ref:
                refs[ref.id] = _ref(ref)
        _walk(md.dynamic, channels, computed)
        if md.sub_module_defs is not None:
            for sub in md.sub_module_defs.module_def:
                module_def(sub)

    if program.module_defs is not None:
        for md in program.module_defs.module_def:
            module_def(md)
    return ApplicationDigest(
        application_id=program.id,
        objects=objects,
        refs=refs,
        channels=channels,
        arguments={
            arg_id: ArgumentDef(
                name=name, allocates=allocates, computed=arg_id in computed
            )
            for arg_id, (name, allocates) in arguments.items()
        },
    )


def _value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _flag(value: object) -> bool | None:
    """An ``Enable_t`` value as xknxproject reads it: Enabled is True, anything else False."""
    if value is None:
        return None
    return _value(value) == "Enabled"


def _flags(item: ComObject | ComObjectRef) -> tuple[bool | None, ...]:
    return tuple(_flag(getattr(item, f"{name}_flag")) for name in FLAG_NAMES)


def _object(co: ComObject) -> ObjectDef:
    return ObjectDef(
        name=co.name,
        text=co.text,
        function_text=co.function_text,
        object_size=_value(co.object_size),
        flags=_flags(co),
        datapoint_types=" ".join(co.datapoint_type),
        number=co.number,
        base_number=(
            co.base_number
            if isinstance(co, ModuleDefStaticComObjectsComObject)
            else None
        ),
    )


def _ref(ref: ComObjectRef) -> ObjectDef:
    dpas = parse_semantics_dpas(ref.semantics)
    return ObjectDef(
        name=ref.name,
        text=ref.text,
        function_text=ref.function_text,
        object_size=_value(ref.object_size),
        flags=_flags(ref),
        datapoint_types=" ".join(ref.datapoint_type),
        ref_id=ref.ref_id,
        text_parameter_ref_id=ref.text_parameter_ref_id,
        dpas=tuple(dpas) if dpas is not None else None,
    )


def _element_fields(cls: type) -> tuple[str, ...]:
    """The fields of a schema class that can hold elements: not those of plain values (ids,
    names, numbers), which make up most of them."""
    names = _ELEMENT_FIELDS.get(cls)
    if names is None:
        names = tuple(
            f.name
            for f in dataclasses.fields(cls)
            if set(_TOKEN.findall(str(f.type))) - _PLAIN_TYPES - {"list"}
        )
        _ELEMENT_FIELDS[cls] = names
    return names


def _walk(node: object, channels: dict[str, ChannelDef], computed: set[str]) -> None:
    """Collect the channels and the computed module arguments of a dynamic section."""
    if node is None:
        return
    stack: list[object] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)  # pyright: ignore[reportUnknownArgumentType]
            continue
        cls = type(item)
        if not hasattr(cls, "__dataclass_fields__"):
            continue
        if isinstance(item, ApplicationProgramChannel):
            blocks = parse_semantics_functional_blocks(item.semantics)
            channels[item.id] = ChannelDef(
                name=item.name,
                text=item.text,
                text_parameter_ref_id=item.text_parameter_ref_id,
                functional_blocks=tuple(blocks) if blocks is not None else None,
            )
        elif isinstance(item, ModuleNumericArg) and (
            item.allocator_ref_id is not None or item.base_value is not None
        ):
            computed.add(item.ref_id)
        for name in _element_fields(cls):
            value = getattr(item, name)
            if value is not None:
                stack.append(value)
