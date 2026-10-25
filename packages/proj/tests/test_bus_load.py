"""The number of devices and the bus current of lines and segments, with the 10 mA estimate for
devices without data."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.bus_load import (
    BusLoad,
    Hardware,
    device_current,
    needs_estimate,
    shown_current,
    total,
)
from xknxeditor.proj.core.medium import MEDIUM_IOT, MEDIUM_IP, MEDIUM_RF, MEDIUM_TP


def test_what_a_device_adds() -> None:
    sensor = Hardware(bus_current=12.5)
    supply = Hardware(bus_current=None, power_supply=True)
    assert device_current(sensor, has_address=True, estimate=True) == (12.5, False)
    assert device_current(supply, has_address=True, estimate=True) == (0.0, False)
    assert device_current(sensor, has_address=False, estimate=True) == (0.0, False)
    assert device_current(None, has_address=True, estimate=True) == (10.0, True)
    assert device_current(Hardware(0.0), has_address=True, estimate=True) == (
        10.0,
        True,
    )
    assert device_current(None, has_address=True, estimate=False) == (0.0, False)
    assert total([(12.5, False), (10.0, True)], 3) == BusLoad(3, 22.5, True)
    assert total([], 0) == BusLoad()


def test_which_devices_are_estimated() -> None:
    coupler = Hardware(coupler=True)
    assert needs_estimate(None, MEDIUM_TP, MEDIUM_TP)
    assert not needs_estimate(None, MEDIUM_IOT, MEDIUM_IP)
    assert not needs_estimate(None, MEDIUM_RF, MEDIUM_RF)
    assert needs_estimate(coupler, MEDIUM_RF, MEDIUM_RF)
    assert not needs_estimate(coupler, MEDIUM_IOT, MEDIUM_IP)


def test_what_a_device_shows() -> None:
    assert shown_current(Hardware(5.0), estimate=False) == (5.0, False)
    assert shown_current(None, estimate=True) == (10.0, True)
    assert shown_current(Hardware(power_supply=True), estimate=True) == (320.0, True)
    assert shown_current(None, estimate=False) == (None, False)


def test_lines_and_segments(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    line = next(ln for ln in area.lines if ln.address == 1)
    main = line.segments[0].id
    sub = svc.add_segment(pid, line.id)
    svc.add_device(pid, main, "P-1", address=1, hardware2program_ref_id="H-12")
    svc.add_device(pid, main, "P-2", address=2, hardware2program_ref_id="H-none")
    svc.add_device(pid, main, "P-3", address=3, hardware2program_ref_id="H-power")
    svc.add_device(pid, main, "P-4", hardware2program_ref_id="H-12")
    svc.add_device(pid, sub, "P-1", address=5, hardware2program_ref_id="H-12")
    hardware = {
        "H-12": Hardware(12.0),
        "H-power": Hardware(None, power_supply=True),
    }
    lines, segments = svc.bus_loads(pid, hardware)
    # The main segment: 12 + an estimated 10, the power supply and the device without an
    # address add nothing.
    assert segments[main] == BusLoad(4, 22.0, True)
    assert segments[sub] == BusLoad(1, 12.0, False)
    # The line counts the devices of all its segments, its current is its main segment's.
    assert lines[line.id] == BusLoad(5, 22.0, True)
    empty = next(ln for ln in area.lines if ln.address == 0)
    assert lines[empty.id] == BusLoad()

    device = svc.add_device(pid, sub, "P-3", address=6, hardware2program_ref_id="H-p")
    assert svc.device_bus_current(pid, device, Hardware(power_supply=True)) == (
        320.0,
        True,
    )
    assert svc.device_bus_current(pid, device, Hardware(8.0)) == (8.0, False)
