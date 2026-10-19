"""Per-mask resource map from the KNX master data.

A device mask does not describe its download with a manufacturer id or a nibble
of the mask version. ``MaskVersion`` / ``HawkConfigurationData`` publishes two
catalogues, and the download follows those.

``Features`` are integers on the mask. The one that changes the transport is
``PropertyMappedLsms``. ``1`` means each Load State Machine is
``PID_LOAD_STATE_CONTROL`` (object, property 5, 10 octets). ``0`` means the
mask's memory load-control record. MV-0705 and MV-5705 publish both a
``LoadControl_M112`` address at ``0x0104`` and ``PropertyMappedLsms`` = 1; the
feature wins, and the procedure writes the property. MV-0700 and MV-0701 leave
the feature at 0 and write the 11-octet record. ``memory_mapped_lsm`` still
reports that memory address for the property-mapped M112 masks, because the
product procedure is the absolute-segment one and a partial download has to
run it in full. The transport decision is ``property_mapped_lsms``, which the
procedure checks before it touches the memory record.

``Resources`` name a loadable object (the address table, the association
table, the group-object table, a load control, the individual address). Each
resource has a ``Location`` and a ``ResourceType`` ``Flavour``.

The location's address space says where the bytes sit:

- ``StandardMemory`` / ``UserMemory``: a fixed address (``StartAddress``).
- ``Pointer``: the product XML address is the maximum span. ETS may place the
  used table inside that span. The flavour says whether it slides.
- ``RelativeMemory``: System B, addressed from an interface object.
- ``SystemProperty`` / ``Property``: an interface object and a property id.
- ``Constant``: a fixed value, not a write.
- ``None``: the resource has a flavour and no address. MV-0705's group-object
  table is this: the table lives in the application segment, and the flavour
  says which record to write there.

The flavour is which realisation of that resource this mask uses. The set is
closed; a new device uses one of these, or the master data has grown a new
one and the generator has to learn it.

Address table:

- ``AddressTable_Bcu1`` in standard memory is a one-octet head followed by the
  individual address. On BCU1 the head is the pointer at ``0x0116``. On BIM
  M112 (MV-0700, MV-0701, MV-0705, MV-2705, MV-5705) the head is the count at
  ``0x4000``. Either way the two octets after the head are the device's own
  address and stay out of an application segment write that covers them.
- ``AddressTable_Bcu1PL`` is the same layout for the powerline masks
  (MV-1011..MV-1013), head at ``0x0116``.
- ``AddressTable_Bcu1`` as a ``Pointer`` (BCU2, MV-0020 / MV-0021 / MV-0025)
  has no fixed head. The individual address is the separate
  ``IndividualAddress`` resource at ``0x0117``, length 2, and those two octets
  are the ones a segment write leaves alone.
- ``AddressTable_SystemB`` is relative memory (MV-07B0 and the other System B
  masks). Nothing in the low EEPROM or at ``0x4001`` is reserved for it.

Association table:

- ``AssociationTable_Bcu1`` is the one-octet association list (a count, then
  one ``[address index, object number]`` entry per link), placed at the
  ``GroupAssociationTablePtr`` inside the shared BCU1 EEPROM segment. A second
  link to the same object is another entry. An unlinked object is left out.
  The powerline masks use this same flavour.
- ``AssociationTable_Bcu2`` is the BCU2 table reached through its pointer. It
  is not the BCU1 slot layout and not the M112 list.
- ``AssociationTable_M112`` is the list of ``[address index, object number]``
  pairs. The product data declares it at its maximum size. When
  ``PropertyMappedLsms`` is 1 and that segment starts where the address-table
  segment ends (MV-0705: ``0x4000`` for 511 octets, then ``0x41FF``), ETS
  writes only the used address table and places the association table
  immediately after it. The association segment still ends at the declared
  end. MV-0701 has the same flavour and the same contiguous declaration, with
  ``PropertyMappedLsms`` 0, so the association table stays at the declared
  address. A gap between the two declared segments stays at the declared
  address too.
- ``AssociationTable_SystemB`` is the relative System B table.

Group-object table:

- ``GroupObjectTable_Bcu10``, ``GroupObjectTable_Bcu11`` and
  ``GroupObjectTable_Bcu1PL`` are the 3-octet records inside the shared EEPROM
  segment (a count, then flag in the third octet of each record). Laying a
  4-octet System B record out from the start of that segment overwrites the
  application id and the table pointers.
- ``GroupObjectTable_Bcu2`` is the BCU2 table. MV-002x uses it. The 3-octet
  overlay does not apply.
- ``GroupObjectTable_M112`` is the 4-octet record: 5-octet header, flag at
  ``5 + number * 4``. The communication bit ``0x04`` is cleared for an unused
  static object and for a hole in the table count. That rule is the encoder,
  shared by every mask with this record.
- ``GroupObjectTable_SystemB`` and ``GroupObjectTable_System300`` are the
  relative System B records.

Load control:

- ``LoadControl_Bcu2`` is the property event, including on System B
  (MV-07B0's ``ApplicationLoadControl`` is object 4, property 5) and on
  MV-2705, which is property-mapped M112 tables with this load-control flavour
  rather than the memory record.
- ``LoadControl_M112`` is the 11-octet memory record. Octet 0 is
  ``(lsm << 4) | opcode``. An additional record inserts a reserved 0 after the
  segment type. It is written only when ``PropertyMappedLsms`` is 0. When the
  feature is 1 the events are property writes to the LSM index, and an index
  with no LoadStatus (PEI on MV-0705) is not sent.

A mask can carry two ``HawkConfigurationData`` blocks. The one with
``LegacyVersion`` unset is the mask. The other is an older view of the same
version: MV-07B0's second block is BCU1/M112 tables at ``0x4000`` with
``PropertyMappedLsms`` 0, and MV-091A's first block is the legacy one. The
generator keeps the current block. Inside that block BCU2 lists a pointer
twice, ``MgmtStyle`` ``simple`` (the memory address) and ``lsm`` (the
property). ``PropertyMappedLsms`` selects the ``lsm`` occurrence.

``LegacyPatchManufacturerIdInTaskSegment`` is an application option. It is not
a feature and not a flavour. A memory-mapped mask is not a reason to rewrite
the task-segment manufacturer.

The fixed line-coupler octets at ``0x010D``-``0x011C`` are still chosen by the
091x mask family. No resource flavour describes that map.

When a download mismatches ETS, read this mask's feature and flavours before
editing. A flavour that is already implemented is a regression test. A flavour
the bundled table lacks is a generator change
(``packages/download/tools/gen_mask_resources.py`` against ``knx_master.xml``).
A manufacturer test or a new set of mask ids is the branch this table replaces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from xknxeditor.namespaces.intermediate.hawk_configuration_data_t import (
    HawkConfigurationData,
)
from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_features_feature_name import (
    HawkConfigurationDataFeaturesFeatureName as MaskFeature,
)
from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_resources_resource import (
    HawkConfigurationDataResourcesResource,
)
from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_resources_resource_resource_type_flavour import (
    HawkConfigurationDataResourcesResourceResourceTypeFlavour as ResourceFlavour,
)
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_location_t import ResourceLocation
from xknxeditor.namespaces.intermediate.resource_mgmt_style_t import ResourceMgmtStyle
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from xknxeditor.prod import MasterData

# Bundled per-mask resource table (see mask_resources.json / packages/download/tools/gen_mask_resources.py).
# A resource row is [addressSpace, interfaceObjectRef, propertyId, startAddress, flavour, length].
_BUNDLED_TABLE = "mask_resources.json"
_ROW_SPACE = 0
_ROW_OBJECT = 1
_ROW_PROPERTY = 2
_ROW_START = 3
_ROW_FLAVOUR = 4
_ROW_LENGTH = 5

# BIM M112 loadable-part index to the named LoadStatus resource. Missing names
# mean that LSM has no status byte to poll.
_LSM_STATUS_RESOURCES: MappingProxyType[int, ResourceName] = MappingProxyType(
    {
        1: ResourceName.GROUP_ADDRESS_TABLE_LOAD_STATUS,
        2: ResourceName.GROUP_ASSOCIATION_TABLE_LOAD_STATUS,
        3: ResourceName.APPLICATION_LOAD_STATUS,
        4: ResourceName.GROUP_OBJECT_TABLE_LOAD_STATUS,
        5: ResourceName.PEIPROG_LOAD_STATUS,
    }
)

# Address spaces that place a resource in interface object relative memory (the
# System B group communication tables and parameter segment).
_RELATIVE_SPACES = frozenset(
    {
        ResourceAddrSpace.RELATIVE_MEMORY,
        ResourceAddrSpace.RELATIVE_MEMORY_BY_OBJECT_TYPE,
    }
)

# 3-octet group-object records in the shared BCU EEPROM segment.
_BCU1_GROUP_OBJECT_FLAVOURS = frozenset(
    {
        ResourceFlavour.GROUP_OBJECT_TABLE_BCU10,
        ResourceFlavour.GROUP_OBJECT_TABLE_BCU11,
        ResourceFlavour.GROUP_OBJECT_TABLE_BCU1_PL,
    }
)

# Standard-memory address tables whose first octet is a head and whose next
# two octets are the individual address.
_ADDRESS_TABLE_WITH_INDIVIDUAL = frozenset(
    {
        ResourceFlavour.ADDRESS_TABLE_BCU1,
        ResourceFlavour.ADDRESS_TABLE_BCU1_PL,
    }
)


@dataclass(frozen=True, slots=True)
class MaskResources:
    """The resolved resources of a single device mask version."""

    _by_name: Mapping[ResourceName, ResourceLocation]
    # Octet length per resource, where the master data gives it.
    _lengths: Mapping[ResourceName, int] = MappingProxyType({})
    _flavours: Mapping[ResourceName, ResourceFlavour] = MappingProxyType({})
    _features: Mapping[MaskFeature, int] = MappingProxyType({})

    def location(self, name: ResourceName) -> ResourceLocation | None:
        """The raw location of a named resource, or ``None`` if the mask lacks it."""
        return self._by_name.get(name)

    def length(self, name: ResourceName) -> int | None:
        """The octet length of a named resource, if known."""
        return self._lengths.get(name)

    def flavour(self, name: ResourceName) -> ResourceFlavour | None:
        """The realisation of a named resource, or ``None`` if the mask lacks it."""
        return self._flavours.get(name)

    def feature(self, name: MaskFeature) -> int | None:
        """A mask feature value, or ``None`` when the mask does not publish it."""
        return self._features.get(name)

    def property_ref(self, name: ResourceName) -> tuple[int, int] | None:
        """Return ``(interface object ref, property id)`` for a property resource."""
        location = self._by_name.get(name)
        if (
            location is None
            or location.address_space != ResourceAddrSpace.SYSTEM_PROPERTY
            or location.interface_object_ref is None
            or location.property_id is None
        ):
            return None
        return location.interface_object_ref, location.property_id

    def memory_address(self, name: ResourceName) -> int | None:
        """Return the start address for a memory-located resource, else ``None``."""
        location = self._by_name.get(name)
        if location is None or location.address_space not in (
            ResourceAddrSpace.STANDARD_MEMORY,
            ResourceAddrSpace.USER_MEMORY,
        ):
            return None
        return location.start_address

    def is_relative(self, name: ResourceName) -> bool:
        """Whether a resource lives in interface object relative memory (System B)."""
        location = self._by_name.get(name)
        return location is not None and location.address_space in _RELATIVE_SPACES

    def memory_mapped_lsm(self) -> tuple[int, int] | None:
        """Return ``(control_address, lsm1_status_address)`` when the mask publishes one.

        BIM M112 shares one 11-octet load-control address across LSMs. Status
        bytes are looked up per LSM name, not by arithmetic past the last
        documented LoadStatus resource. The address is reported for MV-0705 as
        well: that mask is property-mapped, and ``property_mapped_lsms`` is what
        stops the procedure from writing the record. Callers that need "this
        product procedure uses absolute segments" keep using this tuple.
        """
        control = self.memory_address(ResourceName.APPLICATION_LOAD_CONTROL)
        if control is None:
            control = self.memory_address(ResourceName.GROUP_ADDRESS_TABLE_LOAD_CONTROL)
        status = self.memory_address(ResourceName.GROUP_ADDRESS_TABLE_LOAD_STATUS)
        if control is None or status is None:
            return None
        return control, status

    def memory_mapped_lsm_status(self, index: int) -> int | None:
        """Return the memory LoadStatus address for ``index``, if the mask has one.

        BIM M112 documents LSM 1-3 as address table, association table and
        application. LSM 4/5 exist only when the mask publishes group-object or
        PEI program status. Inventing consecutive addresses past that (e.g.
        ``0xB6EE`` for LSM 5 on MV-0701) makes the device stop answering.
        """
        name = _LSM_STATUS_RESOURCES.get(index)
        if name is None:
            return None
        return self.memory_address(name)

    def omits_load_event(self, index: int) -> bool:
        """Whether a property-mapped ``LoadControl_M112`` mask has no status for ``index``.

        MV-0705's procedure still lists an LSM 5 task segment and load after
        restart. The mask publishes no PEI LoadStatus, and ETS does not send
        those events. The LSM index is used as the object index, which on this
        mask is the first application object; it rejects property 5.
        """
        if self.feature(MaskFeature.PROPERTY_MAPPED_LSMS) != 1:
            return False
        flavour = self.flavour(ResourceName.APPLICATION_LOAD_CONTROL)
        if flavour is None:
            flavour = self.flavour(ResourceName.GROUP_ADDRESS_TABLE_LOAD_CONTROL)
        if flavour is not ResourceFlavour.LOAD_CONTROL_M112:
            return False
        name = _LSM_STATUS_RESOURCES.get(index)
        if name is None:
            return False
        return self.location(name) is None

    def reserved_addresses(self) -> frozenset[int]:
        """Octets a segment write leaves untouched because the device owns them.

        ``IndividualAddress`` in standard memory (BCU1, BCU2, powerline: 2
        octets at ``0x0117``). And, for ``AddressTable_Bcu1`` /
        ``AddressTable_Bcu1PL`` in standard memory, the two octets after the
        one-octet head: the same individual address on BCU1, the address inside
        the M112 table at ``0x4000``.
        """
        addresses: set[int] = set()
        individual = self.memory_address(ResourceName.INDIVIDUAL_ADDRESS)
        if individual is not None:
            length = self.length(ResourceName.INDIVIDUAL_ADDRESS) or 2
            addresses.update(range(individual, individual + length))
        start = self.memory_address(ResourceName.GROUP_ADDRESS_TABLE)
        if (
            start is not None
            and self.flavour(ResourceName.GROUP_ADDRESS_TABLE)
            in _ADDRESS_TABLE_WITH_INDIVIDUAL
        ):
            addresses.add(start + 1)
            addresses.add(start + 2)
        return frozenset(addresses)


def mask_resources(
    master: MasterData | None, mask_version_id: str
) -> MaskResources | None:
    """Resolve the resource map for ``mask_version_id``.

    Prefers the loaded master data (the device's own product data); falls back to
    the bundled per-mask table so any known mask can be downloaded even when the
    loaded product data does not describe it. Returns ``None`` when neither has it.
    Flavour dispatch uses the bundled table, which is the current hawk
    configuration of each mask in ``knx_master.xml``.
    """
    resolved = _from_master(master, mask_version_id)
    return resolved if resolved is not None else bundled_mask_resources(mask_version_id)


def _current_configurations(
    configurations: Sequence[HawkConfigurationData],
) -> Sequence[HawkConfigurationData]:
    """Drop ``LegacyVersion`` blocks when the mask also has a current one."""
    current = [cfg for cfg in configurations if cfg.legacy_version is None]
    return current or configurations


def _property_mapped(configuration: HawkConfigurationData) -> bool:
    if configuration.features is None:
        return False
    return any(
        feature.name is MaskFeature.PROPERTY_MAPPED_LSMS and feature.value == 1
        for feature in configuration.features.feature
    )


def _prefer_lsm(
    kept: HawkConfigurationDataResourcesResource,
    candidate: HawkConfigurationDataResourcesResource,
    property_mapped: bool,
) -> bool:
    """Replace a ``simple`` pointer with the ``lsm`` property on a property-mapped mask."""
    if not property_mapped:
        return False
    return (
        ResourceMgmtStyle.LSM in candidate.mgmt_style
        and ResourceMgmtStyle.LSM not in kept.mgmt_style
    )


def _from_master(
    master: MasterData | None, mask_version_id: str
) -> MaskResources | None:
    """Build the resource map for ``mask_version_id`` from loaded master data."""
    if master is None:
        return None
    raw = master.raw
    if raw is None or raw.mask_versions is None:
        return None
    for mask_version in raw.mask_versions.mask_version:
        if mask_version.id != mask_version_id:
            continue
        by_name: dict[ResourceName, ResourceLocation] = {}
        lengths: dict[ResourceName, int] = {}
        flavours: dict[ResourceName, ResourceFlavour] = {}
        features: dict[MaskFeature, int] = {}
        chosen: dict[ResourceName, HawkConfigurationDataResourcesResource] = {}
        for configuration in _current_configurations(
            mask_version.hawk_configuration_data
        ):
            property_mapped = _property_mapped(configuration)
            if configuration.features is not None:
                for feature in configuration.features.feature:
                    features.setdefault(feature.name, feature.value)
            if configuration.resources is None:
                continue
            picked: dict[ResourceName, HawkConfigurationDataResourcesResource] = {}
            for resource in configuration.resources.resource:
                previous = picked.get(resource.name)
                if previous is None or _prefer_lsm(previous, resource, property_mapped):
                    picked[resource.name] = resource
            for name, resource in picked.items():
                chosen.setdefault(name, resource)
        for name, resource in chosen.items():
            location = resource.location
            if location is not None:
                by_name.setdefault(name, location)
            lengths.setdefault(name, resource.resource_type.length)
            flavour = resource.resource_type.flavour
            if flavour is not None:
                flavours.setdefault(name, flavour)
        if by_name:
            return MaskResources(
                _by_name=by_name,
                _lengths=lengths,
                _flavours=flavours,
                _features=features,
            )
    return None


@cache
def _bundled_table() -> Mapping[str, object]:
    """Load the bundled per-mask resource table (cached)."""
    text = (files("xknxeditor.download") / _BUNDLED_TABLE).read_text("utf-8")
    table = _object_dict(json.loads(text))
    return table if table is not None else {}


def property_mapped_lsms(mask_version: str) -> bool:
    """Whether ``mask_version`` drives load state machines through a property.

    Reads ``PropertyMappedLsms`` from the bundled table. False for MV-0700 and
    MV-0701, and for any mask the table does not list.
    """
    resources = bundled_mask_resources(mask_version)
    return (
        resources is not None
        and resources.feature(MaskFeature.PROPERTY_MAPPED_LSMS) == 1
    )


def reserved_memory(mask_version: str) -> frozenset[int]:
    """Addresses ``mask_version`` keeps out of application segment data."""
    resources = bundled_mask_resources(mask_version)
    if resources is None:
        return frozenset()
    return resources.reserved_addresses()


def bcu1_group_object_table(mask_version: str) -> bool:
    """Whether the group-object table is the 3-octet BCU1 record."""
    resources = bundled_mask_resources(mask_version)
    if resources is None:
        return False
    return (
        resources.flavour(ResourceName.GROUP_OBJECT_TABLE)
        in _BCU1_GROUP_OBJECT_FLAVOURS
    )


def m112_group_object_table(mask_version: str) -> bool:
    """Whether the group-object table is the 4-octet M112 record.

    Only this flavour has the per-slot communication-bit rule: ETS clears the
    bit on static objects without a ComObjectRef and on holes in the table
    count. ``GroupObjectTable_Bcu2`` (MV-0020/0021/0025) is a different record.
    """
    resources = bundled_mask_resources(mask_version)
    if resources is None:
        return False
    return (
        resources.flavour(ResourceName.GROUP_OBJECT_TABLE)
        is ResourceFlavour.GROUP_OBJECT_TABLE_M112
    )


def bcu1_association_table(mask_version: str) -> bool:
    """Whether the association table is the BCU1 list at its pointer."""
    resources = bundled_mask_resources(mask_version)
    if resources is None:
        return False
    return (
        resources.flavour(ResourceName.GROUP_ASSOCIATION_TABLE)
        is ResourceFlavour.ASSOCIATION_TABLE_BCU1
    )


def m112_association_table(mask_version: str) -> bool:
    """Whether the association table is the property-mapped M112 list.

    Only this flavour declares the address and association tables at their
    maximum size, contiguous, so only it is a candidate for ETS packing the
    association table against the used address table (see
    :func:`xknxeditor.download.image.packed_group_tables`).
    ``AssociationTable_Bcu2`` (MV-0020/0021/0025) is also property-mapped but
    is a different table reached through its own pointer; it is not packed.
    """
    resources = bundled_mask_resources(mask_version)
    if resources is None:
        return False
    return (
        resources.flavour(ResourceName.GROUP_ASSOCIATION_TABLE)
        is ResourceFlavour.ASSOCIATION_TABLE_M112
    )


def bundled_mask_resources(mask_version_id: str) -> MaskResources | None:
    """Build a resource map for ``mask_version_id`` from the bundled table."""
    entry = _object_dict(_bundled_table().get(mask_version_id))
    if entry is None:
        return None
    features = _features(entry.get("features"))
    resources = _object_dict(entry.get("resources"))
    if resources is None:
        return None
    by_name: dict[ResourceName, ResourceLocation] = {}
    lengths: dict[ResourceName, int] = {}
    flavours: dict[ResourceName, ResourceFlavour] = {}
    for name, values in resources.items():
        row = _object_list(values)
        if row is None:
            continue
        try:
            resource_name = ResourceName(name)
        except ValueError:
            continue
        space = _at(row, _ROW_SPACE)
        if isinstance(space, str):
            try:
                address_space = ResourceAddrSpace(space)
            except ValueError:
                address_space = None
            if address_space is not None:
                by_name[resource_name] = ResourceLocation(
                    address_space=address_space,
                    interface_object_ref=_as_int(_at(row, _ROW_OBJECT)),
                    property_id=_as_int(_at(row, _ROW_PROPERTY)),
                    start_address=_as_int(_at(row, _ROW_START)),
                )
        flavour = _flavour(_at(row, _ROW_FLAVOUR))
        if flavour is not None:
            flavours[resource_name] = flavour
        length = _at(row, _ROW_LENGTH)
        if isinstance(length, int):
            lengths[resource_name] = length
    if not by_name and not flavours and not features:
        return None
    return MaskResources(
        _by_name=by_name,
        _lengths=lengths,
        _flavours=flavours,
        _features=features,
    )


def _object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast(list[object], value)


def _at(row: list[object], index: int) -> object:
    return row[index] if index < len(row) else None


def _flavour(value: object) -> ResourceFlavour | None:
    if not isinstance(value, str):
        return None
    try:
        return ResourceFlavour(value)
    except ValueError:
        return None


def _features(value: object) -> dict[MaskFeature, int]:
    raw = _object_dict(value)
    if raw is None:
        return {}
    features: dict[MaskFeature, int] = {}
    for name, number in raw.items():
        if not isinstance(number, int):
            continue
        try:
            features[MaskFeature(name)] = number
        except ValueError:
            continue
    return features


def _as_int(value: object) -> int | None:
    """Coerce a bundled-table field to ``int``; ``None`` for anything else."""
    return value if isinstance(value, int) else None
