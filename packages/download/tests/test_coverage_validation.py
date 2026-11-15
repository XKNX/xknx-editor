"""Partial controls and declared limits on a synthetic System B app."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from xknxeditor.download.errors import ImageError
from xknxeditor.download.image import GroupCommunication, build_image
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.download.scope import DownloadScope, control_in_scope
from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_group_communication import MAPPING
from .test_wire_audit import CaptureDevice, capture_application, capture_master

APP_ID = "M-00FA_A-1000-10-0001"


@pytest.fixture
def application() -> Application:
    return parse_application_xml(
        (Path(__file__).parent / "fixtures/systemb_application.xml").read_bytes(),
        "M-00FA",
    )[0]


@pytest.mark.parametrize(
    ("scope", "unload", "load", "complete"),
    [
        (DownloadScope.PARAMETERS, [], [4], [4]),
        (DownloadScope.GROUP_COMMUNICATION, [2, 1], [3, 1, 2], [3, 2, 1]),
        (
            DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
            [2, 1],
            [4, 3, 1, 2],
            [4, 3, 2, 1],
        ),
    ],
)
def test_real_partial_lifecycles(application, scope, unload, load, complete):
    master = capture_master()
    before = deepcopy(master)
    controls = resolve_download_controls(application, master, scope=scope)
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlUnload)] == unload
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlLoad)] == load
    assert [
        c.lsm_idx for c in controls if isinstance(c, LdCtrlLoadCompleted)
    ] == complete
    assert [
        c.obj_idx
        for c in controls
        if isinstance(c, LdCtrlLoadImageProp) and c.prop_id == 27
    ] == [1, 2, 3, 4]
    assert [c.obj_idx for c in controls if isinstance(c, LdCtrlCompareProp)] == (
        [] if scope is DownloadScope.GROUP_COMMUNICATION else [4]
    )
    assert not any(
        getattr(c, "obj_idx", None) == 5 or getattr(c, "lsm_idx", None) == 5
        for c in controls
    )
    assert all(control_in_scope(c, scope, MAPPING) for c in controls)
    assert master == before


@pytest.mark.parametrize(
    "scope",
    [
        DownloadScope.PARAMETERS,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    ],
)
async def test_real_partial_procedures_complete_offline(application, scope):
    _, tables = capture_application()
    image = replace(
        build_image(application), relative_segments=tables.relative_segments
    )
    controls = resolve_download_controls(application, capture_master(), scope=scope)
    device = CaptureDevice()
    await LoadProcedureRunner(
        application,
        image,
        DeviceProgrammer(device),
        controls=controls,
        scope=scope,
        object_types=MAPPING,
    ).run()
    assert device.load_states == {
        1: LoadState.LOADED,
        2: LoadState.LOADED,
        3: LoadState.LOADED,
        4: LoadState.LOADED,
        5: LoadState.UNLOADED,
    }
    assert device.restarted


def test_second_application_partial_controls_retained(application):
    controls = resolve_download_controls(
        application,
        capture_master(),
        scope=DownloadScope.PARAMETERS,
        has_application_program2=True,
    )
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlLoad)] == [5, 4]
    assert [c.obj_idx for c in controls if isinstance(c, LdCtrlCompareProp)] == [5, 4]
    assert not any(isinstance(c, LdCtrlUnload) for c in controls)


def test_manufacturer_auto_fragment_is_explicit(application):
    assert application.load_procedures is not None
    application.load_procedures.load_procedure[0].choice.append(LdCtrlUnload(lsm_idx=4))
    controls = resolve_download_controls(
        application, capture_master(), scope=DownloadScope.PARAMETERS
    )
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlUnload)] == [4]
    assert (
        application.load_procedures.load_procedure[0].choice[-1].applies_to
        is LdCtrlProcType.AUTO
    )


def test_automatic_properties_have_property_specific_scope():
    for index, gated_pid in [(1, 23), (2, 23), (3, 51), (3, 52)]:
        write = LdCtrlWriteProp(obj_idx=index, prop_id=gated_pid, verify=False)
        assert not control_in_scope(write, DownloadScope.PARAMETERS, MAPPING)
        assert control_in_scope(
            replace(write, prop_id=27), DownloadScope.PARAMETERS, MAPPING
        )
        assert control_in_scope(
            replace(write, applies_to=LdCtrlProcType.ALL),
            DownloadScope.PARAMETERS,
            MAPPING,
        )
        assert control_in_scope(write, DownloadScope.GROUP_COMMUNICATION, MAPPING)
    assert control_in_scope(LdCtrlLoad(obj_type=3), DownloadScope.GROUP_COMMUNICATION)


@pytest.mark.parametrize("kind", ["address", "association"])
@pytest.mark.parametrize("count", [128, 129])
def test_declared_table_limits(application, kind, count):
    ui = application.dynamic_ui()
    assert ui is not None
    refs = sorted(ui.active_parameter_driven_com_object_ref_ids())
    links = [
        GroupObjectLink(
            refs[0] if kind == "address" else refs[i % 3],
            1 + (i if kind == "address" else i // 3),
            True,
        )
        for i in range(count)
    ]
    gc = GroupCommunication(0x1105, links)
    if count == 129:
        with pytest.raises(
            ImageError, match=f"{kind} table has 129 entries; MaxEntries=128"
        ):
            build_image(application, ui=ui, group_communication=gc)
    else:
        image = build_image(application, ui=ui, group_communication=gc)
        data = next(
            s.data
            for s in image.relative_segments
            if s.object_type == (1 if kind == "address" else 2)
        )
        assert int.from_bytes(data[:2], "big") == 128


@pytest.mark.parametrize("evaluated", [False, True])
def test_unknown_override_rejected(application, evaluated):
    ui = application.dynamic_ui()
    assert ui is not None
    if evaluated:
        ui.ui()
    with pytest.raises(ValueError, match="unknown parameter ref"):
        build_image(application, ui=ui, parameter_values={"missing": "1"})
    assert ui.get_parameter_ref("missing") is None


@pytest.mark.parametrize(
    ("suffix", "value"),
    [
        ("P-5_R-4", "9"),
        ("P-6_R-5", "-1"),
        ("P-7_R-6", "9"),
        ("UP-8_R-7", "0"),
        ("UP-8_R-7", "601"),
        ("UP-8_R-7", "65536"),
    ],
)
def test_numeric_domains_rejected_before_encoding(application, suffix, value):
    with pytest.raises(ValueError, match="outside"):
        build_image(application, parameter_values={f"{APP_ID}_{suffix}": value})


@pytest.mark.parametrize(
    ("suffix", "value"),
    [("P-5_R-4", "0"), ("P-5_R-4", "8"), ("UP-8_R-7", "1"), ("UP-8_R-7", "600")],
)
def test_numeric_domain_boundaries(application, suffix, value):
    assert build_image(application, parameter_values={f"{APP_ID}_{suffix}": value})


@pytest.mark.parametrize("suffix", ["P-5_R-4", "UP-8_R-7"])
def test_imported_numeric_values_outside_the_range_are_downloaded(application, suffix):
    # A value imported as it was stored is written as its bits, cut to the parameter's size;
    # a value set in the editor is checked when it is set (test_numeric_domains_rejected_...).
    ui = DynamicUI(
        application.program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=f"{APP_ID}_{suffix}", value="700")
        ],
    )
    assert build_image(application, ui=ui)


def test_an_imported_value_that_is_no_number_is_refused(application):
    ui = DynamicUI(
        application.program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=f"{APP_ID}_P-5_R-4", value="seven")
        ],
    )
    with pytest.raises(ImageError, match="seven"):
        build_image(application, ui=ui)


def test_numeric_restriction_string_can_be_unlisted(application):
    assert build_image(application, parameter_values={f"{APP_ID}_P-1_R-0": "4"})


def test_unknown_communication_reference_rejected(application):
    with pytest.raises(ImageError, match="unknown communication object ref"):
        build_image(
            application,
            group_communication=GroupCommunication(
                0x1105, [GroupObjectLink("missing", 1, True)]
            ),
        )


def test_inactive_links_excluded_from_both_tables(application):
    ui = application.dynamic_ui()
    assert ui is not None
    idx = ApplicationIndexer(application.program)
    inactive = next(
        iter(
            idx.com_object_refs.keys() - ui.active_parameter_driven_com_object_ref_ids()
        )
    )

    gc = GroupCommunication(0x1105, [GroupObjectLink(inactive, 1, True)])
    image = build_image(application, ui=ui, group_communication=gc)
    assert all(
        s.data == bytes(2) for s in image.relative_segments if s.object_type in (1, 2)
    )
    number = idx.com_objects[idx.com_object_refs[inactive].ref_id].number
    recovered = build_image(
        application,
        ui=ui,
        group_communication=replace(gc, group_object_descriptors={number: (0x84, 1)}),
    )
    assert all(
        s.data[:2] == b"\x00\x01"
        for s in recovered.relative_segments
        if s.object_type in (1, 2)
    )


@pytest.mark.parametrize("value", ["missing", "-1", "4294967296"])
def test_invalid_restriction_rejected_without_storage(application, value):
    with pytest.raises(ValueError, match="invalid restriction"):
        build_image(application, parameter_values={f"{APP_ID}_P-1_R-0": value})


def test_restriction_label_resolves_before_branch_selection(application):
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(f"{APP_ID}_P-1_R-0", "°F (9.027)")
    assert ui.get_parameter_ref(f"{APP_ID}_P-1_R-0") == "1"
    assert build_image(application, ui=ui)
