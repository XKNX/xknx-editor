"""The download image: the data a Load Procedure writes into a device.

The image is assembled from the parsed application program. Parameter values are
encoded into their code segments (producing the memory image) and into interface
object properties. Group communication tables (address, association, group object)
are part of the segment data as well; project specific values can be supplied via
``parameter_values``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from xknx.profile.const import ResourceObjectType

from xknxeditor.namespaces.intermediate.application_program_static_t_code_relative_segment import (
    ApplicationProgramStaticCodeRelativeSegment,
)
from xknxeditor.namespaces.intermediate.com_object_t import ComObject
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName
from xknxeditor.prod import Application
from xknxeditor.prod.errors import EncodingError

from .errors import ImageError
from .factory_image import FactoryImage, FactoryLocation
from .merge import device_mask_version, iter_load_controls, procedure_parameter_refs
from .project_data import (
    GroupObjectLink,
    SeedDevice,
    com_object_instance_refs_from_device,
    module_instances_from_device,
    parameter_instance_refs_from_device,
)
from .resources import (
    bcu1_association_table,
    bcu1_group_object_table,
    bcu2_association_table,
    bcu2_group_object_table,
    bundled_mask_resources,
    m112_association_table,
    m112_group_object_table,
    mask_number,
    property_group_tables,
    reserved_memory,
    segments_have_inline_checksum,
)
from .secure_config import GroupSecurity, SecurityTables, security_tables
from .tables import (
    SEGMENT_SELECTOR_BIT,
    Association,
    ComObjectDescriptor,
    association_slots,
    build_association_list,
    build_association_table,
    build_com_object_table,
    build_group_address_table,
    com_object_flag_byte,
    memory_size_code,
    size_code,
)
from .tables_system300 import PropertyTables, property_tables
from .tables_systemb import (
    association_table_wide,
    build_association_table_b,
    build_group_address_table_b,
    build_group_object_table_b,
    group_address_index_b,
)

# A coupler's group address filter table lives in the Router object's relative memory
# (PID_TABLE_REFERENCE), so it is written by an ``LdCtrlWriteRelMem ObjType=6``.
_ROUTER_OBJECT_TYPE = ResourceObjectType.OBJECT_ROUTER


# Fixed bytes a TP1 line coupler (mask version 091x) gets with the application's
# segment data, when the segment covers 0x010D-0x011C.
_COUPLER_BYTES = {
    0x010D: 0xFF,
    0x0112: 0xC3,
    0x0113: 0xC3,
    0x0114: 0xC3,
    0x0115: 0xC3,
    0x0116: 0x01,
    0x011A: 0xFF,
    0x011B: 0xCC,
    0x011C: 0xFF,
}


def _coupler_bytes(
    mask_version: str, base: int, data: bytes, mask: bytes, initialized: bytes
) -> tuple[bytes, bytes]:
    """``data`` and ``initialized`` with the fixed line coupler bytes, for a segment
    that covers them; bytes a parameter writes (``mask``) keep its value."""
    version = mask_number(mask_version)
    if (
        version is None
        or version & 0xFFE0 != 0x0900
        or base > 0x010D
        or base + len(data) <= 0x011C
    ):
        return data, initialized
    patched, valid = bytearray(data), bytearray(initialized)
    for address, value in _COUPLER_BYTES.items():
        if not mask[address - base]:
            patched[address - base] = value
        valid[address - base] = 0xFF
    return bytes(patched), bytes(valid)


if TYPE_CHECKING:
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI


@dataclass(frozen=True, slots=True)
class PackedGroupTables:
    """Where an M112 list with dynamic table management places the two group tables.

    The product data declares the address table and the association table at
    their maximum size. The download allocates only the used address table and
    starts the association table on the next octet; that one still ends where its
    ``MaxEntries`` end.
    """

    address_address: int
    address_length: int
    declared_address_size: int
    association_address: int
    association_length: int
    declared_association_address: int
    declared_association_size: int


# Applications (id prefixes) whose group tables stay where the product data declares them,
# also with DynamicTableManagement.
STATIC_GROUP_TABLES = ("M-0004_A-9019-01", "M-00A6_A-0004-10")


def dynamic_tables(application: Application) -> bool:
    """Whether the download places the group tables (``DynamicTableManagement``)."""
    program = application.program
    application_id: str = getattr(program, "id", None) or ""
    return bool(program.dynamic_table_management) and not application_id.startswith(
        STATIC_GROUP_TABLES
    )


@dataclass(frozen=True, slots=True)
class MovedTables:
    """Where a BCU2 download with dynamic table management places the group tables.

    The address table at 0116h is allocated at its used length (plus the segment's
    checksum octet) by the load state machine 1 segment. The association table starts
    after it and still ends where the load state machine 2 segment ends.
    """

    address_length: int
    association_address: int


@dataclass(frozen=True, slots=True)
class GroupTables:
    """The memory segments of the group communication tables, and where a BCU2 download
    moves them (``None``: they stay where the product data declares them)."""

    segments: list[MemorySegment]
    moved: MovedTables | None = None


def _packs_group_tables(application: Application) -> bool:
    """Whether the download packs the M112 group tables (see :func:`packed_group_tables`)."""
    return dynamic_tables(application) and m112_association_table(
        application.program.mask_version
    )


def _table_segment_initialized(
    application: Application, segment_id: str, initialized: bytes
) -> bytes:
    """The seed octets a full download writes in a group table's code segment.

    The table builders supply the tables themselves. M112 tables that stay where the
    product declares them leave the rest of their segment to its data: the octets
    after the address table's and the association table's ``MaxEntries`` are
    written from the segment, as far as its mask allows. Packed tables end their
    segments, and other table layouts write nothing of the seed.
    """
    static = application.program.static
    com_object_table = static.com_object_table
    if (
        not m112_association_table(device_mask_version(application))
        or _packs_group_tables(application)
        or (
            com_object_table is not None and com_object_table.code_segment == segment_id
        )
    ):
        return bytes(len(initialized))
    result = bytearray(initialized)
    # The count, the device address and two octets per group address; the count
    # and two octets per association.
    for table, head in ((static.address_table, 3), (static.association_table, 1)):
        if table is None or table.code_segment != segment_id:
            continue
        start = table.offset or 0
        end = min(len(result), start + head + 2 * table.max_entries)
        result[start:end] = bytes(end - start)
    return bytes(result)


def packed_group_tables(
    application: Application, address_table_length: int
) -> PackedGroupTables | None:
    """Return the placement of the address and association tables, if packed.

    An application with ``DynamicTableManagement`` whose association flavour is
    ``AssociationTable_M112`` gets the address table at its used length and the
    association table right after it, up to the end of the association table's
    ``MaxEntries``. ``None`` without dynamic table management: both tables stay
    where the product declares them. See the resource comment in ``resources.py``.
    ``address_table_length`` includes the count octet and the device address.
    """
    if not _packs_group_tables(application):
        return None
    if address_table_length <= 0:
        return None
    static = application.program.static
    if static.code is None:
        return None
    address = static.address_table
    association = static.association_table
    if (
        address is None
        or association is None
        or address.code_segment is None
        or association.code_segment is None
        or (address.offset or 0) != 0
    ):
        return None
    segments = {segment.id: segment for segment in static.code.absolute_segment}
    address_segment = segments.get(address.code_segment)
    association_segment = segments.get(association.code_segment)
    if (
        address_segment is None
        or association_segment is None
        or address_segment.size <= 0
        or association_segment.size <= 0
    ):
        return None
    if address_table_length > address_segment.size:
        return None
    start = address_segment.address + address_table_length
    end = (
        association_segment.address
        + (association.offset or 0)
        + 1
        + 2 * association.max_entries
    )
    if start > end:
        raise ImageError(
            f"the address table ({address_table_length} octets) leaves no room for "
            f"the association table before {end:#06x}"
        )
    return PackedGroupTables(
        address_address=address_segment.address,
        address_length=address_table_length,
        declared_address_size=address_segment.size,
        association_address=start,
        association_length=end - start,
        declared_association_address=association_segment.address,
        declared_association_size=association_segment.size,
    )


@dataclass(frozen=True, slots=True)
class GroupCommunication:
    """The data needed to build a device's group communication tables."""

    device_address: int
    links: Sequence[GroupObjectLink]
    # Optional explicit ``{group object number: (flag byte, size code)}``. When set,
    # the group object table uses these instead of resolving flags from the evaluated
    # UI. Recovery needs this: a device can carry objects that its stored (recoverable)
    # parameters no longer activate in the application's UI (e.g. optional channels on a
    # System B device), so the descriptors read off the device are the authoritative
    # source for re-encoding.
    group_object_descriptors: Mapping[int, tuple[int, int]] | None = None
    # For a line/backbone coupler: the pre-built group-address filter-table bitmap (see
    # :mod:`xknxeditor.download.filter_table`). When set, the image writes it into the Router
    # object's relative memory so the coupler's own ``LdCtrlWriteRelMem ObjType=6`` programs it.
    filter_table: bytes | None = None
    # The KNX Data Secure keys and accepted senders of a securely commissioned device; the download
    # writes the security tables (see :mod:`xknxeditor.download.secure_config`) from them.
    security: GroupSecurity | None = None


