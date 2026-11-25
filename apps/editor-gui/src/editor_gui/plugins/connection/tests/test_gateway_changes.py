"""A changed gateway address or connection type applies to the open connection (#351), and the
open project's Bus Connection replaces the gateway set in the settings with "Use the Project's
Bus Connection"."""

from __future__ import annotations

from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.io.connection import ConnectionType

from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import (
    ConnectionPlugin,
    ConnectionState,
    tunnel_type,
)
from editor_gui.plugins.connection.service import ConnectionService

_BUS = 'Type=IpTunneling;HostAddress=192.168.16.88;ProtocolType=Udp;Name="IP-Interface"'
_TCP_BUS = "Type=IpTunneling;HostAddress=192.168.16.89;Port=3672;ProtocolType=Tcp"


class _Project:
    def __init__(self, bus: str | None = None) -> None:
        self.is_open = True
        self.generation = 1
        self.revision = 1
        self.bus = bus

    def backbone_bus_connection(self) -> tuple[str, str] | None:
        return (self.bus, "IP-Interface") if self.bus is not None else None


def _plugin(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: ConnectionState = ConnectionState.CONNECTED,
    project: _Project | None = None,
) -> Any:
    plugin = cast("Any", object.__new__(ConnectionPlugin))
    plugin._usb_target = None
    plugin._usb_addresses = {}
    plugin._usb_local_device = False
    plugin._ip_settings = SimpleNamespace(to_settings=lambda: {})
    plugin._api = SimpleNamespace(
        project=project or _Project(),
        connection=ConnectionService(),
        main_thread=None,
    )
    plugin._log = SimpleNamespace(info=lambda *_a, **_k: None)
    plugin._state = state
    plugin._controller_ip = "192.168.0.221"
    plugin._connection_type = ConnectionType.TUNNELING
    plugin._multicast_group = "224.0.23.12"
    plugin._selected_gateway = cast("Any", object())
    plugin._preferred_gateway_ip = "192.168.0.221"
    plugin._connect_at_startup = True
    plugin._block_bus_writes_setting = False
    plugin._session = 0
    plugin._operation_open = False
    plugin._operation_pending = None
    plugin._operation_restore = None
    plugin.started = []
    plugin.saved = []

    def run_async(coro: Coroutine[Any, Any, None]) -> None:
        plugin.started.append(coro.cr_code.co_name)
        coro.close()

    plugin._run_async = run_async
    monkeypatch.setattr(
        plugin_module, "save_settings", lambda name, data: plugin.saved.append(data)
    )
    return plugin


def test_a_new_gateway_address_connects_there_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(monkeypatch)
    plugin._set_controller_ip(" 192.168.0.30 ")
    assert plugin._controller_ip == "192.168.0.30"
    # the gateway scanned for the old address is not this one
    assert plugin._selected_gateway is None
    assert plugin.started == ["_reopen"]
    assert plugin.saved[-1]["controller_ip"] == "192.168.0.30"
    # the same address again, or none, changes nothing
    plugin._set_controller_ip("192.168.0.30")
    plugin._set_controller_ip("  ")
    assert plugin.started == ["_reopen"]


@pytest.mark.parametrize("state", [ConnectionState.DISCONNECTED, ConnectionState.ERROR])
def test_without_an_open_connection_the_address_is_only_kept(
    monkeypatch: pytest.MonkeyPatch, state: ConnectionState
) -> None:
    plugin = _plugin(monkeypatch, state=state)
    plugin._set_controller_ip("192.168.0.30")
    assert plugin._controller_ip == "192.168.0.30"
    assert plugin.started == []


def test_a_routing_or_usb_connection_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(monkeypatch)
    plugin._connection_type = ConnectionType.ROUTING
    plugin._set_controller_ip("192.168.0.30")
    assert plugin.started == []
    plugin = _plugin(monkeypatch)
    plugin._usb_target = cast("Any", SimpleNamespace(to_settings=lambda: {}))
    plugin._set_controller_ip("192.168.0.30")
    assert plugin.started == []


