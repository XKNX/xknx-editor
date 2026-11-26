"""Siemens DALI-2 plans, operation lifecycle and narrow-screen rendering."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui.siemens_dali2 import SiemensDali2Panel
from editor_gui.programming_siemens_dali2 import prepare_operation
from xknxeditor.dali.siemens_dali2 import (
    ColorTemperatureState,
    Device,
    GearState,
    Instance,
    RgbwState,
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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "status", "sensors": True},
        {"action": "level", "sensors": True},
        {"action": "level", "level": 255},
        {"action": "level", "level": True},
    ],
)
def test_invalid_gear_test_never_reaches_queue(kwargs):
    with pytest.raises(ValueError):
        prepare_operation(APP, **kwargs)


@pytest.mark.parametrize("version", ["01", "02", "03", "30"])
async def test_gear_tests_use_shared_plan_on_selected_line(monkeypatch, version):
    result = GearState(1, 7, 2, 99, 0, 0)
    commissioner = SimpleNamespace(
        read_gear_state=AsyncMock(return_value=result),
        set_level=AsyncMock(return_value=result),
    )
    factory = []

    def create(*args):
        factory.append(args)
        return commissioner

    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner", create
    )
    gateway = device()
    gateway.app.id = f"M-0001_A-9A1C-{version}-ABCD"
    calls = []
    panel = SiemensDali2Panel(lambda *args: calls.append(args))
    state = panel._state(gateway)
    state.line, state.address, state.level = 1, 7, 120
    state.gear_states[(0, 7)] = GearState(0, 7, 0, 20, 0, 0)
    panel._start(gateway, "status", "Status")
    assert calls[-1][-1] is True
    await calls[-1][1](object())
    calls[-1][3]()
    assert state.gear_states[(1, 7)] is result and not state.busy
    commissioner.read_gear_state.assert_awaited_once_with(7, timeout=15)
    panel._start(gateway, "level", "Test")
    assert (1, 7) not in state.gear_states and (0, 7) in state.gear_states
    await calls[-1][1](object())
    calls[-1][3]()
    commissioner.set_level.assert_awaited_once_with(7, 120, timeout=15)
    assert all(args[1] == 1 for args in factory)
    assert state.gear_states[(1, 7)] is result


def test_refresh_clears_only_queried_line_status():
    panel = SiemensDali2Panel(lambda *args: None)
    state = panel._state(device())
    for line in (0, 1):
        state.gear_states[(line, 7)] = GearState(line, 7, 0, 99, 0, 0)
    panel._store(state, prepare_operation(APP, "read", sensors=True), inventory(True))
    assert len(state.gear_states) == 2
    panel._store(state, prepare_operation(APP, "read", line=1), inventory(line=1))
    assert set(state.gear_states) == {(0, 7)}


@pytest.mark.parametrize("version", ["02", "03", "30"])
@pytest.mark.parametrize("action", ["temperature", "rgbw"])
@pytest.mark.parametrize("write", [False, True])
async def test_color_plan_and_panel_preserve_target_and_actual_result(
    monkeypatch, version, action, write
):
    result = (
        ColorTemperatureState(1, 7, 370)
        if action == "temperature"
        else RgbwState(1, 7, 1, 2, 3, 4)
    )
    commissioner = SimpleNamespace(
        color_temperature=AsyncMock(return_value=result),
        rgbw=AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    gateway = device()
    gateway.app.id = f"M-0001_A-9A1C-{version}-ABCD"
    queued = []
    panel = SiemensDali2Panel(lambda *args: queued.append(args))
    state = panel._state(gateway)
    state.line, state.address, state.kelvin, state.rgbw = 1, 7, 4000, (5, 6, 7, 8)
    state.colors[(0, 7, action)] = result
    state.colors[(1, 7, action)] = result
    panel._start(gateway, action, action, set_color=write)
    assert (1, 7, action) not in state.colors and (0, 7, action) in state.colors
    assert len(queued) == 1 and queued[0][-1]
    await queued[0][1](object())
    queued[0][3]()
    assert state.colors[(1, 7, action)] is result and not state.busy
    method = (
        commissioner.color_temperature if action == "temperature" else commissioner.rgbw
    )
    method.assert_awaited_once_with(
        7,
        (4000 if action == "temperature" else (5, 6, 7, 8)) if write else None,
        timeout=15,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "temperature", "kelvin": 999},
        {"action": "temperature", "kelvin": 20001},
        {"action": "rgbw", "rgbw": (1, 2, 3, 256)},
        {"action": "rgbw", "rgbw": (1, 2, 3)},
        {"action": "temperature", "sensors": True},
        {"action": "rgbw", "sensors": True},
    ],
)
def test_invalid_color_plan_never_queues(kwargs):
    with pytest.raises(ValueError):
        prepare_operation(APP, **kwargs)


@pytest.mark.parametrize("action", ["temperature", "rgbw"])
def test_version_one_color_service_is_not_guessed(action):
    with pytest.raises(ValueError, match="version"):
        prepare_operation("M-0001_A-9A1C-01-33CF", action)


def test_color_cache_cleared_by_line_refresh_and_gateway_address_change():
    gateway = device()
    panel = SiemensDali2Panel(lambda *args: None)
    state = panel._state(gateway)
    for line in (0, 1):
        state.colors[(line, 7, "temperature")] = ColorTemperatureState(line, 7, 250)
    panel._store(state, prepare_operation(APP, "read", line=1), inventory(line=1))
    assert set(state.colors) == {(0, 7, "temperature")}
    gateway.individual_address = "1.1.21"
    assert not panel._state(gateway).colors and not panel._state(gateway).scans


@pytest.mark.parametrize("action", ["temperature", "rgbw"])
@pytest.mark.parametrize("write", [False, True])
async def test_color_buttons_reach_selected_gear(monkeypatch, action, write):
    from editor_gui.plugins.project.strings import S

    queued = []
    panel = SiemensDali2Panel(lambda *args: queued.append(args))
    gateway = device()
    state = panel._state(gateway)
    state.line, state.address = 1, 7
    stack = []
    push, pop = imgui.push_id, imgui.pop_id

    def push_id(value):
        stack.append(value)
        push(value)

    def pop_id():
        stack.pop()
        pop()

    monkeypatch.setattr(imgui, "push_id", push_id)
    monkeypatch.setattr(imgui, "pop_id", pop_id)
    monkeypatch.setattr(
        imgui,
        "button",
        lambda text: (
            stack[-1] == action
            and text == (S.E64_TEST if write else S.E64_BUS_ACTIONS["read"])
        ),
    )
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(320, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        imgui.new_frame()
        imgui.begin("Colors")
        panel._colors(gateway, state)
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    assert len(queued) == 1 and queued[0][-1]
    queued[0][3]()
    assert not state.busy


@pytest.mark.parametrize("action", ["temperature", "rgbw"])
async def test_failed_color_test_drops_both_previous_colors(monkeypatch, action):
    commissioner = SimpleNamespace(
        color_temperature=AsyncMock(side_effect=ValueError("color failed")),
        rgbw=AsyncMock(side_effect=ValueError("color failed")),
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    queued = []
    panel = SiemensDali2Panel(lambda *args: queued.append(args))
    gateway = device()
    state = panel._state(gateway)
    state.address = 7
    state.colors[(0, 7, "temperature")] = ColorTemperatureState(0, 7, 250)
    state.colors[(0, 7, "rgbw")] = RgbwState(0, 7, 1, 2, 3, 4)
    panel._start(gateway, action, action, set_color=True)
    assert not state.colors
    with pytest.raises(ValueError, match="color failed"):
        await queued[0][1](object())
    queued[0][3]()
    assert not state.busy and not state.colors and state.error == "color failed"


@pytest.mark.parametrize("action", ["status", "level"])
async def test_gear_test_button_queues_selected_gear_on_second_line(
    monkeypatch, action
):
    from editor_gui.plugins.project.strings import S

    queued = []
    result = GearState(1, 7, None, None, 1, 2)
    commissioner = SimpleNamespace(
        read_gear_state=AsyncMock(return_value=result),
        set_level=AsyncMock(return_value=result),
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    label = S.E64_STATUS if action == "status" else S.E64_TEST
    monkeypatch.setattr(imgui, "button", lambda text: text == label)
    panel = SiemensDali2Panel(lambda *args: queued.append(args))
    gateway = device()
    state = panel._state(gateway)
    state.line, state.address, state.level = 1, 7, 99
    state.scans[(1, False)] = inventory(line=1)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        imgui.new_frame()
        imgui.begin("DALI-2")
        panel.render(gateway, True)
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    assert len(queued) == 1 and queued[0][-1]
    await queued[0][1](object())
    queued[0][3]()
    assert state.gear_states[(1, 7)] is result
    if action == "status":
        commissioner.read_gear_state.assert_awaited_once_with(7, timeout=15)
    else:
        commissioner.set_level.assert_awaited_once_with(7, 99, timeout=15)


async def test_failed_status_refresh_drops_previous_values_and_releases_panel(
    monkeypatch,
):
    commissioner = SimpleNamespace(
        read_gear_state=AsyncMock(side_effect=ValueError("query failed"))
    )
    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner",
        lambda *args: commissioner,
    )
    queued = []
    panel = SiemensDali2Panel(lambda *args: queued.append(args))
    gateway = device()
    state = panel._state(gateway)
    state.address = 7
    state.gear_states[(0, 7)] = GearState(0, 7, 0, 99, 0, 0)
    panel._start(gateway, "status", "Status")
    assert not state.gear_states
    with pytest.raises(ValueError, match="query failed"):
        await queued[0][1](object())
    queued[0][3]()
    assert not state.busy and not state.gear_states and state.error == "query failed"


@pytest.mark.parametrize(
    "change", ["project", "removed", "address", "application", "replaced"]
)
def test_panel_keeps_guard_through_wrapper_and_rejects_changed_target(change):
    gateway = device()
    current = [gateway]
    project = SimpleNamespace(generation=1, find_device_by_node_id=lambda _: current[0])
    calls = []
    panel = SiemensDali2Panel(
        lambda *args: calls.append(args), lambda: project.generation, _project=project
    )
    panel._state(gateway).address = 7
    panel._start(gateway, "status", "Status")
    operation = calls[0][1]
    operation.validate_device(gateway)
    if change == "project":
        project.generation += 1
    elif change == "removed":
        current[0] = None
    elif change == "address":
        gateway.individual_address = "1.1.21"
    elif change == "application":
        gateway.app.id = "changed"
    else:
        current[0] = device()
        current[0].individual_address = "1.1.21"
    with pytest.raises(ValueError):
        operation.validate_device(gateway)
    calls[0][3]()
    assert not panel._state(gateway).busy


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


async def test_panel_selects_second_line_and_input_inventory(monkeypatch):
    from editor_gui.plugins.project.strings import S

    calls = []
    scan = inventory(sensors=True, line=1)
    commissioner = SimpleNamespace(read_cached=AsyncMock(return_value=scan))
    factory = []

    def create(*args):
        factory.append(args)
        return commissioner

    monkeypatch.setattr(
        "editor_gui.programming_siemens_dali2.SiemensDali2Commissioner", create
    )
    monkeypatch.setattr(
        "editor_gui.plugins.project.ui.siemens_dali2.combo", lambda *_: (True, 1)
    )
    monkeypatch.setattr(imgui, "button", lambda text: text == S.E64_BUS_ACTIONS["read"])
    panel = SiemensDali2Panel(lambda *args: calls.append(args))
    gateway = device()
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        imgui.new_frame()
        imgui.begin("DALI-2")
        panel.render(gateway, True)
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    assert len(calls) == 1 and calls[0][-1] is False
    await calls[0][1](object())
    calls[0][3]()
    assert factory[0][1] == 1
    commissioner.read_cached.assert_awaited_once_with(sensors=True, timeout=300)
    assert panel._state(gateway).scans[(1, True)] is scan


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
        state.address = 7
        state.gear_states[(0, 7)] = GearState(0, 7, 2, 99, 0, 0)
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


def test_hint_before_a_scan_names_the_scan_buttons(monkeypatch):
    from editor_gui.plugins.project.strings import S

    buttons: list[str] = []
    hints: list[str] = []

    def button(text):
        buttons.append(text)
        return False

    monkeypatch.setattr(imgui, "button", button)
    monkeypatch.setattr(imgui, "text_disabled", hints.append)
    panel = SiemensDali2Panel(lambda *args: None)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        imgui.new_frame()
        imgui.begin("DALI-2")
        panel.render(device(), True)
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    assert S.SIEMENS_DALI2_NO_SCAN in hints
    scan_buttons = [S.E64_BUS_ACTIONS["read"], S.DALI_SCAN]
    assert all(label in buttons for label in scan_buttons)
    assert all(f"'{label}'" in S.SIEMENS_DALI2_NO_SCAN for label in scan_buttons)
