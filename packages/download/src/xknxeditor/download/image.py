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

from xknxeditor.namespaces.intermediate.application_program_static_t_code_relative_segment import (
    ApplicationProgramStaticCodeRelativeSegment,
)
from xknxeditor.prod import Application
from xknxeditor.prod.errors import EncodingError

from .errors import ImageError
from .project_data import (
    GroupObjectLink,
    SeedDevice,
    com_object_instance_refs_from_device,
    module_instances_from_device,
    parameter_instance_refs_from_device,
)
from .tables import (
    Association,
    ComObjectDescriptor,
    build_association_table,
    build_com_object_table,
    build_group_address_table,
    com_object_flag_byte,
    size_code,
)
from .tables_systemb import (
    association_table_wide,
    build_association_table_b,
    build_group_address_table_b,
    build_group_object_table_b,
    group_address_index_b,
)

# Interface object types of the three group communication tables (KNX Standard
# v3.0.0, Chapter 3/5/1 "Resources": the Address Table Object, the Association
# Table Object, and the Group Object Table Object, Object Type 9, section 4.8).
_ADDRESS_TABLE_TYPE = 1
_ASSOCIATION_TABLE_TYPE = 2
_GROUP_OBJECT_TABLE_TYPE = 9
# The Router (line coupler) interface object (KNX Standard v3.0.0, 3/5/1). A coupler's group
# address filter table lives in this object's relative memory (PID_TABLE_REFERENCE), so it is
# written by an ``LdCtrlWriteRelMem ObjType=6`` keyed on this object type.
_ROUTER_OBJECT_TYPE = 6

