"""Read a device's configured group communication tables and parameter memory.

Given the application installed on a device (identified via :mod:`.identify` and
loaded from the catalog), this locates the group communication tables the way
:mod:`xknxeditor.download` writes them - at the application's code-segment addresses
for the memory-mapped model, or through each object's table reference for System
B - and reads them back. The bytes are then decoded by :mod:`.tables_decode`.

Table lengths are taken from the application (the count octet for the address and
association tables, the declared segment size for the com object table), so only the
configured region is read. All reads are read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError, XKNXException
from xknx.profile.const import ResourceObjectType

from xknxeditor.download.errors import LoadStateError, VerificationError
from xknxeditor.download.merge import device_mask_version
from xknxeditor.download.resources import (
    bcu1_group_object_table,
    bcu2_group_object_table,
    bundled_mask_resources,
    mask_number,
    property_group_tables,
)
from xknxeditor.download.tables_system300 import ADDRESS_TABLE, ASSOCIATION_TABLE
from xknxeditor.download.tables_systemb import association_table_wide
from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_resources_resource_resource_type_flavour import (
    HawkConfigurationDataResourcesResourceResourceTypeFlavour as ResourceFlavour,
)
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

from .tables_decode import (
    DecodedGroupObject,
    DecodedLink,
    decode_association_table,
    decode_association_table_b,
    decode_com_object_table,
    decode_group_address_table,
    decode_group_address_table_b,
    decode_group_object_records_b,
    decode_group_object_table_b,
    decode_short_com_object_table,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.encode import PropWrite, TypedPropertyKey

# Memory-mapped com object table record layout (see download.image): M112 records are
# 4 octets with the flag byte at 5 + number*4; BCU1/BCU2 records are 3 octets after a
# 2-octet header.
_COM_OBJECT_HEADER = 5
_COM_OBJECT_RECORD = 4
_SHORT_HEADER = 2
_SHORT_RECORD = 3


@dataclass(frozen=True, slots=True)
class RawGroupCommunication:
    """The decoded group communication a device is programmed with."""

    device_address: int | None
    group_addresses: list[int]
    links: list[DecodedLink]
    group_objects: dict[int, DecodedGroupObject]


@dataclass(frozen=True, slots=True)
class RawConfiguration:
    """Everything read off a device: tables plus raw parameter-segment memory."""

    group_communication: RawGroupCommunication
    memory_mapped: bool
    parameter_segments: dict[str, bytes]


def _segment_layout(application: Application) -> tuple[dict[str, int], dict[str, int]]:
    """The base address and the size of each code segment. An application without a dynamic
    section (an interface without parameters) still has its segments, tables included."""
    ui = application.dynamic_ui()
    if ui is not None:
        return ui.segment_base_addrs(), ui.segment_sizes()
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.encode import segment_seed

    indexer = ApplicationIndexer(application.program)
    return (
        {sid: indexer.segment_base_addr(sid) for sid in indexer.code_segments},
        {
            sid: len(segment_seed(segment))
            for sid, segment in indexer.code_segments.items()
        },
    )


# The mask version number of System 300 (``MV-0300``, with or without its configuration).
_SYSTEM_300 = 0x0300


def _is_memory_mapped(application: Application) -> bool:
    """Whether the application uses fixed segment addresses (vs System B relative). Tables in
    a relative segment are in their objects' relative memory."""
    static = application.program.static
    address_table = static.address_table
    if address_table is None or address_table.code_segment is None:
        return False
    relative = static.code.relative_segment if static.code is not None else ()
    return all(segment.id != address_table.code_segment for segment in relative)


def _has_com_objects(application: Application) -> bool:
    """Whether the application defines any com object (its numbers can start at 0)."""
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    return bool(ApplicationIndexer(application.program).com_objects)


def _highest_com_object_number(application: Application) -> int:
    """The largest com object number the application defines (0 if none)."""
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    return max(
        (com_object.number for com_object in indexer.com_objects.values()),
        default=0,
    )


