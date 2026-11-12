"""Tests for the address-range bus scan."""

from __future__ import annotations

import pytest
from _fakes import FakeConnection, FakeXknx
from xknx.telegram import IndividualAddress

from xknxeditor.recover.identify import (
    OBJECT_TYPE_APPLICATION_PROGRAM,
    PID_PROGRAM_VERSION,
    AppId,
)
from xknxeditor.recover.scan import (
    DiscoveredDevice,
    iter_addresses,
    probe_and_identify,
    probe_device,
    scan_bus,
)


def test_iter_addresses_walks_device_then_line() -> None:
    addresses = [str(a) for a in iter_addresses("1.1.254", "1.2.1")]
    assert addresses == ["1.1.254", "1.1.255", "1.2.0", "1.2.1"]


def test_iter_addresses_single() -> None:
    assert [str(a) for a in iter_addresses("1.1.5", "1.1.5")] == ["1.1.5"]


def test_iter_addresses_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="after end"):
        list(iter_addresses("1.1.10", "1.1.1"))


async def test_probe_device_returns_responder() -> None:
    xknx = FakeXknx({"1.1.5": FakeConnection(descriptor=0x07B0)})
    device = await probe_device(xknx, IndividualAddress("1.1.5"))
    assert device == DiscoveredDevice(address="1.1.5", mask_version=0x07B0)
    # The connection is always closed again.
    assert xknx.management.disconnected == ["1.1.5"]


async def test_probe_device_absent_returns_none() -> None:
    xknx = FakeXknx({})
    assert await probe_device(xknx, IndividualAddress("1.1.9")) is None


async def test_probe_and_identify_uses_single_connection() -> None:
    connection = FakeConnection(
        descriptor=0x0705,
        object_types={0: 0, 1: 1, 2: 2, 3: OBJECT_TYPE_APPLICATION_PROGRAM},
        properties={(3, PID_PROGRAM_VERSION): bytes([0x00, 0x83, 0x00, 0x8A, 0x25])},
    )
    xknx = FakeXknx({"1.1.5": connection})
    device, app_id = await probe_and_identify(xknx, IndividualAddress("1.1.5"))
    assert device == DiscoveredDevice(address="1.1.5", mask_version=0x0705)
    assert app_id == AppId("M-0083", 0x008A, 0x25)
    # One connect/disconnect for both the descriptor and the application id.
    assert xknx.management.connected == ["1.1.5"]
    assert xknx.management.disconnected == ["1.1.5"]


async def test_probe_and_identify_absent_returns_none() -> None:
    xknx = FakeXknx({})
    assert await probe_and_identify(xknx, IndividualAddress("1.1.9")) == (None, None)


async def test_scan_bus_collects_only_responders_with_progress() -> None:
    xknx = FakeXknx(
        {
            "1.1.1": FakeConnection(descriptor=0x0705),
            "1.1.3": FakeConnection(descriptor=0x07B0),
        }
    )
    seen: list[tuple[int, int]] = []
    found = await scan_bus(
        xknx, "1.1.1", "1.1.3", progress=lambda done, total: seen.append((done, total))
    )
    assert found == [
        DiscoveredDevice(address="1.1.1", mask_version=0x0705),
        DiscoveredDevice(address="1.1.3", mask_version=0x07B0),
    ]
    assert seen == [(1, 3), (2, 3), (3, 3)]


async def test_probe_and_identify_bcu1_reads_the_id_from_memory() -> None:
    connection = FakeConnection(
        descriptor=0x0013,
        property_services=False,
        memory={0x0104: bytes([0x02, 0xA0, 0x1E, 0x10])},
    )
    xknx = FakeXknx({"1.1.3": connection})
    device, app_id = await probe_and_identify(xknx, IndividualAddress("1.1.3"))
    assert device == DiscoveredDevice(address="1.1.3", mask_version=0x0013)
    assert app_id == AppId("M-0002", 0xA01E, 0x10)
