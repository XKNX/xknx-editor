"""Applications and devices that need another handling than the standard one."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram.apci import DeviceDescriptorRead

from xknxeditor.download import special_products
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.merge import filter_application_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.serial_numbers import is_suspicious_serial_number
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.mask_version_t_management_model import (
    MaskVersionManagementModel,
)

from .conftest import FakeDevice
from .test_procedure import _ADDRESS_TABLE_TYPE, _application, _FakeManager


def _serial(manufacturer: int, number: int) -> bytes:
    return manufacturer.to_bytes(2, "big") + number.to_bytes(4, "big")


@pytest.mark.parametrize(
    ("serial", "suspicious"),
    [
        (_serial(0x0000, 0x12345678), True),
        (_serial(0xFFFF, 0x12345678), True),
        (_serial(0x3030, 0x30303030), True),  # text
        (_serial(0x0083, 0x00000000), True),
        (_serial(0x0083, 0xFFFFFFFF), True),
        (_serial(0x0200, 0x00123456), True),  # no manufacturer, small number
        (_serial(0x0083, 0x6FFFFFFF), True),
        (_serial(0x0083, 0x70000000), False),
        (_serial(0x0083, 0xFFFFCAFE), True),
        (_serial(0x0008, 0x10910000), True),  # a number many devices share
        (_serial(0x0008, 0x10910001), False),
        (_serial(0x000A, 0x12341F11), True),
        (_serial(0x00C9, 0x09C192E9), True),
        (_serial(0x00C9, 0x09C192E8), False),
        (_serial(0x0081, 0xFF31004A), True),
        (_serial(0x0002, 0x01362FC6), True),
        (_serial(0x00FA, 0x12345678), False),
        (b"\x00\x01", False),
        (None, False),
    ],
)
def test_suspicious_serial_numbers(serial: bytes | None, suspicious: bool) -> None:
    assert is_suspicious_serial_number(serial) is suspicious


def test_application_lists_match_by_prefix() -> None:
    assert special_products.never_read_serial_number("M-004E_A-0003-10-ABCD")
    assert not special_products.never_read_serial_number("M-004E_A-0003-11-ABCD")
    assert not special_products.never_read_serial_number(None)
    assert special_products.restart_wait("M-00FD_A-7001-10-0000", 3.0) == 10.0
    assert special_products.restart_wait("M-00FD_A-7001-10-0000", 12.0) == 12.0
    assert special_products.restart_wait("M-0083_A-0001-10-0000", 3.0) == 3.0
    assert special_products.memory_service_count("M-0083_A-0065-12-0000", None) == 12
    assert special_products.memory_service_count("M-0083_A-0065-12-0000", 8) == 8
    assert special_products.memory_service_count("M-0083_A-0068-12-0000", None) is None
    assert not special_products.dynamic_group_communication_tables(
        "M-0004_A-9019-01-0000", True
    )
    assert special_products.dynamic_group_communication_tables(
        "M-0004_A-9020-01-0000", True
    )
    assert not special_products.dynamic_group_communication_tables(None, False)
    assert special_products.descriptor_read_on_reconnect(
        "{cc18bbdf-c882-4dd2-80fc-ec0c62428f85}"
    )
    assert not special_products.descriptor_read_on_reconnect(None)
    assert special_products.device_manufacturer_id(0xABB2) == 0x0002
    assert special_products.device_manufacturer_id(0xABB7) == 0x0007
    assert special_products.device_manufacturer_id(0x0083) == 0x0083


def _runner(application_id: str, *controls: object, **kwargs: Any) -> tuple[Any, Any]:
    application = _application(*controls)
    cast(Any, application).id = application_id
    manager = _FakeManager(FakeDevice(object_types={0: 0x0000, 1: _ADDRESS_TABLE_TYPE}))
    runner = LoadProcedureRunner(
        application,
        DownloadImage(segments=(), properties=()),
        connection_manager=manager,
        **kwargs,
    )
    return runner, manager


@pytest.mark.parametrize(
    ("application_id", "wait"),
    [("M-00FD_A-1103-10-0000", 10.0), ("M-0083_A-0001-10-0000", 3.0)],
)
async def test_some_devices_need_longer_after_a_restart(
    application_id: str, wait: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", sleep)
    runner, manager = _runner(
        application_id,
        LdCtrlConnect(),
        LdCtrlRestart(),
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
    )
    await runner.run()
    assert manager.opens == 2
    assert waits and waits[-1] == pytest.approx(wait, abs=0.5)


def test_some_devices_take_short_memory_services() -> None:
    runner, _ = _runner("M-0083_A-006A-11-0000", LdCtrlConnect())
    assert runner._memory_service_count == 12  # pyright: ignore[reportPrivateUsage]
    runner, _ = _runner("M-0083_A-0001-11-0000", LdCtrlConnect())
    assert runner._memory_service_count is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("plugin", "reads"), [("{A1D70116-ADBD-431B-8F8D-D2722BFB8127}", 2), (None, 1)]
)
async def test_some_plugins_identify_the_device_after_every_reconnect(
    plugin: str | None, reads: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", sleep)
    application = _application(
        LdCtrlConnect(),
        LdCtrlRestart(),
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
    )
    cast(Any, application).program.static = SimpleNamespace(
        extension=SimpleNamespace(ets_download_plugin=plugin)
    )
    device = FakeDevice(object_types={0: 0x0000, 1: _ADDRESS_TABLE_TYPE})
    manager = _FakeManager(device)
    runner = LoadProcedureRunner(
        application,
        DownloadImage(segments=(), properties=()),
        connection_manager=manager,
        expected_descriptor=device.descriptor,
        restart_cooldown=0,
    )
    await runner.run()
    sent = sum(isinstance(p, DeviceDescriptorRead) for p in device.sent)
    assert sent == reads


def _filter_app(mask_version: str, application_id: str) -> Any:
    return SimpleNamespace(
        program=SimpleNamespace(mask_version=mask_version, id=application_id)
    )


def _master(mask_version: str, model: MaskVersionManagementModel) -> Any:
    return SimpleNamespace(
        mask_versions=SimpleNamespace(
            mask_version=[SimpleNamespace(id=mask_version, management_model=model)]
        )
    )


def test_a_legacy_object_table_announcement_is_left_out() -> None:
    controls = [
        LdCtrlLoad(lsm_idx=5),
        LdCtrlTaskSegment(lsm_idx=5, address=0x4000),
        LdCtrlLoadCompleted(lsm_idx=5),
        LdCtrlLoad(lsm_idx=3),
        LdCtrlConnect(),
    ]
    # MV-0701 has no group object table load control; System B has one.
    kept = filter_application_controls(
        controls, _filter_app("MV-0701", "M-0009_A-20B1-10-30B0"), None
    )
    assert kept == controls[2:]
    assert (
        filter_application_controls(
            controls, _filter_app("MV-07B0", "M-0009_A-501C-10-CBD9"), None
        )
        == controls
    )


@pytest.mark.parametrize(
    ("application_id", "second_program", "kept"),
    [
        # The second program's load state machine is left out of the first program's procedure;
        # without a second program its Unload stays, so its slot is emptied.
        ("M-0002_A-0001-10-0000", False, ["unload4", "load3"]),
        ("M-0002_A-0001-10-0000", True, ["load3"]),
        # The listed applications keep every control.
        (
            "M-0001_A-8020-02-0000",
            True,
            ["unload4", "load4", "segment4", "done4", "load3"],
        ),
    ],
)
def test_bim_m112_leaves_out_the_second_programs_controls(
    application_id: str, second_program: bool, kept: list[str]
) -> None:
    named = {
        "unload4": LdCtrlUnload(lsm_idx=4),
        "load4": LdCtrlLoad(lsm_idx=4),
        "segment4": LdCtrlAbsSegment(
            lsm_idx=4,
            address=0x8000,
            size=16,
            seg_type=0,
            access=0,
            mem_type=3,
            seg_flags=0,
        ),
        "done4": LdCtrlLoadCompleted(lsm_idx=4),
        "load3": LdCtrlLoad(lsm_idx=3),
    }
    result = filter_application_controls(
        list(named.values()),
        _filter_app("MV-07B0", application_id),
        _master("MV-07B0", MaskVersionManagementModel.BIM_M112),
        has_application_program2=second_program,
    )
    assert result == [named[name] for name in kept]


def test_listed_applications_keep_static_group_communication_tables() -> None:
    from xknxeditor.download.image import (
        _bcu1_dynamic_tables,  # pyright: ignore[reportPrivateUsage]
    )

    def app(application_id: str) -> Any:
        return SimpleNamespace(
            program=SimpleNamespace(
                id=application_id,
                mask_version="MV-0012",
                dynamic_table_management=True,
            )
        )

    assert _bcu1_dynamic_tables(app("M-0083_A-0001-10-0000"))
    assert not _bcu1_dynamic_tables(app("M-00A6_A-0004-10-0000"))
