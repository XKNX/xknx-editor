"""Tests for the files.vXX -> intermediate converter."""

from __future__ import annotations

import pytest

from xknxeditor.namespaces.adapters.convert import (
    Context,
    ConversionError,
    PuidAllocator,
    convert,
)
from xknxeditor.namespaces.files.v10.device_instance_t_additional_addresses import (
    DeviceInstanceAdditionalAddresses as V10AdditionalAddresses,
)
from xknxeditor.namespaces.files.v10.group_address_t import (
    GroupAddress as V10GroupAddress,
)
from xknxeditor.namespaces.files.v11.master_data_t import MasterData as V11MasterData
from xknxeditor.namespaces.files.v20.device_instance_t_binary_data_binary_data import (
    DeviceInstanceBinaryDataBinaryData as V20BinaryData,
)
from xknxeditor.namespaces.files.v20.group_address_t import (
    GroupAddress as V20GroupAddress,
)
from xknxeditor.namespaces.intermediate.device_instance_t_additional_addresses import (
    DeviceInstanceAdditionalAddresses as IRAdditionalAddresses,
)
from xknxeditor.namespaces.intermediate.device_instance_t_binary_data_binary_data import (
    DeviceInstanceBinaryDataBinaryData as IRBinaryData,
)
from xknxeditor.namespaces.intermediate.group_address_t import (
    GroupAddress as IRGroupAddress,
)
from xknxeditor.namespaces.intermediate.master_data_t import MasterData as IRMasterData


def v10() -> Context:
    return Context(version="v10")


def test_master_data_id_synthesized_for_pre_v12() -> None:
    """project/10 and /11 master data carry no Id; the unified model requires one."""
    src = V11MasterData(version=723, signature=b"sig")
    out = convert(Context(version="v11"), src, IRMasterData)
    assert out.id == "MD-1"
    assert out.version == 723  # real fields still copied


def test_function_point_ids_synthesized_for_v14() -> None:
    """Up to v14 a FunctionPoint has no Id; newer master data names it FP-<type number>_<role>."""
    from xknxeditor.namespaces.files.v14.function_type_t import (
        FunctionType as V14FunctionType,
    )
    from xknxeditor.namespaces.files.v14.function_type_t_function_point import (
        FunctionTypeFunctionPoint as V14FunctionPoint,
    )
    from xknxeditor.namespaces.intermediate.function_type_t import (
        FunctionType as IRFunctionType,
    )

    src = V14FunctionType(
        id="FT-1",
        number=1,
        text="switchable light",
        function_point=[
            V14FunctionPoint(text="switching", role="DR-1", datapoint_type="DPST-1-1"),
            V14FunctionPoint(text="status", role="DR-2", datapoint_type="DPST-1-1"),
        ],
    )
    out = convert(Context(version="v14"), src, IRFunctionType)
    assert [(p.id, p.text) for p in out.function_point] == [
        ("FP-1_DR-1", "switching"),
        ("FP-1_DR-2", "status"),
    ]


def test_compare_prop_with_a_range_instead_of_inline_data() -> None:
    from xknxeditor.namespaces.files.v20.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp as V20CompareProp,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp as IRCompareProp,
    )

    src = V20CompareProp(obj_idx=0, prop_id=201, range="[2216203124736,]")
    out = convert(Context(version="v20"), src, IRCompareProp)
    assert out.range == "[2216203124736,]"
    assert out.inline_data is None


# --- PuidAllocator --------------------------------------------------------


def test_puid_allocator_is_sequential():
    alloc = PuidAllocator()
    assert [alloc.allocate() for _ in range(3)] == [1, 2, 3]


# --- generic field copy ---------------------------------------------------


def test_copies_same_named_scalar_fields():
    src = V10GroupAddress(id="GA-1", address=42, name="Living", central=True)
    out = convert(v10(), src, IRGroupAddress)
    assert out.id == "GA-1"
    assert out.address == 42
    assert out.name == "Living"
    assert out.central is True