async def read_group_communication(
    programmer: DeviceProgrammer,
    application: Application | None,
    *,
    linked_objects_only: bool = False,
) -> RawGroupCommunication:
    """Read and decode the three group communication tables from a device.

    Without ``application`` the device must be System B, which reports where its tables
    are. With ``linked_objects_only``, a System B group object table is read only where
    the association table links an object, instead of for every object number.
    """
    if application is not None:
        if not _has_com_objects(application):
            # No group objects (a coupler, an IP router): nothing linked, nothing to read.
            return RawGroupCommunication(
                device_address=None, group_addresses=[], links=[], group_objects={}
            )
        mask = device_mask_version(application)
        if property_group_tables(mask):
            return await _read_system_300(programmer)
        if mask_number(mask) == _SYSTEM_300:
            # The memory configuration of System 300: its procedure only allocates the
            # table segments, and the download writes no more of the tables than the count
            # of the group address table. No group address and no link is there to read.
            return RawGroupCommunication(
                device_address=None, group_addresses=[], links=[], group_objects={}
            )
    if application is not None and _is_memory_mapped(application):
        return await _read_memory_mapped(programmer, application)
    return await _read_system_b(
        programmer, application, linked_objects_only=linked_objects_only
    )


async def read_property_values(
    programmer: DeviceProgrammer, application: Application
) -> dict[TypedPropertyKey, bytes]:
    """Read the interface-object properties that back the application's parameters.

    Returns ``{(object_index, object_type, property_id, occurrence): bytes}`` (keyed as
    :func:`~xknxeditor.prod.parser_v2.encode.encode_to_typed_properties` keys them) for
    every distinct property a top-level static parameter writes to, read once per
    object/property: as many elements from the first on as the parameters take (an array
    property of one octet per element holds a parameter at offset 4 in its fifth element).
    An object addressed by type is the device's object of that type and occurrence. The
    properties module instances keep their parameters in, in objects addressed by type (one
    object per instance), are read from every object of that type the device has, as the
    instances are only known once the parameters are decoded; a property the application
    shares with them is read as far as theirs. Absent or unreadable properties are skipped,
    and elements after the first that cannot be read are left out. This is the raw material
    property parameter recovery decodes.
    """
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.encode import collect_writes

    indexer = ApplicationIndexer(application.program)
    writes = collect_writes(application.program, indexer, {}, None)
    needed = _property_sizes(indexer, writes.prop)
    module_sizes = _module_properties(indexer)
    stored: list[tuple[_PropertyTarget, TypedPropertyKey]] = []
    for write in writes.prop:
        target = _property_target(write)
        if target is not None:
            key = (write.object_index, write.object_type, write.property_id)
            stored.append((target, (*key, write.occurrence)))
    # A union without a default member writes nothing without a configuration: its property
    # is read as well, as far as the union goes.
    for target, key, size in _union_properties(application):
        needed[target] = max(needed.get(target, 0), size)
        stored.append((target, key))
    cache: dict[_PropertyTarget, bytes] = {}
    result: dict[TypedPropertyKey, bytes] = {}
    for target, key in stored:
        if target not in cache:
            size = needed.get(target, 0)
            object_type = target[1]
            if object_type is not None:
                # Module instances keep parameters further on in the same property.
                size = max(size, module_sizes.get((object_type, target[3]), 0))
            cache[target] = await _read_target(programmer, target, size)
        if cache[target]:
            result[key] = cache[target]
    for (object_type, property_id), size in module_sizes.items():
        for occurrence in range(_MAX_OCCURRENCES):
            try:
                await programmer.locate_object(object_type, occurrence)
            except ManagementConnectionError:
                raise
            except (LoadStateError, VerificationError, XKNXException):
                break  # no more objects of the type
            target = (None, object_type, occurrence, property_id)
            if target not in cache:
                cache[target] = await _read_target(programmer, target, size)
            if cache[target]:
                result[(None, object_type, property_id, occurrence)] = cache[target]
    return result


# The objects of one type read for the module instances that keep their parameters in them.
_MAX_OCCURRENCES = 1024
# The octets read of a property a module instance keeps a parameter in at an offset of its
# own (a BaseOffset): every element the device holds, up to this many octets.
_WHOLE_PROPERTY = 1 << 16


