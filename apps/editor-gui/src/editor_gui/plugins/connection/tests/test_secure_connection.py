"""The editor's own connection with KNX IP Secure and KNX Data Secure."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.cemi.cemi_frame import CEMILData
from xknx.dpt import DPTBinary
from xknx.exceptions import DataSecureError
from xknx.io.connection import ConnectionType
from xknx.knxip.dib import TunnelingSlotStatus
from xknx.telegram import GroupAddress, IndividualAddress
from xknx.telegram.apci import GroupValueWrite, SecureAPDU
from xknx.telegram.tpci import TDataGroup

from editor_gui.plugins.connection.group_security import ConnectionDataSecure
from editor_gui.plugins.connection.plugin import ConnectionPlugin
from editor_gui.plugins.connection.service import ConnectionService, SecureTunnel
from editor_gui.plugins.keyring.service import KeyringIpSecrets

_TUNNELS = [
    SecureTunnel(2, "1.1.250", "one", "auth"),
    SecureTunnel(3, "1.1.251", "two", "auth"),
]


def test_a_free_tunnel_of_the_project_first() -> None:
    service = ConnectionService()
    service.project_tunnels = lambda host: _TUNNELS if host == "1.1.10" else []
    assert service.secure_tunnel("1.1.10") == _TUNNELS[0]
    assert service.secure_tunnel("1.1.10", ["1.1.251"]) == _TUNNELS[1]
    assert service.secure_tunnel("1.1.11") is None


def test_the_keyring_fills_in() -> None:
    service = ConnectionService()
    service.project_tunnels = lambda host: []
    service.keyring = cast(
        Any,
        SimpleNamespace(
            ip_secrets=lambda host: KeyringIpSecrets(
                "auth", "mgmt", [(4, "1.1.252", "ring")]
            )
        ),
    )
    assert service.secure_tunnel("1.1.10") == SecureTunnel(4, "1.1.252", "ring", "auth")


def test_data_secure_from_the_project() -> None:
    service = ConnectionService()
    asked: list[int | None] = []

    def tables(local: int | None) -> Any:
        asked.append(local)
        return {0x0801: bytes(16)}, {0x1105: 41}, {0x0801}

    service.project_data_secure = tables
    data_secure = service.data_secure(0x11FF)
    assert isinstance(data_secure, ConnectionDataSecure)
    assert asked == [0x11FF]
    assert service.secure_group_keys() == {0x0801: bytes(16)}
    assert data_secure.sent_secured == {GroupAddress("1/0/1")}
    service.project_data_secure = lambda local: None
    assert service.data_secure() is None
    assert service.secure_group_keys() == {}


def _write(destination: str, source: str = "1.2.5") -> CEMILData:
    return CEMILData(
        src_addr=IndividualAddress(source),
        dst_addr=GroupAddress(destination),
        tpci=TDataGroup(),
        payload=GroupValueWrite(DPTBinary(1)),
    )


def _data_secure(senders: dict[IndividualAddress, int]) -> ConnectionDataSecure:
    return ConnectionDataSecure(
        group_key_table={
            GroupAddress("1/0/1"): bytes(16),
            GroupAddress("1/0/2"): bytes(16),
        },
        individual_address_table=senders,
        sent_secured={GroupAddress("1/0/1")},
    )


def test_plain_telegrams_are_always_taken() -> None:
    data_secure = _data_secure({})
    # Also for a group address with a key, secured somewhere or not.
    for destination in ("1/0/1", "1/0/2", "1/0/3"):
        frame = _write(destination)
        assert data_secure.received_cemi(frame) is frame


def test_sent_secured_only_where_the_connection_secures_it() -> None:
    sender = _data_secure({})
    assert isinstance(sender.outgoing_cemi(_write("1/0/1")).payload, SecureAPDU)
    plain = _write("1/0/2")
    assert sender.outgoing_cemi(plain) is plain
    # Without a choice, secured on every group address with a key.
    every = ConnectionDataSecure(
        group_key_table={GroupAddress("1/0/2"): bytes(16)}, individual_address_table={}
    )
    assert every.sent_secured == {GroupAddress("1/0/2")}


def test_secured_telegrams_decrypted_also_from_unknown_senders() -> None:
    sender = _data_secure({})
    secured = sender.outgoing_cemi(_write("1/0/1", "1.1.20"))
    # A receiver that doesn't know the sender yet takes it and counts from its number on.
    receiver = _data_secure({})
    decrypted = receiver.received_cemi(secured)
    assert isinstance(decrypted.payload, GroupValueWrite)
    with pytest.raises(DataSecureError, match="Sequence number too low"):
        receiver.received_cemi(secured)
    # A known sender must exceed its last valid Sequence Number.
    known = _data_secure({IndividualAddress("1.1.20"): 1 << 40})
    with pytest.raises(DataSecureError, match="Sequence number too low"):
        known.received_cemi(secured)


def test_the_keyring_alone_sends_secured_on_all_its_keys() -> None:
    service = ConnectionService()
    service.project_data_secure = lambda local: None
    service.keyring = cast(
        Any,
        SimpleNamespace(data_secure_tables=lambda: ({0x0801: bytes(16)}, {})),
    )
    data_secure = service.data_secure()
    assert isinstance(data_secure, ConnectionDataSecure)
    assert data_secure.sent_secured == {GroupAddress("1/0/1")}
    service.keyring = cast(Any, SimpleNamespace(data_secure_tables=lambda: None))
    assert service.data_secure() is None


def _plugin(gateway: Any, *, routing: bool = False, **connection: Any) -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._usb_target = None
    plugin._usb_addresses = {}
    plugin._usb_info = None
    plugin._usb_devices = []
    plugin._connection_type = (
        ConnectionType.ROUTING if routing else ConnectionType.TUNNELING
    )
    plugin._selected_gateway = gateway
    plugin._controller_ip = "192.0.2.1"
    plugin._multicast_group = "224.0.23.12"
    plugin._api = SimpleNamespace(connection=SimpleNamespace(**connection))
    plugin._credentials_error = None
    return plugin


def _gateway(**fields: Any) -> Any:
    values: dict[str, Any] = {
        "individual_address": IndividualAddress("1.1.10"),
        "tunnelling_requires_secure": False,
        "routing_requires_secure": False,
        "tunnelling_slots": {},
    }
    return SimpleNamespace(**(values | fields))


def test_a_secure_tunnel_when_the_interface_requires_it() -> None:
    gateway = _gateway(
        tunnelling_requires_secure=True,
        tunnelling_slots={
            IndividualAddress("1.1.250"): TunnelingSlotStatus(True, True, False),
            IndividualAddress("1.1.251"): TunnelingSlotStatus(True, True, True),
        },
    )
    service = ConnectionService()
    service.project_tunnels = lambda host: _TUNNELS
    plugin = _plugin(gateway, secure_tunnel=service.secure_tunnel)
    config = ConnectionPlugin._connection_config(plugin, plugin._selected_gateway)  # pyright: ignore[reportPrivateUsage]
    assert config.connection_type is ConnectionType.TUNNELING_TCP_SECURE
    assert config.individual_address == IndividualAddress("1.1.251")
    assert config.secure_config is not None
    assert (config.secure_config.user_id, config.secure_config.user_password) == (
        3,
        "two",
    )


def test_plain_tunnel_and_secure_routing() -> None:
    plugin = _plugin(_gateway(), secure_tunnel=lambda *a: None)
    config = ConnectionPlugin._connection_config(plugin, plugin._selected_gateway)  # pyright: ignore[reportPrivateUsage]
    assert config.connection_type is ConnectionType.TUNNELING
    plugin = _plugin(
        _gateway(routing_requires_secure=True),
        routing=True,
        secure_backbone=lambda: (bytes(range(16)), 1500),
    )
    config = ConnectionPlugin._connection_config(plugin, plugin._selected_gateway)  # pyright: ignore[reportPrivateUsage]
    assert config.connection_type is ConnectionType.ROUTING_SECURE
    assert config.secure_config is not None
    assert config.secure_config.backbone_key == bytes(range(16))
    assert config.secure_config.latency_ms == 1500


def _reopening(plugin: Any, description: Any) -> list[bool]:
    """Run the reopen check of ``plugin`` against ``description``; the reopens it starts."""
    reopened: list[bool] = []

    async def gateway_description() -> Any:
        return description

    async def reopen(session: int, retry: bool = True) -> None:
        reopened.append(retry)

    plugin._session = 1
    plugin._gateway_description = gateway_description
    plugin._reopen = reopen
    plugin._log = SimpleNamespace(info=lambda *a, **k: None)  # pyright: ignore[reportUnknownLambdaType]
    config = ConnectionPlugin._connection_config  # pyright: ignore[reportPrivateUsage]
    plugin._config = config(plugin, plugin._selected_gateway)
    assert asyncio.run(ConnectionPlugin._reopen_if_changed(plugin, 1))  # pyright: ignore[reportPrivateUsage]
    return reopened


def test_reopened_when_the_interface_now_requires_secure_tunnelling() -> None:
    secured = _gateway(tunnelling_requires_secure=True)
    service = ConnectionService()
    service.project_tunnels = lambda host: _TUNNELS
    plugin = _plugin(_gateway(), secure_tunnel=service.secure_tunnel)
    assert _reopening(plugin, _gateway()) == []
    plugin = _plugin(_gateway(), secure_tunnel=service.secure_tunnel)
    assert _reopening(plugin, secured) == [True]
    # Without credentials it is tried once, to show why it fails.
    plugin = _plugin(_gateway(), secure_tunnel=lambda *a: None)
    assert _reopening(plugin, secured) == [False]


def test_the_interfaces_download_reopens_the_connection() -> None:
    from concurrent.futures import Future

    service = ConnectionService()
    reopened: list[bool] = []
    service.set_reopen_controller(lambda: reopened.append(True))
    done: Future[Any] = Future()
    done.set_result(None)
    service._reopen_after_interface_download(done)  # pyright: ignore[reportPrivateUsage]
    failed: Future[Any] = Future()
    failed.set_exception(RuntimeError("timeout"))
    service._reopen_after_interface_download(failed)  # pyright: ignore[reportPrivateUsage]
    assert reopened == [True]


def test_connected_once_the_credentials_are_there() -> None:
    from editor_gui.plugins.connection.plugin import ConnectionState

    tunnels: list[SecureTunnel] = []
    plugin = _plugin(
        None, secure_tunnel=lambda host, free: tunnels[0] if tunnels else None
    )
    started: list[object] = []
    plugin._state = ConnectionState.ERROR
    plugin._awaiting_credentials = _gateway(tunnelling_requires_secure=True)
    plugin._retry_on_credentials = True
    plugin._credentials_check_at = 0.0
    plugin._log = SimpleNamespace(info=lambda *a, **k: None)  # pyright: ignore[reportUnknownLambdaType]
    plugin._run_async = lambda coro: (started.append(coro), coro.close())
    plugin._connect_async = lambda: SimpleNamespace(close=lambda: None)
    plugin._connect_with_credentials()
    assert started == []
    tunnels.append(_TUNNELS[0])
    plugin._credentials_check_at = 0.0
    plugin._connect_with_credentials()
    assert len(started) == 1
    assert plugin._state is ConnectionState.CONNECTING


def _waiting(project: Any) -> tuple[Any, list[tuple[str, str]]]:
    """A plugin whose connection failed for missing credentials, logging into the list."""
    from editor_gui.plugins.connection.plugin import ConnectionState

    logged: list[tuple[str, str]] = []
    plugin = _plugin(None, secure_tunnel=lambda host, free: None)
    plugin._api.project = project
    plugin._state = ConnectionState.ERROR
    plugin._awaiting_credentials = _gateway(tunnelling_requires_secure=True)
    plugin._retry_on_credentials = True
    plugin._credentials_check_at = 0.0
    plugin._log = SimpleNamespace(
        info=lambda event, **_: logged.append(("info", event)),
        error=lambda event, **_: logged.append(("error", event)),
    )
    return plugin, logged


def test_missing_credentials_at_startup_wait_for_the_project() -> None:
    from editor_gui.plugins.connection.plugin import MissingCredentialsError

    # The project given at startup is still opening: no error (a toast) yet.
    project = SimpleNamespace(loading=True, is_open=False)
    plugin, logged = _waiting(project)
    plugin._report_failure(MissingCredentialsError(None), startup=True)
    assert [level for level, _ in logged] == ["info"]
    plugin._connect_with_credentials()
    assert [level for level, _ in logged] == ["info"]
    # It opened without them: now it is an error, once.
    project.loading, project.is_open = False, True
    plugin._credentials_check_at = 0.0
    plugin._connect_with_credentials()
    plugin._credentials_check_at = 0.0
    plugin._connect_with_credentials()
    assert [level for level, _ in logged] == ["info", "error"]


def test_missing_credentials_are_an_error_when_connecting_later() -> None:
    from editor_gui.plugins.connection.plugin import MissingCredentialsError

    # Connected by the user, or at startup with the project already open.
    for startup, project in (
        (False, SimpleNamespace(loading=True, is_open=False)),
        (True, SimpleNamespace(loading=False, is_open=True)),
    ):
        plugin, logged = _waiting(project)
        plugin._report_failure(MissingCredentialsError(None), startup=startup)
        assert logged == [("error", "connection failed")]


def test_refresh_gives_the_tables_for_the_connections_own_address() -> None:
    service = ConnectionService()
    asked: list[int | None] = []

    def tables(local: int | None) -> Any:
        asked.append(local)
        return {0x0801: bytes(16)}, {}, set[int]()

    service.project_data_secure = tables
    # Without a connection there is nothing to refresh.
    service.refresh_data_secure()
    assert asked == []
    handler = SimpleNamespace(data_secure=None)
    service._xknx = cast(  # pyright: ignore[reportPrivateUsage]
        Any,
        SimpleNamespace(
            current_address=IndividualAddress("1.2.250"), cemi_handler=handler
        ),
    )
    service.refresh_data_secure()
    assert asked == [IndividualAddress("1.2.250").raw]
    assert isinstance(handler.data_secure, ConnectionDataSecure)
    assert handler.data_secure.sent_secured == frozenset()
