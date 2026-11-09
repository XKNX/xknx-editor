"""The octets the product data gives only some bits of are kept by standard-memory address,
so only those of a segment in standard memory: user memory is another address space, which
the standard-memory writes that merge them never reach."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import cast

from xknxeditor.download.image import build_image
from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
    ApplicationProgramStaticCodeAbsoluteSegment,
)
from xknxeditor.prod import Application, load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_wire_audit import _FIXTURE

# One octet given whole, one in its low nibble only.
_DATA = b"\x12\x05"
_MASK = b"\xff\x0f"


def _bit_masks(user_memory: bool) -> dict[int, int]:
    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    assert program.static.code is not None
    for table in (
        program.static.address_table,
        program.static.association_table,
        program.static.com_object_table,
    ):
        if table is not None:
            table.code_segment = None
    program.static.code.absolute_segment = [
        ApplicationProgramStaticCodeAbsoluteSegment(
            id="s0",
            size=len(_DATA),
            address=0x4000,
            user_memory=user_memory,
            data=_DATA,
            mask=_MASK,
        )
    ]
    program.static.code.relative_segment = []
    app = cast(
        Application,
        SimpleNamespace(
            program=program, manufacturer_id="M-00FA", load_procedures=None
        ),
    )
    ui = cast(
        DynamicUI,
        SimpleNamespace(
            segment_base_addrs=lambda: {"s0": 0x4000},
            encode_to_memory_masked=lambda: {"s0": (bytes(2), bytes(2))},
            encode_to_typed_properties_masked=lambda: {},
            module_load_controls=lambda: {},
        ),
    )
    return dict(build_image(app, ui=ui).bit_masks)


def test_an_octet_given_in_part_in_standard_memory_is_kept() -> None:
    assert _bit_masks(user_memory=False) == {0x4001: 0x0F}


def test_an_octet_given_in_part_in_user_memory_is_not_kept() -> None:
    assert _bit_masks(user_memory=True) == {}