def _module_properties(indexer: ApplicationIndexer) -> dict[tuple[int, int], int]:
    """The properties of objects addressed by type the module definitions keep their
    parameters in, ``{(object type, property id): octets}``: as many as the parameters take
    from the first element on, all the device holds where an instance has an offset of its
    own."""
    from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_union import (
        ModuleDefStaticParametersUnion,
    )
    from xknxeditor.namespaces.intermediate.property_parameter_t import (
        PropertyParameter,
    )
    from xknxeditor.namespaces.intermediate.property_union_t import PropertyUnion
    from xknxeditor.prod.parser_v2.encode import type_size_in_bit

    sizes: dict[tuple[int, int], int] = {}
    for module_def in indexer.module_defs.values():
        parameters = module_def.static.parameters
        for item in parameters.choice if parameters is not None else []:
            storage = item.choice
            if not isinstance(storage, PropertyParameter | PropertyUnion):
                continue
            if storage.object_type is None or storage.object_index is not None:
                continue
            if getattr(storage, "base_offset", None) is not None:
                end = _WHOLE_PROPERTY
            else:
                if isinstance(item, ModuleDefStaticParametersUnion):
                    bits = item.size_in_bit
                else:
                    parameter_type = indexer.parameter_types.get(item.parameter_type)
                    bits = (
                        type_size_in_bit(parameter_type.choice, for_property=True) or 0
                        if parameter_type is not None
                        else 0
                    )
                end = (storage.offset * 8 + storage.bit_offset + bits + 7) // 8
            key = (storage.object_type, storage.property_id)
            sizes[key] = max(sizes.get(key, 0), end)
    return sizes


# The property a parameter is stored in: (object index, object type, occurrence of an object
# addressed by type, property id); an object addressed by index has no type and occurrence.
_PropertyTarget = tuple[int | None, int | None, int, int]


def _union_properties(
    application: Application,
) -> list[tuple[_PropertyTarget, TypedPropertyKey, int]]:
    """The properties the application's unions are stored in, each with its key (as
    :func:`~xknxeditor.prod.parser_v2.encode.encode_to_typed_properties` keys it) and the
    octets the union takes of it from the first element on."""
    from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_union import (
        ApplicationProgramStaticParametersUnion,
    )
    from xknxeditor.namespaces.intermediate.property_union_t import PropertyUnion

    parameters = application.program.static.parameters
    found: list[tuple[_PropertyTarget, TypedPropertyKey, int]] = []
    for item in parameters.choice if parameters is not None else []:
        if not isinstance(item, ApplicationProgramStaticParametersUnion):
            continue
        storage = item.choice
        if not isinstance(storage, PropertyUnion):
            continue
        if storage.object_index is not None:
            target: _PropertyTarget = (
                storage.object_index,
                None,
                0,
                storage.property_id,
            )
        elif storage.object_type is not None:
            target = (
                None,
                storage.object_type,
                storage.occurrence,
                storage.property_id,
            )
        else:
            continue
        key = (
            storage.object_index,
            storage.object_type,
            storage.property_id,
            storage.occurrence,
        )
        size = (storage.offset * 8 + storage.bit_offset + item.size_in_bit + 7) // 8
        found.append((target, key, size))
    return found


def _property_target(write: PropWrite) -> _PropertyTarget | None:
    """The property ``write`` is stored in, ``None`` for one of an object addressed neither
    by index nor by type."""
    if write.object_index is not None:
        return (write.object_index, None, 0, write.property_id)
    if write.object_type is not None:
        return (None, write.object_type, write.occurrence, write.property_id)
    return None


async def _read_target(
    programmer: DeviceProgrammer, target: _PropertyTarget, size: int
) -> bytes:
    """The property ``target`` as far as the device holds it, up to ``size`` octets (at
    least its first element); empty when the device has no such object or property."""
    object_index, object_type, occurrence, property_id = target
    try:
        if object_index is None:
            assert object_type is not None
            object_index = await programmer.locate_object(object_type, occurrence)
        data = await programmer.read_property(object_index, property_id)
    except ManagementConnectionError:
        raise
    except (LoadStateError, VerificationError, XKNXException):
        return b""
    if data and len(data) < size:
        data += await _read_more_elements(
            programmer, (object_index, property_id), len(data), size
        )
    return data


def _property_sizes(
    indexer: ApplicationIndexer, writes: Iterable[PropWrite]
) -> dict[_PropertyTarget, int]:
    """The octets the property parameters ``writes`` take of each property (see
    :func:`_property_target`), from the first element on."""
    from xknxeditor.prod.parser_v2.encode import type_size_in_bit

    sizes: dict[_PropertyTarget, int] = {}
    for write in writes:
        parameter_type = indexer.parameter_types.get(write.parameter_type)
        target = _property_target(write)
        if target is None or parameter_type is None:
            continue
        bits = type_size_in_bit(parameter_type.choice, for_property=True)
        if not bits:
            continue
        end = (write.offset * 8 + write.bit_offset + bits + 7) // 8
        sizes[target] = max(sizes.get(target, 0), end)
    return sizes