@dataclass(frozen=True, slots=True)
class MemorySegment:
    """A block of memory to be written at an absolute address.

    ``mask`` (when set) has one byte per data byte: ``0xFF`` for a byte to write,
    ``0x00`` for a byte to leave untouched on the device. With no mask the whole
    block is written. Masking lets a download write only the bytes it actually
    encoded, never overwriting regions it did not produce.
    """

    address: int
    data: bytes
    mask: bytes | None = None
    initialized_mask: bytes | None = None
    # ``(offset, value)`` octets a partial download writes in place of ``data``: the object
    # count of a group object table, where a full download keeps the product's octet.
    partial_octets: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        for mask in (self.mask, self.initialized_mask):
            if mask is not None and (
                len(mask) != len(self.data) or any(b not in (0, 255) for b in mask)
            ):
                raise ImageError(
                    "memory image requires one whole-byte mask per data byte"
                )

    @property
    def end(self) -> int:
        """First address past this segment."""
        return self.address + len(self.data)

    def masked_runs(
        self, *, include_initialized: bool = False, partial: bool = False
    ) -> list[tuple[int, bytes]]:
        """Return ``(address, data)`` for each contiguous run this segment writes.

        With no mask the whole block is one run; otherwise each maximal run of
        ``0xFF`` mask bytes becomes one write. ``partial`` applies ``partial_octets``.
        """
        source = self.data
        if partial and self.partial_octets:
            patched = bytearray(source)
            for offset, value in self.partial_octets:
                patched[offset] = value
            source = bytes(patched)
        mask = self.mask
        if (
            include_initialized
            and self.initialized_mask is not None
            and mask is not None
        ):
            mask = bytes(
                a | b for a, b in zip(mask, self.initialized_mask, strict=True)
            )
        if mask is None:
            return [(self.address, source)] if source else []
        runs: list[tuple[int, bytes]] = []
        i, n = 0, len(source)
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                runs.append((self.address + i, bytes(source[i:j])))
                i = j
            else:
                i += 1
        return runs


@dataclass(frozen=True, slots=True)
class RelativeSegment:
    """A block written relative to an interface object's table base.

    Used by the System B model, where the group communication tables live in
    relative memory addressed through an object's table reference (resolved on
    the device at run time), keyed here by that object's interface object type.
    ``data``/``mask`` follow :class:`MemorySegment` starting at relative offset 0.
    """

    object_type: int
    data: bytes
    mask: bytes | None = None
    # The associations an association table segment encodes, to encode them in another format.
    associations: tuple[Association, ...] | None = None

    def masked_runs(self) -> list[tuple[int, bytes]]:
        """Return ``(relative offset, data)`` for each contiguous written run."""
        return MemorySegment(0, self.data, self.mask).masked_runs()


@dataclass(frozen=True, slots=True)
class PropertyValue:
    """A value to be written to an interface object property."""

    object_index: int | None
    property_id: int
    occurrence: int
    data: bytes
    element_size: int | None = None
    # The object type of an object addressed by type rather than by index.
    object_type: int | None = None
    # The bits of ``data`` a parameter value was written to; the others keep the property's base
    # (the factory contents or zeros). ``None``: all of ``data``.
    mask: bytes | None = None
    # The bytes a parameter covers, active or not: a parameters-only download writes these and
    # keeps the device's own value of the others. ``None``: all of ``data``.
    covered: bytes | None = None


@dataclass(frozen=True, slots=True)
class ObjectMemorySegment:
    """Application memory keyed by LSM index and relative offset, not object type."""

    object_index: int
    memory: MemorySegment


@dataclass(frozen=True, slots=True)
class DownloadImage:
    """The complete set of data to be written into a device."""

    segments: tuple[MemorySegment, ...]
    properties: tuple[PropertyValue, ...]
    relative_segments: tuple[RelativeSegment, ...] = ()
    # Interface object index -> the segment data whose MCB CRC covers it.
    object_segments: Mapping[int, bytes] = MappingProxyType({})
    # A line/backbone coupler's group-address filter-table bitmap. Written by the coupler's own
    # load procedure regardless of realisation: System B via ``LdCtrlWriteRelMem`` on the Router
    # object (type 6, relative memory), BCU1 via ``LdCtrlWriteMem`` in the ``LcFilter`` absolute
    # address space. A single source of truth for both write paths.
    filter_table: bytes | None = None
    application_segments: tuple[ObjectMemorySegment, ...] = ()
    # The security tables, for a device with secured group addresses.
    security_tables: SecurityTables | None = None
    # The contents the device leaves the factory with: the base of the properties the load
    # procedure writes.
    factory_image: FactoryImage | None = None
    # The values of the active parameters a ``choose`` in the load procedure tests, by
    # parameter ref id: they select the controls the procedure runs.
    procedure_parameters: Mapping[str, str] = MappingProxyType({})
    # The group communication tables of a device that holds them as properties (System 300).
    property_tables: PropertyTables | None = None
    # The memory segments of the mask (start, length), from its master data: part of the
    # image, with nothing to write unless a code segment or table covers them.
    mask_memory: tuple[tuple[int, int], ...] = ()
    # The allocations of a BCU2 download that places the group tables itself.
    moved_tables: MovedTables | None = None

    def relative_segment(self, object_type: int) -> RelativeSegment | None:
        """Return the relative segment for an interface object type, if any."""
        for segment in self.relative_segments:
            if segment.object_type == object_type:
                return segment
        return None

    @property
    def has_association_table(self) -> bool:
        """Whether the image carries a System B association table."""
        segment = self.relative_segment(ResourceObjectType.OBJECT_ASSOCIATION_TABLE)
        return segment is not None and segment.associations is not None

    def with_association_table_format(self, wide: bool) -> DownloadImage:
        """This image with its System B association table in format 1 (``wide``, two
        octets per field) or format 0 (one octet per field).

        The download rewrites the association table, so it must use at least the width
        the data needs: a device whose wide table has not been loaded yet reports
        format 0, but narrowing would drop any group object number or address index
        above 255 (e.g. OpenKNX devices with hundreds of objects)."""
        segment = self.relative_segment(ResourceObjectType.OBJECT_ASSOCIATION_TABLE)
        if segment is None or segment.associations is None:
            return self
        wide = wide or any(
            a.group_object_number > 0xFF or a.group_address_index + 1 > 0xFF
            for a in segment.associations
        )
        data = build_association_table_b(segment.associations, wide=wide)
        if data == segment.data:
            return self
        table = replace(segment, data=data, mask=b"\xff" * len(data))
        return replace(
            self,
            relative_segments=tuple(
                table if s is segment else s for s in self.relative_segments
            ),
        )

    def read(self, address: int, size: int) -> bytes:
        """Return ``size`` octets from ``address`` within a single segment.

        Load Procedure steps that reference the image by address (LoadImageMem)
        read their data through here. The requested range has to lie completely
        within one segment.
        """
        for segment in self.segments:
            if segment.address <= address and address + size <= segment.end:
                start = address - segment.address
                return segment.data[start : start + size]
        raise ImageError(
            f"no image data for address range {address:#06x}..{address + size:#06x}"
        )

    def read_optional(self, address: int, size: int) -> bytes | None:
        """Like :meth:`read` but return ``None`` when the range is not in the image."""
        try:
            return self.read(address, size)
        except ImageError:
            return None

    def masked_writes(
        self,
        address: int,
        size: int,
        *,
        include_initialized: bool = False,
        partial: bool = False,
    ) -> list[tuple[int, bytes]] | None:
        """Return the ``(address, data)`` runs to write within ``[address, size)``.

        Collects the masked runs of every segment that *overlaps* the range,
        clipped to it (see :meth:`MemorySegment.masked_runs`; ``partial`` for a
        partial download), in ascending address order. Returns ``None`` only when
        no segment overlaps the range at all (as :meth:`read_optional` reports
        missing data), and an empty list when overlapping segments write nothing
        within it.

        Overlap - not full containment - is the right test because an allocation is
        commonly larger than the data that fills it: the group-address and
        association table ``LdCtrlAbsSegment`` controls declare the table's full
        capacity (e.g. 513/511 octets) while the image segment holds only the used
        entries (a handful of octets). Requiring the whole allocation to lie inside
        one segment made ``masked_writes`` return ``None`` for those tables, so the
        write path (``_abs_segment``'s ``if runs``) and the preflight silently
        skipped them and the association table (the group-address links) was never
        written. Clipping to the overlap writes exactly the octets the image
        produced, exactly as a shorter WriteMem/AbsSegment should. (Callers relying
        on a single fully-containing segment - e.g. ``_relative_runs`` - are
        unaffected: a contained range still overlaps exactly that one segment.)
        """
        end = address + size
        out: list[tuple[int, bytes]] = []
        covered = False
        for segment in sorted(self.segments, key=lambda s: s.address):
            if segment.address >= end or segment.end <= address:
                continue  # no overlap with the requested range
            covered = True
            for run_address, run_data in segment.masked_runs(
                include_initialized=include_initialized, partial=partial
            ):
                lo = max(run_address, address)
                hi = min(run_address + len(run_data), end)
                if lo < hi:
                    out.append((lo, run_data[lo - run_address : hi - run_address]))
        if not covered and any(
            start <= address and end <= start + length
            for start, length in self.mask_memory
        ):
            # The mask's own memory is part of every image, with nothing to write.
            covered = True
        return out if covered else None


