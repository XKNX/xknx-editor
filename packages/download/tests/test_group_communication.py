"""System B plan materialization and the MV-07B0 regression, without device I/O."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram.apci import PropertyValueRead, PropertyValueWrite
from xsdata.formats.dataclass.parsers import XmlParser

from xknxeditor.download.errors import ImageError, UnsupportedProcedureError
from xknxeditor.download.group_communication import (
    materialize_group_communication_controls,
)
from xknxeditor.download.image import DownloadImage, MemorySegment, RelativeSegment
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope, mask_object_types
from xknxeditor.namespaces.intermediate.hawk_configuration_data_t import (
    HawkConfigurationData,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.master_data_t import MasterData
from xknxeditor.prod import Application

from .conftest import FakeDevice

MAPPING = {0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: 4}


def mask_fixture() -> tuple[Application, MasterData]:
    configuration = XmlParser().from_path(
        Path(__file__).parent / "fixtures/systemb_master.xml", HawkConfigurationData
    )
    mask = SimpleNamespace(id="MV-07B0", hawk_configuration_data=[configuration])
    master = cast(
        "MasterData",
        SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask])),
    )
    application = cast(
        "Application",
        SimpleNamespace(
            manufacturer_id="M-00FA",
            load_procedure_style=LoadProcedureStyle.DEFAULT_PROCEDURE,
            load_procedures=None,
            program=SimpleNamespace(
                mask_version="MV-07B0",
                application_number=0x0200,
                application_version=0x10,
            ),
        ),
    )
    return application, master


def _image(*relative: RelativeSegment) -> DownloadImage:
    # Parameter bytes intentionally overlap the table offsets. They must never be selected.
    return DownloadImage(
        segments=(MemorySegment(0, b"PARAMETERS" * 8),),
        properties=(),
        relative_segments=relative,
    )


def _lifecycle() -> list[object]:
    return [
        LdCtrlUnload(lsm_idx=1),
        LdCtrlLoad(lsm_idx=1),
        LdCtrlRelSegment(lsm_idx=1, size=2, mode=0, fill=0),
        LdCtrlWriteRelMem(obj_idx=1, offset=0, size=1048576, verify=True),
        LdCtrlLoadCompleted(lsm_idx=1),
        LdCtrlRestart(),
    ]


def test_resize_preserves_order_wire_addressing_and_templates() -> None:
    controls = _lifecycle()
    original = deepcopy(controls)
    result = materialize_group_communication_controls(
        _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
    )
    assert [type(c) for c in result] == [type(c) for c in controls]
    assert controls == original
    assert result[2] == LdCtrlRelSegment(lsm_idx=1, size=4, mode=0, fill=0)
    assert result[3] == LdCtrlWriteRelMem(obj_idx=1, offset=0, size=4, verify=True)
    assert result[-1] is controls[-1]


def test_ap1_capture_order_and_allocations() -> None:
    application, master = mask_fixture()
    image = _image(
        *(RelativeSegment(t, bytes(n)) for t, n in ((1, 88), (2, 90), (9, 402)))
    )
    controls = materialize_group_communication_controls(
        image,
        resolve_download_controls(application, master, scope=DownloadScope.APPLICATION),
        MAPPING,
        DownloadScope.APPLICATION,
    )
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlUnload)] == [
        5,
        4,
        3,
        2,
        1,
    ]
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlLoad)] == [4, 3, 1, 2]
    from xknxeditor.download.load_state import data_relative_allocation

    assert [
        data_relative_allocation(c.size, mode=c.mode, fill=c.fill).hex()
        for c in controls
        if isinstance(c, LdCtrlRelSegment)
    ] == [
        "030b0000019200000000",
        "030b0000005800000000",
        "030b0000005a00000000",
    ]


@pytest.mark.parametrize("missing", [0, 1, 2, 3, 4])
def test_incomplete_or_misordered_lifecycle_rejected(missing: int) -> None:
    controls = _lifecycle()
    if missing == 0:
        controls[1], controls[2] = controls[2], controls[1]  # allocation before Load
    else:
        del controls[missing]
    with pytest.raises(UnsupportedProcedureError, match=r"lifecycle|completion"):
        materialize_group_communication_controls(
            _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
        )


def test_missing_controls_are_not_synthesized() -> None:
    # A procedure that does not allocate or write a table is run as it is.
    assert materialize_group_communication_controls(
        _image(RelativeSegment(1, b"ADDR")), [LdCtrlRestart()], MAPPING
    ) == [LdCtrlRestart()]


def test_missing_table_cannot_fall_back_to_parameter_memory() -> None:
    with pytest.raises(ImageError, match="missing table image"):
        materialize_group_communication_controls(_image(), _lifecycle(), MAPPING)


def test_scoped_out_tables_need_no_image() -> None:
    assert materialize_group_communication_controls(
        _image(),
        [replace(c, applies_to=LdCtrlProcType.GRP) for c in _lifecycle()[:-1]]
        + [LdCtrlRestart()],
        MAPPING,
        DownloadScope.PARAMETERS,
    ) == [LdCtrlRestart()]


@pytest.mark.parametrize(
    "scope",
    [DownloadScope.FULL, DownloadScope.APPLICATION, DownloadScope.GROUP_COMMUNICATION],
)
async def test_system_b_writes_table_images_with_correct_allocations(
    scope: DownloadScope,
) -> None:
    application, master = mask_fixture()
    mapping = mask_object_types(master, "MV-07B0")
    assert mapping == MAPPING
    controls = resolve_download_controls(application, master, scope=scope)
    image = _image(
        RelativeSegment(1, b"ADDR"),
        RelativeSegment(2, b"ASSOC!"),
        RelativeSegment(9, b"GROUPS!!"),
    )
    device = FakeDevice(object_types=mapping, descriptor=0x07B0)
    device.absent_objects = {5}
    device.table_references = {1: 0x1000, 2: 0x2000, 3: 0x3000}
    runner = LoadProcedureRunner(
        application,
        image,
        DeviceProgrammer(device),
        controls=controls,
        scope=scope,
        object_types=mapping,
    )
    await runner.run()
    for index, object_type in ((1, 1), (2, 2), (3, 9)):
        data = image.relative_segment(object_type).data
        assert (
            bytes(
                device.memory[device.table_references[index] + i]
                for i in range(len(data))
            )
            == data
        )
        assert device.load_states[index] is LoadState.LOADED
        events = [
            p.data
            for p in device.sent
            if isinstance(p, PropertyValueWrite)
            and p.property_id == 5
            and p.object_index == index
        ]
        assert [e[0] for e in events] == (
            [] if scope is DownloadScope.GROUP_COMMUNICATION and index == 3 else [4]
        ) + [
            1,
            3,
            2,
        ]  # Unload, Load, allocation, Complete
        assert int.from_bytes(next(e for e in events if e[0] == 3)[2:6], "big") == len(
            data
        )
    if scope is DownloadScope.GROUP_COMMUNICATION:
        assert not any(getattr(p, "object_index", 0) in (4, 5) for p in device.sent)
    # Indexed controls must not introduce object-type discovery reads.
    assert not any(
        isinstance(p, PropertyValueRead) and p.property_id == 1 for p in device.sent
    )
    assert device.restarted


async def test_invalid_plan_fails_before_bus_access() -> None:
    application, _ = mask_fixture()
    device = FakeDevice()
    runner = LoadProcedureRunner(
        application,
        _image(),
        DeviceProgrammer(device),
        controls=_lifecycle(),
        object_types=MAPPING,
    )
    with pytest.raises(ImageError, match="missing table image"):
        await runner.run()
    assert device.sent == []
    assert not runner.state_mutated


def test_a_restart_while_a_table_is_unloaded_is_kept() -> None:
    # A second program's fragment (M-0077 ACA-AC-KNX) restarts the device after the unloads,
    # before the tables load.
    controls = _lifecycle()
    controls.insert(1, LdCtrlRestart())
    prepared = materialize_group_communication_controls(
        _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
    )
    assert [type(c) for c in prepared] == [type(c) for c in controls]
    # In the middle of a table's load it is refused.
    controls = _lifecycle()
    controls.insert(3, LdCtrlRestart())
    with pytest.raises(UnsupportedProcedureError, match="restart before"):
        materialize_group_communication_controls(
            _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
        )


def test_the_first_allocation_of_a_table_gets_its_size() -> None:
    controls = _lifecycle()
    controls.insert(2, LdCtrlRelSegment(lsm_idx=1, size=2, mode=1, fill=0xFF))
    prepared = materialize_group_communication_controls(
        _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
    )
    allocations = [c for c in prepared if isinstance(c, LdCtrlRelSegment)]
    # A later allocation keeps the size the procedure gives it.
    assert [(c.mode, c.fill, c.size) for c in allocations] == [(1, 0xFF, 4), (0, 0, 2)]
    assert len(prepared) == len(controls)


def test_an_allocation_merged_before_the_load_is_kept() -> None:
    # A product fragment merged right after the connection allocates the table before the
    # procedure unloads and loads it; it is sent as it is and gets the table's size.
    controls = [LdCtrlRelSegment(lsm_idx=1, size=65536, mode=0, fill=0), *_lifecycle()]
    prepared = materialize_group_communication_controls(
        _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
    )
    allocations = [c for c in prepared if isinstance(c, LdCtrlRelSegment)]
    assert [c.size for c in allocations] == [4, 2]
    assert isinstance(prepared[0], LdCtrlRelSegment)


def test_a_table_the_procedure_does_not_allocate_needs_no_data() -> None:
    # The second table is only unloaded: the procedure loads it by other means, if at all.
    controls = [LdCtrlUnload(lsm_idx=2), *_lifecycle()]
    prepared = materialize_group_communication_controls(
        _image(RelativeSegment(1, b"ADDR")), controls, MAPPING
    )
    assert prepared[0] == LdCtrlUnload(lsm_idx=2)
    with pytest.raises(ImageError, match="missing table image for object type 2"):
        materialize_group_communication_controls(
            _image(RelativeSegment(1, b"ADDR")),
            [*controls, LdCtrlRelSegment(lsm_idx=2, size=2, mode=0, fill=0)],
            MAPPING,
        )


async def test_preflight_reads_all_three_table_images_without_mutation() -> None:
    application, master = mask_fixture()
    device = FakeDevice(object_types=MAPPING, descriptor=0x07B0)
    device.table_references = {1: 0x1000, 2: 0x2000, 3: 0x3000}
    image = _image(
        RelativeSegment(1, b"ADDR"),
        RelativeSegment(2, b"ASSOC!"),
        RelativeSegment(9, b"GROUPS!!"),
    )
    controls = resolve_download_controls(
        application, master, scope=DownloadScope.GROUP_COMMUNICATION
    )
    runner = LoadProcedureRunner(
        application,
        image,
        DeviceProgrammer(device),
        controls=controls,
        scope=DownloadScope.GROUP_COMMUNICATION,
        object_types=MAPPING,
    )
    report = await runner.preflight()
    assert len(report.segments) == 3
    assert [segment.address for segment in report.segments] == [0x3000, 0x2000, 0x1000]
    assert [segment.planned for segment in report.segments] == [
        b"GROUPS!!",
        b"ASSOC!",
        b"ADDR",
    ]
    assert not runner.state_mutated
    assert not device.memory
    assert not device.load_states
    assert not device.restarted


def test_entrypoint_prepares_grp_procedure_with_the_mask_mapping() -> None:
    from xknxeditor.download.download import _resolve_controls

    application, master = mask_fixture()
    image = _image(
        RelativeSegment(1, b"ADDR"),
        RelativeSegment(2, b"ASSOC!"),
        RelativeSegment(9, b"GROUPS!!"),
    )
    controls = _resolve_controls(
        application,
        SimpleNamespace(raw=master),
        image,
        DownloadScope.GROUP_COMMUNICATION,
    )
    assert [c.lsm_idx for c in controls if isinstance(c, LdCtrlUnload)] == [2, 1]
    assert [
        (c.lsm_idx, c.size) for c in controls if isinstance(c, LdCtrlRelSegment)
    ] == [(3, 8), (1, 4), (2, 6)]
    assert isinstance(controls[-1], LdCtrlRestart)


async def test_runner_logs_resolved_table_target(caplog) -> None:
    import logging

    application, _ = mask_fixture()
    device = FakeDevice(descriptor=0x07B0)
    device.table_references[1] = 0x1000
    runner = LoadProcedureRunner(
        application,
        _image(RelativeSegment(1, b"ADDR")),
        DeviceProgrammer(device),
        controls=_lifecycle(),
        object_types=MAPPING,
    )
    with caplog.at_level(logging.DEBUG, logger="xknxeditor.download.programmer"):
        await runner.run()
    assert "control=(2, 6) kind=LdCtrlLoad target-index=1 target-type=1" in caplog.text
    assert "event=01000000000000000000 expected=LOADING" in caplog.text
