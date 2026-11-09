"""A memory write reports how far it is, block by block, so a long one shows movement."""

from __future__ import annotations

from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

from .test_procedure import _application, _device  # pyright: ignore[reportPrivateUsage]

_DATA = bytes(range(40))


async def _run(
    control: object, image: DownloadImage, **device_setup: int
) -> list[tuple[int, int]]:
    device = _device({0: 0x0000, 1: 3})
    device.table_references[1] = device_setup.get("base", 0)
    for offset, value in enumerate(device_setup.get("seed", b"")):  # type: ignore[arg-type]
        device.memory[0x500 + offset] = value
    reported: list[tuple[int, int]] = []
    runner = LoadProcedureRunner(_application(control), image, DeviceProgrammer(device))
    await runner.run(memory_progress=lambda done, total: reported.append((done, total)))
    return reported


async def test_a_memory_write_reports_each_block() -> None:
    # The standard APDU of 15 octets carries 12 octets of memory per block.
    control = LdCtrlWriteMem(address=0x500, size=40, verify=False, inline_data=_DATA)
    reported = await _run(control, DownloadImage(segments=(), properties=()))
    assert reported == [(0, 40), (12, 40), (24, 40), (36, 40), (40, 40)]


async def test_a_relative_memory_write_reports_each_block() -> None:
    control = LdCtrlWriteRelMem(
        obj_type=3, occurrence=0, offset=0, size=40, verify=False, inline_data=_DATA
    )
    reported = await _run(
        control, DownloadImage(segments=(), properties=()), base=0x4000
    )
    assert reported == [(0, 40), (12, 40), (24, 40), (36, 40), (40, 40)]


async def test_runs_count_together_and_an_unchanged_one_counts_as_done() -> None:
    # Two runs of one control; the device already holds the first, so a verified
    # (optimistic) write skips it, and it still counts.
    image = DownloadImage(
        segments=(
            MemorySegment(address=0x500, data=_DATA[:20]),
            MemorySegment(address=0x600, data=_DATA[20:]),
        ),
        properties=(),
    )
    control = LdCtrlWriteMem(address=0x500, size=0x114, verify=True, inline_data=None)
    reported = await _run(control, image, seed=_DATA[:20])  # type: ignore[arg-type]
    assert reported[0] == (0, 40)
    assert reported[-1] == (40, 40)
    assert [done for done, _ in reported] == sorted(done for done, _ in reported)
    assert (20, 40) in reported