def build_image(
    application: Application,
    *,
    ui: DynamicUI | None = None,
    device: SeedDevice | None = None,
    parameter_values: Mapping[str, str] | None = None,
    group_communication: GroupCommunication | None = None,
    factory_image: FactoryImage | None = None,
) -> DownloadImage:
    """Assemble the download image for ``application``.

    Provide exactly one parameter state source: ``ui`` (an already-configured
    evaluator, e.g. one an editor mutates live), ``device`` (seed a fresh
    evaluator from a configured project device - parameter values, module
    instances and com object flags), or neither (the application defaults).
    ``parameter_values`` maps parameter reference ids to values and overrides the
    result before encoding (e.g. individual tweaks). ``group_communication`` adds
    the address and association table segments (needed for a full or group
    communication download); the load procedure writes them only in scope.
    ``factory_image`` (the product's ``LoadedImage``) is the base of the properties the
    procedure writes.
    """
    if ui is None and device is not None:
        from xknxeditor.prod.parser_v2.dynamic import DynamicUI

        ui = DynamicUI(
            application.program,
            parameter_instance_refs=parameter_instance_refs_from_device(device),
            module_instances=module_instances_from_device(device),
            com_object_instance_refs=com_object_instance_refs_from_device(device),
        )
    elif ui is None:
        ui = application.dynamic_ui()
        if ui is None:
            # No parameters (e.g. an interface): nothing to encode, the procedure still loads it.
            from xknxeditor.prod.parser_v2.dynamic import DynamicUI

            program, tree_builder = application.resolved()
            ui = DynamicUI(program, tree_builder=tree_builder)

    if parameter_values:
        for ref_id, value in parameter_values.items():
            ui.set_parameter_ref(ref_id, value)

    base_addresses = ui.segment_base_addrs()
    # A value that cannot encode against its type would leave a cell at the segment seed;
    # refuse to build (and thus flash) a partial image, surfacing it as an ImageError.
    try:
        encoded = ui.encode_to_memory_masked()
    except EncodingError as exc:
        raise ImageError(f"cannot encode memory image: {exc}") from exc
    static = application.program.static
    code = static.code
    declarations = (
        {s.id: s for s in (*code.absolute_segment, *code.relative_segment)}
        if code
        else {}
    )
    table_ids = {
        table.code_segment
        for table in (
            static.address_table,
            static.association_table,
            static.com_object_table,
        )
        if table is not None
    }
    reserved = reserved_memory(device_mask_version(application))
    segments: list[MemorySegment] = []
    application_segments: list[ObjectMemorySegment] = []
    for segment_id, (data, mask) in encoded.items():
        if not data or segment_id not in base_addresses:
            continue
        declaration = declarations.get(segment_id)
        seed = declaration.data if declaration is not None else None
        initialized = bytes([0xFF]) * min(len(seed or b""), len(data))
        initialized = initialized.ljust(len(data), b"\x00")
        if declaration is not None and declaration.mask is not None:
            if len(declaration.mask) > len(data):
                raise ImageError(f"initialized mask length mismatch for {segment_id}")
            # Data (and its mask) may cover only the start of the segment; the rest is not
            # initialized.
            seed_mask = declaration.mask.ljust(len(data), b"\x00")
            initialized = bytes(
                a & b for a, b in zip(initialized, seed_mask, strict=True)
            )
        if segment_id in table_ids:
            # Table builders supply initialized table bytes. Retain any parameters
            # sharing this code segment, as the earlier image builder did.
            initialized = _table_segment_initialized(
                application, segment_id, initialized
            )
        elif not isinstance(
            declaration, ApplicationProgramStaticCodeRelativeSegment
        ) and not (declaration is not None and declaration.user_memory):
            base = base_addresses[segment_id]
            if reserved:
                initialized = bytes(
                    0 if base + offset in reserved else value
                    for offset, value in enumerate(initialized)
                )
            data, initialized = _coupler_bytes(
                device_mask_version(application), base, data, mask, initialized
            )
        # A declared segment stays in the image even when nothing in it is written (a zero
        # mask and no parameters): a partial download turns its allocation into a WriteMem
        # over the whole segment, which then writes nothing. Table segments get their bytes
        # from the table builders below.
        if not any(mask) and not any(initialized) and segment_id in table_ids:
            continue
        memory = MemorySegment(base_addresses[segment_id], data, mask, initialized)
        if isinstance(declaration, ApplicationProgramStaticCodeRelativeSegment):
            application_segments.append(
                ObjectMemorySegment(declaration.load_state_machine, memory)
            )
        else:
            segments.append(memory)

    relative_segments: list[RelativeSegment] = []
    tables: PropertyTables | None = None
    moved: MovedTables | None = None
    if group_communication is not None:
        static = application.program.static
        memory_mapped = (
            static.address_table is not None
            and static.address_table.code_segment is not None
        )
        mask_version = device_mask_version(application)
        if property_group_tables(mask_version):
            tables = _group_communication_properties(
                application, ui, group_communication, factory_image
            )
        elif mask_number(mask_version) == _SYSTEM_300:
            segments = _system300_memory_tables(
                application, ui, group_communication, segments
            )
        elif memory_mapped:
            group_tables = _group_communication_segments(
                application,
                ui,
                base_addresses,
                encoded,
                group_communication,
                factory_image,
            )
            segments.extend(group_tables.segments)
            moved = group_tables.moved
        else:
            relative_segments.extend(
                _group_communication_relative_segments(
                    application, ui, group_communication, factory_image
                )
            )
    # A coupler's filter table is a distinct interface object (the Router object), not one of the
    # three group-communication tables; it is carried as its own image field and written by the
    # coupler's own load procedure (System B relative memory or BCU1 LcFilter absolute memory).
    filter_table = (
        group_communication.filter_table if group_communication is not None else None
    )
    security = (
        _security_tables(application, ui, group_communication)
        if group_communication is not None and group_communication.security is not None
        else None
    )

    try:
        encoded_properties = ui.encode_to_typed_properties_masked()
    except EncodingError as exc:
        raise ImageError(f"cannot encode property image: {exc}") from exc
    properties = tuple(
        PropertyValue(
            object_index=key[0],
            object_type=key[1],
            property_id=key[2],
            occurrence=key[3],
            data=prop.data,
            mask=prop.mask,
            covered=prop.covered,
        )
        for key, prop in encoded_properties.items()
        if prop.data
    )

    # Keep complete object data for offline validation. Never overwrite an earlier
    # segment, infer identity from an ID string, or invent bytes for gaps.
    object_segments: dict[int, bytes] = {}
    for index in {s.object_index for s in application_segments}:
        data = bytearray()
        for segment in sorted(
            (s.memory for s in application_segments if s.object_index == index),
            key=lambda s: s.address,
        ):
            if segment.address != len(data):
                if segment.address < len(data):
                    raise ImageError(
                        f"overlapping application segments for object {index}"
                    )
                break
            data.extend(segment.data)
        else:
            object_segments[index] = bytes(data)

    return DownloadImage(
        segments=tuple(segments),
        properties=properties,
        relative_segments=tuple(relative_segments),
        object_segments=MappingProxyType(object_segments),
        filter_table=filter_table,
        application_segments=tuple(application_segments),
        security_tables=security,
        factory_image=factory_image,
        procedure_parameters=MappingProxyType(_procedure_parameters(application, ui)),
        property_tables=tables,
        moved_tables=moved,
    )