def test_recurses_into_lists_of_dataclasses():
    src = V10AdditionalAddresses(address=[5, 7])
    out = convert(v10(), src, IRAdditionalAddresses)
    assert [a.address for a in out.address] == [5, 7]
    assert all(isinstance(a, type(out.address[0])) for a in out.address)


# --- universal PUID synthesis --------------------------------------------


def test_synthesizes_puid_when_source_lacks_it():
    ctx = v10()
    a = convert(ctx, V10GroupAddress(id="GA-1", address=1, name="a"), IRGroupAddress)
    b = convert(ctx, V10GroupAddress(id="GA-2", address=2, name="b"), IRGroupAddress)
    assert (a.puid, b.puid) == (1, 2)  # sequential, fabricated


def test_preserves_existing_puid():
    src = V20GroupAddress(id="GA-1", address=1, name="a", puid=99)
    out = convert(Context(version="v20"), src, IRGroupAddress)
    assert out.puid == 99  # real PUID kept, not synthesized


# --- GroupAddress.DatapointType: list -> single ---------------------------


def test_datapoint_type_single_entry():
    src = V10GroupAddress(id="GA-1", address=1, name="a", datapoint_type=["DPST-1-1"])
    out = convert(v10(), src, IRGroupAddress)
    assert out.datapoint_type == "DPST-1-1"


def test_datapoint_type_empty_becomes_none():
    src = V10GroupAddress(id="GA-1", address=1, name="a", datapoint_type=[])
    out = convert(v10(), src, IRGroupAddress)
    assert out.datapoint_type is None


def test_datapoint_type_multiple_rejected():
    src = V10GroupAddress(
        id="GA-1", address=1, name="a", datapoint_type=["DPST-1-1", "DPST-5-1"]
    )
    with pytest.raises(ConversionError, match="DatapointType"):
        convert(v10(), src, IRGroupAddress)


# --- AdditionalAddresses: text ints -> Address objects --------------------


def test_additional_addresses_wraps_ints():
    out = convert(
        v10(), V10AdditionalAddresses(address=[1, 255]), IRAdditionalAddresses
    )
    assert [a.address for a in out.address] == [1, 255]


def test_additional_addresses_empty():
    out = convert(v10(), V10AdditionalAddresses(address=[]), IRAdditionalAddresses)
    assert out.address == []


# --- BinaryData: AutoCopy -> DoNotCopy (inverted) -------------------------


def test_auto_copy_inverts_to_do_not_copy():
    out = convert(Context(version="v20"), V20BinaryData(auto_copy=False), IRBinaryData)
    assert out.do_not_copy is True  # not False

    out = convert(Context(version="v20"), V20BinaryData(auto_copy=True), IRBinaryData)
    assert out.do_not_copy is False  # not True


# --- AddInData/AddInId casing: v10 capital-'In' -> v12+ lowercase (via aliases) -----


def test_addin_id_casing_aliased():
    from xknxeditor.namespaces.files.v10.addin_data_t import AddinData as V10AddinData
    from xknxeditor.namespaces.intermediate.addin_data_t import AddinData as IRAddinData

    src = V10AddinData(add_in_id="{0815-GUID}", name="vendor.addin")
    out = convert(v10(), src, IRAddinData)
    assert (
        out.addin_id == "{0815-GUID}"
    )  # add_in_id -> addin_id, GUID string copied as-is
    assert out.name == "vendor.addin"


def test_project_addin_data_casing_aliased():
    from xknxeditor.namespaces.files.v10.addin_data_t import AddinData as V10AddinData
    from xknxeditor.namespaces.files.v10.project_t_add_in_data import (
        ProjectAddInData as V10ProjectAddInData,
    )
    from xknxeditor.namespaces.intermediate.project_t_addin_data import (
        ProjectAddinData as IRProjectAddinData,
    )

    src = V10ProjectAddInData(add_in_data=[V10AddinData(add_in_id="g1", name="a")])
    out = convert(v10(), src, IRProjectAddinData)
    assert [a.addin_id for a in out.addin_data] == ["g1"]  # nested rename + recursion


