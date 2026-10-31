"""A preflight leaves every write out, so a compare that checks the device's reaction to an
earlier write of the procedure (a load state handshake) is not checked; every other compare
still is."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram.apci import (
    MemoryWrite,
    PropertyExtValueWriteCon,
    PropertyValueWrite,
)

from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import LdCtrlCompareMem
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.prod import Application

from .conftest import FakeDevice
from .test_extended_properties import ExtendedDevice

# The manufacturer object whose PID 5 the ise KNX Remote Access steps through its load states.
_LOAD_STATE_OBJECT = 40960
_LOAD_STATE = 5


def _application(*controls: object, extended: bool = False) -> Application:
    procedure = LoadProceduresLoadProcedure(choice=list(controls))  # type: ignore[arg-type]
    return cast(
        "Application",
        SimpleNamespace(
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
            manufacturer_id="M-007C",
            program=SimpleNamespace(
                pei_type=0,
                application_number=0x0004,
                application_version=0x62,
                mask_version="MV-07B0",
                is_secure_enabled=False,
                static=SimpleNamespace(
                    options=SimpleNamespace(
                        supports_extended_property_services=extended
                    )
                ),
            ),
        ),
    )


def _write(state: bytes, **target: int) -> LdCtrlWriteProp:
    # A load event is 10 octets: the event, then its additional information.
    return LdCtrlWriteProp(
        prop_id=_LOAD_STATE,
        count=1,
        verify=False,
        inline_data=state.ljust(10, b"\x00"),
        **target,
    )


def _compare(state: bytes, **target: int) -> LdCtrlCompareProp:
    return LdCtrlCompareProp(prop_id=_LOAD_STATE, count=1, inline_data=state, **target)


def _handshake(**target: int) -> list[object]:
    """Unload (04 -> 00), start loading (01 -> 02), load completed (02 -> 01)."""
    return [
        _write(b"\x04", **target),
        _compare(b"\x00", **target),
        _write(b"\x01", **target),
        _compare(b"\x02", **target),
        _write(b"\x02", **target),
        _compare(b"\x01", **target),
    ]


async def test_a_load_state_handshake_by_object_index_previews() -> None:
    device = FakeDevice(object_types={0: 0, 5: _LOAD_STATE_OBJECT})
    device.load_states[5] = LoadState.LOADED
    runner = LoadProcedureRunner(
        _application(*_handshake(obj_idx=5)),
        DownloadImage((), ()),
        DeviceProgrammer(device),
    )

    report = await runner.preflight()

    assert not any(isinstance(p, PropertyValueWrite) for p in device.sent)
    assert [(p.planned[:1], p.procedure_step) for p in report.properties] == [
        (b"\x04", True),
        (b"\x01", True),
        (b"\x02", True),
    ]


async def test_a_load_state_handshake_by_object_type_previews() -> None:
    device = ExtendedDevice()
    device.ext_values[_LOAD_STATE_OBJECT, 1, _LOAD_STATE] = b"\x01"
    runner = LoadProcedureRunner(
        _application(*_handshake(obj_type=_LOAD_STATE_OBJECT), extended=True),
        DownloadImage((), ()),
        DeviceProgrammer(device, max_apdu_length=50),
    )

    report = await runner.preflight()

    assert not any(isinstance(p, PropertyExtValueWriteCon) for p in device.sent)
    assert {p.object_label for p in report.properties} == {f"type {_LOAD_STATE_OBJECT}"}


async def test_a_compare_before_any_write_is_still_checked() -> None:
    device = ExtendedDevice()
    device.ext_values[_LOAD_STATE_OBJECT, 1, _LOAD_STATE] = b"\x01"
    runner = LoadProcedureRunner(
        _application(
            _compare(b"\x00", obj_type=_LOAD_STATE_OBJECT),
            _write(b"\x04", obj_type=_LOAD_STATE_OBJECT),
            extended=True,
        ),
        DownloadImage((), ()),
        DeviceProgrammer(device, max_apdu_length=50),
    )

    with pytest.raises(CompareMismatch) as error:
        await runner.preflight()

    # The object by type and instance, not the key of the extended property services.
    assert str(error.value) == (
        f"property compare failed for object type {_LOAD_STATE_OBJECT} instance 1 "
        "property 5: expected 00 read 01"
    )
    assert error.value.details == (
        f"The device reports property 5 of object type {_LOAD_STATE_OBJECT} instance 1 "
        "01, the application expects 00."
    )
    assert error.value.with_message("x").object_label == error.value.object_label


async def test_a_compare_of_another_property_after_a_write_is_still_checked() -> None:
    device = FakeDevice(object_types={0: 0, 5: _LOAD_STATE_OBJECT})
    device.properties[5, 6] = b"\x01"
    runner = LoadProcedureRunner(
        _application(
            _write(b"\x04", obj_idx=5),
            LdCtrlCompareProp(obj_idx=5, prop_id=6, count=1, inline_data=b"\x00"),
        ),
        DownloadImage((), ()),
        DeviceProgrammer(device),
    )

    with pytest.raises(CompareMismatch, match="object 5 property 6"):
        await runner.preflight()


def _memory_runner(*controls: object) -> tuple[LoadProcedureRunner, FakeDevice]:
    device = FakeDevice(object_types={0: 0})
    device.memory.update({0x4000: 0xFF, 0x4001: 0xFF})
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=b"\x01\x02"),), properties=()
    )
    return (
        LoadProcedureRunner(_application(*controls), image, DeviceProgrammer(device)),
        device,
    )


async def test_a_memory_compare_after_a_write_there_is_not_checked() -> None:
    runner, device = _memory_runner(
        LdCtrlWriteMem(address=0x4000, size=2, verify=False, inline_data=None),
        LdCtrlCompareMem(address=0x4001, size=1, inline_data=b"\x02"),
    )

    report = await runner.preflight()

    assert not any(isinstance(p, MemoryWrite) for p in device.sent)
    assert report.total_changed_bytes == 2


@pytest.mark.parametrize("address", [0x3FFF, 0x4002])
async def test_a_memory_compare_beside_a_write_is_still_checked(address: int) -> None:
    runner, _ = _memory_runner(
        LdCtrlWriteMem(address=0x4000, size=2, verify=False, inline_data=None),
        LdCtrlCompareMem(address=address, size=1, inline_data=b"\x02"),
    )

    with pytest.raises(CompareMismatch, match="memory compare failed"):
        await runner.preflight()
