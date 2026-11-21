"""Logic tests for the DALI commissioning tab (gating + async op-runner state machine).

The imgui rendering isn't unit-tested; this covers the parts that decide behaviour: which devices
show the tab, and that a started operation toggles busy / stashes a scan result / surfaces errors.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.plugins.project.ui.dali_commissioning import (
    DaliCommissioningPanel,
    _assignments_from,
    _scenes_from,
)
from editor_gui.programming_dali import (
    dali_channel_count,
    dali_object_index_base,
    is_dali_dca_app,
)
from xknxeditor.dali.model import (
    DaliBusState,
    EcgAssignment,
    EcgState,
    Scene,
    SceneValue,
)


def test_gating_matches_only_dali_dca_apps() -> None:
    assert is_dali_dca_app("M-0083_A-0154-40-0F69-O00EF")  # MDT
    assert is_dali_dca_app("M-0083_A-0153-10-297A-O00EF")  # MDT
    assert is_dali_dca_app("M-0048_A-DA11-21-9400-O00EF")  # Theben P64
    assert not is_dali_dca_app("M-0083_A-9999-40")  # other MDT app
    assert not is_dali_dca_app("M-0001_A-0154")  # other manufacturer
    assert not is_dali_dca_app(None)


def test_object_index_base_is_vendor_specific() -> None:
    assert dali_object_index_base("M-0083_A-0154-40-0F69-O00EF") == 6  # MDT
    assert dali_object_index_base("M-0048_A-DA11-21-9400-O00EF") == 7  # Theben
    assert dali_object_index_base("M-0001_A-0154") == 6  # unknown -> default


def test_channel_count_from_order_number() -> None:
    assert dali_channel_count("SCN-DA641.04") == 1  # 1x64 gateway
    assert dali_channel_count("SCN-DA642.04") == 2  # 2x64 gateway
    assert dali_channel_count("scn-da642") == 2  # case-insensitive
    assert dali_channel_count("Something Else") == 1  # unknown -> single channel
    assert dali_channel_count("") == 1
    assert dali_channel_count(None) == 1


def _device() -> Any:
    return SimpleNamespace(
        node_id=1, app=None, individual_address="1.1.1", name="Gateway"
    )


def _runner(
    commissioner: Any,
    *,
    started: bool = True,
    texts: list[str] | None = None,
    writes: list[bool] | None = None,
):
    """A fake of the queue the panel hands its operations to: runs the wrapped coroutine (like
    the async loop would), swallowing exceptions (the real future captures them), then reports
    the end; ``started=False`` ends it without running it (canceled while it waited).
    ``writes`` gets whether each operation changes something."""

    def run(
        device: Any,
        wrapped: Any,
        channel: int,
        text: str,
        ended: Callable[[], None],
        changes: bool,
    ) -> None:
        if texts is not None:
            texts.append(text)
        if writes is not None:
            writes.append(changes)
        if started:
            with contextlib.suppress(Exception):  # the real future captures the error
                asyncio.run(wrapped(commissioner))
        ended()

    return run


def test_scan_stashes_result_and_clears_busy() -> None:
    bus = DaliBusState(channel=0, ecgs=[EcgState(slot=0, present=True, group_index=5)])
    commissioner = SimpleNamespace(scan=lambda: _coro(bus))
    texts: list[str] = []
    panel = DaliCommissioningPanel(_runner(commissioner, texts=texts))

    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)

    state = panel._state((1, ""), 0)
    assert state.busy == ""  # released
    assert state.error == ""
    assert state.scan is bus
    # Named in the Operations panel as the button that started it.
    assert texts == ["Scan Bus"]


def test_error_is_surfaced_and_busy_released() -> None:
    async def boom(_c: Any) -> object:
        raise RuntimeError("no ack")

    commissioner = SimpleNamespace()
    panel = DaliCommissioningPanel(_runner(commissioner))
    panel._start(_device(), "commission", "Program Groups…", boom)

    state = panel._state((1, ""), 0)
    assert state.busy == ""
    assert "no ack" in state.error


def test_released_when_it_never_ran() -> None:
    panel = DaliCommissioningPanel(_runner(SimpleNamespace(), started=False))
    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)
    assert panel._state((1, ""), 0).busy == ""  # canceled while it waited in the queue


def test_the_end_of_an_older_operation_keeps_a_newer_one() -> None:
    ends: list[Callable[[], None]] = []

    def run(
        device: Any,
        wrapped: Any,
        channel: int,
        text: str,
        ended: Callable[[], None],
        _writes: bool,
    ) -> None:
        ends.append(ended)

    panel = DaliCommissioningPanel(run)
    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan())
    state = panel._state((1, ""), 0)
    assert state.busy == "scan"
    # A second start while the first is queued is ignored.
    panel._start(_device(), "broadcast", "All Off", lambda c: c.broadcast(False))
    assert len(ends) == 1
    ends[0]()
    assert state.busy == ""
    panel._start(_device(), "broadcast", "All Off", lambda c: c.broadcast(False))
    ends[0]()  # the first one's end, once more
    assert state.busy == "broadcast"
    ends[1]()
    assert state.busy == ""


async def _coro(value: Any) -> Any:
    return value


@pytest.mark.parametrize("operation", ["groups", "scenes"])
def test_persistence_uses_only_completed_operation_snapshot(operation):
    queued = []
    saved = []

    def persist(*args):
        saved.append(args)
        return True

    panel = DaliCommissioningPanel(lambda *args: queued.append(args), persist)
    device = _device()
    key = panel._key(device)
    state = panel._state(key, 0)
    state.scan = DaliBusState(
        channel=0, ecgs=[EcgState(slot=0, present=True, group_index=1, ets_ecg_index=0)]
    )
    state.group_edits = {0: 5}
    state.scene_edits = {2: {4: 128}}
    if operation == "groups":
        panel._start_program_groups(device, key)
    else:
        panel._start_program_scenes(device, key)
    state.group_edits[0] = 9
    state.scene_edits[2][4] = 200

    async def write_scenes(scenes, *, levels_only):
        assert levels_only

    commissioner = SimpleNamespace(
        commission=lambda _: _coro(True), write_scenes=write_scenes
    )
    asyncio.run(queued[0][1](commissioner))
    panel._persist_backup(device, 0, state)
    assert len(saved) == 1
    _, scan, assignments, scenes, channel = saved[0]
    assert channel == 0
    if operation == "groups":
        assert [(a.slot, a.group_index) for a in assignments] == [(0, 5)]
        assert scan.ecgs[0].group_index == 5
        assert scenes == []
    else:
        assert assignments == []
        assert scenes[0].values[0].level == 128
    assert state.group_edits == {0: 9}
    assert state.scene_edits == {2: {4: 200}}
    assert not state.persist_pending


def test_failed_write_does_not_persist():
    async def fail(_):
        raise RuntimeError("write failed")

    panel = DaliCommissioningPanel(_runner(SimpleNamespace(commission=fail)))
    device = _device()
    key = panel._key(device)
    state = panel._state(key, 0)
    state.scan = DaliBusState(channel=0, ecgs=[EcgState(slot=0, present=True)])
    state.group_edits[0] = 5
    panel._start_program_groups(device, key)
    assert not state.persist_pending
    assert "write failed" in state.error


def test_missing_e64_backup_is_not_reported_saved():
    from editor_gui.programming_dali import persist_dali_backup

    device = _device()
    device.app = SimpleNamespace(id="M-00EF_A-DA11-22-A545")
    project = SimpleNamespace(
        find_device_by_node_id=lambda _: device,
        device_binary_data=lambda *_: None,
        persist_dali_backup=lambda *_: pytest.fail("no backup to persist"),
    )
    assert not persist_dali_backup(
        project, device, DaliBusState(0), [], [], 0, label="DALI"
    )


@pytest.mark.parametrize("switch_project", [False, True])
def test_completed_write_saves_without_rendering_tab(switch_project):
    queued, saved = [], []
    generation = [1]

    def persist(*args):
        saved.append(args)
        return True

    panel = DaliCommissioningPanel(
        lambda *args: queued.append(args), persist, _generation=lambda: generation[0]
    )
    device = _device()
    key = panel._key(device)
    state = panel._state(key, 0)
    state.scan = DaliBusState(0, ecgs=[EcgState(0, present=True)])
    state.group_edits[0] = 5
    panel._start_program_groups(device, key)
    if switch_project:
        generation[0] += 1
    asyncio.run(queued[0][1](SimpleNamespace(commission=lambda _: _coro(True))))
    queued[0][4]()
    assert len(saved) == (0 if switch_project else 1)


def test_queued_completion_persists_on_ui_thread():
    import threading
    from concurrent.futures import Future

    from editor_gui.plugins.project.program_queue import ProgramQueue, QueueItem

    ui_calls, operations, saved_on = [], [], []
    future = Future()
    queue = ProgramQueue(
        is_busy=lambda: False,
        start=lambda _: future,
        submit=ui_calls.append,
    )

    def run(device, op, channel, text, ended, writes):
        operations.append(op)
        item = QueueItem(device.node_id, "1.1.1", "DALI", None, ended=Future())
        item.ended.add_done_callback(lambda _: ended())
        queue.enqueue(item)

    def persist(*args):
        saved_on.append(threading.get_ident())
        return True

    panel = DaliCommissioningPanel(run, persist)
    device = _device()
    key = panel._key(device)
    state = panel._state(key, 0)
    state.scan = DaliBusState(0, ecgs=[EcgState(0, present=True)])
    state.group_edits[0] = 5
    panel._start_program_groups(device, key)

    def finish():
        result = asyncio.run(
            operations[0](SimpleNamespace(commission=lambda _: _coro(True)))
        )
        future.set_result(result)

    worker = threading.Thread(target=finish)
    worker.start()
    worker.join()
    assert not saved_on
    assert len(ui_calls) == 1
    ui_calls.pop()()
    assert saved_on == [threading.get_ident()]
    assert not state.persist_pending


def test_assignments_from_applies_group_edits() -> None:
    bus = DaliBusState(
        channel=0,
        ecgs=[
            EcgState(slot=0, present=True, group_index=5, ets_ecg_index=0),
            EcgState(slot=3, present=True, group_index=2, ets_ecg_index=3),
            EcgState(slot=7, present=False, group_index=255, ets_ecg_index=255),
        ],
    )
    assignments = _assignments_from(bus, {0: 7})  # move slot 0 to group 7
    assert assignments == [
        EcgAssignment(slot=0, ets_index=0, group_index=7),  # edited
    ]
    assert _assignments_from(bus, {}) == []


def test_scenes_from_builds_sorted_values_and_skips_empty() -> None:
    scenes = _scenes_from({0: {5: 254, 2: 100}, 1: {}})
    assert scenes == [
        Scene(
            index=0,
            knx_scene=1,
            values=[
                SceneValue(scene=0, target_index=2, level=100),
                SceneValue(scene=0, target_index=5, level=254),
            ],
        )
    ]  # scene 1 (empty) dropped, values sorted by group


def test_new_scan_clears_stale_group_edits() -> None:
    bus = DaliBusState(channel=0, ecgs=[EcgState(slot=0, present=True, group_index=5)])
    commissioner = SimpleNamespace(scan=lambda: _coro(bus))
    panel = DaliCommissioningPanel(_runner(commissioner))
    panel._state((1, ""), 0).group_edits[0] = 9  # stale edit from a prior scan

    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)

    assert panel._state((1, ""), 0).group_edits == {}  # dropped on fresh bus state


def test_only_a_scan_is_marked_as_writing_nothing() -> None:
    writes: list[bool] = []
    commissioner = SimpleNamespace(
        scan=lambda: _coro(DaliBusState(channel=0, ecgs=[])),
        broadcast=lambda _on: _coro(None),
    )
    panel = DaliCommissioningPanel(_runner(commissioner, writes=writes))
    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)
    panel._start(_device(), "broadcast", "All On", lambda c: c.broadcast(True))
    # Block Bus Writes refuses all but the scan before they start.
    assert writes == [False, True]


def test_dali_state_survives_readdress_and_late_results_stay_in_old_project():
    generation = [1]
    queued = []
    panel = DaliCommissioningPanel(
        lambda *args: queued.append(args), _generation=lambda: generation[0]
    )
    device = _device()
    key = panel._key(device)
    state = panel._state(key, 0)
    state.scene_edits[1] = {2: 50}
    state.pending = "scenes"
    device.individual_address = "1.2.3"
    assert panel._state(panel._key(device), 0) is state
    other = SimpleNamespace(node_id=2, app=None, individual_address="1.2.3")
    assert not panel._state(panel._key(other), 0).scene_edits
    assert not panel._state(key, 1).pending
    scan = DaliBusState(channel=0, ecgs=[])
    panel._start(device, "scan", "Scan", lambda _: _coro(scan), store_scan=True)
    generation[0] += 1
    fresh = panel._state(panel._key(device), 0)
    assert fresh is not state and not fresh.scene_edits and not fresh.pending
    asyncio.run(queued[0][1](None))
    queued[0][4]()
    assert state.scan is scan
    assert fresh.scan is None


def test_siemens_dali_results_are_isolated_by_project_and_device():
    from editor_gui.plugins.project.ui.siemens_dali import SiemensDaliPanel
    from xknxeditor.dali.siemens import SiemensN141BusState

    generation = [1]
    queued = []
    panel = SiemensDaliPanel(
        lambda *args: queued.append(args), _generation=lambda: generation[0]
    )
    device = _device()
    scan = SiemensN141BusState(line=0, object_index=1, ecgs=[])
    panel._start(device, "scan", "Scan", lambda _: _coro(scan))
    state = panel._state(panel._key(device))
    device.individual_address = "1.2.3"
    assert panel._state(panel._key(device)) is state
    generation[0] += 1
    fresh = panel._state(panel._key(device))
    asyncio.run(queued[0][1](None))
    queued[0][3]()
    assert state.scan is scan and fresh.scan is None


def test_siemens_twin_scan_uses_selected_line(monkeypatch):
    from unittest.mock import AsyncMock

    from imgui_bundle import imgui

    from editor_gui.plugins.project.strings import S
    from editor_gui.plugins.project.ui.siemens_dali import SiemensDaliPanel
    from xknxeditor.dali.siemens import SiemensN141BusState

    queued = []
    panel = SiemensDaliPanel(lambda *args: queued.append(args))
    device = _device()
    device.app = SimpleNamespace(id="M-0001_A-9834-11-0000")
    state = panel._state(panel._key(device))
    state.scan = SiemensN141BusState(line=0, object_index=35, ecgs=[])
    scan = AsyncMock(return_value=SiemensN141BusState(line=1, object_index=36, ecgs=[]))
    monkeypatch.setattr("editor_gui.plugins.project.ui.siemens_dali.scan_n141_03", scan)
    monkeypatch.setattr(
        "editor_gui.plugins.project.ui.siemens_dali.combo", lambda *_: (True, 1)
    )
    monkeypatch.setattr(imgui, "button", lambda text: text == S.SIEMENS_DALI_SCAN_BUS)
    context = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    try:
        imgui.new_frame()
        imgui.begin("DALI")
        panel.render(device, True)
        imgui.end()
        imgui.end_frame()
    finally:
        imgui.destroy_context(context)
    assert state.scan is None and state.line == 1
    asyncio.run(queued[0][1](None))
    queued[0][3]()
    scan.assert_awaited_once_with(None, line=1)
    assert state.scan.line == 1


@pytest.mark.parametrize("siemens", [False, True])
def test_widget_state_is_scoped_to_project_device_and_channel(monkeypatch, siemens):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.siemens_dali import SiemensDaliPanel

    generation = [1]
    panel_type = SiemensDaliPanel if siemens else DaliCommissioningPanel
    panel = panel_type(lambda *_: None, _generation=lambda: generation[0])
    ids = []
    monkeypatch.setattr(panel, "_render", lambda *_: ids.append(imgui.get_id("field")))
    context = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    device = _device()

    def render():
        imgui.new_frame()
        imgui.begin("DALI")
        panel.render(device, True)
        imgui.end()
        imgui.end_frame()

    try:
        render()
        device.individual_address = "1.2.3"
        render()
        device.node_id = 2
        render()
        generation[0] += 1
        render()
        assert ids[0] == ids[1]
        assert len(set(ids)) == 3
        if not siemens:
            panel._channel_count = lambda _: 2
            panel._active_channel[panel._key(device)] = 1
            render()
            assert len(set(ids)) == 4
    finally:
        imgui.destroy_context(context)
