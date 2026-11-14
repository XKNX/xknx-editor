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


def _segment_service(segment_coupler: bool) -> Any:
    """Line 1.1: main segment 1 with the line coupler 1.1.0 and a sensor 1.1.7, sub segment 2
    with the segment coupler 1.1.100 and an actuator 1.1.105; a sensor 1.2.3 on line 1.2."""
    devices = {
        1: ("1.1.0", 1, set[int]()),
        2: ("1.1.7", 1, {_SHARED}),
        3: ("1.1.100", 2, set[int]()),
        4: ("1.1.105", 2, {_SHARED, _TUNNEL, _SPARE}),
        5: ("1.2.3", 3, {_TUNNEL}),
    }
    service = cast(Any, SimpleNamespace())
    service._pid = "P"
    service.devices = [
        SimpleNamespace(node_id=node, individual_address=address)
        for node, (address, _segment, _links) in devices.items()
    ]
    service.device_segments = lambda: {
        node: segment for node, (_a, segment, _l) in devices.items()
    }
    service.segment_medium = lambda segment: ("MT-0", segment != 2)
    service._svc = SimpleNamespace(
        device_programs=lambda pid: {node: f"H2P-{node}" for node in devices},
    )
    service._coupler_device = lambda program: (
        SimpleNamespace(segment_coupler=segment_coupler)
        if program in ("H2P-1", "H2P-3")
        else None
    )
    service._coupled_segment = lambda device: ProjectService._coupled_segment(  # pyright: ignore[reportPrivateUsage]
        service, device
    )
    service._all_device_group_addresses = lambda: {
        int(address.split(".")[0]) << 12
        | int(address.split(".")[1]) << 8
        | int(address.split(".")[2]): links
        for address, _segment, links in devices.values()
    }
    service.passed: list[tuple[int, int | None]] = []

    def _pass_through(address: int, segment: int | None) -> tuple[list[int], list[int]]:
        service.passed.append((address, segment))
        return [], [_OWN] if segment == 2 else []

    service._coupler_pass_through = _pass_through
    return service


def _routed(service: Any, node: int) -> list[int] | None:
    device = service.devices[node - 1]
    address = int(device.individual_address.split(".")[2]) | 0x1100
    table = ProjectService._filter_table_for(  # pyright: ignore[reportPrivateUsage]
        service, device, address
    )
    return None if table is None else addresses_in_filter_table(table)


def test_a_segment_coupler_filters_between_its_segment_and_the_rest() -> None:
    service = _segment_service(segment_coupler=True)
    # 1/0/1 and 1/2/3 have partners outside the sub segment, 1/2/4 has none; 1/2/5 is the
    # sub segment's additional address.
    assert _routed(service, 3) == [_SHARED, _TUNNEL, _OWN]
    assert service.passed == [(0x1164, 2)]


def test_the_line_coupler_filters_the_whole_line() -> None:
    service = _segment_service(segment_coupler=True)
    # Both segments are behind the line coupler: only 1/2/3 crosses it (to line 1.2).
    assert _routed(service, 1) == [_TUNNEL]
    assert service.passed == [(0x1100, None)]


def test_no_table_for_other_devices() -> None:
    # A device in a sub segment that is no segment coupler, and one on a main segment.
    assert _routed(_segment_service(segment_coupler=False), 3) is None
    assert _routed(_segment_service(segment_coupler=True), 2) is None
