"""Compare controls that pass on a mismatch (Invert) or read again until the device reaches
the state (RetryInterval/TimeOut), as a Siemens 5WG1141 procedure waits for a device step."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import LdCtrlCompareMem
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.prod import Application

from .conftest import FakeDevice

_EMPTY = DownloadImage(segments=(), properties=())


def _runner(device: FakeDevice, *controls: object) -> LoadProcedureRunner:
    procedure = LoadProceduresLoadProcedure(choice=list(controls))  # type: ignore[arg-type]
    application = cast(
        Application,
        SimpleNamespace(
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
            manufacturer_id="M-0001",
            program=SimpleNamespace(
                pei_type=0,
                application_number=1,
                application_version=1,
                mask_version="MV-07B0",
            ),
        ),
    )
    return LoadProcedureRunner(application, _EMPTY, DeviceProgrammer(device))


def _compare(value: bytes, **extra: object) -> LdCtrlCompareProp:
    return LdCtrlCompareProp(
        obj_idx=0, prop_id=57, start_element=1, count=1, inline_data=value, **extra
    )


async def test_an_inverted_compare_passes_on_a_different_value() -> None:
    device = FakeDevice()
    device.properties[0, 57] = b"\x02"
    await _runner(device, _compare(b"\x01", invert=True)).run()
    with pytest.raises(CompareMismatch, match="inverted compare failed"):
        await _runner(device, _compare(b"\x02", invert=True)).run()


async def test_a_polled_compare_waits_for_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice()
    device.properties[0, 57] = b"\x01"
    waits: list[float] = []
    sleep = asyncio.sleep

    async def wait(delay: float) -> None:
        waits.append(delay)
        if len(waits) == 3:
            device.properties[0, 57] = b"\x02"
        await sleep(0)

    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", wait)
    # Wait until the device leaves 01 (every 500 ms, at most 500 s), then it must hold 02.
    await _runner(
        device,
        _compare(b"\x01", invert=True, retry_interval=500, time_out=500),
        _compare(b"\x02"),
    ).run()
    assert waits[:3] == [0.5, 0.5, 0.5]


async def test_a_polled_compare_fails_when_the_time_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice()
    device.properties[0, 57] = b"\x01"
    clock = iter(range(0, 10_000, 1))
    monkeypatch.setattr(
        "xknxeditor.download.procedure.time.monotonic", lambda: next(clock)
    )

    async def wait(_delay: float) -> None:
        return None

    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", wait)
    with pytest.raises(CompareMismatch, match="property compare failed"):
        await _runner(device, _compare(b"\x02", retry_interval=100, time_out=3)).run()


async def test_a_memory_compare_can_be_inverted() -> None:
    device = FakeDevice()
    device.memory[0x80] = 0x02
    await _runner(
        device, LdCtrlCompareMem(address=0x80, size=1, inline_data=b"\x01", invert=True)
    ).run()


async def test_the_preview_reads_an_inverted_compare_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def wait(_delay: float) -> None:
        raise AssertionError("a preview does not wait")

    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", wait)
    device = FakeDevice()
    device.properties[0, 57] = b"\x02"
    await _runner(
        device, _compare(b"\x01", invert=True, retry_interval=500, time_out=500)
    ).preflight()
    device.properties[0, 57] = b"\x01"
    with pytest.raises(CompareMismatch):
        await _runner(
            device, _compare(b"\x01", invert=True, retry_interval=500, time_out=500)
        ).preflight()
