"""After a Master Reset, a device showing its product's factory stamp is taken to hold the
factory image: writes of what it already holds are left out."""

from __future__ import annotations

from typing import Any, cast

import pytest
from xknx.telegram.apci import (
    FunctionPropertyCommand,
    MemoryWrite,
    PropertyValueWrite,
)

from xknxeditor.download.factory_image import (
    FactoryImage,
    FactoryLocation,
    FactorySegment,
    FactoryStamp,
    ObjectStamp,
    PropertyStamp,
)
from xknxeditor.download.image import DownloadImage, MemorySegment, PropertyValue
from xknxeditor.download.procedure import (
    LoadProcedureRunner,
    _leave_factory_runs,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_master_reset_t import LdCtrlMasterReset
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

from .conftest import FakeDevice
from .test_procedure import _application, _FakeManager

# MV-07B0 counts its application objects from index 6: object 8 is application object 2.
_STAMP = PropertyStamp(FactoryLocation.APP_PROPERTY, 0, 53, 0, b"\xfe\xa5\xfa\x4e")
_PRODUCT_STAMP = FactoryStamp(properties=(_STAMP,))
_FACTORY = (
    FactorySegment(
        FactoryLocation.APP_PROPERTY, 2, 60, 0, 4, b"\x11\x22\x33\x44", None
    ),
    FactorySegment(FactoryLocation.MEMORY, 0, 0, 0x4000, 40, bytes(range(40)), None),
)


def _runner(
    controls: list[object],
    *,
    properties: tuple[PropertyValue, ...] = (),
    segments: tuple[MemorySegment, ...] = (),
    stamp: FactoryStamp | None = _PRODUCT_STAMP,
    shows: bytes = _STAMP.data,
    application_id: str | None = None,
) -> tuple[LoadProcedureRunner, FakeDevice]:
    device = FakeDevice(object_types={0: 0})
    device.properties[6, 53] = shows
    device.property_element_sizes[6, 53] = 2
    device.properties[8, 60] = b"ABCD"
    device.property_element_sizes[8, 60] = 1
    application = _application(LdCtrlConnect(), *controls)
    if application_id is not None:
        cast(Any, application).id = application_id
    runner = LoadProcedureRunner(
        application,
        DownloadImage(
            segments, properties, factory_image=FactoryImage(_FACTORY, stamp)
        ),
        connection_manager=_FakeManager(device),
        restart_cooldown=0,
    )
    return runner, device


def _write() -> LdCtrlWriteProp:
    return LdCtrlWriteProp(obj_idx=8, prop_id=60, count=4, verify=True)


def _parameters(data: bytes, covered: bytes) -> tuple[PropertyValue, ...]:
    return (PropertyValue(8, 60, 0, data, mask=covered, covered=covered),)


def _property_writes(device: FakeDevice) -> list[bytes]:
    return [
        p.data
        for p in device.sent
        if isinstance(p, PropertyValueWrite) and p.object_index == 8
    ]


async def test_a_property_holding_its_factory_contents_is_not_written() -> None:
    # The parameters write what the factory image holds (an inactive one its base).
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=5, channel_number=0), _write()],
        properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00"),
    )
    await runner.run()
    assert _property_writes(device) == []
    assert device.properties[8, 60] == b"ABCD"


async def test_a_changed_property_is_completed_from_the_factory_contents() -> None:
    # Byte 1 changes; the short gap before it takes the factory contents, the trailing bytes
    # nothing covers keep the device's value.
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=5, channel_number=0), _write()],
        properties=_parameters(b"\x00\x99\x00\x00", b"\x00\xff\x00\x00"),
    )
    await runner.run()
    assert _property_writes(device) == [b"\x11\x99CD"]


async def test_a_partly_covered_byte_takes_the_rest_from_the_factory() -> None:
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=5, channel_number=0), _write()],
        properties=_parameters(b"\x0f\x22\x33\x44", b"\x0f\xff\xff\xff"),
    )
    await runner.run()
    # Low nibble from the parameter, high nibble from the factory contents (0x11).
    assert _property_writes(device) == [b"\x1f\x22\x33\x44"]


