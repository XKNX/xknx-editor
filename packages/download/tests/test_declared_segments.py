"""A declared code segment with nothing to write (a zero mask and no parameters) stays in the
image: a partial download turns its allocation into a WriteMem over the whole segment, which
writes nothing there instead of failing for missing image data."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import cast

from xknx.telegram.apci import MemoryRead, MemoryWrite

from xknxeditor.download.image import DownloadImage, build_image
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
    ApplicationProgramStaticCodeAbsoluteSegment,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.prod import Application, load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .conftest import FakeDevice
from .test_wire_audit import _FIXTURE

_EMPTY = 0x46C0
_SIZE = 63


def _image(*, table: bool = False) -> tuple[Application, DownloadImage]:
    """An MV-0705 application with a segment carrying a parameter at 0x4500 and a segment at
    0x46C0 whose product mask is zero and that holds no parameter."""
    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    assert program.static.code is not None
    program.static.code.absolute_segment = [
        ApplicationProgramStaticCodeAbsoluteSegment(
            id="used", size=4, address=0x4500, data=bytes(4)
        ),
        ApplicationProgramStaticCodeAbsoluteSegment(
            id="empty",
            size=_SIZE,
            address=_EMPTY,
            data=bytes(_SIZE),
            mask=bytes(_SIZE),
        ),
    ]
    program.static.code.relative_segment = []
    program.mask_version = "MV-0705"
    program.static.address_table = None
    program.static.association_table = None
    program.static.com_object_table = (
        SimpleNamespace(code_segment="empty") if table else None
    )
    procedure = LoadProceduresLoadProcedure(
        choice=[
            LdCtrlWriteMem(address=0x4500, size=4, verify=False, inline_data=None),
            LdCtrlWriteMem(address=_EMPTY, size=_SIZE, verify=True, inline_data=None),
        ]  # type: ignore[arg-type]
    )
    app = cast(
        Application,
        SimpleNamespace(
            id="M-0080_A-7011-11-20D3",
            program=program,
            manufacturer_id="M-0080",
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
        ),
    )
    ui = cast(
        DynamicUI,
        SimpleNamespace(
            segment_base_addrs=lambda: {"used": 0x4500, "empty": _EMPTY},
            encode_to_memory_masked=lambda: {
                "used": (b"\x00\x00\x00\x07", b"\x00\x00\x00\xff"),
                "empty": (bytes(_SIZE), bytes(_SIZE)),
            },
            encode_to_typed_properties_masked=lambda: {},
        ),
    )
    return app, build_image(app, ui=ui)


def _runner(
    app: Application, image: DownloadImage
) -> tuple[LoadProcedureRunner, FakeDevice]:
    device = FakeDevice()
    programmer = DeviceProgrammer(device)
    programmer.skip_property_verify_probe()
    return LoadProcedureRunner(app, image, programmer), device


def test_the_segment_stays_in_the_image_with_nothing_to_write() -> None:
    _app, image = _image()
    assert [s.address for s in image.segments] == [0x4500, _EMPTY]
    for partial in (True, False):
        assert (
            image.masked_writes(
                _EMPTY, _SIZE, include_initialized=not partial, partial=partial
            )
            == []
        )


def test_an_empty_table_segment_is_still_left_to_the_table_builder() -> None:
    _app, image = _image(table=True)
    assert [s.address for s in image.segments] == [0x4500]


async def test_preview_and_download_skip_the_segment() -> None:
    app, image = _image()
    runner, device = _runner(app, image)
    report = await runner.preflight()
    assert report.changed_segments
    assert all(d.address < _EMPTY for d in report.changed_segments)
    reads = [p for p in device.sent if isinstance(p, MemoryRead)]
    assert all(not 0 <= r.address - _EMPTY < _SIZE for r in reads)

    runner, device = _runner(app, image)
    await runner.run()
    writes = [p for p in device.sent if isinstance(p, MemoryWrite)]
    assert writes
    assert all(w.address < _EMPTY for w in writes)
    assert device.memory[0x4503] == 0x07


def test_the_mask_memory_is_part_of_the_image() -> None:
    # A range in the mask's own memory has nothing to write; outside it, data is missing.
    image = DownloadImage(segments=(), properties=(), mask_memory=((0x100, 880),))
    assert image.masked_writes(0x111, 1, include_initialized=True) == []
    assert image.masked_writes(0x500, 1, include_initialized=True) is None
