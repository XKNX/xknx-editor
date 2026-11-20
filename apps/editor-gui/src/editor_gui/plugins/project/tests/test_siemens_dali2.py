"""Siemens DALI-2 plans, operation lifecycle and narrow-screen rendering."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.siemens_dali2 import SiemensDali2Panel
from editor_gui.programming_siemens_dali2 import prepare_operation
from xknxeditor.dali.siemens_dali2 import Device, Instance, Scan, line_count

APP = "M-0001_A-9A1C-30-B1F5"


def device():
    return SimpleNamespace(
        node_id=3, app=SimpleNamespace(id=APP), individual_address="1.1.20"
    )


def inventory(sensors=False, line=0):
    return Scan(
        line,
        sensors,
        1,
        (Device(7, 0x123456, 0, 2, (6, 8), 24, (Instance(3, 4, 12),)),),
        b"\x08\x01",
    )


@pytest.mark.parametrize(
    "app,count",
    [
        (APP, 2),
        ("M-0001_A-9A1B-02-ABCD", 1),
        ("m-0001_a-9a1b-03-abcd-O0001", 1),
        ("M-0001_A-9A1B-01-ABCD", 0),
        (None, 0),
        ("M-0001_A-9837-02-ABCD", 0),
    ],
)
def test_supported_line_count(app, count):
    assert line_count(app) == count


@pytest.mark.parametrize(
    "kwargs",
    [
        {"line": 2},
        {"address": 64},
        {"address": True},
        {"action": "unknown"},
        {"timeout": 0},
        {"timeout": float("inf")},
        {"action": "address", "address": 7, "new_address": 7, "random_address": 1},
        {"action": "address", "address": 7, "new_address": 8, "random_address": 0},
    ],
)
def test_invalid_plan_is_rejected_before_bus_access(kwargs):
    with pytest.raises(ValueError):
        prepare_operation(APP, **({"action": "blink"} | kwargs))


async def test_panel_uses_shared_plans_and_keeps_completed_scan(monkeypatch):
    calls = []
    scan = inventory()
    commissioner = SimpleNamespace(
        read_cached=AsyncMock(return_value=scan),
        identify=AsyncMock(),
        change_short_address=AsyncMock(return_value=scan),
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    panel = SiemensDali2Panel(lambda *args: calls.append(args))
    gateway = device()
    panel._start(gateway, "read", "read")
    assert calls[-1][-1] is False
    await calls[-1][1](object())
    calls[-1][3]()
    state = panel._state(gateway)
    assert state.scans[(0, False)] is scan and not state.busy
    panel._start(gateway, "blink", "blink")
    assert calls[-1][-1] is True
    await calls[-1][1](object())
    calls[-1][3]()
    assert state.scans[(0, False)] is scan
    commissioner.identify.assert_awaited_once()
    state.address, state.target = 7, 12
    panel._start(gateway, "address", "address")
    await calls[-1][1](object())
    calls[-1][3]()
    commissioner.change_short_address.assert_awaited_once_with(
        7, 12, 0x123456, sensors=False, timeout=300
    )
    assert state.address == 12


def test_panel_cancellation_generation_and_late_completion():
    calls = []
    generation = [0]
    panel = SiemensDali2Panel(lambda *args: calls.append(args), lambda: generation[0])
    gateway = device()
    panel._start(gateway, "read", "read")
    state = panel._state(gateway)
    panel._start(gateway, "read", "read")
    assert len(calls) == 1
    calls[0][3]()
    panel._start(gateway, "read", "read")
    calls[0][3]()
    assert state.busy
    generation[0] += 1
    new_state = panel._state(gateway)
    assert new_state is not state
    calls[1][3]()
    assert not new_state.busy and not new_state.scans


async def test_panel_displays_execution_and_queue_errors(monkeypatch):
    calls = []
    commissioner = SimpleNamespace(
        read_cached=AsyncMock(side_effect=ValueError("broken cache"))
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    gateway = device()
    panel = SiemensDali2Panel(lambda *args: calls.append(args))
    panel._start(gateway, "read", "read")
    with pytest.raises(ValueError, match="broken cache"):
        await calls[0][1](object())
    calls[0][3]()
    assert panel._state(gateway).error == "broken cache"
    assert not panel._state(gateway).busy

    def fail(*args):
        raise ValueError("queue failed")

    panel._run = fail
    panel._start(gateway, "read", "read")
    assert panel._state(gateway).error == "queue failed"
    assert not panel._state(gateway).busy


@pytest.mark.parametrize("sensors,width", [(False, 360), (True, 360), (True, 1100)])
def test_panel_renders_in_narrow_and_wide_windows(sensors, width):
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(width, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        gateway = device()
        panel = SiemensDali2Panel(lambda *args: None)
        state = panel._state(gateway)
        state.sensors = sensors
        state.scans[(0, sensors)] = inventory(sensors)
        for _ in range(3):
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(width, 700))
            imgui.begin("DALI-2")
            panel.render(gateway, True)
            imgui.end()
            imgui.end_frame()
    finally:
        imgui.destroy_context(context)