# --- MulticastTTL relocation: v10/v11 Topology>Line -> v14+ Installation -----------


def test_multicast_ttl_relocated_from_line():
    """v11 stored MulticastTTL on the line; the unified model has it on the installation."""
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _installation

    src = NS(multicast_ttl=None, topology=NS(area=[NS(line=[NS(multicast_ttl=8)])]))
    assert _installation(v10(), src) == {"multicast_ttl": 8}


def test_multicast_ttl_kept_when_on_installation():
    """v14+ already have it at installation level — leave it for the generic copy."""
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _installation

    src = NS(multicast_ttl=4, topology=NS(area=[]))
    assert _installation(Context(version="v20"), src) == {}


# --- Baggage: legacy fields retained as a superset in the IR -----------------------


def _ir_baggage():
    from xknxeditor.namespaces.intermediate.manufacturer_data_t_manufacturer_baggages_baggage import (
        ManufacturerDataManufacturerBaggagesBaggage,
    )

    return ManufacturerDataManufacturerBaggagesBaggage


def test_baggage_v11_legacy_fields_copied():
    from xknxeditor.namespaces.files.v11.manufacturer_data_t_manufacturer_baggages_baggage import (
        ManufacturerDataManufacturerBaggagesBaggage as V11Baggage,
    )
    from xknxeditor.namespaces.files.v11.manufacturer_data_t_manufacturer_baggages_baggage_file_info import (
        ManufacturerDataManufacturerBaggagesBaggageFileInfo as V11FileInfo,
    )

    src = V11Baggage(
        file_info=V11FileInfo(),
        target_path="x/y",
        name="b",
        id="B-1",
        install_on_import=False,
        data=b"payload",
        group_addresses16_bit_enabled=False,
    )
    out = convert(Context(version="v11"), src, _ir_baggage())
    assert out.install_on_import is False  # real value copied
    assert out.data == b"payload"  # inline Data preserved
    assert out.group_addresses16_bit_enabled is False


def test_baggage_v23_defaults_for_missing_legacy_fields():
    from xknxeditor.namespaces.files.v23.manufacturer_data_t_manufacturer_baggages_baggage import (
        ManufacturerDataManufacturerBaggagesBaggage as V23Baggage,
    )
    from xknxeditor.namespaces.files.v23.manufacturer_data_t_manufacturer_baggages_baggage_file_info import (
        ManufacturerDataManufacturerBaggagesBaggageFileInfo as V23FileInfo,
    )

    src = V23Baggage(
        file_info=V23FileInfo(),
        target_path="x/y",
        name="b",
        id="B-1",
        file_integrity="DEADBEEF",
    )
    out = convert(Context(version="v23"), src, _ir_baggage())
    assert out.install_on_import is True  # IR default for versions lacking it
    assert out.group_addresses16_bit_enabled is True
    assert out.file_integrity == "DEADBEEF"
    assert out.data is None


# --- Line -> Segment upgrade: flat pre-v21 line wraps into one Segment --------------


def test_flat_line_wraps_into_segment():
    from xknxeditor.namespaces.files.v11.topology_t_area_line import (
        TopologyAreaLine as V11Line,
    )
    from xknxeditor.namespaces.intermediate.topology_t_area_line import (
        TopologyAreaLine as IRLine,
    )

    src = V11Line(
        id="L-1", name="Main", address=1, medium_type_ref_id="MT-1", domain_address=42
    )
    out = convert(Context(version="v11"), src, IRLine)

    # line-level attrs stay on the line
    assert out.id == "L-1" and out.name == "Main" and out.address == 1
    # medium attrs moved into a single synthesized segment
    assert len(out.segment) == 1
    seg = out.segment[0]
    assert seg.medium_type_ref_id == "MT-1"
    assert seg.domain_address == 42
    assert seg.id == "L-1-S1"  # derived, distinct from the line's Id
    assert seg.number == 1
    # the IR line no longer has a direct device home (clean v23 shape)
    assert not hasattr(out, "device_instance")


