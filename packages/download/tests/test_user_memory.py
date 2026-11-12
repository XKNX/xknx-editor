"""Load controls in the user memory (``AddressSpace="User"``).

The user memory is an address space of its own, read and written with A_UserMemory_Read/Write
at every address (KNX 3/3/7 3.5.6; A_UserMemory reaches 1 Mbyte with a 20-bit address, at most
15 octets per service, 3/5/3 3.5.1.4). The image keeps its code segments apart
(``DownloadImage.user_segments``): a write there never reaches the standard memory, even at the
same address.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from xknx.telegram import Telegram
from xknx.telegram.apci import (
    APCI,
    MemoryRead,
    MemoryWrite,
    UserMemoryRead,
    UserMemoryResponse,
    UserMemoryWrite,
)

from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.preflight import USER_MEMORY_KEY, memory_key
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import LdCtrlCompareMem
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
    LdCtrlLoadImageMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
    LdCtrlMemAddrSpace,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)

from .conftest import FakeDevice

if TYPE_CHECKING:
    from xknxeditor.prod import Application

_USER = LdCtrlMemAddrSpace.USER


class _UserMemoryDevice(FakeDevice):
    """A device whose user memory is kept apart from its standard memory, so a write in the
    wrong address space lands in the wrong one."""

    def __init__(self) -> None:
        super().__init__(object_types={0: 0x0000}, descriptor=0x2920)
        self.user_memory: dict[int, int] = {}
        # Writes the device takes but does not keep (a write-protected range).
        self.user_read_only = False

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        if isinstance(payload, UserMemoryWrite):
            self.sent.append(payload)
            self._store_user(payload.address, payload.data)
            return
        await super().send_data(payload, wait_for_ack)

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if isinstance(payload, UserMemoryRead):
            self.sent.append(payload)
            data = bytes(
                self.user_memory.get(payload.address + i, 0)
                for i in range(payload.count)
            )
            return self._telegram(
                UserMemoryResponse(address=payload.address, data=data)
            )
        if isinstance(payload, UserMemoryWrite):
            self.sent.append(payload)
            self._store_user(payload.address, payload.data)
            return self._telegram(
                UserMemoryResponse(address=payload.address, data=payload.data)
            )
        return await super().request(payload, expected)

    def _store_user(self, address: int, data: bytes) -> None:
        if self.user_read_only:
            return
        for index, value in enumerate(data):
            self.user_memory[address + index] = value

    def user(self, address: int, size: int) -> bytes:
        return bytes(self.user_memory.get(address + i, 0) for i in range(size))

    def standard(self, address: int, size: int) -> bytes:
        return bytes(self.memory.get(address + i, 0) for i in range(size))


def _application(*controls: object) -> Application:
    procedure = LoadProceduresLoadProcedure(
        choice=[LdCtrlConnect(), *controls, LdCtrlDisconnect()]  # type: ignore[list-item]
    )
    fake = SimpleNamespace(
        load_procedures=LoadProcedures(load_procedure=[procedure]),
        load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
        manufacturer_id="M-0008",
        program=SimpleNamespace(
            pei_type=0,
            application_number=1,
            application_version=1,
            mask_version="MV-2920",
        ),
    )
    return cast("Application", fake)


def _runner(
    image: DownloadImage,
    *controls: object,
    scope: DownloadScope = DownloadScope.FULL,
    device: _UserMemoryDevice | None = None,
) -> tuple[LoadProcedureRunner, _UserMemoryDevice]:
    device = device or _UserMemoryDevice()
    runner = LoadProcedureRunner(
        _application(*controls), image, DeviceProgrammer(device), scope=scope
    )
    return runner, device


def _write(
    address: int,
    size: int,
    *,
    inline: bytes | None = None,
    verify: bool = False,
    applies_to: LdCtrlProcType = LdCtrlProcType.FULL,
    space: LdCtrlMemAddrSpace = _USER,
) -> LdCtrlWriteMem:
    return LdCtrlWriteMem(
        address=address,
        size=size,
        verify=verify,
        inline_data=inline,
        address_space=space,
        applies_to=applies_to,
    )


def _sent(device: FakeDevice, kind: type[APCI]) -> list[APCI]:
    return [payload for payload in device.sent if isinstance(payload, kind)]


async def test_an_inline_write_takes_the_user_memory_services() -> None:
    # A firmware image (inline data) for the user memory below 64 KiB, and one above it.
    low = bytes(range(40))
    high = bytes(range(100, 120))
    runner, device = _runner(
        DownloadImage(segments=(), properties=()),
        _write(0xC800, len(low), inline=low),
        _write(0x30000, len(high), inline=high),
    )
    await runner.run()
    assert device.user(0xC800, len(low)) == low
    assert device.user(0x30000, len(high)) == high
    assert device.memory == {}
    assert not _sent(device, MemoryWrite)
    writes = cast("list[UserMemoryWrite]", _sent(device, UserMemoryWrite))
    # At most 11 octets per standard frame (APDU 15, 4 octets of the service besides data).
    assert max(len(w.data) for w in writes) == 11


async def test_an_image_write_takes_the_user_segments_not_the_standard_ones() -> None:
    # Standard and user memory both hold a segment at 4000h: each write takes its own.
    image = DownloadImage(
        segments=(MemorySegment(0x4000, b"\x11\x22\x33\x44"),),
        properties=(),
        user_segments=(MemorySegment(0x4000, b"\xaa\xbb\xcc\xdd"),),
    )
    runner, device = _runner(
        image,
        _write(0x4000, 4, space=LdCtrlMemAddrSpace.STANDARD),
        _write(0x4000, 4),
    )
    await runner.run()
    assert device.standard(0x4000, 4) == b"\x11\x22\x33\x44"
    assert device.user(0x4000, 4) == b"\xaa\xbb\xcc\xdd"


async def test_inline_data_under_a_user_segment_takes_the_image_data() -> None:
    image = DownloadImage(
        segments=(),
        properties=(),
        user_segments=(MemorySegment(0x1002, b"\x55\x66", b"\xff\xff"),),
    )
    runner, device = _runner(image, _write(0x1000, 4, inline=b"\x01\x02\x03\x04"))
    await runner.run()
    assert device.user(0x1000, 4) == b"\x01\x02\x55\x66"


async def test_a_parameters_download_writes_the_parameters_of_a_user_segment() -> None:
    # The seed (initialized) octets go with a full download only.
    segment = MemorySegment(
        0x2000, b"\x01\x02\x03\x04", b"\xff\x00\xff\x00", b"\xff\xff\xff\xff"
    )
    image = DownloadImage(segments=(), properties=(), user_segments=(segment,))
    write = _write(0x2000, 4, applies_to=LdCtrlProcType.FULL_PAR)
    runner, device = _runner(image, write, scope=DownloadScope.PARAMETERS)
    await runner.run()
    assert device.user_memory == {0x2000: 0x01, 0x2002: 0x03}
    runner, device = _runner(image, write)
    await runner.run()
    assert device.user(0x2000, 4) == b"\x01\x02\x03\x04"


async def test_a_verified_write_reads_the_user_memory_back() -> None:
    image = DownloadImage(
        segments=(),
        properties=(),
        user_segments=(MemorySegment(0x3000, bytes(range(1, 21))),),
    )
    runner, device = _runner(image, _write(0x3000, 20, verify=True))
    await runner.run()
    assert device.user(0x3000, 20) == bytes(range(1, 21))
    assert _sent(device, UserMemoryRead)
    assert not _sent(device, MemoryRead)

    runner, device = _runner(image, _write(0x3000, 20, verify=True))
    device.user_read_only = True
    with pytest.raises(CompareMismatch):
        await runner.run()


@pytest.mark.parametrize("held", [b"\x5a\xa5", b"\x00\x00"])
async def test_a_compare_reads_the_user_memory(held: bytes) -> None:
    compare = LdCtrlCompareMem(
        address=0x8000, size=2, inline_data=b"\x5a\xa5", address_space=_USER
    )
    runner, device = _runner(DownloadImage(segments=(), properties=()), compare)
    device.user_memory.update({0x8000: held[0], 0x8001: held[1]})
    # The standard memory holds the expected octets: a compare there would pass.
    device.memory.update({0x8000: 0x5A, 0x8001: 0xA5})
    if held == b"\x5a\xa5":
        await runner.run()
    else:
        with pytest.raises(CompareMismatch, match="user memory compare failed"):
            await runner.run()
    assert not _sent(device, MemoryRead)


async def test_load_image_reads_the_user_memory_into_the_image() -> None:
    # What LoadImageMem read from the user memory is what a later write there writes back.
    runner, device = _runner(
        DownloadImage(segments=(), properties=()),
        LdCtrlLoadImageMem(address=0x500, size=3, address_space=_USER),
        _write(0x500, 3, inline=b"\x00\x00\x00"),
    )
    device.user_memory.update({0x500: 7, 0x501: 8, 0x502: 9})
    device.memory.update({0x500: 1, 0x501: 2, 0x502: 3})
    await runner.run()
    assert device.user(0x500, 3) == b"\x07\x08\x09"
    assert device.standard(0x500, 3) == b"\x01\x02\x03"


async def test_the_preview_reads_the_user_memory_and_writes_nothing() -> None:
    image = DownloadImage(
        segments=(MemorySegment(0x600, b"\x01\x02"),),
        properties=(),
        user_segments=(MemorySegment(0x600, b"\x0a\x0b"),),
    )
    runner, device = _runner(
        image,
        _write(0x600, 2),
        _write(0x600, 2, space=LdCtrlMemAddrSpace.STANDARD),
        # The same range in both spaces with other inline data: no step of the procedure.
        _write(0x700, 1, inline=b"\x01"),
        _write(0x700, 1, inline=b"\x02", space=LdCtrlMemAddrSpace.STANDARD),
        # A compare after the previewed write checks what the preview left out: not checked
        # (the device holds other data there).
        LdCtrlCompareMem(
            address=0x600, size=2, inline_data=b"\x0a\x0b", address_space=_USER
        ),
    )
    device.user_memory.update({0x600: 0x0A, 0x601: 0x0C})
    device.memory.update({0x600: 0x01, 0x601: 0x02})
    report = await runner.preflight()
    assert not _sent(device, UserMemoryWrite)
    assert not _sent(device, MemoryWrite)
    user, standard, user_step, standard_step = report.segments
    assert (user.user_memory, user.current, user.planned) == (
        True,
        b"\x0a\x0c",
        b"\x0a\x0b",
    )
    assert (standard.user_memory, standard.changed) == (False, False)
    assert not user_step.procedure_step and not standard_step.procedure_step
    assert user.memory_key(1) == USER_MEMORY_KEY + 0x601
    assert standard.memory_key(1) == 0x601


async def test_a_user_byte_the_device_rewrites_is_kept_by_its_key() -> None:
    class _Rewriting(_UserMemoryDevice):
        async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
            telegram = await super().request(payload, expected)
            if isinstance(payload, UserMemoryRead):
                self.user_memory[0x900] = self.user_memory.get(0x900, 0) + 1
            return telegram

    image = DownloadImage(
        segments=(), properties=(), user_segments=(MemorySegment(0x900, b"\x40"),)
    )
    runner, _device = _runner(image, _write(0x900, 1), device=_Rewriting())
    report = await runner.preflight()
    assert report.device_written == {memory_key(0x900, user=True)}


async def test_the_programmer_takes_the_user_services_at_every_address() -> None:
    device = _UserMemoryDevice()
    programmer = DeviceProgrammer(device)
    data = bytes(range(16))
    # Across 64 KiB: no cut at the boundary, A_UserMemory_Write below it as well.
    await programmer.write_user_memory(0xFFF8, data)
    writes = cast("list[UserMemoryWrite]", _sent(device, UserMemoryWrite))
    assert [(w.address, len(w.data)) for w in writes] == [(0xFFF8, 11), (0x10003, 5)]
    assert await programmer.read_user_memory(0xFFF8, 16) == data
    assert not _sent(device, MemoryRead)
    assert await programmer.read_memory(0xFFF8, 2, user=True) == data[:2]
