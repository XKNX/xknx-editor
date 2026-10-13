"""Mask-version memory that the application's segment data never writes."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import cast

import pytest

from xknxeditor.download.image import build_image
from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
    ApplicationProgramStaticCodeAbsoluteSegment,
)
from xknxeditor.prod import Application, load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_wire_audit import _FIXTURE


def _image_masks(mask_version: str, address: int, size: int) -> tuple[bytes, bytes]:
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
            encode_to_typed_properties=lambda: {},
        ),
    )
    (memory,) = build_image(app, ui=ui).segments
    assert memory.initialized_mask is not None and memory.mask is not None
    return memory.initialized_mask, memory.mask


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
    initialized, parameters = _image_masks(mask_version, address, 10)
    assert [n for n, value in enumerate(initialized) if not value] == list(kept_out)
    # Parameter bytes are written regardless.
    assert parameters[-1] == 0xFF