def test_segmented_line_passes_through():
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _line

    src = NS(segment=["already-here"])
    assert _line(Context(version="v23"), src) == {}  # v21+ untouched


# --- Installation BusAccess push-down: ≤v14 installation-level default -> per-Segment ---


def _v14_bus_access(name: str):
    from xknxeditor.namespaces.files.v14.bus_access_t import BusAccess as V14BusAccess

    return V14BusAccess(
        name=name, edi="{00000000-0000-0000-0000-000000000000}", parameter="p"
    )


def test_installation_busaccess_pushed_to_segments_lacking_one():
    """≤v14 had an installation-wide BusAccess default; the IR keeps it only per-Segment. The
    installation value is copied onto every Segment that doesn't define its own."""
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _installation

    src = NS(
        multicast_ttl=4,  # at installation level -> no TTL lift
        bus_access=_v14_bus_access("default-access"),
        topology=NS(
            area=[
                NS(
                    id="A-1",
                    address=1,
                    line=[
                        NS(id="L-1", address=1, medium_type_ref_id="MT-1"),
                        NS(id="L-2", address=2, medium_type_ref_id="MT-1"),
                    ],
                )
            ]
        ),
    )
    out = _installation(Context(version="v14"), src)
    segs = [
        s for area in out["topology"].area for line in area.line for s in line.segment
    ]
    assert len(segs) == 2
    assert all(s.bus_access is not None for s in segs)
    assert all(s.bus_access.name == "default-access" for s in segs)


def test_line_level_busaccess_wins_over_installation_default():
    """A Segment that already carries its line's own BusAccess is not overwritten by the
    installation-level default."""
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _installation

    src = NS(
        multicast_ttl=4,
        bus_access=_v14_bus_access("default-access"),
        topology=NS(
            area=[
                NS(
                    id="A-1",
                    address=1,
                    line=[
                        NS(
                            id="L-1",
                            address=1,
                            medium_type_ref_id="MT-1",
                            bus_access=_v14_bus_access("line-specific"),
                        ),  # _line copies this onto its segment
                        NS(
                            id="L-2", address=2, medium_type_ref_id="MT-1"
                        ),  # no own -> gets installation default
                    ],
                )
            ]
        ),
    )
    out = _installation(Context(version="v14"), src)
    by_line = {line.id: line for area in out["topology"].area for line in area.line}
    assert by_line["L-1"].segment[0].bus_access.name == "line-specific"
    assert by_line["L-2"].segment[0].bus_access.name == "default-access"


def test_installation_no_busaccess_leaves_topology_to_generic():
    """v20+ have no installation-level BusAccess: the override must not own/rebuild topology."""
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _installation

    src = NS(multicast_ttl=4, bus_access=None, topology=NS(area=[]))
    assert "topology" not in _installation(Context(version="v20"), src)


# --- v13->v14: HorizontalRuler bool -> UIHint enum ---------------------------------


def test_horizontal_ruler_maps_to_uihint():
    from xknxeditor.namespaces.files.v13.parameter_separator_t import (
        ParameterSeparator as V13Sep,
    )
    from xknxeditor.namespaces.intermediate.parameter_separator_t import (
        ParameterSeparator as IRSep,
    )
    from xknxeditor.namespaces.intermediate.parameter_separator_t_uihint import (
        ParameterSeparatorUihint,
    )

    out = convert(
        Context(version="v13"), V13Sep(id="S-1", text="t", horizontal_ruler=True), IRSep
    )
    assert out.uihint is ParameterSeparatorUihint.HORIZONTAL_RULER

    out = convert(
        Context(version="v13"),
        V13Sep(id="S-2", text="t", horizontal_ruler=False),
        IRSep,
    )
    assert out.uihint is None


# --- v13->v14: Buildings/BuildingPart -> Locations/Space (aliases) -----------------


