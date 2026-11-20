"""Starting an operation on a real connection object, and what is left when the start fails."""

from __future__ import annotations

import asyncio
import threading
import warnings
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from xknx import XKNX
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.project.service import RouteCoupler
from xknxeditor.download.communication_path import communication_paths
from xknxeditor.download.scope import DownloadScope

_DEVICE = SimpleNamespace(
    individual_address="1.2.20",
    name="Sensor",
    download_plugin_required=False,
    factory_image=None,
    second_application=None,
)


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """The bus thread's loop."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def _service(loop: asyncio.AbstractEventLoop) -> tuple[ConnectionService, XKNX]:
    # A real XKNX: it takes no weak reference and no attribute it has no slot for.
    xknx = XKNX()
    xknx.current_address = IndividualAddress("1.1.250")
    service = ConnectionService()
    service.set_logger(MagicMock())
    service.set_connection(xknx, loop)
    service.project_device = lambda address: None
    service.project_route_couplers = lambda: [
        RouteCoupler("1.2.0", "MT-0", "MT-2", False)
    ]
    return service, xknx


def test_an_operation_runs_with_the_couplers_of_the_project(
    loop: asyncio.AbstractEventLoop,
) -> None:
    service, xknx = _service(loop)

    async def operation() -> str:
        return "done"

    future = service.run_async(operation())
    assert future is not None
    assert future.result(5) == "done"
    paths = communication_paths(xknx)
    assert paths is not None
    assert IndividualAddress("1.2.0") in paths.installation.couplers
    # the paths end with the connection
    service.set_connection(None, None)
    assert communication_paths(xknx) is None


def test_a_device_info_reaches_the_bus(
    loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xknx.management import management
    from xknx.telegram import Telegram, tpci

    monkeypatch.setattr(management, "MANAGEMENT_ACK_TIMEOUT", 0.005)
    monkeypatch.setattr(management, "MANAGEMENT_CONNECTION_TIMEOUT", 0.01)
    monkeypatch.setattr(management, "MANAGEMENT_MAX_REPETITIONS", 1)
    service, xknx = _service(loop)
    sent: list[Telegram] = []

    async def send_telegram(telegram: Telegram) -> None:
        # the device does not answer
        sent.append(telegram)

    xknx.cemi_handler = cast(Any, SimpleNamespace(send_telegram=send_telegram))
    monkeypatch.setattr(service, "_security_for", lambda device: None)
    future = service.device_info(
        cast(Any, _DEVICE),
        group_communication=False,
        resolve_application=lambda app_id: None,
    )
    assert future is not None
    with pytest.raises(Exception) as failure:
        future.result(30)
    assert not isinstance(failure.value, TypeError)
    # the whole way down to the bus, and the bus is free afterwards
    assert any(
        isinstance(telegram.tpci, tpci.TConnect)
        and str(telegram.destination_address) == "1.2.20"
        for telegram in sent
    )
    assert service.busy_operation is None


def test_an_operation_that_cannot_be_prepared_is_not_started(
    loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = _service(loop)

    def broken() -> None:
        raise TypeError("no paths")

    monkeypatch.setattr(service, "_update_installation", broken)
    ran: list[bool] = []

    async def operation() -> None:
        ran.append(True)

    with warnings.catch_warnings():
        # the coroutine is closed, not left unawaited
        warnings.simplefilter("error", RuntimeWarning)
        assert service.run_async(operation()) is None
    assert ran == []
    service.evaluate_device(cast(Any, _DEVICE), DownloadScope.FULL)
    # the bus is free again for the next one
    assert service.busy_operation is None
    assert service.begin_operation("test", "1.2.20")


def test_an_operation_that_fails_to_start_frees_the_bus(
    loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = _service(loop)

    def no_keys(device: object) -> None:
        raise TypeError("cannot read the keys")

    monkeypatch.setattr(service, "_security_for", no_keys)
    with pytest.raises(TypeError, match="cannot read the keys"):
        service.evaluate_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert service.busy_operation is None
    (ended,) = service.take_ended_operations()
    assert (ended.kind, ended.address, ended.outcome) == ("test", "1.2.20", "failed")
    assert ended.message == "cannot read the keys"
    # an operation already running is not ended by one that raises before it gets the bus
    assert service.begin_operation("program", "1.2.21")
    monkeypatch.setattr(service, "not_connected", no_keys)
    with pytest.raises(TypeError):
        service.evaluate_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert service.busy_operation == ("program", "1.2.21")
    assert service.take_ended_operations() == []
