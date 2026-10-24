"""Mask-version memory that the application's segment data never writes."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import cast

import pytest

from xknxeditor.download.image import MemorySegment, build_image
from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
    ApplicationProgramStaticCodeAbsoluteSegment,
)
from xknxeditor.prod import Application, load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_wire_audit import _FIXTURE


def _memory(mask_version: str, address: int, size: int) -> MemorySegment:
    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    assert program.static.code is not None
    segment = ApplicationProgramStaticCodeAbsoluteSegment(
        id="segment", size=size, address=address, data=bytes(range(size))
    )
    program.static.code.absolute_segment = [segment]
    program.static.code.relative_segment = []
    program.mask_version = mask_version
    parameter_mask = bytes(size - 1) + b"\xff"
    app = cast(Application, SimpleNamespace(program=program, manufacturer_id="M-00FA"))
    ui = cast(
        DynamicUI,
        SimpleNamespace(
            segment_base_addrs=lambda: {"segment": address},
            encode_to_memory_masked=lambda: {
                "segment": (bytes(range(size)), parameter_mask)
            },
            encode_to_typed_properties_masked=lambda: {},
        ),
    )
    (memory,) = build_image(app, ui=ui).segments
    return memory


@pytest.mark.parametrize(
    ("mask_version", "address", "kept_out"),
    [
        ("MV-0705", 0x4000, (1, 2)),
        ("MV-0012", 0x0110, (7, 8)),
        ("MV-0021", 0x0110, (7, 8)),
        ("MV-07B0", 0x4000, ()),
        ("MV-0910", 0x4000, ()),
    ],
)
def test_reserved_bytes_are_left_out_of_the_segment_data(
    mask_version: str, address: int, kept_out: tuple[int, ...]
) -> None:
    memory = _memory(mask_version, address, 10)
    initialized, parameters = memory.initialized_mask, memory.mask
    assert initialized is not None and parameters is not None
    assert [n for n, value in enumerate(initialized) if not value] == list(kept_out)
    # Parameter bytes are written regardless.
    assert parameters[-1] == 0xFF


@pytest.mark.parametrize("mask_version", ["MV-0910", "MV-091A"])
def test_line_coupler_bytes_come_with_the_segment_data(mask_version: str) -> None:
    memory = _memory(mask_version, 0x0100, 0x1D)
    assert memory.initialized_mask is not None
    expected = {0x10D: 0xFF, 0x112: 0xC3, 0x116: 0x01, 0x11A: 0xFF, 0x11B: 0xCC}
    for address, value in expected.items():
        assert memory.data[address - 0x100] == value
        assert memory.initialized_mask[address - 0x100] == 0xFF
    # 0x11C holds a parameter, which keeps its value.
    assert memory.data[0x1C] == 0x1C


def test_other_masks_and_short_segments_keep_their_data() -> None:
    assert _memory("MV-0705", 0x0100, 0x20).data[0x0D] == 0x0D
    assert _memory("MV-091A", 0x0110, 0x10).data[0x0B] == 0x0B
