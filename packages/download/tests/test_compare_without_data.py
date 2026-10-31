"""Compare controls without inline data check the image's data, as far as the image goes; with
neither there is nothing to compare (a Wago cIP compares the manufacturer this way)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram.apci import MemoryRead, PropertyValueRead

from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.image import DownloadImage, MemorySegment
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


def _runner(
    device: FakeDevice, image: DownloadImage, *controls: object
) -> LoadProcedureRunner:
    procedure = LoadProceduresLoadProcedure(choice=list(controls))  # type: ignore[arg-type]
    application = cast(
        Application,
        SimpleNamespace(
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
            manufacturer_id="M-00FA",
            program=SimpleNamespace(
                pei_type=0,
                application_number=1,
                application_version=1,
                mask_version="MV-0705",
            ),
        ),
    )
    return LoadProcedureRunner(application, image, DeviceProgrammer(device))


_EMPTY = DownloadImage(segments=(), properties=())


async def test_a_property_compare_without_any_data_compares_nothing() -> None:
    device = FakeDevice()
    compare = LdCtrlCompareProp(
        obj_idx=0,
        prop_id=12,
        start_element=1,
        count=1,
        inline_data=None,  # pyright: ignore[reportArgumentType]
    )
    await _runner(device, _EMPTY, compare).run()
    assert not [p for p in device.sent if isinstance(p, PropertyValueRead)]
    await _runner(device, _EMPTY, compare).preflight()


async def test_a_memory_compare_without_inline_data_checks_the_image() -> None:
    image = DownloadImage(
        segments=(MemorySegment(0xA5A5, b"\x00\x0b"),),
        properties=(),
    )
    device = FakeDevice()
    device.memory.update({0xA5A5: 0x00, 0xA5A6: 0x0B})
    # The image covers the first two of the three octets: those are compared.
    compare = LdCtrlCompareMem(
        address=0xA5A5,
        size=3,
        inline_data=None,  # pyright: ignore[reportArgumentType]
    )
    await _runner(device, image, compare).run()
    reads = [p for p in device.sent if isinstance(p, MemoryRead)]
    assert [(p.address, p.count) for p in reads] == [(0xA5A5, 2)]
    device.memory[0xA5A6] = 0x0C
    with pytest.raises(CompareMismatch):
        await _runner(device, image, compare).run()
    # Outside the image there is nothing to compare.
    device.sent.clear()
    elsewhere = LdCtrlCompareMem(
        address=0xA5AA,
        size=1,
        inline_data=None,  # pyright: ignore[reportArgumentType]
    )
    await _runner(device, image, elsewhere).run()
    assert not [p for p in device.sent if isinstance(p, MemoryRead)]