def test_building_part_aliases_to_space():
    from xknxeditor.namespaces.files.v13.building_part_t import (
        BuildingPart as V13BuildingPart,
    )
    from xknxeditor.namespaces.files.v13.locations_t import Locations as V13Locations
    from xknxeditor.namespaces.intermediate.locations_t import Locations as IRLocations

    src = V13Locations(
        building_part=[V13BuildingPart(id="B-1", name="House", type_value="x", puid=7)]
    )
    out = convert(Context(version="v13"), src, IRLocations)
    assert [s.id for s in out.space] == ["B-1"]  # BuildingPart -> Space
    assert out.space[0].name == "House"


# --- v13->v14: loaded credential -> hash (fake, must raise) ------------------------


def test_loaded_credential_hash_raises():
    from xknxeditor.namespaces.adapters.convert import fake_hash
    from xknxeditor.namespaces.files.v13.security_t import Security as V13Security
    from xknxeditor.namespaces.intermediate.security_t import Security as IRSecurity

    src = V13Security(loaded_device_authentication_code="s3cr3t")
    with pytest.raises(NotImplementedError, match="hashing algorithm unknown"):
        convert(Context(version="v13"), src, IRSecurity)

    # but a security record without loaded plaintext converts fine
    out = convert(Context(version="v13"), V13Security(), IRSecurity)
    assert out.loaded_device_authentication_code_hash is None

    with pytest.raises(NotImplementedError):
        fake_hash("anything")


# --- v14->v20: Connectors -> Links/Acknowledges -----------------------------------


def test_connectors_map_to_links_and_acknowledges():
    from xknxeditor.namespaces.files.v14.com_object_instance_ref_t import (
        ComObjectInstanceRef as V14Ref,
    )
    from xknxeditor.namespaces.files.v14.com_object_instance_ref_t_connectors import (
        ComObjectInstanceRefConnectors as Conn,
    )
    from xknxeditor.namespaces.files.v14.com_object_instance_ref_t_connectors_receive import (
        ComObjectInstanceRefConnectorsReceive as Receive,
    )
    from xknxeditor.namespaces.files.v14.com_object_instance_ref_t_connectors_send import (
        ComObjectInstanceRefConnectorsSend as Send,
    )
    from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
        ComObjectInstanceRef as IRRef,
    )

    src = V14Ref(
        ref_id="O-1",
        connectors=Conn(
            send=Send(group_address_ref_id="GA-1", acknowledge=True),
            receive=[
                Receive(group_address_ref_id="GA-2"),
                Receive(group_address_ref_id="GA-3", acknowledge=True),
            ],
        ),
    )
    out = convert(Context(version="v14"), src, IRRef)
    assert out.links == ["GA-1", "GA-2", "GA-3"]  # all linked refs
    assert out.acknowledges == ["GA-1", "GA-3"]  # only the acknowledged ones


# --- v14->v20: IsCommunicationObjectVisibilityCalculated -> IsActivityCalculated ---


def test_is_activity_calculated_aliased():
    from xknxeditor.namespaces.files.v14.device_instance_t import (
        DeviceInstance as V14Device,
    )
    from xknxeditor.namespaces.intermediate.device_instance_t import (
        DeviceInstance as IRDevice,
    )

    src = V14Device(
        id="D-1",
        product_ref_id="P-1",
        puid=1,
        is_communication_object_visibility_calculated=True,
    )
    out = convert(Context(version="v14"), src, IRDevice)
    assert out.is_activity_calculated is True


# --- v14->v20: RuntimeUnidirectional -> RFRx/RFTx split ---------------------------


def _hw_src(**hw_attrs):
    """A SimpleNamespace Hardware_t with two Hardware2Program children (no own RF caps)."""
    from types import SimpleNamespace as NS

    hw_attrs.setdefault("rftx_capabilities", None)
    hw_attrs.setdefault("rfrx_capabilities", None)
    hw_attrs.setdefault("runtime_unidirectional", None)
    return NS(
        hardware2_programs=NS(hardware2_program=[NS(id="HP-1"), NS(id="HP-2")]),
        **hw_attrs,
    )


def _h2p_caps(out):
    progs = out["hardware2_programs"].hardware2_program
    return [(p.rftx_capabilities, p.rfrx_capabilities) for p in progs]