def _procedure_parameters(application: Application, ui: DynamicUI) -> dict[str, str]:
    """The current values of the active parameters a ``choose`` in the load procedure tests;
    an inactive one has none, so its ``choose`` selects no controls."""
    refs = procedure_parameter_refs(application)
    if not refs:
        return {}
    active = ui.active_parameter_ref_ids()
    values: dict[str, str] = {}
    for ref in refs & active:
        value = ui.get_value(ref)
        if value is not None:
            values[ref] = value
    return values


def _security_tables(
    application: Application, ui: DynamicUI, gc: GroupCommunication
) -> SecurityTables | None:
    """The security tables for the address table the download writes: the same active links,
    so a group key refers to the index its group address gets there."""
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    assert gc.security is not None
    indexer = ApplicationIndexer(application.program)
    number_of: dict[str, int] = {}
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        if com_object is not None:
            number_of[ref_id] = com_object.number
    links = _active_links(ui, gc, number_of)
    group_addresses = sorted({link.group_address for link in links})
    tables = security_tables(
        group_addresses,
        [(link.group_address, number_of[link.com_object_ref_id]) for link in links],
        gc.security,
    )
    if tables is not None:
        _validate_security_capacity(application, tables)
    return tables


def _validate_security_capacity(
    application: Application, tables: SecurityTables
) -> None:
    """Reject security tables the device cannot hold, before any write (the application declares
    the sizes; 0 = not declared)."""
    program = application.program
    for name, limit, count in (
        (
            "Group Key Table",
            program.max_security_group_key_table_entries,
            len(tables.group_keys),
        ),
        (
            "Security Individual Address Table",
            program.max_security_individual_address_entries,
            len(tables.individual_addresses),
        ),
        (
            "Point-to-point Key Table",
            program.max_security_p2_pkey_table_entries,
            len(tables.p2p_keys),
        ),
    ):
        if limit and count > limit:
            raise ImageError(f"{name} has {count} entries; the device holds {limit}")


def _group_communication_segments(
    application: Application,
    ui: DynamicUI,
    base_addresses: Mapping[str, int],
    encoded: Mapping[str, tuple[bytes, bytes]],
    gc: GroupCommunication,
    factory_image: FactoryImage | None = None,
) -> GroupTables:
    """Build the address, association and com object table segments from links.

    Group addresses come from the links; each association references a group
    address by its address-table index and the com object by its number (read
    from the application's com object table). The com object table overlays each
    resolved com object's flags and size onto the segment seed. The table
    locations come from the application's static table definitions, on BCU1 and
    BCU2 also from the download's own placement (see :func:`_bcu_group_tables`).
    ``factory_image`` decides the communication bits (see
    ``_communication_by_link``).
    """
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    static = application.program.static
    number_of: dict[str, int] = {}
    indexer = ApplicationIndexer(application.program)
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        if com_object is not None:
            number_of[ref_id] = com_object.number

    links = _active_links(ui, gc, number_of)
    group_addresses = sorted({link.group_address for link in links})
    mask_version = device_mask_version(application)
    system1 = bcu1_association_table(mask_version)
    bcu = system1 or bcu2_association_table(mask_version)
    if not bcu and not _packs_group_tables(application):
        _validate_table_capacity(application, group_addresses, [])
    index_of = {address: i + 1 for i, address in enumerate(group_addresses)}
    linked_numbers = {number_of[link.com_object_ref_id] for link in links}

    result: list[MemorySegment] = []
    packed: PackedGroupTables | None = None
    com_object_table = static.com_object_table
    referenced_numbers = set(number_of.values())
    # The table has one record per object number up to the highest, holes included.
    record_count = 1 + max(
        {
            com_object.number
            for com_object in (
                com_object_table.com_object if com_object_table is not None else ()
            )
        }
        | referenced_numbers,
        default=-1,
    )
    options = getattr(static, "options", None)
    # Easy controller style: the application's objects all communicate while none of
    # them is active, and the BCU2 association table has no reserved slots.
    easy = bool(
        options is not None
        and options.easy_ctrl_mode_mode_style_empty_group_com_tables
        and not _has_active_objects(ui)
    )
    if com_object_table is not None and com_object_table.code_segment is not None:
        seed = encoded.get(com_object_table.code_segment)
        base = base_addresses.get(com_object_table.code_segment)
        if seed is not None and base is not None:
            by_link = _communication_by_link(
                application, factory_image, base + (com_object_table.offset or 0)
            )
            # The objects' own communication flags replace the link state when the
            # factory image holds the table.
            product_flags = None if by_link else _product_communication(application)
            if product_flags is not None and easy:
                product_flags = dict.fromkeys(product_flags, True)
            active_numbers = {
                number_of[ref_id]
                for ref_id in ui.instantiated_com_object_ref_ids()
                if ref_id in number_of
            }
            table_offset = com_object_table.offset or 0
            # A count the product data leaves unset is written.
            count_unset = not _seed_initialized(
                application, com_object_table.code_segment, table_offset
            )
            partial_octets: tuple[tuple[int, int], ...] = ()
            initialized: bytearray | None = None
            # GroupObjectTable_Bcu10 / Bcu11 / Bcu1PL keep the address, association
            # and group-object tables inside the one EEPROM segment. A 4-octet
            # record laid out from the segment start overwrites the application id
            # and the table pointers.
            if bcu1_group_object_table(mask_version):
                data, mask, partial_octets = _short_com_object_table(
                    seed[0],
                    ui,
                    linked_numbers,
                    active_numbers or None,
                    product_flags,
                    application,
                    table_offset,
                    record_count,
                    set_flags=BCU1_FLAGS,
                    count_unset=count_unset,
                )
                seed_mask = bytearray(b"\xff" * len(data))
                run_control = 0x010D - base
                if 0 <= run_control < len(seed_mask):
                    # Leave the run byte to the procedure's inline stop/start writes.
                    seed_mask[run_control] = 0
                initialized = seed_mask
            elif bcu2_group_object_table(mask_version):
                # GroupObjectTable_Bcu2 is not reallocated; a full download writes
                # the table only, not the rest of the seed.
                data, mask, partial_octets = _short_com_object_table(
                    seed[0],
                    ui,
                    linked_numbers,
                    active_numbers or None,
                    product_flags,
                    application,
                    table_offset,
                    record_count,
                    pointers=not (application.program.id or "").startswith(
                        BCU2_HIDDEN_POINTERS
                    ),
                    count_unset=count_unset,
                )
            elif m112_group_object_table(mask_version):
                # The static table also holds objects with no dynamic ComObjectRef
                # (unused channels, antitheft, …), and a hole has no object at all;
                # their seed still has the communication bit set. Leaving it makes
                # LoadCompleted checksum the wrong image and enter ERROR.
                data, mask = _build_com_object_table(
                    seed[0],
                    ui,
                    linked_numbers,
                    set(range(record_count)) | referenced_numbers,
                    active_numbers or None,
                    product_flags,
                    table_offset,
                )
                mask, partial_octets = _m112_table_writes(
                    data, mask, table_offset, record_count
                )
                # GroupObjectTable_M112 sits on the manufacturer seed (header and
                # data pointers). After AllocAbsDataSeg those seed octets must go
                # out too, or the BCU checksums an empty object table.
                initialized = bytearray(b"\xff" * len(data))
            else:
                data, mask = _build_com_object_table(
                    seed[0],
                    ui,
                    linked_numbers,
                    referenced_numbers,
                    active_numbers or None,
                    product_flags,
                    table_offset,
                )
            result.append(
                MemorySegment(
                    base,
                    data,
                    mask=mask,
                    initialized_mask=None
                    if initialized is None
                    else bytes(initialized),
                    partial_octets=partial_octets,
                )
            )
    if bcu:
        moved = _bcu_group_tables(
            application,
            base_addresses,
            gc.device_address,
            group_addresses,
            links,
            number_of,
            record_count,
            result,
            system1=system1,
            easy=easy,
        )
        return GroupTables(result, moved)
    address_table = static.address_table
    if address_table is not None and address_table.code_segment is not None:
        base = base_addresses.get(address_table.code_segment)
        if base is not None:
            offset = address_table.offset or 0
            data = build_group_address_table(gc.device_address, group_addresses)
            address = base + offset
            result.append(MemorySegment(address, data, mask=_address_table_mask(data)))
            packed = packed_group_tables(application, len(data))
    association_table = static.association_table
    if association_table is not None and association_table.code_segment is not None:
        base = base_addresses.get(association_table.code_segment)
        if base is not None:
            associations = [
                Association(
                    index_of[link.group_address],
                    number_of[link.com_object_ref_id],
                    sending=link.sending,
                )
                for link in links
            ]
            data = build_association_table(associations)
            if packed is None:
                _validate_table_capacity(application, group_addresses, associations)
                address = base + (association_table.offset or 0)
            else:
                # Packed tables are bound by the room left, not by MaxEntries.
                if len(data) > packed.association_length:
                    raise ImageError(
                        f"association table has {len(associations)} entries; "
                        f"{packed.association_length} octets are left for it"
                    )
                address = packed.association_address
            result.append(MemorySegment(address, data, mask=b"\xff" * len(data)))
    return GroupTables(result)


