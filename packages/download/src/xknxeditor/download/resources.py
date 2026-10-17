"""Per-mask resource location map from the KNX master data.

The KNX project schema (namespace ``http://knx.org/xml/project``) describes, per
device mask version, where every loadable resource lives: element
``MaskVersion`` -> ``HawkConfigurationData`` -> ``Resources`` -> ``Resource``,
each with a ``Location`` (an address space plus either a memory ``StartAddress``
or an interface object ``InterfaceObjectRef`` + ``PropertyID``). This maps, per
mask, the Load State Machine control (``ApplicationLoadControl`` etc.), the table
base pointers (``GroupAddressTablePtr`` etc.), the Run State Machine control
(``ApplicationRunControl``) and more.

Resolving these from the master data - rather than assuming fixed property ids or
addresses - lets the download follow whatever a given mask defines. Callers that
do not supply master data fall back to the conventional defaults used by the
memory-mapped and System B device models handled elsewhere in this package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_location_t import ResourceLocation
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

if TYPE_CHECKING:
    from collections.abc import Mapping

    from xknxeditor.prod import MasterData

# Bundled per-mask resource table (see mask_resources.json / packages/download/tools/gen_mask_resources.py).
_BUNDLED_TABLE = "mask_resources.json"

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


@dataclass(frozen=True, slots=True)
class MaskResources:
    """The resolved resource locations of a single device mask version."""

    _by_name: Mapping[ResourceName, ResourceLocation]
    # Octet length per resource, where the master data gives it.
    _lengths: Mapping[ResourceName, int] = MappingProxyType({})

    def location(self, name: ResourceName) -> ResourceLocation | None:
        """The raw location of a named resource, or ``None`` if the mask lacks it."""
        return self._by_name.get(name)

    def length(self, name: ResourceName) -> int | None:
        """The octet length of a named resource, if known."""
        return self._lengths.get(name)

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
        """Return ``(control_address, lsm1_status_address)`` for BIM M112 masks.

        Those masks share one 11-octet load-control address across LSMs. Status
        bytes are looked up per LSM name, not by arithmetic past the last
        documented LoadStatus resource. Property-mapped masks return ``None``.
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


def mask_resources(
    master: MasterData | None, mask_version_id: str
) -> MaskResources | None:
    """Resolve the resource map for ``mask_version_id``.

    Prefers the loaded master data (the device's own product data); falls back to
    the bundled per-mask table so any known mask can be downloaded even when the
    loaded product data does not describe it. Returns ``None`` when neither has it.
    """
    resolved = _from_master(master, mask_version_id)
    return resolved if resolved is not None else bundled_mask_resources(mask_version_id)


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
        for configuration in mask_version.hawk_configuration_data:
            if configuration.resources is None:
                continue
            for resource in configuration.resources.resource:
                if resource.location is not None:
                    by_name.setdefault(resource.name, resource.location)
                    lengths.setdefault(resource.name, resource.resource_type.length)
        if by_name:
            return MaskResources(_by_name=by_name, _lengths=lengths)
    return None


@cache
def _bundled_table() -> Mapping[str, Mapping[str, list[object]]]:
    """Load the bundled per-mask resource table (cached)."""
    text = (files("xknxeditor.download") / _BUNDLED_TABLE).read_text("utf-8")
    return json.loads(text)


def bundled_mask_resources(mask_version_id: str) -> MaskResources | None:
    """Build a resource map for ``mask_version_id`` from the bundled table."""
    entry = _bundled_table().get(mask_version_id)
    if not entry:
        return None
    by_name: dict[ResourceName, ResourceLocation] = {}
    for name, values in entry.items():
        space = values[0]
        if not isinstance(space, str):
            continue
        try:
            resource_name = ResourceName(name)
            address_space = ResourceAddrSpace(space)
        except ValueError:
            continue
        by_name[resource_name] = ResourceLocation(
            address_space=address_space,
            interface_object_ref=_as_int(values[1]),
            property_id=_as_int(values[2]),
            start_address=_as_int(values[3]),
        )
    return MaskResources(_by_name=by_name) if by_name else None


def _as_int(value: object) -> int | None:
    """Coerce a bundled-table field to ``int``; ``None`` for anything else."""
    return value if isinstance(value, int) else None