def test_unidirectional_pushes_tx_only_onto_hardware2programs():
    """≤v14 RuntimeUnidirectional=True → Tx-only, pushed onto every Hardware2Program."""
    from xknxeditor.namespaces.adapters.convert import _hardware
    from xknxeditor.namespaces.intermediate.rftx_capabilities_t import RftxCapabilities

    out = _hardware(Context(version="v14"), _hw_src(runtime_unidirectional=True))
    assert _h2p_caps(out) == [(RftxCapabilities.READY, None)] * 2


def test_bidirectional_pushes_rx_and_tx_onto_hardware2programs():
    """≤v14 RuntimeUnidirectional=False → Tx+Rx, pushed onto every Hardware2Program."""
    from xknxeditor.namespaces.adapters.convert import _hardware
    from xknxeditor.namespaces.intermediate.rfrx_capabilities_t import RfrxCapabilities
    from xknxeditor.namespaces.intermediate.rftx_capabilities_t import RftxCapabilities

    out = _hardware(Context(version="v14"), _hw_src(runtime_unidirectional=False))
    assert _h2p_caps(out) == [(RftxCapabilities.READY, RfrxCapabilities.READY)] * 2


def test_v20_hardware_rf_caps_moved_onto_hardware2programs():
    """v20 carried the pair on Hardware_t; relocate it onto each Hardware2Program."""
    from xknxeditor.namespaces.adapters.convert import _hardware
    from xknxeditor.namespaces.files.v20.rfrx_capabilities_t import (
        RfrxCapabilities as V20Rfrx,
    )
    from xknxeditor.namespaces.files.v20.rftx_capabilities_t import (
        RftxCapabilities as V20Rftx,
    )

    src = _hw_src(rftx_capabilities=V20Rftx.READY, rfrx_capabilities=V20Rfrx.READY)
    out = _hardware(Context(version="v20"), src)
    assert _h2p_caps(out) == [(V20Rftx.READY, V20Rfrx.READY)] * 2


def test_v21_hardware_rf_caps_left_to_generic():
    """v21+ already carry the pair per-Hardware2Program — the override stays out of the way."""
    from types import SimpleNamespace as NS

    from xknxeditor.namespaces.adapters.convert import _hardware

    src = NS(
        rftx_capabilities=None,
        rfrx_capabilities=None,
        runtime_unidirectional=None,
        hardware2_programs=NS(hardware2_program=[NS(id="HP-1")]),
    )
    assert _hardware(Context(version="v21"), src) == {}


# --- structured logging via an injected structlog logger --------------------------


def test_injected_logger_captures_per_segment_events():
    """A caller-injected structlog logger receives one event per pushed segment, each carrying
    the auto-bound source version."""
    from types import SimpleNamespace as NS

    import structlog
    from structlog.testing import capture_logs

    from xknxeditor.namespaces.adapters.convert import _installation

    src = NS(
        multicast_ttl=4,
        bus_access=_v14_bus_access("default-access"),
        topology=NS(
            area=[
                NS(
                    id="A-1",
                    address=1,
                    line=[
                        NS(id="L-1", address=1, medium_type_ref_id="MT-1"),
                        NS(id="L-2", address=2, medium_type_ref_id="MT-1"),
                    ],
                )
            ]
        ),
    )
    with capture_logs() as logs:
        _installation(Context(version="v14", logger=structlog.get_logger()), src)

    pushed = [
        e["segment"]
        for e in logs
        if e["event"] == "convert.installation.busaccess_pushed_down"
    ]
    assert pushed == ["L-1-S1", "L-2-S1"]  # one event per segment
    assert all(e["version"] == "v14" for e in logs)  # version auto-bound everywhere


def test_default_logger_is_noop():
    """Without an injected logger the converter logs nothing and pulls in no logging lib."""
    from xknxeditor.namespaces.adapters.convert import _NullLogger

    ctx = Context(version="v10")
    assert isinstance(ctx.logger, _NullLogger)
    assert ctx.logger.info("ignored", x=1) is None  # no-op