def _address_table_mask(data: bytes) -> bytes:
    """The download writes only the count and the group address entries of an address
    table; the two own-address octets after the count are written during
    individual-address programming, not by this download, so keep them out of the mask
    (they legitimately differ per realisation, e.g. a 0xffff placeholder)."""
    mask = bytearray(b"\xff" * len(data))
    mask[1] = mask[2] = 0x00
    return bytes(mask)


def _has_active_objects(ui: DynamicUI) -> bool:
    """Whether the device has an active group object: one it instantiated, or else one
    its parameters show."""
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    if ui.instantiated_com_object_ref_ids():
        return True
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            return True
        if isinstance(node, (UiTab, UiParameterBlock)):
            stack.extend(node.children)
    return False


def _seed_initialized(application: Application, segment_id: str, offset: int) -> bool:
    """Whether the product data gives the octet at ``offset`` of a code segment."""
    code = application.program.static.code
    for segment in code.absolute_segment if code is not None else ():
        if segment.id == segment_id:
            data: bytes = getattr(segment, "data", None) or b""
            mask: bytes | None = getattr(segment, "mask", None)
            return offset < len(data) and (
                mask is None or (offset < len(mask) and mask[offset] != 0)
            )
    return False


# GrAssocTabPtr: one octet at 0111h; the association table lies at 100h + its value.
_ASSOCIATION_POINTER = 0x0111
# The BCU1 and BCU2 address table: the count, the device's own address and the group
# addresses from 0119h.
_ADDRESS_TABLE = 0x0116
_GROUP_ADDRESSES = _ADDRESS_TABLE + 3
# The segment flag of an allocation that ends in a checksum octet.
_CHECKSUM_SEGMENT = 0x80


def _bcu_group_tables(
    application: Application,
    base_addresses: Mapping[str, int],
    device_address: int,
    group_addresses: Sequence[int],
    links: Sequence[GroupObjectLink],
    number_of: Mapping[str, int],
    object_count: int,
    result: list[MemorySegment],
    *,
    system1: bool,
    easy: bool,
) -> MovedTables | None:
    """Add the BCU1 (``system1``) or BCU2 address and association tables to ``result``.

    The address table lies at 0116h. The association list follows the group object
    numbers, each object's links in their order. On BCU1, and on BCU2 unless the
    application keeps its tables in easy controller style (``easy``), it starts with a
    reserved slot per group object (see :func:`association_slots`).

    BCU1 puts a dynamically managed association table right after the address table,
    and always points GrAssocTabPtr at the association table. BCU2 moves a dynamically
    managed one there too, after the checksum octet of the address table's segment;
    it points GrAssocTabPtr at the table when the product declares it in
    0100h..01FFh. The seed octets of the declared table areas are not written.
    Returns the BCU2 placement for the load procedure.
    """
    static = application.program.static
    index_of = {address: i + 1 for i, address in enumerate(group_addresses)}
    associations = [
        Association(
            index_of[link.group_address],
            number_of[link.com_object_ref_id],
            sending=link.sending,
        )
        for link in sorted(links, key=lambda link: number_of[link.com_object_ref_id])
    ]
    if system1 or not easy:
        associations = association_slots(associations, object_count)
    address_data = build_group_address_table(device_address, group_addresses)
    association_data = build_association_list(associations)
    address_entries = (
        static.address_table.max_entries if static.address_table is not None else 0
    )
    table = static.association_table
    association_entries = table.max_entries if table is not None else 0
    base = (
        base_addresses.get(table.code_segment)
        if table is not None and table.code_segment is not None
        else None
    )
    nominal = None if base is None or table is None else base + (table.offset or 0)
    dynamic = dynamic_tables(application)
    moved: MovedTables | None = None
    pointer: int | None = None
    if system1:
        if dynamic:
            address = _ADDRESS_TABLE + len(address_data)
            room = 3 + 2 * address_entries + 1 + 2 * association_entries
            if len(address_data) + len(association_data) > room:
                raise ImageError(
                    "group address and association tables need "
                    f"{len(address_data) + len(association_data)} octets from 0116h; "
                    f"the product data declares {room}"
                )
            if nominal is not None and nominal > _GROUP_ADDRESSES:
                _invalidate(
                    result, _GROUP_ADDRESSES, nominal + 1 + 2 * association_entries
                )
        else:
            _check_declared_entries(
                len(group_addresses),
                address_entries,
                len(associations),
                association_entries,
            )
            if nominal is None:
                raise ImageError("the product data has no association table")
            address = nominal
            _invalidate(
                result, _GROUP_ADDRESSES, _GROUP_ADDRESSES + 2 * address_entries
            )
            _invalidate(result, nominal, nominal + 1 + 2 * association_entries)
        pointer = address
    else:
        if len(group_addresses) > 254:
            raise ImageError(
                f"address table has {len(group_addresses)} group addresses; "
                "the device holds 254"
            )
        if not dynamic:
            _check_declared_entries(
                len(group_addresses),
                address_entries,
                len(associations),
                association_entries,
            )
        _invalidate(result, _GROUP_ADDRESSES, _GROUP_ADDRESSES + 2 * address_entries)
        if nominal is None:
            result.append(
                MemorySegment(
                    _ADDRESS_TABLE, address_data, mask=_address_table_mask(address_data)
                )
            )
            return None
        end = nominal + 1 + 2 * association_entries
        _invalidate(result, nominal, end)
        address = nominal
        if dynamic:
            address = (
                _ADDRESS_TABLE + len(address_data) + _address_checksum(application)
            )
            if end < address or len(association_data) > end - address:
                raise ImageError(
                    f"association table has {len(associations)} entries; "
                    f"{max(end - address, 0)} octets are left for it after the "
                    "address table"
                )
            moved = MovedTables(len(address_data), address)
        if 0x0100 <= nominal <= 0x01FF:
            pointer = address
    result.append(
        MemorySegment(
            _ADDRESS_TABLE, address_data, mask=_address_table_mask(address_data)
        )
    )
    result.append(
        MemorySegment(address, association_data, mask=b"\xff" * len(association_data))
    )
    if pointer is not None:
        _set_octet(result, _ASSOCIATION_POINTER, pointer & 0xFF)
    return moved


