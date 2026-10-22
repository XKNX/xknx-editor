"""Property compares that test a value range instead of inline data (e.g. a minimum version)."""

from __future__ import annotations

from typing import Any

import pytest

from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner, _value_in_range
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp

from .conftest import FakeDevice
from .test_wire_audit import seeded_application


@pytest.mark.parametrize(
    ("text", "data", "expected"),
    [
        ("[2216203124736,]", bytes.fromhex("020400000000"), True),  # the minimum itself
        ("[2216203124736,]", bytes.fromhex("020500000000"), True),
        ("[2216203124736,]", bytes.fromhex("0203ffffffff"), False),
        ("(5,10)", b"\x05", False),  # excluded bounds
        ("(5,10)", b"\x06", True),
        ("(5,10)", b"\x0a", False),
        ("[5,10]", b"\x0a", True),
        ("[,10]", b"\x00", True),  # open lower bound
        ("[-2,-1]s", b"\xfe", True),  # signed
        ("[-2,-1]", b"\xfe", False),  # unsigned: 254
    ],
)
def test_value_in_range(text: str, data: bytes, expected: bool) -> None:
    assert _value_in_range(text, data, None) is expected


def test_value_in_range_applies_the_mask() -> None:
    assert _value_in_range("[0,15]", b"\xf3", b"\x0f")


def _range_compare(value_range: str) -> LdCtrlCompareProp:
    # The model wants inline data; a range compare has none (see the import converter).
    fields: dict[str, Any] = {"inline_data": None}
    return LdCtrlCompareProp(obj_idx=0, prop_id=201, range=value_range, **fields)


@pytest.mark.parametrize(
    ("version", "passes"),
    [(bytes.fromhex("020500000000"), True), (bytes.fromhex("020300000000"), False)],
)
async def test_property_range_compare(version: bytes, passes: bool) -> None:
    app, _ = seeded_application()
    device = FakeDevice()
    device.properties[0, 201] = version
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(device),
        controls=[_range_compare("[2216203124736,]")],
    )
    if passes:
        await runner.run()
    else:
        with pytest.raises(CompareMismatch):
            await runner.run()


@pytest.mark.parametrize(("device_value", "passes"), [(0x2A, True), (0x2B, False)])
async def test_memory_compare_without_inline_data_checks_the_image(
    device_value: int, passes: bool
) -> None:
    from xknxeditor.download.image import MemorySegment
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import (
        LdCtrlCompareMem,
    )

    app, _ = seeded_application()
    device = FakeDevice()
    device.memory[0x4000] = device_value
    fields: dict[str, Any] = {"inline_data": None}
    control = LdCtrlCompareMem(address=0x4000, size=1, **fields)
    runner = LoadProcedureRunner(
        app,
        DownloadImage((MemorySegment(0x4000, b"\x2a"),), ()),
        DeviceProgrammer(device),
        controls=[control],
    )
    if passes:
        await runner.run()
    else:
        with pytest.raises(CompareMismatch):
            await runner.run()
