"""The lost-connection watchdog: xknx's own auto-reconnect gets a grace period, then a full
teardown/reconnect is forced (what a manual disconnect/connect does). A USB link is left to replug.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.core.connection_state import XknxConnectionState

import editor_gui.plugins.connection.plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionType


def _plugin(*, usb: bool = False) -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._watching_lost = False
    plugin._session = 1
    plugin._link_up = False
    plugin._lost_since = 0.0  # the outage started at t=0 on the fake clock
    plugin._config = object()
    plugin._usb_target = object() if usb else None
    plugin._connection_type = ConnectionType.TUNNELING
    plugin._multicast_group = "224.0.23.12"
    plugin._controller_ip = "192.0.2.1"
    plugin._log = SimpleNamespace(info=lambda *a, **k: None)
    return plugin


def _run_watchdog(
    plugin: Any, monkeypatch: pytest.MonkeyPatch, clock: list[float]
) -> None:
    calls = {"n": 0}

    def monotonic() -> float:
        value = clock[min(calls["n"], len(clock) - 1)]  # clamp: last value repeats
        calls["n"] += 1
        return value

    # Drive the coroutine by hand with a zero interval and a fake clock. The only suspension point
    # is asyncio.sleep(0) (a bare yield), so stepping with send(None) needs no event loop — which
    # keeps the test independent of pytest-asyncio's loop management.
    monkeypatch.setattr(plugin_module, "_REOPEN_SECONDS", 0)
    monkeypatch.setattr(plugin_module.time, "monotonic", monotonic)
    coro = ConnectionPlugin._reopen_while_lost(plugin, 1)  # pyright: ignore[reportPrivateUsage]
    try:
        while True:
            coro.send(None)
    except StopIteration:
        pass
    finally:
        coro.close()


def test_forces_full_reconnect_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin()
    forced: list[int] = []

    async def reopen_if_changed(_session: int) -> bool:
        return True  # same config: xknx is left to reconnect in place, which never recovers

    async def reopen(session: int, retry: bool = True) -> None:
        forced.append(session)
        plugin._link_up = True  # the forced reconnect succeeds

    plugin._reopen_if_changed = reopen_if_changed
    plugin._reopen = reopen
    # _lost_since = 0, clock at 100s -> past the grace period -> force on the first iteration.
    _run_watchdog(plugin, monkeypatch, clock=[100.0])
    assert forced == [1]


def test_no_force_while_xknx_recovers_within_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _plugin()
    forced: list[int] = []

    async def reopen_if_changed(_session: int) -> bool:
        plugin._link_up = True  # xknx restored the link on its own
        return True

    async def reopen(session: int, retry: bool = True) -> None:
        forced.append(session)

    plugin._reopen_if_changed = reopen_if_changed
    plugin._reopen = reopen
    _run_watchdog(plugin, monkeypatch, clock=[0.0])
    assert forced == []  # link came back; no forced reconnect


def test_usb_link_is_not_force_reconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = _plugin(usb=True)
    forced: list[int] = []
    calls = {"n": 0}

    async def reopen_if_changed(_session: int) -> bool:
        calls["n"] += 1
        if calls["n"] >= 2:
            plugin._session = 2  # end the watch (as a new connect/disconnect would)
        return True

    async def reopen(session: int, retry: bool = True) -> None:
        forced.append(session)

    plugin._reopen_if_changed = reopen_if_changed
    plugin._reopen = reopen
    # Always past the grace period; a USB link must still never be force-reconnected.
    _run_watchdog(plugin, monkeypatch, clock=[100.0])
    assert forced == []


def test_no_force_when_config_cleared_mid_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bus monitor mode (or a failed credentialed reopen) clears _config during the watch; the
    # watchdog must stop rather than force a normal reconnect alongside it.
    plugin = _plugin()
    forced: list[int] = []

    async def reopen_if_changed(_session: int) -> bool:
        plugin._config = None  # the connection was taken over / torn down
        return True

    async def reopen(session: int, retry: bool = True) -> None:
        forced.append(session)

    plugin._reopen_if_changed = reopen_if_changed
    plugin._reopen = reopen
    _run_watchdog(plugin, monkeypatch, clock=[100.0])
    assert forced == []  # config gone -> no forced reconnect


def test_grace_is_measured_per_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    # _on_link_state tracks a single outage: it stamps _lost_since on the first loss, clears it on
    # reconnect, and stamps afresh on the next loss — so a brief reconnect does not consume the
    # grace of the outage that follows.
    plugin = _plugin()
    plugin._lost_since = None
    plugin._session = 1
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(dispatch_link_state=lambda _up: None)
    )
    plugin._run_async = lambda coro: coro.close()  # don't start the real watchdog here

    clock = iter([5.0, 100.0])
    monkeypatch.setattr(plugin_module.time, "monotonic", lambda: next(clock))

    ConnectionPlugin._on_link_state(plugin, XknxConnectionState.DISCONNECTED)  # pyright: ignore[reportPrivateUsage]
    assert plugin._lost_since == 5.0
    ConnectionPlugin._on_link_state(plugin, XknxConnectionState.CONNECTED)  # pyright: ignore[reportPrivateUsage]
    assert plugin._lost_since is None  # outage ended
    ConnectionPlugin._on_link_state(plugin, XknxConnectionState.DISCONNECTED)  # pyright: ignore[reportPrivateUsage]
    assert plugin._lost_since == 100.0  # fresh grace for the new outage
