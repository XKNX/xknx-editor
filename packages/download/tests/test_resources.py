"""Tests for the per-mask resource location resolver."""

from __future__ import annotations

from xknxeditor.download.resources import MaskResources, mask_resources
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_location_t import ResourceLocation
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName


def _prop(obj: int, pid: int) -> ResourceLocation:
    return ResourceLocation(
        address_space=ResourceAddrSpace.SYSTEM_PROPERTY,
        interface_object_ref=obj,
        property_id=pid,
    )


def test_property_ref_resolves_object_and_pid() -> None:
    resources = MaskResources({ResourceName.APPLICATION_LOAD_CONTROL: _prop(4, 5)})
    assert resources.property_ref(ResourceName.APPLICATION_LOAD_CONTROL) == (4, 5)
    assert resources.memory_address(ResourceName.APPLICATION_LOAD_CONTROL) is None


def test_memory_address_resolves_start_address() -> None:
    resources = MaskResources(
        {
            ResourceName.GROUP_OBJECT_TABLE_PTR: ResourceLocation(
                address_space=ResourceAddrSpace.STANDARD_MEMORY, start_address=0x112
            )
        }
    )
    assert resources.memory_address(ResourceName.GROUP_OBJECT_TABLE_PTR) == 0x112
    assert not resources.is_relative(ResourceName.GROUP_OBJECT_TABLE_PTR)


def test_is_relative_for_object_relative_memory() -> None:
    resources = MaskResources(
        {
            ResourceName.GROUP_ADDRESS_TABLE_PTR: ResourceLocation(
                address_space=ResourceAddrSpace.RELATIVE_MEMORY_BY_OBJECT_TYPE
            )
        }
    )
    assert resources.is_relative(ResourceName.GROUP_ADDRESS_TABLE_PTR)
    assert resources.memory_address(ResourceName.GROUP_ADDRESS_TABLE_PTR) is None


def test_missing_resource_returns_none() -> None:
    resources = MaskResources({})
    assert resources.location(ResourceName.APPLICATION_RUN_CONTROL) is None
    assert resources.property_ref(ResourceName.APPLICATION_RUN_CONTROL) is None
    assert resources.memory_address(ResourceName.APPLICATION_RUN_CONTROL) is None
    assert not resources.is_relative(ResourceName.APPLICATION_RUN_CONTROL)


def test_mask_resources_unknown_mask_is_none() -> None:
    assert mask_resources(None, "MV-FFFF-unknown") is None


def test_bundled_table_covers_known_masks() -> None:
    from xknxeditor.download.resources import bundled_mask_resources

    system_b = bundled_mask_resources("MV-07B0")
    assert system_b is not None
    assert system_b.property_ref(ResourceName.APPLICATION_LOAD_CONTROL) == (4, 5)

    memory_mapped = bundled_mask_resources("MV-0705")
    assert memory_mapped is not None
    assert memory_mapped.memory_address(ResourceName.APPLICATION_LOAD_CONTROL) == 260
    assert memory_mapped.memory_mapped_lsm() == (260, 46826)

    m112 = bundled_mask_resources("MV-0701")
    assert m112 is not None
    assert m112.memory_mapped_lsm() == (260, 46826)
    assert m112.memory_mapped_lsm_status(1) == 46826
    assert m112.memory_mapped_lsm_status(2) == 46827
    assert m112.memory_mapped_lsm_status(3) == 46828
    assert m112.memory_mapped_lsm_status(5) is None
    assert system_b.memory_mapped_lsm() is None

    assert bundled_mask_resources("MV-9999-unknown") is None


def test_mask_resources_falls_back_to_bundled_without_master() -> None:
    resolved = mask_resources(None, "MV-07B0")
    assert resolved is not None
    assert resolved.property_ref(ResourceName.APPLICATION_RUN_CONTROL) == (4, 6)
