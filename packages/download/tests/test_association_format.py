"""The System B association table follows the format the device reports."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from xknxeditor.download.image import DownloadImage, RelativeSegment
from xknxeditor.download.programmer import PID_TABLE, DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.download.tables import Association
from xknxeditor.download.tables_systemb import build_association_table_b

from .conftest import FakeDevice

download_module = importlib.import_module("xknxeditor.download.download")

_ASSOCIATIONS = (
    Association(group_address_index=0, group_object_number=1),
    Association(group_address_index=1, group_object_number=2),
)
_NARROW = bytes.fromhex("0002 0101 0202".replace(" ", ""))
_WIDE = bytes.fromhex("0002 0001 0001 0002 0002".replace(" ", ""))


def _image() -> DownloadImage:
    return DownloadImage(
        segments=(),
        properties=(),
        relative_segments=(
            RelativeSegment(1, b"\x00\x00"),
            RelativeSegment(2, _NARROW, b"\xff" * 6, associations=_ASSOCIATIONS),
        ),
    )


def _device(width: int) -> FakeDevice:
    device = FakeDevice(object_types={0: 0, 1: 1, 2: 2}, descriptor=0x07B0)
    device.property_element_sizes[(2, PID_TABLE)] = width
    return device


def test_image_is_reencoded_in_the_requested_format() -> None:
    image = _image()
    assert image.has_association_table
    assert image.with_association_table_format(False) is image
    wide = image.with_association_table_format(True)
    segment = wide.relative_segment(2)
    assert segment is not None
    assert segment.data == _WIDE
    assert segment.mask == b"\xff" * len(_WIDE)
    assert wide.relative_segment(1) is image.relative_segment(1)


def test_wide_entry_carries_object_number_above_255() -> None:
    data = build_association_table_b(
        [Association(group_address_index=0, group_object_number=406)], wide=True
    )
    assert data == bytes.fromhex("0001 0001 0196".replace(" ", ""))


@pytest.mark.parametrize(("width", "wide"), [(2, False), (4, True), (1, None)])
async def test_programmer_reads_the_association_table_format(
    width: int, wide: bool | None
) -> None:
    programmer = DeviceProgrammer(_device(width))
    assert await programmer.read_association_table_wide() is wide


class _Manager:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device
        self.closed = False

    async def open(self) -> FakeDevice:
        return self.device

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (DownloadScope.FULL, _WIDE),
        (DownloadScope.GROUP_COMMUNICATION, _WIDE),
        (DownloadScope.PARAMETERS, _NARROW),
    ],
)
async def test_download_uses_the_device_format(
    monkeypatch: pytest.MonkeyPatch, scope: DownloadScope, expected: bytes
) -> None:
    manager = _Manager(_device(4))

    def connection_manager(*_args: Any) -> _Manager:
        return manager

    monkeypatch.setattr(download_module, "_connection_manager", connection_manager)
    image = await download_module._device_association_format(  # pyright: ignore[reportPrivateUsage]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,
        _image(),
        scope,
    )
    segment = image.relative_segment(2)
    assert segment is not None
    assert segment.data == expected
    assert manager.closed is (scope is not DownloadScope.PARAMETERS)