def _check_declared_entries(
    addresses: int, address_entries: int, associations: int, association_entries: int
) -> None:
    """Refuse tables longer than the product data declares them (``MaxEntries``)."""
    if addresses > address_entries:
        raise ImageError(
            f"address table has {addresses} entries; MaxEntries={address_entries}"
        )
    if associations > association_entries:
        raise ImageError(
            f"association table has {associations} entries; "
            f"MaxEntries={association_entries}"
        )


def _address_checksum(application: Application) -> int:
    """1 when the address table's segment (load state machine 1) ends in a checksum
    octet, else 0."""
    if not segments_have_inline_checksum(device_mask_version(application)):
        return 0
    control = lsm_segment(application, 1)
    return 1 if control is not None and control.seg_flags & _CHECKSUM_SEGMENT else 0


def lsm_segment(application: Application, lsm: int) -> LdCtrlAbsSegment | None:
    """The first ``LdCtrlAbsSegment`` of load state machine ``lsm`` in the application's
    load procedures."""
    procedures = getattr(application, "load_procedures", None)
    for procedure in procedures.load_procedure if procedures is not None else ():
        for control in iter_load_controls(procedure.choice):
            if isinstance(control, LdCtrlAbsSegment) and control.lsm_idx == lsm:
                return control
    return None


def _invalidate(segments: list[MemorySegment], start: int, end: int) -> None:
    """Leave the seed octets in ``[start, end)`` of ``segments`` unwritten."""
    for i, segment in enumerate(segments):
        initialized = segment.initialized_mask
        low, high = max(start, segment.address), min(end, segment.end)
        if initialized is None or low >= high:
            continue
        valid = bytearray(initialized)
        valid[low - segment.address : high - segment.address] = bytes(high - low)
        segments[i] = replace(segment, initialized_mask=bytes(valid))


def _set_octet(segments: list[MemorySegment], address: int, value: int) -> None:
    """Write ``value`` at ``address``: into the segment that covers it, or as a segment
    of its own."""
    for i, segment in enumerate(segments):
        offset = address - segment.address
        if 0 <= offset < len(segment.data):
            data = bytearray(segment.data)
            data[offset] = value
            mask = bytearray(segment.mask or b"\xff" * len(data))
            mask[offset] = 0xFF
            segments[i] = replace(segment, data=bytes(data), mask=bytes(mask))
            return
    segments.append(MemorySegment(address, bytes([value]), mask=b"\xff"))


def _group_communication_relative_segments(
    application: Application,
    ui: DynamicUI,
    gc: GroupCommunication,
    factory_image: FactoryImage | None = None,
) -> list[RelativeSegment]:
    """Build the System B group communication tables as relative segments.

    The address, association and group object tables live in relative memory
    (System B), keyed here by interface object type. Group addresses come from
    the links; associations reference a group address by its address-table index
    and a group object by its number; the group object table carries flags and
    size for every active object, including unlinked objects with communication disabled.
    """
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    number_of: dict[str, int] = {}
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        if com_object is not None:
            number_of[ref_id] = com_object.number

    links = _active_links(ui, gc, number_of)
    group_addresses = sorted({link.group_address for link in links})
    index_of = group_address_index_b(group_addresses)

    associations = [
        Association(
            index_of[link.group_address] - 1,
            number_of[link.com_object_ref_id],
            sending=link.sending,
        )
        for link in links
    ]
    _validate_table_capacity(application, group_addresses, associations)
    linked_numbers = {association.group_object_number for association in associations}

    # Prefer explicit descriptors (recovered off the device) over UI resolution: a
    # recovered device can link objects the application's UI no longer activates.
    by_link = _communication_by_link(application, factory_image, None)
    if gc.group_object_descriptors is not None:
        descriptors = dict(gc.group_object_descriptors)
    else:
        descriptors = _resolved_group_object_descriptors(
            ui, linked_numbers, by_link=by_link
        )
        if not by_link:
            # An object the device does not instantiate keeps only its own
            # communication flag.
            for number, communication in _product_communication(application).items():
                if communication and number not in descriptors:
                    descriptors[number] = (_COMMUNICATION_BIT, 0)
    highest_number = max((c.number for c in indexer.com_objects.values()), default=0)

    address_data = build_group_address_table_b(group_addresses)
    association_data = build_association_table_b(
        associations,
        wide=association_table_wide(len(group_addresses), highest_number),
    )
    group_object_data = build_group_object_table_b(descriptors, highest_number)

    return [
        RelativeSegment(
            ResourceObjectType.OBJECT_ADDRESS_TABLE,
            address_data,
            b"\xff" * len(address_data),
        ),
        RelativeSegment(
            ResourceObjectType.OBJECT_ASSOCIATION_TABLE,
            association_data,
            b"\xff" * len(association_data),
            associations=tuple(associations),
        ),
        RelativeSegment(
            ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE,
            group_object_data,
            b"\xff" * len(group_object_data),
        ),
    ]


_SYSTEM_300 = 0x0300
# Where the group address table of a System 300 memory configuration is, unless the
# procedure allocates its segment (load state machine 1) elsewhere.
_SYSTEM_300_ADDRESS_TABLE = 0x0116
_ADDRESS_TABLE_LSM = 1


def _system300_memory_tables(
    application: Application,
    ui: DynamicUI,
    gc: GroupCommunication,
    segments: list[MemorySegment],
) -> list[MemorySegment]:
    """The group address table count of a System 300 device that keeps its tables in memory
    (the configuration without property tables).

    Its procedures allocate the table segments and write only the EEPROM segments, which reach
    the first octet of the group address table: its count, 1 + the number of group addresses.
    That octet is set in the image segment that holds it (or a segment of its own); the rest of
    the tables is not written.
    """
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    number_of: dict[str, int] = {}
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        if com_object is not None:
            number_of[ref_id] = com_object.number
    links = _active_links(ui, gc, number_of)
    group_addresses = sorted({link.group_address for link in links})
    index_of = group_address_index_b(group_addresses)
    _validate_table_capacity(
        application,
        group_addresses,
        [
            Association(
                index_of[link.group_address] - 1,
                number_of[link.com_object_ref_id],
                sending=link.sending,
            )
            for link in links
        ],
    )
    address = _allocated_address(application, _ADDRESS_TABLE_LSM)
    if address is None:
        address = _SYSTEM_300_ADDRESS_TABLE
    count = 1 + len(group_addresses)
    if count > 0xFF:
        raise ImageError(f"group address table has too many entries: {count - 1}")
    result: list[MemorySegment] = []
    placed = False
    for segment in segments:
        if not placed and segment.address <= address < segment.end:
            at = address - segment.address
            data, mask = bytearray(segment.data), bytearray(segment.mask or b"")
            data[at] = count
            if mask:
                mask[at] = 0xFF
            segment = replace(
                segment, data=bytes(data), mask=bytes(mask) if mask else None
            )
            placed = True
        result.append(segment)
    if not placed:
        result.append(MemorySegment(address, bytes([count]), b"\xff"))
    return result


