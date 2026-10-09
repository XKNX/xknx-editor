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

from xknx.exceptions import XKNXException

from xknxeditor.download.errors import LoadStateError, VerificationError
from xknxeditor.download.tables_systemb import association_table_wide

from .errors import RecoverError
from .tables_decode import (
    DecodedGroupObject,
    DecodedLink,
    decode_association_table,
    decode_association_table_b,
    decode_com_object_table,
    decode_group_address_table,
    decode_group_address_table_b,
    decode_group_object_table_b,
)

if TYPE_CHECKING:
    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI

# Interface object types of the three group communication tables (KNX 3/5/1).
_ADDRESS_TABLE_TYPE = 1
_ASSOCIATION_TABLE_TYPE = 2
_GROUP_OBJECT_TABLE_TYPE = 9
# Memory-mapped com object table record layout (see download.image).
_COM_OBJECT_HEADER = 5
_COM_OBJECT_RECORD = 4


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


def _require_ui(application: Application) -> DynamicUI:
    ui = application.dynamic_ui()
    if ui is None:
        raise RecoverError(
            f"application {application.id} has no dynamic section to locate its "
            "segments"
        )
    return ui


def _is_memory_mapped(application: Application) -> bool:
    """Whether the application uses fixed segment addresses (vs System B relative)."""
    address_table = application.program.static.address_table
    return address_table is not None and address_table.code_segment is not None


def _highest_com_object_number(application: Application) -> int:
    """The largest com object number the application defines (0 if none)."""
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    return max(
        (com_object.number for com_object in indexer.com_objects.values()),
        default=0,
    )


async def read_group_communication(
    programmer: DeviceProgrammer, application: Application
) -> RawGroupCommunication:
    """Read and decode the three group communication tables from a device."""
    if _is_memory_mapped(application):
        return await _read_memory_mapped(programmer, application)
    return await _read_system_b(programmer, application)


async def read_property_values(
    programmer: DeviceProgrammer, application: Application
) -> dict[tuple[int | None, int, int], bytes]:
    """Read the interface-object properties that back the application's parameters.

    Returns ``{(object_index, property_id, occurrence): bytes}`` for every distinct
    property a top-level static parameter writes to, read once per object/property.
    Absent or unreadable properties are skipped. This is the raw material property
    parameter recovery decodes.
    """
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.encode import collect_writes

    indexer = ApplicationIndexer(application.program)
    writes = collect_writes(application.program, indexer, {}, None)
    cache: dict[tuple[int, int], bytes] = {}
    result: dict[tuple[int | None, int, int], bytes] = {}
    for write in writes.prop:
        if write.object_index is None:
            continue
        key = (write.object_index, write.property_id)
        if key not in cache:
            try:
                cache[key] = await programmer.read_property(
                    write.object_index, write.property_id
                )
            except (LoadStateError, VerificationError, XKNXException):
                cache[key] = b""
        if cache[key]:
            result[(write.object_index, write.property_id, write.occurrence)] = cache[
                key
            ]
    return result


# Relative-segment ids embed the interface object index as "..._RS-<objidx>-...".
_RELATIVE_SEGMENT = re.compile(r"_RS-(\d+)-")


async def read_parameter_memory(
    programmer: DeviceProgrammer, application: Application
) -> dict[str, bytes]:
    """Read every non-empty code segment's current bytes off the device.

    Returns ``{segment_id: bytes}`` sized to the application's segment seeds. For
    the memory-mapped model each segment is read at its fixed base address; for
    System B a relative segment (id ``..._RS-<objidx>-...``) is read through that
    object's table reference. Segments whose location cannot be resolved are
    skipped. This is the raw material parameter recovery matches against.
    """
    ui = _require_ui(application)
    sizes = ui.segment_sizes()
    memory_mapped = _is_memory_mapped(application)
    result: dict[str, bytes] = {}
    # A single unreadable segment (a realisation whose memory map we do not fully
    # model, or a transient timeout) is skipped, not fatal: the device still
    # recovers the parameters from the segments that could be read.
    if memory_mapped:
        base_addresses = ui.segment_base_addrs()
        for segment_id, size in sizes.items():
            base = base_addresses.get(segment_id)
            if not size or base is None:
                continue
            try:
                result[segment_id] = await programmer.read_memory(base, size)
            except (LoadStateError, VerificationError, XKNXException):
                continue
        return result
    for segment_id, size in sizes.items():
        if not size:
            continue
        match = _RELATIVE_SEGMENT.search(segment_id)
        if match is None:
            continue
        object_index = int(match.group(1))
        try:
            base = await programmer.read_table_reference(object_index)
            result[segment_id] = await programmer.read_memory(base, size)
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


async def _read_memory_mapped(
    programmer: DeviceProgrammer, application: Application
) -> RawGroupCommunication:
    static = application.program.static
    ui = _require_ui(application)
    base_addresses = ui.segment_base_addrs()
    sizes = ui.segment_sizes()

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
        assoc_address = base_addresses[association_table.code_segment] + (
            association_table.offset or 0
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
    ):
        seg_size = sizes.get(com_object_table.code_segment)
        if seg_size:
            # The writer overlays records at the segment base (offset 0), so read
            # from the base - not base+offset. Read the declared segment length,
            # bounded by the highest defined object number so a realisation whose
            # table does not span the whole segment is not over-read into unmapped
            # memory (which would time out). The com object table only carries the
            # per-object flags/size, so if this read fails the device still recovers
            # its group addresses and links - just without flag overrides.
            co_address = base_addresses[com_object_table.code_segment]
            highest = _highest_com_object_number(application)
            table_length = min(
                seg_size, _COM_OBJECT_HEADER + (highest + 1) * _COM_OBJECT_RECORD
            )
            try:
                co_bytes = await programmer.read_memory(co_address, table_length)
            except (LoadStateError, VerificationError, XKNXException):
                co_bytes = b""
            if co_bytes:
                group_objects = decode_com_object_table(
                    co_bytes,
                    header_size=_COM_OBJECT_HEADER,
                    record_size=_COM_OBJECT_RECORD,
                )

    return RawGroupCommunication(
        device_address=device_address,
        group_addresses=group_addresses,
        links=links,
        group_objects=group_objects,
    )


async def _read_system_b(
    programmer: DeviceProgrammer, application: Application
) -> RawGroupCommunication:
    addr_base = await programmer.read_table_reference(
        await programmer.locate_object(_ADDRESS_TABLE_TYPE)
    )
    addr_bytes = await _read_count_table(
        programmer, addr_base, count_width=2, entry_width=2
    )
    group_addresses = decode_group_address_table_b(addr_bytes)

    assoc_base = await programmer.read_table_reference(
        await programmer.locate_object(_ASSOCIATION_TABLE_TYPE)
    )
    # An association entry is [group address index + 1][group object number], with
    # one octet (format 0) or two octets (format 1) per field as the device reports.
    wide = await programmer.read_association_table_wide()
    if wide is None:
        wide = association_table_wide(
            len(group_addresses), _highest_com_object_number(application)
        )
    assoc_bytes = await _read_count_table(
        programmer, assoc_base, count_width=2, entry_width=4 if wide else 2
    )
    links = decode_association_table_b(assoc_bytes, group_addresses)

    go_base = await programmer.read_table_reference(
        await programmer.locate_object(_GROUP_OBJECT_TABLE_TYPE)
    )
    # The group object table's count is the highest object number; every number
    # 1..highest has a two-octet record.
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
