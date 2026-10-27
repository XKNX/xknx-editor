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


def test_property_mapped_lsms_distinguishes_m112_masks() -> None:
    from xknxeditor.download.resources import property_mapped_lsms

    assert property_mapped_lsms("MV-0705")
    assert property_mapped_lsms("MV-5705")
    assert not property_mapped_lsms("MV-0701")
    assert not property_mapped_lsms("MV-0700")
    assert not property_mapped_lsms("MV-FFFF-unknown")


def test_flavours_select_the_table_and_load_control_realisation() -> None:
    from xknxeditor.download.resources import (
        bcu1_association_table,
        bcu1_group_object_table,
        bundled_mask_resources,
        reserved_memory,
    )
    from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_features_feature_name import (
        HawkConfigurationDataFeaturesFeatureName as MaskFeature,
    )
    from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_resources_resource_resource_type_flavour import (
        HawkConfigurationDataResourcesResourceResourceTypeFlavour as Flavour,
    )

    m0705 = bundled_mask_resources("MV-0705")
    assert m0705 is not None
    assert m0705.feature(MaskFeature.PROPERTY_MAPPED_LSMS) == 1
    assert m0705.flavour(ResourceName.GROUP_ADDRESS_TABLE) is Flavour.ADDRESS_TABLE_BCU1
    assert (
        m0705.flavour(ResourceName.GROUP_ASSOCIATION_TABLE)
        is Flavour.ASSOCIATION_TABLE_M112
    )
    assert (
        m0705.flavour(ResourceName.GROUP_OBJECT_TABLE)
        is Flavour.GROUP_OBJECT_TABLE_M112
    )
    assert (
        m0705.flavour(ResourceName.APPLICATION_LOAD_CONTROL)
        is Flavour.LOAD_CONTROL_M112
    )
    assert reserved_memory("MV-0705") == frozenset({0x4001, 0x4002})

    m0701 = bundled_mask_resources("MV-0701")
    assert m0701 is not None
    assert m0701.feature(MaskFeature.PROPERTY_MAPPED_LSMS) == 0
    assert not bcu1_group_object_table("MV-0701")
    assert not bcu1_association_table("MV-0701")

    m0011 = bundled_mask_resources("MV-0011")
    assert m0011 is not None
    assert (
        m0011.flavour(ResourceName.GROUP_OBJECT_TABLE)
        is Flavour.GROUP_OBJECT_TABLE_BCU11
    )
    assert (
        m0011.flavour(ResourceName.GROUP_ASSOCIATION_TABLE)
        is Flavour.ASSOCIATION_TABLE_BCU1
    )
    assert bcu1_group_object_table("MV-0011")
    assert bcu1_association_table("MV-0011")
    assert reserved_memory("MV-0012") == frozenset({0x0117, 0x0118})

    # BCU2 is property-mapped and keeps the load-state-machine pointer, not the
    # 3-octet BCU1 overlay. The individual address is still 0x0117.
    m0021 = bundled_mask_resources("MV-0021")
    assert m0021 is not None
    assert m0021.feature(MaskFeature.PROPERTY_MAPPED_LSMS) == 1
    assert (
        m0021.flavour(ResourceName.GROUP_OBJECT_TABLE)
        is Flavour.GROUP_OBJECT_TABLE_BCU2
    )
    assert m0021.property_ref(ResourceName.GROUP_ADDRESS_TABLE_PTR) == (1, 7)
    assert not bcu1_group_object_table("MV-0021")
    assert not bcu1_association_table("MV-0021")
    assert reserved_memory("MV-0021") == frozenset({0x0117, 0x0118})

    # Powerline uses the BCU1 record flavours. System B does not reserve the
    # M112 individual-address octets. MV-2705 is property-mapped with BCU2 load
    # control, and its address table is still the M112 head at 0x4000.
    assert bcu1_group_object_table("MV-1011")
    assert bcu1_association_table("MV-1011")
    assert reserved_memory("MV-1011") == frozenset({0x0117, 0x0118})
    assert reserved_memory("MV-07B0") == frozenset()
    assert reserved_memory("MV-0910") == frozenset()
    m2705 = bundled_mask_resources("MV-2705")
    assert m2705 is not None
    assert m2705.feature(MaskFeature.PROPERTY_MAPPED_LSMS) == 1
    assert (
        m2705.flavour(ResourceName.APPLICATION_LOAD_CONTROL)
        is Flavour.LOAD_CONTROL_BCU2
    )
    assert reserved_memory("MV-2705") == frozenset({0x4001, 0x4002})

    # The current coupler block is the property. The legacy block's memory octet
    # at 0x60 is not the programming mode of MV-0912.
    m0912 = bundled_mask_resources("MV-0912")
    assert m0912 is not None
    assert m0912.property_ref(ResourceName.PROGRAMMING_MODE) == (0, 54)
    system_b = bundled_mask_resources("MV-07B0")
    assert system_b is not None
    assert (
        system_b.flavour(ResourceName.GROUP_ADDRESS_TABLE)
        is Flavour.ADDRESS_TABLE_SYSTEM_B
    )


def test_mask_resources_falls_back_to_bundled_without_master() -> None:
    resolved = mask_resources(None, "MV-07B0")
    assert resolved is not None
    assert resolved.property_ref(ResourceName.APPLICATION_RUN_CONTROL) == (4, 6)


def test_mask_number_ignores_the_management_descriptor() -> None:
    from xknxeditor.download.resources import mask_number

    assert mask_number("MV-0705") == 0x0705
    assert mask_number("MV-07B0") == 0x07B0
    assert mask_number("MV-0300-01000001000000000000") == 0x0300
    assert [mask_number(text) for text in ("", "MV-", "MV-07", "MV-XY12")] == [
        None,
        None,
        None,
        None,
    ]
