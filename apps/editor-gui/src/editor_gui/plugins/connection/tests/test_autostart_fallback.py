"""The autostart's fallback to the configured controller IP when gateway discovery is empty."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.connection.plugin import (
    _DEFAULT_CONTROLLER_IP,
    ConnectionPlugin,
    ConnectionState,
)


class _Logger:
    def info(self, event: str, **kwargs: object) -> None:
        pass

    def debug(self, event: str, **kwargs: object) -> None:
        pass

    def warning(self, event: str, **kwargs: object) -> None:
        pass

    def error(self, event: str, **kwargs: object) -> None:
        pass


def _plugin() -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._log = _Logger()
    plugin._state = ConnectionState.DISCONNECTED
    plugin._gateways = []
    plugin._scanning = True
    plugin._autostart_retry_done = False
    plugin._busmonitor = None
    plugin._preferred_gateway_ip = None
    plugin._controller_ip = _DEFAULT_CONTROLLER_IP
    # ``busmonitor_active`` is a property that reads ``self._api.connection.busmonitor_active``.
    plugin._api = SimpleNamespace(connection=SimpleNamespace(busmonitor_active=False))
    plugin.connect_calls = 0
    plugin.rescan_scheduled = False

    def _connect() -> None:
        # Mirror the real ``connect()`` just enough that the retry below picks up the state
        # transition and skips its own scan.
        plugin.connect_calls += 1
        plugin._state = ConnectionState.CONNECTING

    plugin.connect = _connect
    plugin._run_async = lambda coro: (
        setattr(plugin, "rescan_scheduled", True),
        coro.close(),
    )
    return plugin


def _patch_scanner(monkeypatch: pytest.MonkeyPatch, found: list[Any]) -> None:
    class _Scanner:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def scan(self) -> list[Any]:
            return found

    monkeypatch.setattr("editor_gui.plugins.connection.plugin.GatewayScanner", _Scanner)
    monkeypatch.setattr("editor_gui.plugins.connection.plugin.XKNX", SimpleNamespace)


def test_empty_scan_without_configured_ip_schedules_the_macos_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = (
        _plugin()
    )  # no preferred gateway, controller IP still at the untouched placeholder
    _patch_scanner(monkeypatch, [])
    asyncio.run(ConnectionPlugin._scan_async(plugin, auto_connect=True))  # pyright: ignore[reportPrivateUsage]
    assert plugin.connect_calls == 0
    assert plugin.rescan_scheduled, "the macOS-permission retry must be scheduled"
    assert plugin._autostart_retry_done


def test_empty_scan_falls_back_to_preferred_gateway_ip_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._preferred_gateway_ip = "192.168.1.100"
    _patch_scanner(monkeypatch, [])
    asyncio.run(ConnectionPlugin._scan_async(plugin, auto_connect=True))  # pyright: ignore[reportPrivateUsage]
    assert plugin.connect_calls == 1
    assert plugin._controller_ip == "192.168.1.100"
    # ``connect`` transitioned the state to CONNECTING, so the retry below skips itself.
    assert not plugin.rescan_scheduled


def test_empty_scan_falls_back_to_configured_controller_ip_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._controller_ip = "10.0.0.5"  # user typed one in the settings
    _patch_scanner(monkeypatch, [])
    asyncio.run(ConnectionPlugin._scan_async(plugin, auto_connect=True))  # pyright: ignore[reportPrivateUsage]
    assert plugin.connect_calls == 1
    assert plugin._controller_ip == "10.0.0.5"
    assert not plugin.rescan_scheduled


def test_second_empty_scan_falls_back_without_rescheduling_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._autostart_retry_done = (
        True  # the retry has already run and also found nothing
    )
    plugin._preferred_gateway_ip = "192.168.1.100"
    _patch_scanner(monkeypatch, [])
    asyncio.run(ConnectionPlugin._scan_async(plugin, auto_connect=True))  # pyright: ignore[reportPrivateUsage]
    assert plugin.connect_calls == 1
    assert not plugin.rescan_scheduled


def test_second_empty_scan_without_configured_ip_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    plugin._autostart_retry_done = True
    _patch_scanner(monkeypatch, [])
    asyncio.run(ConnectionPlugin._scan_async(plugin, auto_connect=True))  # pyright: ignore[reportPrivateUsage]
    assert plugin.connect_calls == 0
    assert not plugin.rescan_scheduled
    assert plugin._controller_ip == _DEFAULT_CONTROLLER_IP