async def _read_more_elements(
    programmer: DeviceProgrammer, key: tuple[int, int], width: int, size: int
) -> bytes:
    """The elements after the first of the property ``key`` (``width`` octets each, as the
    first read gave it) up to ``size`` octets, as far as the device holds them (its current
    number of elements, element 0), in frames that fit the APDU; those before an element that
    cannot be read. A property of a single element holds a parameter of more octets than the
    element has nowhere else: nothing more is read."""
    object_index, property_id = key
    try:
        held = int.from_bytes(
            await programmer.read_property(object_index, property_id, start_index=0),
            "big",
        )
        per_frame = max(programmer.property_chunk_size(width), 1)
    except ManagementConnectionError:
        raise
    except (LoadStateError, VerificationError, XKNXException):
        return b""
    count = min(-(-size // width), held)
    data = b""
    start = 2
    while start <= count:
        frame = min(per_frame, count - start + 1)
        try:
            data += await programmer.read_property(
                object_index, property_id, count=frame, start_index=start
            )
        except ManagementConnectionError:
            raise
        except (LoadStateError, VerificationError, XKNXException):
            break
        start += frame
    return data


# Relative-segment ids embed the interface object index as "..._RS-<objidx>-...".
_RELATIVE_SEGMENT = re.compile(r"_RS-(\d+)-")


async def read_parameter_memory(
    programmer: DeviceProgrammer, application: Application
) -> dict[str, bytes]:
    """Read every non-empty code segment's current bytes off the device.

    Returns ``{segment_id: bytes}`` sized to the application's segment seeds. For
    the memory-mapped model each segment is read at its fixed base address; for
    System B a relative segment (id ``..._RS-<objidx>-...``) is read through that
    object's table reference. A segment in the device's user memory, an address space of
    its own which a standard memory read at the same address does not reach, is read with
    the user memory services, and only where a step of the load procedure writes it in the
    user memory (see :func:`_written_in_user_memory`): the download writes nothing else
    there. Segments whose location cannot be resolved are skipped, and so are the segments
    the application's own load procedure writes nothing of (see :func:`_never_written`).
    This is the raw material parameter recovery matches against, keyed by segment id like
    the download image's segments (the user memory's included).
    """
    base_addresses, sizes = _segment_layout(application)
    memory_mapped = _is_memory_mapped(application)
    code = application.program.static.code
    result: dict[str, bytes] = {}
    # A single unreadable segment (a realisation whose memory map we do not fully
    # model, or a transient timeout) is skipped, not fatal: the device still
    # recovers the parameters from the segments that could be read.
    if memory_mapped:
        user_memory = {
            segment.id
            for segment in (code.absolute_segment if code is not None else [])
            if segment.user_memory
        }
        for segment_id, size in sizes.items():
            base = base_addresses.get(segment_id)
            if not size or base is None:
                continue
            user = segment_id in user_memory
            if (
                not _written_in_user_memory(application, base, size)
                if user
                else _never_written(application, base, size)
            ):
                continue
            try:
                result[segment_id] = await programmer.read_memory(base, size, user=user)
            except ManagementConnectionError:
                raise
            except (LoadStateError, VerificationError, XKNXException):
                continue
        return result
    # A relative segment can start further into its object's memory (Offset 4096).
    offsets = {
        segment.id: segment.offset or 0
        for segment in (code.relative_segment if code is not None else [])
    }
    for segment_id, size in sizes.items():
        if not size:
            continue
        match = _RELATIVE_SEGMENT.search(segment_id)
        if match is None:
            continue
        object_index = int(match.group(1))
        try:
            base = await programmer.read_table_reference(object_index)
            result[segment_id] = await programmer.read_memory(
                base + offsets.get(segment_id, 0), size
            )
        except ManagementConnectionError:
            raise
        except (LoadStateError, VerificationError, XKNXException):
            # Segment not allocated / no table reference / unreadable: skip it.
            continue
    return result


async def _read_count_table(
    programmer: DeviceProgrammer, address: int, *, count_width: int, entry_width: int
) -> bytes:
    """Read a count-led table: a ``count_width`` count, then ``count`` entries."""
    header = await programmer.read_memory(address, count_width)
    count = int.from_bytes(header, "big")
    return await programmer.read_memory(address, count_width + count * entry_width)


async def _read_property_array(
    programmer: DeviceProgrammer, object_index: int, property_id: int, width: int
) -> bytes:
    """Every element of an array property: its count (element 0), then the elements in
    frames that fit the APDU."""
    count = int.from_bytes(
        await programmer.read_property(object_index, property_id, start_index=0),
        "big",
    )
    per_frame = max(programmer.property_chunk_size(width), 1)
    data = b""
    start = 1
    while start <= count:
        frame = min(per_frame, count - start + 1)
        data += await programmer.read_property(
            object_index, property_id, count=frame, start_index=start
        )
        start += frame
    return data


async def _read_system_300(programmer: DeviceProgrammer) -> RawGroupCommunication:
    """The group addresses and links of a System 300 device, which keeps its tables as
    properties (see :mod:`xknxeditor.download.tables_system300`): the address table and the
    association table elements without their count. Its group object elements carry the
    flags but no size, so the group objects are not read."""
    address_object = await programmer.locate_object(ADDRESS_TABLE[0])
    addresses = await _read_property_array(
        programmer, address_object, ADDRESS_TABLE[1], 2
    )
    group_addresses = decode_group_address_table_b(
        (len(addresses) // 2).to_bytes(2, "big") + addresses
    )
    association_object = await programmer.locate_object(ASSOCIATION_TABLE[0])
    width = await programmer.read_property_element_size(
        association_object, ASSOCIATION_TABLE[1]
    )
    associations = await _read_property_array(
        programmer, association_object, ASSOCIATION_TABLE[1], width
    )
    links = decode_association_table_b(
        (len(associations) // max(width, 1)).to_bytes(2, "big") + associations,
        group_addresses,
    )
    return RawGroupCommunication(
        device_address=None,
        group_addresses=group_addresses,
        links=links,
        group_objects={},
    )


async def _association_table_address(
    programmer: DeviceProgrammer, application: Application
) -> int | None:
    """The association table's address the device reports through the mask's pointer to
    it (``GroupAssociationTablePtr``: the table reference of object 2 on BIM M112, the octet
    at 0111h plus 100h on BCU1); ``None`` when the mask has no pointer or it reads 0."""
    resources = bundled_mask_resources(device_mask_version(application))
    if resources is None:
        return None
    location = resources.location(ResourceName.GROUP_ASSOCIATION_TABLE)
    if location is None or location.address_space != ResourceAddrSpace.POINTER:
        return None
    pointer = ResourceName.GROUP_ASSOCIATION_TABLE_PTR
    try:
        reference = resources.property_ref(pointer)
        if reference is not None:
            data = await programmer.read_property(*reference)
        else:
            address = resources.memory_address(pointer)
            if address is None:
                return None
            data = await programmer.read_memory(address, resources.length(pointer) or 1)
    except ManagementConnectionError:
        raise
    except (LoadStateError, VerificationError, XKNXException):
        return None
    value = int.from_bytes(data[:2], "big")
    if resources.flavour(pointer) is ResourceFlavour.PTR_STANDARD_MEMORY100:
        value += 0x100
    return value or None


async def _read_memory_mapped(
    programmer: DeviceProgrammer, application: Application
) -> RawGroupCommunication:
    static = application.program.static
    base_addresses, sizes = _segment_layout(application)

    address_table = static.address_table
    assert address_table is not None and address_table.code_segment is not None
    addr_address = base_addresses[address_table.code_segment] + (
        address_table.offset or 0
    )
    # The address table's count includes the leading device-address entry, so the
    # table is count entries of two octets plus the count octet.
    addr_bytes = await _read_count_table(
        programmer, addr_address, count_width=1, entry_width=2
    )
    device_address, group_addresses = decode_group_address_table(addr_bytes)

    links: list[DecodedLink] = []
    association_table = static.association_table
    if association_table is not None and association_table.code_segment is not None:
        # Where the device keeps it, when the mask points to it (a download with dynamic
        # table management packs it right after the address table); else as declared.
        # A pointer to the start of the declared segment is the segment's table
        # reference: the table lies at its offset in it, where the download writes it.
        segment_address = base_addresses[association_table.code_segment]
        pointer = await _association_table_address(programmer, application)
        assoc_address = (
            pointer
            if pointer is not None and pointer != segment_address
            else segment_address + (association_table.offset or 0)
        )
        assoc_bytes = await _read_count_table(
            programmer, assoc_address, count_width=1, entry_width=2
        )
        links = decode_association_table(assoc_bytes, group_addresses)

    group_objects: dict[int, DecodedGroupObject] = {}
    com_object_table = static.com_object_table
    if (
        com_object_table is not None
        and com_object_table.code_segment is not None
        and com_object_table.code_segment in base_addresses
        and not _only_allocated(
            application,
            base_addresses[com_object_table.code_segment]
            + (com_object_table.offset or 0),
        )
    ):
        table_offset = com_object_table.offset or 0
        seg_size = sizes.get(com_object_table.code_segment, 0) - table_offset
        mask_version = device_mask_version(application)
        short = bcu1_group_object_table(mask_version) or bcu2_group_object_table(
            mask_version
        )
        header, record = (
            (_SHORT_HEADER, _SHORT_RECORD)
            if short
            else (_COM_OBJECT_HEADER, _COM_OBJECT_RECORD)
        )
        if seg_size > 0:
            # The table starts at its offset in the segment, as the download writes it.
            # Read up to the end of the segment, bounded by the highest defined object
            # number so a realisation whose table does not span the whole segment is
            # not over-read into unmapped memory (which would time out). The com object
            # table only carries the per-object flags/size, so if this read fails the
            # device still recovers its group addresses and links - just without flag
            # overrides.
            co_address = base_addresses[com_object_table.code_segment] + table_offset
            highest = _highest_com_object_number(application)
            table_length = min(seg_size, header + (highest + 1) * record)
            try:
                co_bytes = await programmer.read_memory(co_address, table_length)
            except ManagementConnectionError:
                raise
            except (LoadStateError, VerificationError, XKNXException):
                co_bytes = b""
            if co_bytes:
                group_objects = (
                    decode_short_com_object_table(co_bytes)
                    if short
                    else decode_com_object_table(
                        co_bytes,
                        header_size=_COM_OBJECT_HEADER,
                        record_size=_COM_OBJECT_RECORD,
                    )
                )

    return RawGroupCommunication(
        device_address=device_address,
        group_addresses=group_addresses,
        links=links,
        group_objects=group_objects,
    )


# The memory type bits of an EEPROM segment, the only one whose data a download writes.
_EEPROM_MEMORY = 0x03


def _only_allocated(application: Application, address: int) -> bool:
    """Whether the application's procedures only allocate the memory at ``address``: an
    absolute segment of another memory type than EEPROM holds it, and no memory write
    reaches it. A download writes nothing there, so what the device holds there tells
    nothing of its configuration."""
    from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import (
        LdCtrlAbsSegment,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    procedures = application.load_procedures
    allocated = False
    for procedure in procedures.load_procedure if procedures is not None else ():
        for control in procedure.choice:
            if not isinstance(control, (LdCtrlAbsSegment, LdCtrlWriteMem)) or not (
                control.address <= address < control.address + control.size
            ):
                continue
            if isinstance(control, LdCtrlWriteMem) or (
                control.mem_type & _EEPROM_MEMORY == _EEPROM_MEMORY
            ):
                return False
            allocated = True
    return allocated


def _memory_controls(controls: Iterable[object]) -> Iterator[object]:
    """The controls of a load procedure that place or write memory at an address of their
    own, those in every branch of its chooses too."""
    from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import (
        LdCtrlAbsSegment,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
        LdCtrlLoadImageMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    for control in controls:
        if isinstance(control, (LdCtrlAbsSegment, LdCtrlWriteMem, LdCtrlLoadImageMem)):
            yield control
        for when in getattr(control, "when", None) or ():
            yield from _memory_controls(getattr(when, "choice", None) or ())


def _never_written(application: Application, address: int, size: int) -> bool:
    """Whether a download writes nothing of the ``size`` bytes at ``address``: the
    application loads with a procedure of its own, which places its memory itself, and no
    step of it reaches them. What the device holds there tells nothing of its configuration
    (a converted product's segment for a project id the procedure never loads)."""
    from xknxeditor.namespaces.intermediate.load_procedure_style_t import (
        LoadProcedureStyle,
    )

    if application.load_procedure_style != LoadProcedureStyle.PRODUCT_PROCEDURE:
        return False
    procedures = application.load_procedures
    placing = False
    for procedure in procedures.load_procedure if procedures is not None else ():
        for control in _memory_controls(procedure.choice):
            placing = True
            # A step in the user memory writes another address space at the same address
            # (see _written_in_user_memory).
            if not _in_user_memory(control) and _overlaps(control, address, size):
                return False
    return placing


def _written_in_user_memory(application: Application, address: int, size: int) -> bool:
    """Whether a step of the application's load procedures writes any of the ``size`` bytes
    at ``address`` of the user memory (a WriteMem or LoadImageMem with
    ``AddressSpace="User"``): the download writes a user memory segment there only."""
    procedures = application.load_procedures
    return any(
        _in_user_memory(control) and _overlaps(control, address, size)
        for procedure in (procedures.load_procedure if procedures is not None else ())
        for control in _memory_controls(procedure.choice)
    )


def _in_user_memory(control: object) -> bool:
    """Whether a memory control accesses the user memory (``AddressSpace="User"``)."""
    from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
        LdCtrlMemAddrSpace,
    )

    return getattr(control, "address_space", None) is LdCtrlMemAddrSpace.USER


def _overlaps(control: object, address: int, size: int) -> bool:
    """Whether a memory control's range reaches any of the ``size`` bytes at ``address``."""
    start = int(getattr(control, "address", 0))
    length = int(getattr(control, "size", 0))
    return start < address + size and address < start + length


def _record_ranges(numbers: list[int], max_records: int) -> list[tuple[int, int]]:
    """``(first, count)`` runs covering ``numbers``; nearby numbers share one run."""
    ranges: list[tuple[int, int]] = []
    for number in sorted(set(numbers)):
        if ranges and number - ranges[-1][0] < max_records:
            first, _ = ranges[-1]
            ranges[-1] = (first, number - first + 1)
        else:
            ranges.append((number, 1))
    return ranges


async def _read_system_b(
    programmer: DeviceProgrammer,
    application: Application | None,
    *,
    linked_objects_only: bool = False,
) -> RawGroupCommunication:
    addr_base = await programmer.read_table_reference(
        await programmer.locate_object(ResourceObjectType.OBJECT_ADDRESS_TABLE)
    )
    addr_bytes = await _read_count_table(
        programmer, addr_base, count_width=2, entry_width=2
    )
    group_addresses = decode_group_address_table_b(addr_bytes)

    assoc_base = await programmer.read_table_reference(
        await programmer.locate_object(ResourceObjectType.OBJECT_ASSOCIATION_TABLE)
    )
    # An association entry is [group address index + 1][group object number], with
    # one octet (format 0) or two octets (format 1) per field as the device reports.
    go_base = await programmer.read_table_reference(
        await programmer.locate_object(ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE)
    )
    wide = await programmer.read_association_table_wide()
    if wide is None:
        if application is not None:
            highest_number = _highest_com_object_number(application)
        else:
            highest_number = int.from_bytes(
                await programmer.read_memory(go_base, 2), "big"
            )
        wide = association_table_wide(len(group_addresses), highest_number)
    assoc_bytes = await _read_count_table(
        programmer, assoc_base, count_width=2, entry_width=4 if wide else 2
    )
    links = decode_association_table_b(assoc_bytes, group_addresses)

    # The group object table's count is the highest object number; every number
    # 1..highest has a two-octet record.
    if linked_objects_only:
        highest = int.from_bytes(await programmer.read_memory(go_base, 2), "big")
        linked = [
            link.group_object_number
            for link in links
            if 1 <= link.group_object_number <= highest
        ]
        group_objects: dict[int, DecodedGroupObject] = {}
        for first, count in _record_ranges(
            linked, max(programmer.memory_chunk_size // 2, 1)
        ):
            data = await programmer.read_memory(go_base + first * 2, count * 2)
            group_objects.update(decode_group_object_records_b(first, data))
    else:
        go_bytes = await _read_count_table(
            programmer, go_base, count_width=2, entry_width=2
        )
        group_objects = decode_group_object_table_b(go_bytes)

    return RawGroupCommunication(
        device_address=None,
        group_addresses=group_addresses,
        links=links,
        group_objects=group_objects,
    )