def _allocated_address(application: Application, lsm: int) -> int | None:
    """The address of the first segment the application's own procedure allocates for the
    load state machine ``lsm``, if it allocates one."""
    from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import (
        LdCtrlAbsSegment,
    )

    procedures = application.load_procedures
    for procedure in procedures.load_procedure if procedures is not None else ():
        for control in procedure.choice:
            if isinstance(control, LdCtrlAbsSegment) and control.lsm_idx == lsm:
                return control.address
    return None


def _group_communication_properties(
    application: Application,
    ui: DynamicUI,
    gc: GroupCommunication,
    factory_image: FactoryImage | None = None,
) -> PropertyTables:
    """Build the group communication tables of a device that holds them as properties
    (System 300, see :mod:`.tables_system300`).

    The group addresses and links are those of the System B tables. Every group object
    number up to the highest gets its flags: an active object from its configuration, any
    other object from its own flags (its communication flag only while linked, unless the
    factory image holds the table), a hole in the numbering none.
    """
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    number_of: dict[str, int] = {}
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        if com_object is not None:
            number_of[ref_id] = com_object.number

    links = _active_links(ui, gc, number_of)
    group_addresses = sorted({link.group_address for link in links})
    index_of = group_address_index_b(group_addresses)
    associations = [
        Association(
            index_of[link.group_address] - 1,
            number_of[link.com_object_ref_id],
            sending=link.sending,
        )
        for link in links
    ]
    _validate_table_capacity(application, group_addresses, associations)
    linked_numbers = {association.group_object_number for association in associations}

    by_link = _communication_by_link(application, factory_image, None)
    product_flags = None if by_link else _product_communication(application)
    if gc.group_object_descriptors is not None:
        visible = dict(gc.group_object_descriptors)
    else:
        visible = _resolved_group_object_descriptors(
            ui, linked_numbers, by_link=by_link
        )
    # Objects the device instantiates; recovered descriptors stand for themselves.
    active_numbers = (
        {
            number_of[ref_id]
            for ref_id in ui.instantiated_com_object_ref_ids()
            if ref_id in number_of
        }
        if gc.group_object_descriptors is None
        else set(visible)
    )
    static_objects = {
        com_object.number: com_object for com_object in indexer.com_objects.values()
    }
    highest_number = max(
        {*static_objects, *number_of.values(), *visible, *linked_numbers}, default=0
    )
    flags: dict[int, int] = {}
    for number in range(1, highest_number + 1):
        descriptor = visible.get(number)
        if active_numbers and number not in active_numbers:
            descriptor = None
        if descriptor is not None:
            flags[number] = descriptor[0]
        elif number in static_objects:
            flags[number] = _static_flag_byte(
                static_objects[number],
                _inactive_communication(number, linked_numbers, product_flags),
            )
        else:
            flags[number] = _HOLE_FLAGS
    return property_tables(group_addresses, associations, flags, highest_number)


def _active_links(
    ui: DynamicUI, gc: GroupCommunication, number_of: dict[str, int]
) -> list[GroupObjectLink]:
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    active: set[str] = set()
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            active.add(node.ref_id)
            number_of[node.ref_id] = node.number
        elif isinstance(node, (UiTab, UiParameterBlock)):
            stack.extend(node.children)
    active = ui.instantiated_com_object_ref_ids() or active
    result: list[GroupObjectLink] = []
    for link in gc.links:
        number = number_of.get(link.com_object_ref_id)
        if number is None:
            raise ImageError(
                f"unknown communication object ref {link.com_object_ref_id!r}"
            )
        if gc.group_object_descriptors is not None:
            enabled = number in gc.group_object_descriptors
        else:
            enabled = link.com_object_ref_id in active
        if enabled:
            result.append(link)
    return result


def _validate_table_capacity(
    application: Application,
    addresses: Sequence[int],
    associations: Sequence[Association],
) -> None:
    for name, table, count in (
        ("address", application.program.static.address_table, len(addresses)),
        (
            "association",
            application.program.static.association_table,
            len(associations),
        ),
    ):
        if table is not None and count > table.max_entries:
            raise ImageError(
                f"{name} table has {count} entries; MaxEntries={table.max_entries}"
            )


def _communication_by_link(
    application: Application,
    factory_image: FactoryImage | None,
    table_address: int | None,
) -> bool:
    """Whether only active, linked objects get the communication bit.

    That is the rule unless the product's factory image holds the group object table
    (at the mask's resource location, or at ``table_address`` in memory): then every
    object keeps its own communication flag.
    """
    if factory_image is None:
        return True
    resources = bundled_mask_resources(device_mask_version(application))
    location = (
        resources.location(ResourceName.GROUP_OBJECT_TABLE) if resources else None
    )
    for segment in factory_image.segments:
        if (
            location is not None
            and location.address_space is ResourceAddrSpace.RELATIVE_MEMORY
            and segment.location is FactoryLocation.RELATIVE_MEMORY
            and segment.object_ref == location.interface_object_ref
            and segment.property_id == location.property_id
            and segment.address == (location.start_address or 0)
        ):
            return False
        if segment.location is FactoryLocation.MEMORY and segment.address in (
            table_address,
            location.start_address
            if location is not None
            and location.address_space is ResourceAddrSpace.STANDARD_MEMORY
            else None,
        ):
            return False
    return True


def _product_communication(application: Application) -> dict[int, bool]:
    """The communication flag of each group object of the application, by number."""
    table = application.program.static.com_object_table
    return {
        com_object.number: com_object.communication_flag is Enable.ENABLED
        for com_object in (table.com_object if table is not None else ())
    }


def _resolved_group_object_descriptors(
    ui: DynamicUI,
    linked_numbers: set[int],
    *,
    by_link: bool = True,
    memory_sizes: bool = False,
) -> dict[int, tuple[int, int]]:
    """Map each active object's number to its ``(flag byte, size code)``.

    Walks the configured UI so flags and size come from the resolved com object
    reference. With ``by_link``, unlinked active objects retain all flags except
    communication. ``memory_sizes`` takes the size codes of a BCU1/BCU2 record (see
    :func:`memory_size_code`); otherwise an object without a size code is left out.
    """
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    descriptors: dict[int, tuple[int, int]] = {}
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            if memory_sizes:
                size = memory_size_code(node.object_size)
            else:
                try:
                    size = size_code(node.object_size)
                except ImageError:
                    continue
            flags = com_object_flag_byte(
                priority=node.priority or "Low",
                communication=node.communication
                and (not by_link or node.number in linked_numbers),
                read=node.read,
                write=node.write,
                transmit=node.transmit,
                update=node.update,
                read_on_init=node.read_on_init,
            )
            descriptors[node.number] = (flags, size)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            stack.extend(node.children)
    return descriptors


# Layout of a com object table record: 5 octet header, then 4 octet records; the
# flag byte is the first octet of each record (offset 5 + number*4).
_COM_OBJECT_HEADER = 5
_COM_OBJECT_RECORD = 4
_COMMUNICATION_BIT = 0x04


