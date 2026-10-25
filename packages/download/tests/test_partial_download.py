"""Partial downloads only into a device that runs the projected application."""

from __future__ import annotations

import importlib
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram.apci import PropertyValueRead, PropertyValueResponse

from xknxeditor.download.download import (
    _partial_scope,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.partial import (
    DifferentProgramLoaded,
    FullDownloadDeclined,
    IncompatibleMaskVersion,
    application_id,
    partial_download_mismatch,
)
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.resources import MaskResources
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.application_program_static_t_options import (
    ApplicationProgramStaticOptions,
)
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_location_t import ResourceLocation
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName
from xknxeditor.prod import Application, load

from .conftest import FakeDevice
from .test_wire_audit import _FIXTURE

download_module = importlib.import_module("xknxeditor.download.download")


def _property(index: int, pid: int) -> ResourceLocation:
    return ResourceLocation(
        address_space=ResourceAddrSpace.SYSTEM_PROPERTY,
        interface_object_ref=index,
        property_id=pid,
        start_address=0,
    )


# System B: application on object 4, PEI program on object 5.
_SYSTEM_B = MaskResources(
    _by_name={
        ResourceName.APPLICATION_LOAD_STATUS: _property(4, 5),
        ResourceName.APPLICATION_ID: _property(4, 13),
        ResourceName.PEIPROG_LOAD_STATUS: _property(5, 5),
        ResourceName.PEIPROG_ID: _property(5, 13),
    }
)
# BCU2: the load status is a memory byte.
_BCU2 = MaskResources(
    _by_name={
        ResourceName.APPLICATION_LOAD_STATUS: ResourceLocation(
            address_space=ResourceAddrSpace.STANDARD_MEMORY, start_address=0xB6EC
        ),
        ResourceName.APPLICATION_ID: _property(3, 13),
    },
    _lengths={ResourceName.APPLICATION_LOAD_STATUS: 1},
)


def _application(mask: str = "MV-07B0", **options: bool) -> Application:
    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    program.mask_version = mask
    program.static.options = ApplicationProgramStaticOptions(**options)
    return cast(Application, SimpleNamespace(program=program, manufacturer_id="M-00FA"))


def _device(app: Application, descriptor: int = 0x07B0) -> FakeDevice:
    device = FakeDevice(descriptor=descriptor)
    device.load_states[4] = LoadState.LOADED
    device.properties[4, 13] = application_id(app)
    return device


async def _mismatch(
    device: FakeDevice, app: Application, resources: MaskResources = _SYSTEM_B
) -> str | None:
    return await partial_download_mismatch(DeviceProgrammer(device), app, resources)


async def test_a_device_with_the_application_takes_a_partial_download() -> None:
    app = _application()
    assert await _mismatch(_device(app), app) is None


async def test_an_unloaded_or_other_application_is_reported() -> None:
    app = _application()
    device = _device(app)
    device.load_states[4] = LoadState.UNLOADED
    assert "not loaded: load status=0" in str(await _mismatch(device, app))
    device = _device(app)
    device.properties[4, 13] = b"\x00\xfa\x99\x99\x01"
    assert "different application program" in str(await _mismatch(device, app))


async def test_another_mask_version_stops_the_download() -> None:
    app = _application()
    with pytest.raises(IncompatibleMaskVersion, match="MV-0705 instead of MV-07B0"):
        await _mismatch(_device(app, 0x0705), app)


async def test_memory_load_status() -> None:
    app = _application("MV-0705")
    device = FakeDevice(descriptor=0x0705)
    device.properties[3, 13] = application_id(app)
    device.memory[0xB6EC] = 1
    assert await _mismatch(device, app, _BCU2) is None
    device.memory[0xB6EC] = 2
    assert "load status=2" in str(await _mismatch(device, app, _BCU2))


@pytest.mark.parametrize(
    ("status", "pei_id", "legacy", "accepted"),
    [
        (b"\x00", None, False, True),
        (b"\x01", b"\x00\x00\x00\x00\x00", False, True),
        (b"\x01", b"\x00\xfa\x00\x01\x02", False, False),
        (b"\x02", None, False, False),
        (b"\x02", None, True, True),
    ],
)
async def test_pei_program(
    status: bytes, pei_id: bytes | None, legacy: bool, accepted: bool
) -> None:
    app = _application(legacy_allow_partial_download_if_ap2_mismatch=legacy)
    device = _device(app)
    device.load_states[5] = LoadState(status[0])
    if pei_id is not None:
        device.properties[5, 13] = pei_id
    assert (await _mismatch(device, app) is None) is accepted


async def test_patched_manufacturer_is_accepted() -> None:
    app = _application(legacy_patch_manufacturer_id_in_task_segment=True)
    device = _device(app)
    device.properties[4, 13] = b"\x00\x02" + application_id(app)[2:]
    assert await _mismatch(device, app) is None


def test_four_octet_application_id() -> None:
    app = _application()
    assert application_id(app, 4) == b"\xfa" + application_id(app)[2:]


class _Manager:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device

    async def open(self) -> FakeDevice:
        return self.device

    async def close(self) -> None:
        return None


async def _check(
    monkeypatch: pytest.MonkeyPatch,
    device: FakeDevice,
    app: Application,
    scope: DownloadScope,
    confirm: Any = None,
) -> tuple[DownloadScope, bool]:
    monkeypatch.setattr(
        download_module, "_connection_manager", lambda *args: _Manager(device)
    )
    monkeypatch.setattr(
        "xknxeditor.download.resources.mask_resources", lambda *args: _SYSTEM_B
    )
    return await _partial_scope(
        cast(Any, None), cast(Any, None), None, app, None, scope, confirm
    )


async def _scope(
    monkeypatch: pytest.MonkeyPatch,
    device: FakeDevice,
    app: Application,
    scope: DownloadScope,
    confirm: Any = None,
) -> DownloadScope:
    return (await _check(monkeypatch, device, app, scope, confirm))[0]


async def test_partial_download_into_another_application_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _application()
    device = _device(app)
    device.load_states[4] = LoadState.UNLOADED
    with pytest.raises(DifferentProgramLoaded) as error:
        await _scope(monkeypatch, device, app, DownloadScope.PARAMETERS)
    assert "not loaded" in error.value.reason
    assert await _scope(monkeypatch, _device(app), app, DownloadScope.FULL) is (
        DownloadScope.FULL
    )


async def test_prefer_partial_keeps_the_full_load_for_memory_mapped_lsm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BIM M112 AbsSegment reloads need Unload of LSM 3: the full procedure runs,
    without the code seed when the device has the application loaded."""
    app = _application(
        "MV-0701",
        prefer_partial_download_if_application_loaded=True,
        prompt_before_full_download=True,
    )
    loaded = _device(app, 0x0701)
    assert await _check(monkeypatch, loaded, app, DownloadScope.FULL) == (
        DownloadScope.FULL,
        True,
    )
    other = _device(app, 0x0701)
    other.load_states[4] = LoadState.UNLOADED
    assert await _check(
        monkeypatch, other, app, DownloadScope.FULL, lambda reason: _yes()
    ) == (DownloadScope.FULL, False)
    plain = _application("MV-0701")
    assert await _check(
        monkeypatch, _device(plain, 0x0701), plain, DownloadScope.FULL
    ) == (DownloadScope.FULL, False)


async def _yes() -> bool:
    return True


async def test_prefer_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _application(
        prefer_partial_download_if_application_loaded=True,
        prompt_before_full_download=True,
    )
    assert (
        await _scope(monkeypatch, _device(app), app, DownloadScope.FULL)
        is DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION
    )
    other = _device(app)
    other.load_states[4] = LoadState.UNLOADED
    asked: list[str] = []

    async def confirm(reason: str) -> bool:
        asked.append(reason)
        return len(asked) == 1

    assert (
        await _scope(monkeypatch, other, app, DownloadScope.FULL, confirm)
        is DownloadScope.FULL
    )
    with pytest.raises(FullDownloadDeclined):
        await _scope(monkeypatch, other, app, DownloadScope.FULL, confirm)
    assert len(asked) == 2 and "not loaded" in asked[0]


async def test_resources_the_device_does_not_have_are_skipped() -> None:
    # Many System B devices have no PEI program object: its load state read is rejected.
    class NoPeiDevice(FakeDevice):
        def _handle_property_read(
            self, payload: PropertyValueRead
        ) -> PropertyValueResponse:
            if payload.object_index == 5:
                return PropertyValueResponse(
                    object_index=5,
                    property_id=payload.property_id,
                    count=0,
                    start_index=payload.start_index,
                    data=b"",
                )
            return super()._handle_property_read(payload)

    app = _application()
    device = NoPeiDevice(descriptor=0x07B0)
    device.load_states[4] = LoadState.LOADED
    device.properties[4, 13] = application_id(app)
    assert await _mismatch(device, app) is None
