"""A System 300 application that does not name its configuration takes it from the device's
management descriptor (PID 72), as the mask's procedures and resources are the configuration's
(e.g. the Unload procedure)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

from xknxeditor.download.merge import (
    device_mask_version,
    learn_device_mask,
    system300_mask_unresolved,
)

from .conftest import FakeDevice

download_module = importlib.import_module("xknxeditor.download.download")


def _application(app_id: str, mask: str = "MV-0300") -> Any:
    return cast(
        Any,
        SimpleNamespace(
            id=app_id,
            program=SimpleNamespace(mask_version=mask),
            load_procedures=SimpleNamespace(load_procedure=[]),
        ),
    )


def test_the_management_descriptor_completes_the_mask() -> None:
    application = _application("M-00FD_A-TEST-01-0001")
    assert system300_mask_unresolved(application)
    learn_device_mask(application, bytes.fromhex("01000001000000000000"))
    assert device_mask_version(application) == "MV-0300-01000001000000000000"
    assert not system300_mask_unresolved(application)
    # Other masks and an empty descriptor are left alone.
    other = _application("M-00FD_A-TEST-02-0001", "MV-07B0")
    learn_device_mask(other, bytes.fromhex("0100"))
    assert device_mask_version(other) == "MV-07B0"
    empty = _application("M-00FD_A-TEST-03-0001")
    learn_device_mask(empty, b"")
    assert system300_mask_unresolved(empty)


class _Manager:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device

    async def open(self) -> FakeDevice:
        return self.device

    async def close(self) -> None:
        return None


async def test_a_download_reads_the_descriptor_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeDevice(descriptor=0x0300)
    device.properties[0, 72] = bytes.fromhex("01000000000000000000")
    monkeypatch.setattr(
        download_module, "_connection_manager", lambda *args: _Manager(device)
    )
    application = _application("M-00FD_A-TEST-04-0001")
    await download_module._learn_device_mask(  # pyright: ignore[reportPrivateUsage]
        object(), "1.1.1", None, application
    )
    assert device_mask_version(application) == "MV-0300-01000000000000000000"
    # Known now: nothing more is read.
    device.sent.clear()
    await download_module._learn_device_mask(  # pyright: ignore[reportPrivateUsage]
        object(), "1.1.1", None, application
    )
    assert not device.sent
