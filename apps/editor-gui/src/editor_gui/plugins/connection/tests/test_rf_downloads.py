"""Downloads to devices on KNX RF lines: the interface gets the line's domain address first, and a
new device gets it with its individual address."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from xknx.exceptions import CommunicationError
from xknx.telegram import IndividualAddress

import editor_gui.programming as programming
import xknxeditor.download.commissioning as commissioning
from editor_gui.plugins.connection import service as service_module
from editor_gui.plugins.connection import usb
from editor_gui.plugins.connection.service import ConnectionService
from xknxeditor.download.scope import DownloadScope

DOMAIN_ADDRESS = bytes.fromhex("00fa12345678")
_DEVICE = SimpleNamespace(
    individual_address="1.2.20",
    name="Sensor",
    download_plugin_required=False,
    factory_image=None,
    second_application=None,
)


def _service(
    monkeypatch: pytest.MonkeyPatch, domain: bytes | None
) -> tuple[ConnectionService, list[Any], list[bytes]]:
    calls: list[Any] = []
    ensured: list[bytes] = []

    async def capture(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    async def ensure(xknx: object, domain_address: bytes) -> bool:
        ensured.append(domain_address)
        return True

    def run_now(coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(asyncio.run(coro))
        return future

    monkeypatch.setattr(programming, "download_device", capture)
    monkeypatch.setattr(commissioning, "download_individual_address", capture)
    monkeypatch.setattr(usb, "ensure_rf_domain_address", ensure)
    service = ConnectionService()
    service._xknx = MagicMock(current_address=IndividualAddress("1.2.250"))  # pyright: ignore[reportPrivateUsage]
    service.set_logger(MagicMock())
    service.project_rf_domain_address = lambda device: domain
    monkeypatch.setattr(service, "run_async", run_now)
    return service, calls, ensured


def test_a_download_gets_the_lines_domain_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, calls, _ = _service(monkeypatch, DOMAIN_ADDRESS)
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    (kwargs,) = calls
    assert kwargs["rf_domain_address"] == DOMAIN_ADDRESS


@pytest.mark.parametrize("domain", [DOMAIN_ADDRESS, None])
def test_an_address_download_gets_the_lines_domain_address(
    monkeypatch: pytest.MonkeyPatch, domain: bytes | None
) -> None:
    service, calls, ensured = _service(monkeypatch, domain)
    service.program_device(cast(Any, _DEVICE), DownloadScope.INDIVIDUAL_ADDRESS)
    (kwargs,) = calls
    assert kwargs["domain_address"] == domain
    # the interface gets it before the address is written
    assert ensured == ([domain] if domain is not None else [])


def test_without_a_project_there_is_no_domain_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, calls, _ = _service(monkeypatch, DOMAIN_ADDRESS)
    service.project_rf_domain_address = None
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert calls[0]["rf_domain_address"] is None


async def test_the_operation_is_dropped_when_the_interface_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(xknx: object, domain_address: bytes) -> bool:
        raise CommunicationError("refused")

    ran: list[bool] = []

    async def operation() -> None:
        ran.append(True)

    monkeypatch.setattr(usb, "ensure_rf_domain_address", refuse)
    pending = operation()
    with pytest.raises(CommunicationError, match="refused"):
        await service_module._in_rf_domain(  # pyright: ignore[reportPrivateUsage]
            cast(Any, None), DOMAIN_ADDRESS, pending
        )
    assert not ran
    # closed, not left unawaited
    assert pending.cr_frame is None
    await service_module._in_rf_domain(  # pyright: ignore[reportPrivateUsage]
        cast(Any, None), None, operation()
    )
    assert ran == [True]


async def test_download_device_sets_the_domain_address_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def ensure(xknx: object, domain_address: bytes) -> bool:
        order.append(f"ensure {domain_address.hex()}")
        return True

    async def download(*args: Any, **kwargs: Any) -> object:
        order.append(f"download {kwargs['commission_domain_address']!r}")
        return SimpleNamespace()

    async def no_route(xknx: object, couplers: object, limit: int | None) -> int | None:
        return limit

    monkeypatch.setattr(usb, "ensure_rf_domain_address", ensure)
    monkeypatch.setattr(programming, "download", download)
    monkeypatch.setattr(programming, "_route_limited", no_route)
    monkeypatch.setattr(programming, "_image_for", lambda *args: None)
    monkeypatch.setattr(programming, "_application", lambda device: None)
    monkeypatch.setattr(programming, "_address", lambda device: "1.2.20")
    from editor_gui import plugin_providers

    monkeypatch.setattr(plugin_providers, "programming_error", lambda device: None)
    monkeypatch.setattr(plugin_providers, "prepared_for", lambda *args: None)
    await programming.download_device(
        cast(Any, None), cast(Any, _DEVICE), rf_domain_address=DOMAIN_ADDRESS
    )
    await programming.download_device(cast(Any, None), cast(Any, _DEVICE))
    assert order == [
        "ensure 00fa12345678",
        f"download {DOMAIN_ADDRESS!r}",
        "download None",
    ]


class _Coupler:
    """A coupler device of the project."""

    individual_address = "1.6.0"


@pytest.mark.parametrize("rf_connection", [False, True])
def test_the_projects_couplers_reach_the_connection(
    monkeypatch: pytest.MonkeyPatch, rf_connection: bool
) -> None:
    from editor_gui import device as device_module
    from xknxeditor.download.communication_path import (
        KnxMedium,
        communication_paths,
    )

    service, _calls, _ = _service(monkeypatch, DOMAIN_ADDRESS)
    monkeypatch.setattr(usb, "is_rf_usb_connection", lambda xknx: rf_connection)
    monkeypatch.setattr(device_module, "Device", _Coupler)
    service.project_route_couplers = lambda: [
        ("1.2.0", "MT-0", "MT-2", False),
        ("1.6.0", "MT-5", "MT-0", True),
    ]
    coupler = _Coupler()
    service.project_device = lambda address: coupler if address == "1.6.0" else None
    monkeypatch.setattr(service, "_security_for", lambda device: f"keys of {device}")
    service.project_access_key = lambda: 0x01020304
    installation = service._installation()  # pyright: ignore[reportPrivateUsage]
    media = installation.couplers[IndividualAddress("1.2.0")]
    assert (media.primary, media.secondary) == (KnxMedium.TP, KnxMedium.RF)
    assert media.security is None
    assert not media.block_unicast
    secure = installation.couplers[IndividualAddress("1.6.0")]
    assert secure.primary is KnxMedium.IP
    assert secure.block_unicast and secure.block_broadcast
    # reached with the coupler's own keys, and the project's access key
    assert secure.security == f"keys of {coupler}"
    assert secure.access_key == 0x01020304
    assert installation.rf_connection is rf_connection
    # given to the connection before an operation
    service._update_installation()  # pyright: ignore[reportPrivateUsage]
    paths = communication_paths(cast(Any, service._xknx))  # pyright: ignore[reportPrivateUsage]
    assert paths is not None
    assert paths.installation == installation


async def test_the_couplers_are_not_read_on_the_bus_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xknxeditor.download.communication_path import communication_paths

    service, _calls, _ = _service(monkeypatch, DOMAIN_ADDRESS)
    service._xknx = MagicMock(current_address=IndividualAddress("1.2.250"))  # pyright: ignore[reportPrivateUsage]
    read: list[bool] = []

    def couplers() -> list[tuple[str, str, str, bool]]:
        read.append(True)
        return []

    service.project_route_couplers = couplers
    service._loop = asyncio.get_running_loop()  # pyright: ignore[reportPrivateUsage]
    service._update_installation()  # pyright: ignore[reportPrivateUsage]
    assert read == []
    assert communication_paths(cast(Any, service._xknx)) is None  # pyright: ignore[reportPrivateUsage]
    # without a connection or a project nothing is read
    service._loop = None  # pyright: ignore[reportPrivateUsage]
    service._xknx = None  # pyright: ignore[reportPrivateUsage]
    service._update_installation()  # pyright: ignore[reportPrivateUsage]
    assert read == []


def test_an_rf_interface_on_a_domain_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, calls, ensured = _service(monkeypatch, DOMAIN_ADDRESS)
    monkeypatch.setattr(usb, "is_rf_usb_connection", lambda xknx: True)
    service._xknx = MagicMock(current_address=IndividualAddress("1.6.250"))  # pyright: ignore[reportPrivateUsage]
    service.project_device = lambda address: None
    service.project_route_couplers = lambda: [
        ("1.2.0", "MT-0", "MT-2", False),
        ("1.6.0", "MT-0", "MT-2", False),
    ]
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    service.program_device(cast(Any, _DEVICE), DownloadScope.INDIVIDUAL_ADDRESS)
    # it keeps its own domain address: its coupler routes to the device
    assert calls[0]["rf_interface_domain"] is False
    assert ensured == []
    # on the device's own line it takes the device's
    service._xknx = MagicMock(current_address=IndividualAddress("1.2.250"))  # pyright: ignore[reportPrivateUsage]
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert calls[2]["rf_interface_domain"] is True
    # not an RF device: nothing to take
    service.project_rf_domain_address = lambda device: None
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert calls[3]["rf_interface_domain"] is False
    device = SimpleNamespace(**{**vars(_DEVICE), "individual_address": ""})
    service.project_rf_domain_address = lambda device: DOMAIN_ADDRESS
    assert (
        service._interface_domain_address(cast(Any, device))  # pyright: ignore[reportPrivateUsage]
        is None
    )


async def test_download_device_keeps_a_local_interfaces_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensured: list[bytes] = []

    async def ensure(xknx: object, domain_address: bytes) -> bool:
        ensured.append(domain_address)
        return True

    async def download(*args: Any, **kwargs: Any) -> object:
        return SimpleNamespace()

    async def no_route(xknx: object, couplers: object, limit: int | None) -> int | None:
        return limit

    monkeypatch.setattr(usb, "ensure_rf_domain_address", ensure)
    monkeypatch.setattr(programming, "download", download)
    monkeypatch.setattr(programming, "_route_limited", no_route)
    monkeypatch.setattr(programming, "_image_for", lambda *args: None)
    monkeypatch.setattr(programming, "_application", lambda device: None)
    monkeypatch.setattr(programming, "_address", lambda device: "1.2.20")
    from editor_gui import plugin_providers

    monkeypatch.setattr(plugin_providers, "programming_error", lambda device: None)
    monkeypatch.setattr(plugin_providers, "prepared_for", lambda *args: None)
    await programming.download_device(
        cast(Any, None),
        cast(Any, _DEVICE),
        rf_domain_address=DOMAIN_ADDRESS,
        rf_interface_domain=False,
    )
    assert ensured == []