@pytest.mark.parametrize(
    ("erase_code", "shows", "stamp"),
    [
        # Erase code 6 resets the links only.
        (6, _STAMP.data, FactoryStamp(properties=(_STAMP,))),
        # The device does not show the stamp.
        (5, b"\x00\x00\x00\x00", FactoryStamp(properties=(_STAMP,))),
    ],
)
async def test_without_the_factory_state_everything_is_written(
    erase_code: int, shows: bytes, stamp: FactoryStamp
) -> None:
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=erase_code, channel_number=0), _write()],
        properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00"),
        stamp=stamp,
        shows=shows,
    )
    await runner.run()
    assert _property_writes(device) == [b"\x11\x22CD"]


async def test_a_product_without_a_stamp_is_taken_at_its_word() -> None:
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=7, channel_number=0), _write()],
        properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00"),
        stamp=None,
        shows=b"",
    )
    await runner.run()
    assert _property_writes(device) == []


async def test_a_function_property_master_reset_leaves_out_the_factory_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A listed Hager application resets by a function property command and does not restart;
    # the factory stamp is checked all the same.
    async def sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("xknxeditor.download.procedure.asyncio.sleep", sleep)
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=5, channel_number=0), _write()],
        properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00"),
        application_id="M-0009_A-501C-10-CBD9",
    )
    await runner.run()
    assert any(isinstance(p, FunctionPropertyCommand) for p in device.sent)
    assert device.master_reset is None
    assert _property_writes(device) == []


async def test_without_a_master_reset_everything_is_written() -> None:
    runner, device = _runner(
        [_write()], properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00")
    )
    await runner.run()
    assert _property_writes(device) == [b"\x11\x22CD"]


@pytest.mark.parametrize(
    ("blocks", "matches"),
    [
        # CRC-protected (bit 0 of octet 4 clear) with the stamped CRC, big-endian.
        (b"\x00\x00\x01\x00\x00\x00\x12\x34", True),
        (b"\x00\x00\x01\x00\x00\x00\x12\x35", False),
        # Not CRC-protected while the stamp says it is.
        (b"\x00\x00\x01\x00\x01\x00\x12\x34", False),
    ],
)
async def test_the_stamp_of_object_crcs(blocks: bytes, matches: bool) -> None:
    runner, device = _runner(
        [LdCtrlMasterReset(erase_code=5, channel_number=0), _write()],
        properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00"),
        stamp=FactoryStamp(objects=(ObjectStamp(4, ((True, 0x1234),)),)),
    )
    device.properties[4, 27] = blocks
    device.property_element_sizes[4, 27] = 8
    await runner.run()
    assert _property_writes(device) == ([] if matches else [b"\x11\x22CD"])


async def test_the_preflight_leaves_out_what_the_factory_image_holds() -> None:
    runner, _ = _runner(
        [LdCtrlMasterReset(erase_code=5, channel_number=0), _write()],
        properties=_parameters(b"\x11\x22\x00\x00", b"\xff\xff\x00\x00"),
    )
    report = await runner.preflight()
    assert list(report.properties) == []


async def test_memory_runs_holding_the_factory_contents_are_left_out() -> None:
    # 40 bytes from 0x4000: only bytes 18 and 19 differ from the factory image.
    data = bytearray(range(40))
    data[18:20] = b"\xaa\xbb"
    runner, device = _runner(
        [
            LdCtrlMasterReset(erase_code=5, channel_number=0),
            LdCtrlWriteMem(address=0x4000, size=40, verify=False),
        ],
        segments=(MemorySegment(0x4000, bytes(data)),),
    )
    runner.scope = DownloadScope.FULL
    await runner.run()
    writes = [(p.address, p.data) for p in device.sent if isinstance(p, MemoryWrite)]
    assert writes == [(0x4012, b"\xaa\xbb")]


@pytest.mark.parametrize(
    ("data", "held", "kept"),
    [
        # Equal at the start and the end: left out.
        (b"aaXXaa", b"aaYYaa", "..XX.."),
        # A short equal run inside stays (fewer frames than splitting).
        (b"XaaX", b"YaaY", "XaaX"),
        # A long equal run inside is left out.
        (b"XaaaX", b"YaaaY", "X...X"),
    ],
)
def test_runs_of_factory_contents(data: bytes, held: bytes, kept: str) -> None:
    buffer, written = bytearray(data), bytearray(b"\x01" * len(data))
    _leave_factory_runs(buffer, written, 0, held, 3)
    assert (
        "".join(chr(b) if w else "." for b, w in zip(data, written, strict=True))
        == kept
    )
