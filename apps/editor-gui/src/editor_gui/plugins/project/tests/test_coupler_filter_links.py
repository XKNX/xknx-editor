"""The links a coupler's filter table is computed from: group objects and bus interfaces."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project import service as service_module
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.download.filter_table import (
    addresses_in_filter_table,
    compute_coupler_filter_table,
)
from xknxeditor.proj.core.service import AdditionalAddressInfo

_SHARED, _TUNNEL, _SPARE, _OWN = 0x0801, 0x0A03, 0x0A04, 0x0A05
_COUPLER = 0x1100  # 1.1.0


def _info(index: int, octet: int | None) -> AdditionalAddressInfo:
    address = None if octet is None else f"1.0.{octet}"
    return AdditionalAddressInfo(index, address, octet, "", "")


def _service(monkeypatch: pytest.MonkeyPatch) -> Any:
    actuator = SimpleNamespace(
        node_id=1,
        individual_address="1.1.5",
        com_objects=[SimpleNamespace(db_id=10)],
    )
    router = SimpleNamespace(node_id=2, individual_address="1.0.1", com_objects=[])
    sensor = SimpleNamespace(
        node_id=3, individual_address="1.0.7", com_objects=[SimpleNamespace(db_id=30)]
    )
    links = {10: [_SHARED, _TUNNEL, _SPARE, _OWN], 30: [_SHARED]}
    monkeypatch.setattr(
        service_module,
        "_bus_interfaces",
        lambda device: (
            [
                ("BI-0", "DeviceManagement", 0),
                ("BI-1", "Tunneling", 1),
                ("BI-2", "Tunneling", 2),
            ]
            if device is router
            else []
        ),
    )
    service = cast(Any, SimpleNamespace())
    service._pid = "P"
    service.devices = [actuator, router, sensor]
    service.get_links_for_com_object = lambda co: [
        SimpleNamespace(group_address_id=ga) for ga in links[co]
    ]
    service.get_group_address = lambda ga: SimpleNamespace(
        address=f"{ga >> 11}/{(ga >> 8) & 7}/{ga & 255}"
    )
    service._bus_interface_group_addresses = lambda device: (
        ProjectService._bus_interface_group_addresses(  # pyright: ignore[reportPrivateUsage]
            service, device
        )
    )
    service._svc = SimpleNamespace(
        # The tunnel on additional address 1 is set, the one on 2 is not.
        bus_interface_group_addresses=lambda pid, node: (
            {"BI-0": [_OWN], "BI-1": [_TUNNEL], "BI-2": [_SPARE]} if node == 2 else {}
        ),
        additional_addresses=lambda pid, node: [_info(1, 250), _info(2, None)],
    )
    return service


def test_bus_interface_links_count_for_their_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    links = ProjectService._all_device_group_addresses(  # pyright: ignore[reportPrivateUsage]
        service
    )
    assert links[0x1105] == {_SHARED, _TUNNEL, _SPARE, _OWN}
    # The device's own interface and the tunnel with an address; not the one without.
    assert links[0x1001] == {_TUNNEL, _OWN}
    assert links[0x1007] == {_SHARED}


def test_a_tunnel_partner_lets_an_address_through_the_coupler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(monkeypatch)
    links = ProjectService._all_device_group_addresses(  # pyright: ignore[reportPrivateUsage]
        service
    )
    table = compute_coupler_filter_table(_COUPLER, links)
    # A visualisation on the router's tunnel is the only partner of 1/2/3 and 1/2/5.
    assert addresses_in_filter_table(table) == [_SHARED, _TUNNEL, _OWN]


def test_no_project_no_interface_links(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(monkeypatch)
    service._pid = None
    router = service.devices[1]
    assert (
        ProjectService._bus_interface_group_addresses(  # pyright: ignore[reportPrivateUsage]
            service, router
        )
        == set()
    )
