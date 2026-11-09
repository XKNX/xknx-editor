"""A code segment the application's own load procedure writes nothing of is not read: a
download leaves the device's memory there as it was, so it tells nothing of the configuration
(a converted product's segment for a project id, which no step of its procedure loads)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.recover.read_config import read_parameter_memory

_SIZES = {"AS-4000": 0x10, "AS-4700": 8}
_BASES = {"AS-4000": 0x4000, "AS-4700": 0x4700}


class _Programmer:
    def __init__(self) -> None:
        self.reads: list[tuple[int, int]] = []

    async def read_memory(
        self, address: int, size: int, *, user: bool = False
    ) -> bytes:
        assert not user
        self.reads.append((address, size))
        return bytes(size)


def _segment(address: int, size: int) -> LdCtrlAbsSegment:
    return LdCtrlAbsSegment(
        lsm_idx=3,
        seg_type=0,
        address=address,
        size=size,
        access=0,
        mem_type=3,
        seg_flags=0,
    )


def _application(style: LoadProcedureStyle, *controls: object) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(
                segment_sizes=lambda: _SIZES, segment_base_addrs=lambda: _BASES
            ),
            load_procedure_style=style,
            load_procedures=SimpleNamespace(
                load_procedure=[SimpleNamespace(choice=list(controls))]
            )
            if controls
            else None,
            program=SimpleNamespace(
                static=SimpleNamespace(
                    address_table=SimpleNamespace(code_segment="AS-4000"),
                    code=SimpleNamespace(
                        absolute_segment=[
                            SimpleNamespace(id="AS-4000", user_memory=False),
                            SimpleNamespace(id="AS-4700", user_memory=False),
                        ],
                        relative_segment=[],
                    ),
                )
            ),
        ),
    )


async def _read(application: Any) -> tuple[set[str], list[tuple[int, int]]]:
    programmer = _Programmer()
    read = await read_parameter_memory(cast(Any, programmer), application)
    return set(read), programmer.reads


async def test_a_segment_no_step_of_the_procedure_loads_is_not_read() -> None:
    application = _application(
        LoadProcedureStyle.PRODUCT_PROCEDURE, _segment(0x4000, 0x10)
    )
    assert await _read(application) == ({"AS-4000"}, [(0x4000, 0x10)])


async def test_a_segment_a_step_in_a_branch_or_a_memory_write_reaches_is_read() -> None:
    choose = SimpleNamespace(when=[SimpleNamespace(choice=[_segment(0x4700, 8)])])
    write = LdCtrlWriteMem(address=0x4704, size=2, verify=True)
    for second in (choose, write):
        application = _application(
            LoadProcedureStyle.PRODUCT_PROCEDURE, _segment(0x4000, 0x10), second
        )
        segments, _reads = await _read(application)
        assert segments == {"AS-4000", "AS-4700"}


async def test_every_segment_is_read_without_a_procedure_that_places_them() -> None:
    # The procedure of the mask, merged with the application's or in its place, is not the
    # application's own: what it writes is not known here.
    for application in (
        _application(LoadProcedureStyle.MERGED_PROCEDURE, _segment(0x4000, 0x10)),
        _application(LoadProcedureStyle.DEFAULT_PROCEDURE),
        _application(LoadProcedureStyle.PRODUCT_PROCEDURE),
    ):
        segments, _reads = await _read(application)
        assert segments == {"AS-4000", "AS-4700"}
