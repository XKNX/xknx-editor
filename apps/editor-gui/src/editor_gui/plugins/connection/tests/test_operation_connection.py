"""An online operation that finds no open connection connects through the backbone's Bus
Connection, runs, and closes it again; when there is none, or it is not available, the user
selects a connection ("Select Connection"), or cancels the operation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui
from xknx.io.connection import ConnectionType

from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import (
    ConnectionPlugin,
    ConnectionState,
    join_endpoint,
    split_endpoint,
    tunnel_host,
)
from editor_gui.plugins.connection.service import (
    ConnectionService,
    OperationNotStarted,
)
from editor_gui.plugins.connection.strings import S
from editor_gui.plugins.project.tests._clicks import Clicks

_BUS = 'Type=IpTunneling;HostAddress=192.168.16.88;ProtocolType=Udp;Name="IP-Interface"'


class _Connector:
    """An OperationConnector whose connection the test opens."""

    def __init__(self) -> None:
        self.opening: Future[None] = Future()
        self.users = 0
        self.opened = False

    def open(self) -> Future[None]:
        self.users += 1
        return self.opening

    def release(self) -> None:
        self.users -= 1

    def opened_for_operations(self) -> bool:
        return self.opened

    def submit(self, fn: Callable[[], None]) -> None:
        fn()


def _service() -> tuple[ConnectionService, _Connector]:
    service = ConnectionService()
    service.set_logger(cast(Any, SimpleNamespace(error=lambda *_a, **_k: None)))
    connector = _Connector()
    service.operation_connector = connector
    return service, connector


def test_the_operation_runs_once_connected_and_releases_the_connection() -> None:
    service, connector = _service()
    operation: Future[Any] = Future()
    started: list[int] = []

    def start() -> Future[Any]:
        started.append(1)
        return operation

    result = service.connect_for_operation("program_device", start)
    assert result is not None and not result.done() and started == []
    assert connector.users == 1
    connector.opening.set_result(None)
    assert started == [1] and connector.users == 1
    operation.set_result("done")
    assert result.result() == "done" and connector.users == 0


def test_a_canceled_selection_cancels_the_operation() -> None:
    service, connector = _service()
    result = service.connect_for_operation("program_device", lambda: None)
    assert result is not None
    connector.opening.cancel()
    with pytest.raises(CancelledError):
        result.result()
    assert connector.users == 0


def test_an_operation_that_does_not_start_or_fails() -> None:
    service, connector = _service()
    result = service.connect_for_operation("program_device", lambda: None)
    assert result is not None
    connector.opening.set_result(None)
    with pytest.raises(OperationNotStarted):
        result.result()
    service, connector = _service()
    failing: Future[Any] = Future()
    result = service.connect_for_operation("device_info", lambda: failing)
    assert result is not None
    connector.opening.set_result(None)
    failing.set_exception(ValueError("no answer"))
    with pytest.raises(ValueError, match="no answer"):
        result.result()
    assert connector.users == 0
    # A connection that could not be opened.
    service, connector = _service()
    result = service.connect_for_operation("device_info", lambda: failing)
    assert result is not None
    connector.opening.set_exception(ConnectionError("unreachable"))
    with pytest.raises(ConnectionError):
        result.result()


def test_cancel_stops_the_wait_for_the_connection_or_the_operation() -> None:
    service, connector = _service()
    result = service.connect_for_operation("program_device", lambda: Future())
    assert result is not None
    service.cancel_operation(result)
    assert result.cancelled()
    connector.opening.set_result(None)
    assert connector.users == 0
    service, connector = _service()
    operation: Future[Any] = Future()
    result = service.connect_for_operation("program_device", lambda: operation)
    assert result is not None
    connector.opening.set_result(None)
    service.cancel_operation(result)
    assert operation.cancelled() and result.cancelled()


def test_operations_connect_while_none_is_open() -> None:
    service, connector = _service()
    assert service.can_reach_bus()
    device = cast(Any, SimpleNamespace(individual_address="1.1.5", name="Dimmer"))
    result = service.restart_device(device)
    assert isinstance(result, Future) and connector.users == 1
    # Without a connector an operation is refused as before.
    plain = ConnectionService()
    plain.set_logger(cast(Any, SimpleNamespace(error=lambda *_a, **_k: None)))
    assert not plain.can_reach_bus()
    assert plain.restart_device(device) is None
    assert plain.not_connected_notice()


def test_the_host_of_a_bus_connection() -> None:
    assert tunnel_host(_BUS) == "192.168.16.88"
    assert tunnel_host("Type=Usb;Name=USB") is None
    assert tunnel_host("Type=IpTunneling") is None
    # Another port than 3671 is kept with the host.
    assert tunnel_host("Type=IpTunneling;HostAddress=10.0.0.5;Port=3700") == (
        "10.0.0.5:3700"
    )


def test_an_interface_address_with_its_port() -> None:
    assert split_endpoint("192.168.16.88") == ("192.168.16.88", 3671)
    assert split_endpoint(" 192.168.16.88:3700 ") == ("192.168.16.88", 3700)
    assert split_endpoint("knx.local:3671") == ("knx.local", 3671)
    # Not a port: the address as it is.
    assert split_endpoint("10.0.0.1:abc") == ("10.0.0.1:abc", 3671)
    assert split_endpoint("10.0.0.1:70000") == ("10.0.0.1:70000", 3671)
    assert split_endpoint("fe80::1") == ("fe80::1", 3671)
    assert join_endpoint("10.0.0.5", 3671) == "10.0.0.5"
    assert join_endpoint("10.0.0.5", 3700) == "10.0.0.5:3700"


def test_the_connection_goes_to_the_port_of_the_interface() -> None:
    plugin = _plugin(("Type=IpTunneling;HostAddress=10.0.0.5;Port=3700", "Remote"))
    plugin._multicast_group = "224.0.23.12"
    plugin.open_for_operation()
    assert plugin._controller_ip == "10.0.0.5:3700"
    config = plugin._connection_config(None)
    assert (config.gateway_ip, config.gateway_port) == ("10.0.0.5", 3700)
    plugin._gateway_info = None
    link = plugin._link_info()
    assert (link.host, link.port) == ("10.0.0.5", 3700)


def _plugin(bus: tuple[str, str] | None) -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    log = SimpleNamespace(info=lambda *_a, **_k: None, error=lambda *_a, **_k: None)
    plugin._api = SimpleNamespace(
        project=SimpleNamespace(is_open=True, backbone_bus_connection=lambda: bus),
        connection=ConnectionService(),
        main_thread=None,
    )
    plugin._log = log
    plugin._state = ConnectionState.DISCONNECTED
    plugin._error_message = None
    plugin._controller_ip = "10.0.0.1"
    plugin._connection_type = ConnectionType.ROUTING
    plugin._multicast_group = "224.0.23.12"
    plugin._selected_gateway = None
    plugin._session = 0
    plugin._retry_on_credentials = False
    plugin._scanning = False
    plugin._gateways = []
    plugin._operation_users = 0
    plugin._operation_open = False
    plugin._operation_pending = None
    plugin._operation_restore = None
    plugin._operation_name = ""
    plugin._select_open = False
    plugin._select_shown = False
    plugin._select_failures = []
    plugin._select_ip = ""
    plugin.started = []

    def run_async(coro: Coroutine[Any, Any, None]) -> None:
        plugin.started.append(coro.cr_code.co_name)
        coro.close()

    plugin._run_async = run_async
    plugin.scan = lambda: None
    return plugin


def test_the_bus_connection_is_opened_and_closed_for_the_operations() -> None:
    plugin = _plugin((_BUS, "IP-Interface (192.168.16.88:3671 Udp)"))
    pending = plugin.open_for_operation()
    assert plugin.started == ["_connect_for_operation_async"]
    assert (plugin._controller_ip, plugin._connection_type) == (
        "192.168.16.88",
        ConnectionType.TUNNELING,
    )
    # A second operation waits for the same connection.
    assert plugin.open_for_operation() is pending
    plugin._state = ConnectionState.CONNECTED
    plugin._operation_connected()
    assert pending.done() and plugin.opened_for_operations
    plugin.release_operation()
    assert plugin.started == ["_connect_for_operation_async"]
    plugin.release_operation()
    assert plugin.started[-1] == "_close_for_operations"
    assert plugin._state == ConnectionState.DISCONNECTING
    # The connection the user chose comes back.
    plugin._restore_connection_settings()
    assert (plugin._controller_ip, plugin._connection_type) == (
        "10.0.0.1",
        ConnectionType.ROUTING,
    )


def test_the_connection_opens_and_closes_on_the_bus() -> None:
    """What the connection does in the background: once open, the waiting operations go on;
    once closed, the connection the user chose in the Gateway menu returns."""
    plugin = _plugin((_BUS, "IP-Interface"))
    calls: list[str] = []

    async def connect(reopen: bool = False) -> None:
        calls.append("connect")
        plugin._state = ConnectionState.CONNECTED

    async def disconnect(reopen: bool = False) -> None:
        calls.append("disconnect")
        plugin._state = ConnectionState.DISCONNECTED

    plugin._connect_async = connect
    plugin._disconnect_async = disconnect
    pending = plugin.open_for_operation()
    asyncio.run(plugin._connect_for_operation_async())
    assert pending.done() and plugin.opened_for_operations
    assert plugin._controller_ip == "192.168.16.88"
    plugin.release_operation()
    asyncio.run(plugin._close_for_operations())
    assert calls == ["connect", "disconnect"]
    assert (plugin._controller_ip, plugin._connection_type) == (
        "10.0.0.1",
        ConnectionType.ROUTING,
    )
    assert not plugin.opened_for_operations


def test_the_main_thread_runs_what_the_connection_hands_over() -> None:
    plugin = _plugin(None)
    ran: list[str] = []
    plugin.on_main_thread(lambda: ran.append("at once"))
    with ThreadPoolExecutor(max_workers=1) as main_thread:
        plugin._api.main_thread = main_thread
        plugin.on_main_thread(lambda: ran.append("handed over"))
    assert ran == ["at once", "handed over"]


def test_the_plugin_is_the_connector_of_the_operations() -> None:
    plugin = _plugin(None)
    connector = plugin_module._OperationConnector(plugin)  # pyright: ignore[reportPrivateUsage]
    pending = connector.open()
    assert plugin._operation_users == 1 and plugin._select_open
    assert not connector.opened_for_operations()
    ran: list[int] = []
    connector.submit(lambda: ran.append(1))
    assert ran == [1]
    plugin._cancel_select_connection()
    connector.release()
    assert pending.cancelled() and plugin._operation_users == 0


def test_without_a_bus_connection_the_user_selects_one() -> None:
    plugin = _plugin(None)
    pending = plugin.open_for_operation()
    assert plugin._select_open and plugin._select_failures == []
    plugin._cancel_select_connection()
    assert pending.cancelled() and not plugin._select_open


def test_a_bus_connection_that_fails_is_listed_in_the_selection() -> None:
    plugin = _plugin((_BUS, "IP-Interface"))
    pending = plugin.open_for_operation()
    plugin._state = ConnectionState.ERROR
    plugin._error_message = "timeout"
    plugin._operation_connected()
    assert plugin._select_open and not pending.done()
    assert plugin._select_failures == [("IP-Interface", "timeout")]
    assert plugin._controller_ip == "10.0.0.1"
    # An unsupported kind of connection is not tried.
    plugin = _plugin(("Type=Usb", "USB"))
    plugin.open_for_operation()
    assert plugin._select_failures == [("USB", S.BUS_CONNECTION_UNSUPPORTED)]
    assert plugin.started == []


def test_an_open_connection_is_used_as_it_is() -> None:
    plugin = _plugin((_BUS, "IP-Interface"))
    plugin._state = ConnectionState.CONNECTED
    assert plugin.open_for_operation().done()
    plugin.release_operation()
    assert plugin.started == []


def test_closed_by_the_user_it_is_not_closed_again() -> None:
    plugin = _plugin((_BUS, "IP-Interface"))
    plugin.open_for_operation()
    plugin._state = ConnectionState.CONNECTED
    plugin._operation_connected()
    plugin.disconnect()
    assert not plugin.opened_for_operations and plugin._controller_ip == "10.0.0.1"
    plugin.release_operation()
    assert plugin.started.count("_close_for_operations") == 0


@pytest.fixture
def clicks(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clicks]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield Clicks(monkeypatch, plugin_module)
    imgui.destroy_context(ctx)


def _frames(plugin: Any, count: int = 3) -> None:
    for _ in range(count):
        imgui.new_frame()
        plugin._render_select_connection()
        imgui.end_frame()


def test_the_dialog_connects_to_an_interface_found_or_cancels(clicks: Clicks) -> None:
    plugin = _plugin((_BUS, "IP-Interface"))
    gateway = SimpleNamespace(
        name="Office", ip_addr="192.168.16.90", port=3671, supports_tunnelling=True
    )
    plugin._gateways = [gateway]
    pending = plugin.open_for_operation()
    plugin._state = ConnectionState.ERROR
    plugin._error_message = "timeout"
    plugin._operation_connected()
    _frames(plugin)
    clicks.items.add("Office  (192.168.16.90)##select_192.168.16.90")
    _frames(plugin)
    assert (
        plugin._controller_ip == "192.168.16.90" and plugin._selected_gateway is gateway
    )
    plugin._state = ConnectionState.CONNECTED
    plugin._operation_connected()
    _frames(plugin)
    assert pending.done() and not plugin._select_shown
    # Cancel ends the wait of the operations.
    plugin = _plugin(None)
    pending = plugin.open_for_operation()
    _frames(plugin)
    clicks.button = S.SELECT_CONNECTION_CANCEL
    _frames(plugin)
    assert pending.cancelled()


def test_an_operation_waits_for_its_connection_until_it_is_open() -> None:
    service, connector = _service()
    operation: Future[Any] = Future()
    result = service.connect_for_operation("program_device", lambda: operation)
    assert service.waiting_for_connection(result)
    connector.opening.set_result(None)
    assert not service.waiting_for_connection(result)
    assert not service.waiting_for_connection(None)
    assert not service.waiting_for_connection(operation)