def _build_com_object_table(
    seed: bytes,
    ui: DynamicUI,
    linked_numbers: set[int],
    object_numbers: set[int],
    active_numbers: set[int] | None = None,
    product_flags: Mapping[int, bool] | None = None,
    table_offset: int = 0,
) -> tuple[bytes, bytes]:
    """Overlay each com object's flags onto the table seed (the segment, with the table at
    ``table_offset``).

    Follows the Group Object Table definition (3/5/1 Resources, 4.18): an *active*
    com object gets its full flag byte and size recomputed (communication bit set
    only when the object is linked); every other defined object keeps the
    manufacturer's seed flags with just the communication bit toggled by link
    state. This second, seed-preserving path is what a byte-exact download needs
    for realisations whose seed already carries the real per-object flags.
    ``object_numbers`` bounds the table to the objects the application defines, so
    the pass never touches bytes past the table (some products share the segment
    with parameter data).

    ``active_numbers`` is the set of com objects the device actually instantiated
    (its GroupObjectTree). When given, only those are recomputed; the rest are
    seed-preserved even if the parameter-driven UI still shows them. This matches
    genuine implementations, whose table follows the group object's ``Active`` flag: a device in,
    say, "2x Tunable White" mode does not carry the individual per-channel objects
    that the raw parameter defaults would otherwise activate. When ``None`` (a device
    configured from scratch, with no saved instances) every visible object is active.

    ``product_flags`` (the objects' own communication flags, used when the factory
    image holds the table) replaces the link state as the source of the communication
    bit, for active and other objects alike.
    """
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    descriptors: list[ComObjectDescriptor] = []
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            if active_numbers is not None and node.number not in active_numbers:
                # Not instantiated by this device: leave to the seed-preserving pass.
                continue
            try:
                size = size_code(node.object_size)
            except ImageError:
                continue
            flags = com_object_flag_byte(
                priority=node.priority or "Low",
                communication=node.communication
                and (product_flags is not None or node.number in linked_numbers),
                read=node.read,
                write=node.write,
                transmit=node.transmit,
                update=node.update,
                read_on_init=node.read_on_init,
            )
            descriptors.append(
                ComObjectDescriptor(number=node.number, flags=flags, size=size)
            )
        elif isinstance(node, (UiTab, UiParameterBlock)):
            stack.extend(node.children)

    data, mask = build_com_object_table(
        seed, descriptors, header_size=table_offset + _COM_OBJECT_HEADER
    )
    visible = {descriptor.number for descriptor in descriptors}
    data, mask = bytearray(data), bytearray(mask)
    for number in object_numbers:
        if number in visible:
            continue
        offset = table_offset + _COM_OBJECT_HEADER + number * _COM_OBJECT_RECORD
        if offset >= len(data):
            continue
        flags = seed[offset] & ~_COMMUNICATION_BIT
        if _inactive_communication(number, linked_numbers, product_flags):
            flags |= _COMMUNICATION_BIT
        data[offset] = flags
        mask[offset] = 0xFF
    return bytes(data), bytes(mask)


def _inactive_communication(
    number: int, linked_numbers: set[int], product_flags: Mapping[int, bool] | None
) -> bool:
    """The communication bit of an object the device does not instantiate: its own flag
    when ``product_flags`` is given, else whether it is linked."""
    if product_flags is not None:
        return product_flags.get(number, False)
    return number in linked_numbers


def _object_count(
    data: bytes, mask: bytearray, at: int, count: int
) -> tuple[tuple[int, int], ...]:
    """Mark the object count at ``at`` for writing, and return the partial octets for it.

    The count is the number of records. A full download keeps the product's octet when it
    differs (the product data is inconsistent then); a partial download writes the count.
    """
    if at >= len(data):
        return ()
    mask[at] = 0xFF
    count &= 0xFF
    return () if data[at] == count else ((at, count),)


def _m112_table_writes(
    data: bytes, mask: bytes, table_offset: int, record_count: int
) -> tuple[bytes, tuple[tuple[int, int], ...]]:
    """Mask the whole GroupObjectTable_M112 up to the last record for writing.

    The table is the object count, the RAM flags table pointer (2 octets) and a 4-octet
    record per object number. A partial download writes the count and the pointers from
    the seed along with the flags and sizes, not just those.
    """
    written = bytearray(mask)
    partial = _object_count(data, written, table_offset, record_count)
    end = min(len(written), table_offset + 3 + record_count * _COM_OBJECT_RECORD)
    for offset in range(table_offset + 1, end):
        written[offset] = 0xFF
    return bytes(written), partial


# GroupObjectTable_Bcu1x and GroupObjectTable_Bcu2: the object count, the RAM flags table
# pointer and a 3-octet record per object number: the value pointer, the flag byte and
# the type byte. Which masks those are is the flavour, not a nibble of the mask version
# (see resources.py).
_SHORT_HEADER = 2
_SHORT_RECORD = 3
# Bit 7 of every BCU1 flag byte is set.
BCU1_FLAGS = 0x80
# Applications (id prefixes) whose value pointers in a BCU2 table are not written.
BCU2_HIDDEN_POINTERS = (
    "M-00FD_A-0006-10",
    "M-00FD_A-0001-11",
    "M-00FD_A-0001-1B",
    "M-00FD_A-0003-10",
    "M-00FD_A-0007-10",
    "M-00FD_A-0008-10",
    "M-00FD_A-0009-10",
    "M-00FD_A-000A-10",
    "M-00FD_A-000B-10",
    "M-00FD_A-000C-10",
)
# The flag byte of a hole in the numbering: no flags, low priority.
_HOLE_FLAGS = 0x03


def _static_flag_byte(com_object: ComObject, communication: bool) -> int:
    """The flag byte of an object the device does not instantiate, from its own flags."""
    return com_object_flag_byte(
        priority=com_object.priority.value,
        communication=communication,
        read=com_object.read_flag is Enable.ENABLED,
        write=com_object.write_flag is Enable.ENABLED,
        transmit=com_object.transmit_flag is Enable.ENABLED,
        update=com_object.update_flag is Enable.ENABLED,
        read_on_init=com_object.read_on_init_flag is Enable.ENABLED,
    )


def _short_com_object_table(
    seed: bytes,
    ui: DynamicUI,
    linked_numbers: set[int],
    active_numbers: set[int] | None,
    product_flags: Mapping[int, bool] | None,
    application: Application,
    table_offset: int,
    record_count: int,
    *,
    set_flags: int = 0,
    pointers: bool = True,
    count_unset: bool = False,
) -> tuple[bytes, bytes, tuple[tuple[int, int], ...]]:
    """Write a table of 3-octet records at ``table_offset`` of its segment seed.

    Every record gets its flag byte: an active object from its configuration, any other
    object from its own flags, a hole in the numbering none; ``set_flags`` are set in
    each. Bit 5 of the flag byte stays as the seed has it. An active object also gets its
    size in the type byte, whose top two bits stay. The count, the RAM flags table
    pointer and the value pointers (with ``pointers``) are written from the seed; a count
    the product data leaves unset (``count_unset``) is the number of records.
    """
    data, mask = bytearray(seed), bytearray(len(seed))
    if count_unset and table_offset < len(data):
        data[table_offset] = record_count & 0xFF
    partial = _object_count(bytes(data), mask, table_offset, record_count)
    if table_offset + 1 < len(mask):
        mask[table_offset + 1] = 0xFF
    table = application.program.static.com_object_table
    static_objects = {
        com_object.number: com_object
        for com_object in (table.com_object if table is not None else ())
    }
    visible = _resolved_group_object_descriptors(
        ui, linked_numbers, by_link=product_flags is None, memory_sizes=True
    )
    for number in range(record_count):
        record = table_offset + _SHORT_HEADER + number * _SHORT_RECORD
        if record + _SHORT_RECORD > len(data):
            break
        descriptor = visible.get(number)
        if active_numbers is not None and number not in active_numbers:
            descriptor = None
        if descriptor is not None:
            flags, size = descriptor
            data[record + 2] = (data[record + 2] & 0xC0) | (size & 0x3F)
        elif number in static_objects:
            flags = _static_flag_byte(
                static_objects[number],
                _inactive_communication(number, linked_numbers, product_flags),
            )
        else:
            flags = _HOLE_FLAGS
        data[record + 1] = (
            (data[record + 1] & SEGMENT_SELECTOR_BIT)
            | (flags & ~SEGMENT_SELECTOR_BIT)
            | set_flags
        )
        if pointers:
            mask[record] = 0xFF
        mask[record + 1] = mask[record + 2] = 0xFF
    return bytes(data), bytes(mask), partial
