"""A device's download image starts from its product's factory image (``LoadedImage``) and
carries the stamp of that state (``CheckSums``)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.connection.service import (
    _factory_image,  # pyright: ignore[reportPrivateUsage]
)
from editor_gui.programming import (
    DeviceProgrammingError,
    _image_for,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.catalog.core.hardware import FactoryData
from xknxeditor.download.factory_image import (
    FactoryLocation,
    FactoryStamp,
    PropertyStamp,
)

from .test_delete_product import _project_with_device


def _loaded_image() -> bytes:
    raw = struct.pack("<Ii", 0x00010001, 1)
    raw += struct.pack("<IHHIIB", 4, 1, 60, 0, 2, 1) + b"\x12\x34"
    deflate = zlib.compressobj(wbits=-15)
    return deflate.compress(raw) + deflate.flush()


def test_a_device_reads_its_products_factory_image_once(tmp_path: Path) -> None:
    catalog, project, _ = _project_with_device(tmp_path)
    (device,) = project.devices
    assert device.factory_image is not None
    # The fixture's product has none.
    assert device.factory_image() is None
    assert _factory_image(device) is None

    reads: list[str] = []
    service = cast(Any, catalog)._service

    def get_factory_image(program_id: str) -> FactoryData:
        reads.append(program_id)
        return FactoryData(b"image", b"stamp")

    service.get_factory_image = get_factory_image
    cast(Any, catalog)._factory_images.clear()
    assert catalog.factory_image("HP-1") == FactoryData(b"image", b"stamp")
    assert catalog.factory_image("HP-1") == FactoryData(b"image", b"stamp")
    assert reads == ["HP-1"]


def test_a_device_without_a_source_has_no_factory_image() -> None:
    assert _factory_image(cast(Any, SimpleNamespace(factory_image=None))) is None
    assert _factory_image(
        cast(Any, SimpleNamespace(factory_image=lambda: FactoryData(b"x")))
    ) == FactoryData(b"x")


def test_the_download_image_carries_the_factory_image(tmp_path: Path) -> None:
    _, project, _ = _project_with_device(tmp_path)
    (device,) = project.devices
    assert _image_for(device).factory_image is None
    image = _image_for(device, factory_image=FactoryData(_loaded_image())).factory_image
    assert image is not None
    assert image.stamp is None
    assert image.property_data(1, None, 0, 60, None) == b"\x12\x34"
    with pytest.raises(DeviceProgrammingError, match="factory image is invalid"):
        _image_for(device, factory_image=FactoryData(b"\x00\x01"))


def test_the_download_image_carries_the_factory_stamp(tmp_path: Path) -> None:
    _, project, _ = _project_with_device(tmp_path)
    (device,) = project.devices
    # One property value: application property 0, PID 53, from address 0, 4 octets.
    stamp = struct.pack("<IIi", 0x00010001, 3, 1)
    stamp += struct.pack("<IHHIi", 5, 0, 53, 0, 4) + b"\xfe\xa5\xfa\x4e"
    image = _image_for(
        device, factory_image=FactoryData(_loaded_image(), stamp)
    ).factory_image
    assert image is not None
    assert image.stamp == FactoryStamp(
        properties=(
            PropertyStamp(FactoryLocation.APP_PROPERTY, 0, 53, 0, b"\xfe\xa5\xfa\x4e"),
        )
    )
    with pytest.raises(DeviceProgrammingError, match="factory image is invalid"):
        _image_for(device, factory_image=FactoryData(_loaded_image(), stamp[:-1]))


def test_a_device_without_a_dynamic_ui_gets_an_image(tmp_path: Path) -> None:
    # An application without parameters (a USB interface) has no dynamic UI; its procedure
    # still loads it.
    _, project, _ = _project_with_device(tmp_path)
    (device,) = project.devices
    bare = cast(Any, SimpleNamespace(app=device.app, dynamic_ui=None))
    image = _image_for(bare)
    assert image.factory_image is None
    with pytest.raises(DeviceProgrammingError, match="no application program"):
        _image_for(cast(Any, SimpleNamespace(app=None, dynamic_ui=None)))