def test_a_running_download_is_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(monkeypatch)
    service = plugin._api.connection
    assert service.begin_operation("program", "1.1.5")
    plugin._set_controller_ip("192.168.0.30")
    assert plugin.started == []
    plugin._reopen_once_idle()
    assert plugin.started == []
    service._clear_busy()
    plugin._reopen_once_idle()
    assert plugin.started == ["_reopen"]
    # once only
    plugin._reopen_once_idle()
    assert plugin.started == ["_reopen"]


def test_a_new_connection_type_connects_again(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(monkeypatch)
    plugin._set_connection_type_name("tunneling_tcp")
    assert plugin._connection_type is ConnectionType.TUNNELING_TCP
    assert plugin.started == ["_reopen"]


def test_the_bus_connection_says_its_tunnelling() -> None:
    assert tunnel_type(_BUS) is ConnectionType.TUNNELING
    assert tunnel_type(_TCP_BUS) is ConnectionType.TUNNELING_TCP
    assert tunnel_type('Type=IpTunneling;ProtocolType="tcp"') is (
        ConnectionType.TUNNELING_TCP
    )


def test_the_projects_bus_connection_replaces_the_gateway_while_it_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _Project(_TCP_BUS)
    plugin = _plugin(monkeypatch, project=project)
    # off: nothing changes
    plugin._follow_project_bus_connection()
    assert plugin.started == []
    plugin._set_use_project_bus(True)
    assert plugin.saved[-1]["use_project_bus_connection"] is True
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.16.89:3672"
    assert plugin._connection_type is ConnectionType.TUNNELING_TCP
    assert plugin.started == ["_reopen"]
    # the settings keep the gateway set there, also in the field
    assert plugin._gateway_ip_setting() == "192.168.0.221"
    plugin._save_current_settings()
    assert plugin.saved[-1]["controller_ip"] == "192.168.0.221"
    assert plugin.saved[-1]["connection_type"] == ConnectionType.TUNNELING.value
    # a changed gateway is kept for afterwards, the project's stays in use
    plugin._set_controller_ip("192.168.0.30")
    assert plugin._controller_ip == "192.168.16.89:3672"
    assert plugin.started == ["_reopen"]
    # not looked at again while the project stays as it is
    plugin._follow_project_bus_connection()
    assert plugin.started == ["_reopen"]
    # closed: the gateway set in the settings again
    project.is_open = False
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.0.30"
    assert plugin._connection_type is ConnectionType.TUNNELING
    assert plugin._project_restore is None
    assert plugin.started == ["_reopen", "_reopen"]


def test_a_project_without_a_bus_connection_keeps_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _Project(None)
    plugin = _plugin(monkeypatch, project=project)
    plugin._set_use_project_bus(True)
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.0.221"
    assert plugin.started == []
    # set to one in the topology: it is used from the next change on
    project.bus = _BUS
    project.revision += 1
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.16.88"
    assert plugin.started == ["_reopen"]


def test_the_projects_bus_connection_waits_for_a_running_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(monkeypatch, project=_Project(_BUS))
    plugin._set_use_project_bus(True)
    service = plugin._api.connection
    assert service.begin_operation("program", "1.1.5")
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.0.221"
    service._clear_busy()
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.16.88"


def test_the_users_choice_ends_the_projects_bus_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(monkeypatch, project=_Project(_BUS))
    plugin._set_use_project_bus(True)
    plugin._follow_project_bus_connection()
    assert plugin._project_restore is not None
    # a connect the user chose is kept as set, until the project changes
    plugin._chosen_connection(startup=False)
    assert plugin._project_restore is None
    plugin._follow_project_bus_connection()
    assert plugin.started == ["_reopen"]
    # the startup connection hands over to the project's again
    plugin._controller_ip = "192.168.0.221"
    plugin._chosen_connection(startup=True)
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.16.88"
    assert plugin.started == ["_reopen", "_reopen"]


def test_a_bus_connection_that_is_no_tunnel_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin(monkeypatch, project=_Project("Type=Usb;DevicePath=x"))
    plugin._set_use_project_bus(True)
    plugin._follow_project_bus_connection()
    assert plugin._controller_ip == "192.168.0.221"
    assert plugin.started == []
