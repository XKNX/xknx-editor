"""The gateway search goes out from every local address, so an interface is found also when the
system routes multicast through a virtual network (WSL, Hyper-V, VPN) without any."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin, scan_addresses


def _adapter(*ips: object) -> Any:
    return SimpleNamespace(ips=[SimpleNamespace(ip=ip) for ip in ips])


def test_every_usable_local_address_is_searched_from() -> None:
    adapters = [
        _adapter("172.17.64.1"),
        _adapter("169.254.41.236"),
        _adapter("192.168.16.172", ("fe80::1", 0, 3)),
        _adapter("127.0.0.1"),
        _adapter("192.168.16.172"),
    ]
    assert scan_addresses(adapters) == ["172.17.64.1", "192.168.16.172"]
    # The adapters of this machine, whatever they are, give IPv4 addresses.
    assert all(a.count(".") == 3 for a in scan_addresses())


def test_the_gateways_found_from_all_addresses_are_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = SimpleNamespace(ip_addr="192.168.16.88", port=3671, name="IP-Interface")
    other = SimpleNamespace(ip_addr="192.168.16.103", port=3671, name="")
    answers: dict[str | None, Any] = {
        "172.17.64.1": [],
        "192.168.16.172": [gateway, other],
        "192.168.16.202": [gateway],
        "192.168.56.1": OSError("no route"),
    }

    class Scanner:
        def __init__(self, _xknx: Any, local_ip: str | None = None, **_k: Any) -> None:
            self.local_ip = local_ip

        async def scan(self) -> Any:
            answer = answers[self.local_ip]
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setattr(plugin_module, "GatewayScanner", Scanner)
    monkeypatch.setattr(
        plugin_module, "scan_addresses", lambda: [a for a in answers if a is not None]
    )
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    log = SimpleNamespace(
        info=lambda *_a, **_k: None,
        debug=lambda *_a, **_k: None,
        warning=lambda *_a, **_k: None,
        error=lambda *_a, **_k: None,
    )
    plugin._log = log
    plugin._gateways = []
    plugin._scanning = True
    asyncio.run(plugin._scan_async())
    assert [g.ip_addr for g in plugin._gateways] == ["192.168.16.88", "192.168.16.103"]
    # Without any usable address the system's choice searches.
    answers.clear()
    answers[None] = [other]
    monkeypatch.setattr(plugin_module, "scan_addresses", lambda: [])
    asyncio.run(plugin._scan_async())
    assert [g.ip_addr for g in plugin._gateways] == ["192.168.16.103"]


def test_a_device_is_found_by_its_serial_number() -> None:
    from editor_gui.plugins.connection.plugin import find_by_serial

    router = SimpleNamespace(
        ip_addr="192.168.16.103", serial_number="00:fa:12:34:56:78"
    )
    other = SimpleNamespace(ip_addr="192.168.16.88", serial_number="00:c5:01:01:00:01")
    rounds: list[list[Any]] = [[other], [other, router]]
    timeouts: list[float] = []

    async def search(timeout: float) -> list[Any]:
        timeouts.append(timeout)
        return rounds.pop(0) if rounds else []

    serial = bytes.fromhex("00fa12345678")
    # It answers the second search.
    assert asyncio.run(find_by_serial(serial, search=search)) == "192.168.16.103"
    assert timeouts == [1.0, 1.0]
    # A device that never answers ends the search after its tries.
    assert asyncio.run(find_by_serial(serial, tries=3, search=search)) is None
    assert len(timeouts) == 5
