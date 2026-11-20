"""Siemens DALI-2 plans, operation lifecycle and narrow-screen rendering."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.siemens_dali2 import SiemensDali2Panel
from editor_gui.programming_siemens_dali2 import prepare_operation
from xknxeditor.dali.siemens_dali2 import (
    Device,
    Instance,
    Scan,
    line_count,
    uses_legacy_identification,
)

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
        ("M-0001_A-9A1B-01-ABCD", 1),
        ("M-0001_A-9A1C-01-33CF", 2),
        ("M-0001_A-9A1C-00-ABCD", 0),
        (None, 0),
        ("M-0001_A-9837-02-ABCD", 0),
    ],
)
def test_supported_line_count(app, count):
    assert line_count(app) == count


@pytest.mark.parametrize("action", ["read", "discover", "address"])
def test_version_one_accepts_inventory_and_address_operations(action):
    operation = prepare_operation(
        "M-0001_A-9A1C-01-33CF",
        action,
        line=1,
        address=7,
        new_address=12,
        random_address=0x123456,
    )
    assert operation.line == 1
    assert operation.writes == (action != "read")


@pytest.mark.parametrize("action", ["on", "off"])
def test_version_one_rejects_sensor_switching_before_queuing(action):
    gateway = device()
    gateway.app.id = "M-0001_A-9A1C-01-33CF"
    panel = SiemensDali2Panel(lambda *args: pytest.fail("Must not queue"))
    panel._state(gateway).sensors = True
    panel._start(gateway, action, action)
    assert "blink and stop only" in panel._state(gateway).error
    assert uses_legacy_identification(gateway.app.id)
    assert not uses_legacy_identification(APP)


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


@pytest.mark.parametrize("app_id", [APP, "M-0001_A-9A1C-01-33CF"])
@pytest.mark.parametrize("sensors,width", [(False, 360), (True, 360), (True, 1100)])
def test_panel_renders_in_narrow_and_wide_windows(sensors, width, app_id):
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(width, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        gateway = device()
        gateway.app.id = app_id
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


async def test_legacy_panel_stop_interrupts_running_session_and_keeps_slot_until_cleanup(
    monkeypatch,
):
    import asyncio

    calls = []
    started = asyncio.Event()
    stopped = asyncio.Event()
    release = asyncio.Event()

    async def identify(*args, stopped: object, **kwargs):
        started.set()
        while not stopped():
            await asyncio.sleep(0)
        done.set()
        await release.wait()

    done = stopped
    commissioner = SimpleNamespace(identify_legacy=identify)
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    gateway = device()
    gateway.app.id = "M-0001_A-9A1C-01-33CF"
    panel = SiemensDali2Panel(lambda *args: calls.append(args))
    panel._start(gateway, "blink", "Blink")
    state = panel._state(gateway)
    target, session = state.session
    task = asyncio.create_task(calls[0][1](object()))
    await started.wait()
    panel._start(gateway, "stop", "Stop")
    await done.wait()
    assert state.busy and not session.ended.done() and len(calls) == 1
    release.set()
    await task
    calls[0][3]()
    assert not state.busy and session.ended.done()
    assert panel._identification.stop(target) is None


def test_legacy_panel_stop_before_queue_start_and_cancelled_queue_cleanup():
    calls = []
    gateway = device()
    gateway.app.id = "M-0001_A-9A1C-01-33CF"
    panel = SiemensDali2Panel(lambda *args: calls.append(args))
    panel._start(gateway, "blink", "Blink")
    state = panel._state(gateway)
    target, session = state.session
    panel._start(gateway, "stop", "Stop")
    assert session.stop.is_set() and not session.ended.done()
    calls[0][3]()
    assert session.ended.done() and panel._identification.stop(target) is None


def test_identification_control_separates_projects_and_propagates_cleanup_error():
    from editor_gui.programming_siemens_dali2 import IdentificationControl

    control = IdentificationControl()
    target = (1, 7, APP, "1.1.20", 0, False)
    session = control.start(target)
    with pytest.raises(ValueError, match="already running"):
        control.start(target)
    assert control.stop((2, *target[1:])) is None
    assert not session.stop.is_set()
    assert control.stop(target) is session
    error = ValueError("cleanup failed")
    control.finish(target, session, error)
    with pytest.raises(ValueError, match="cleanup failed"):
        session.ended.result()
    assert control.stop(target) is None