if TYPE_CHECKING:
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI


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
        self, *, include_initialized: bool = False
    ) -> list[tuple[int, bytes]]:
        """Return ``(address, data)`` for each contiguous run this segment writes.

        With no mask the whole block is one run; otherwise each maximal run of
        ``0xFF`` mask bytes becomes one write.
        """
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
            return [(self.address, self.data)] if self.data else []
        runs: list[tuple[int, bytes]] = []
        i, n = 0, len(self.data)
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                runs.append((self.address + i, bytes(self.data[i:j])))
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

    def relative_segment(self, object_type: int) -> RelativeSegment | None:
        """Return the relative segment for an interface object type, if any."""
        for segment in self.relative_segments:
            if segment.object_type == object_type:
                return segment
        return None

    @property
    def has_association_table(self) -> bool:
        """Whether the image carries a System B association table."""
        segment = self.relative_segment(_ASSOCIATION_TABLE_TYPE)
        return segment is not None and segment.associations is not None

    def with_association_table_format(self, wide: bool) -> DownloadImage:
        """This image with its System B association table in format 1 (``wide``, two
        octets per field) or format 0 (one octet per field)."""
        segment = self.relative_segment(_ASSOCIATION_TABLE_TYPE)
        if segment is None or segment.associations is None:
            return self
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
        self, address: int, size: int, *, include_initialized: bool = False
    ) -> list[tuple[int, bytes]] | None:
        """Return the ``(address, data)`` runs to write within ``[address, size)``.

        Collects the masked runs of every segment that *overlaps* the range,
        clipped to it (see :meth:`MemorySegment.masked_runs`), in ascending
        address order. Returns ``None`` only when no segment overlaps the range at
        all (as :meth:`read_optional` reports missing data), and an empty list when
        overlapping segments write nothing within it.

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
                include_initialized=include_initialized
            ):
                lo = max(run_address, address)
                hi = min(run_address + len(run_data), end)
                if lo < hi:
                    out.append((lo, run_data[lo - run_address : hi - run_address]))
        return out if covered else None


def build_image(
    application: Application,
    *,
    ui: DynamicUI | None = None,
    device: SeedDevice | None = None,
    parameter_values: Mapping[str, str] | None = None,
    group_communication: GroupCommunication | None = None,
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
            raise ImageError("application has no dynamic section to encode")

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
            if len(declaration.mask) != len(data):
                raise ImageError(f"initialized mask length mismatch for {segment_id}")
            initialized = bytes(
                a & b for a, b in zip(initialized, declaration.mask, strict=True)
            )
        if segment_id in table_ids:
            # Table builders supply initialized table bytes. Retain any parameters
            # sharing this code segment, as the earlier image builder did.
            initialized = bytes(len(data))
        if not any(mask) and not any(initialized):
            continue
        memory = MemorySegment(base_addresses[segment_id], data, mask, initialized)
        if isinstance(declaration, ApplicationProgramStaticCodeRelativeSegment):
            application_segments.append(
                ObjectMemorySegment(declaration.load_state_machine, memory)
            )
        else:
            segments.append(memory)

    relative_segments: list[RelativeSegment] = []
    if group_communication is not None:
        static = application.program.static
        memory_mapped = (
            static.address_table is not None
            and static.address_table.code_segment is not None
        )
        if memory_mapped:
            segments.extend(
                _group_communication_segments(
                    application, ui, base_addresses, encoded, group_communication
                )
            )
        else:
            relative_segments.extend(
                _group_communication_relative_segments(
                    application, ui, group_communication
                )
            )
    # A coupler's filter table is a distinct interface object (the Router object), not one of the
    # three group-communication tables; it is carried as its own image field and written by the
    # coupler's own load procedure (System B relative memory or BCU1 LcFilter absolute memory).
    filter_table = (
        group_communication.filter_table if group_communication is not None else None
    )

    try:
        encoded_properties = ui.encode_to_properties()
    except EncodingError as exc:
        raise ImageError(f"cannot encode property image: {exc}") from exc
    properties = tuple(
        PropertyValue(
            object_index=key[0],
            property_id=key[1],
            occurrence=key[2],
            data=data,
        )
        for key, data in encoded_properties.items()
        if data
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
    )


def _group_communication_segments(
    application: Application,
    ui: DynamicUI,
    base_addresses: Mapping[str, int],
    encoded: Mapping[str, tuple[bytes, bytes]],
    gc: GroupCommunication,
) -> list[MemorySegment]:
    """Build the address, association and com object table segments from links.

    Group addresses come from the links; each association references a group
    address by its address-table index and the com object by its number (read
    from the application's com object table). The com object table overlays each
    resolved com object's flags and size onto the segment seed. The table
    locations come from the application's static table definitions.
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
    _validate_table_capacity(application, group_addresses, [])
    index_of = {address: i + 1 for i, address in enumerate(group_addresses)}
    linked_numbers = {number_of[link.com_object_ref_id] for link in links}

    result: list[MemorySegment] = []
    com_object_table = static.com_object_table
    if com_object_table is not None and com_object_table.code_segment is not None:
        seed = encoded.get(com_object_table.code_segment)
        base = base_addresses.get(com_object_table.code_segment)
        if seed is not None and base is not None:
            active_numbers = {
                number_of[ref_id]
                for ref_id in ui.instantiated_com_object_ref_ids()
                if ref_id in number_of
            }
            data, mask = _build_com_object_table(
                seed[0],
                ui,
                linked_numbers,
                set(number_of.values()),
                active_numbers or None,
            )
            result.append(MemorySegment(base, data, mask=mask))
    address_table = static.address_table
    if address_table is not None and address_table.code_segment is not None:
        base = base_addresses.get(address_table.code_segment)
        if base is not None:
            offset = address_table.offset or 0
            data = build_group_address_table(gc.device_address, group_addresses)
            # The download writes only the count and the group address
            # entries; the two own-address octets after the count are written
            # during individual-address programming, not by this download, so keep
            # them out of the mask (they legitimately differ per realisation, e.g.
            # a 0xffff placeholder).
            mask = bytearray(b"\xff" * len(data))
            mask[1] = mask[2] = 0x00
            address = base + offset
            result.append(MemorySegment(address, data, mask=bytes(mask)))
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
            _validate_table_capacity(application, group_addresses, associations)
            data = build_association_table(associations)
            address = base + (association_table.offset or 0)
            result.append(MemorySegment(address, data, mask=b"\xff" * len(data)))
    return result


def _group_communication_relative_segments(
    application: Application,
    ui: DynamicUI,
    gc: GroupCommunication,
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
    descriptors = (
        dict(gc.group_object_descriptors)
        if gc.group_object_descriptors is not None
        else _resolved_group_object_descriptors(ui, linked_numbers)
    )
    highest_number = max((c.number for c in indexer.com_objects.values()), default=0)

    address_data = build_group_address_table_b(group_addresses)
    association_data = build_association_table_b(
        associations,
        wide=association_table_wide(len(group_addresses), highest_number),
    )
    group_object_data = build_group_object_table_b(descriptors, highest_number)

    return [
        RelativeSegment(_ADDRESS_TABLE_TYPE, address_data, b"\xff" * len(address_data)),
        RelativeSegment(
            _ASSOCIATION_TABLE_TYPE,
            association_data,
            b"\xff" * len(association_data),
            associations=tuple(associations),
        ),
        RelativeSegment(
            _GROUP_OBJECT_TABLE_TYPE,
            group_object_data,
            b"\xff" * len(group_object_data),
        ),
    ]


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


def _resolved_group_object_descriptors(
    ui: DynamicUI, linked_numbers: set[int]
) -> dict[int, tuple[int, int]]:
    """Map each active object's number to its ``(flag byte, size code)``.

    Walks the configured UI so flags and size come from the resolved com object
    reference. Unlinked active objects retain all flags except communication.
    """
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    descriptors: dict[int, tuple[int, int]] = {}
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            try:
                size = size_code(node.object_size)
            except ImageError:
                continue
            flags = com_object_flag_byte(
                priority=node.priority or "Low",
                communication=node.communication and node.number in linked_numbers,
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
) -> tuple[bytes, bytes]:
    """Overlay each com object's flags onto the table seed.

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
                communication=node.communication and node.number in linked_numbers,
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

    data, mask = build_com_object_table(seed, descriptors)
    visible = {descriptor.number for descriptor in descriptors}
    data, mask = bytearray(data), bytearray(mask)
    for number in object_numbers:
        if number in visible:
            continue
        offset = _COM_OBJECT_HEADER + number * _COM_OBJECT_RECORD
        if offset >= len(data):
            continue
        flags = seed[offset] & ~_COMMUNICATION_BIT
        if number in linked_numbers:
            flags |= _COMMUNICATION_BIT
        data[offset] = flags
        mask[offset] = 0xFF
    return bytes(data), bytes(mask)
