"""Queued readback and memory preview keep device and project identity intact."""

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.memory_read import MemoryReadRequest, MemoryReadResult, PreviewSegment
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import OperationState, QueueItem
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.memory_preview import MemoryPreviewWindow


def test_read_result_cannot_follow_a_device_or_project_switch():
    generation = [1]
    device = SimpleNamespace(node_id=1, individual_address="1.1.1", app=None)
    window = MemoryPreviewWindow(lambda: [], generation=lambda: generation[0])
    window.open(cast(Any, device))
    request = MemoryReadRequest("x", 0x100, 3)
    result = MemoryReadResult(request, 0x100, b"xyz")
    item = QueueItem(1, "1.1.1", "", None, ended=Future())
    window._read_item = item
    item.result = result
    item.state = OperationState.FINISHED
    item.ended.set_result(item)
    segments = {"x": PreviewSegment(request, b"abc")}
    window._poll_read(segments)
    assert window._read_data["x"] == result
    window._read_item = item
    generation[0] += 1
    window.open(cast(Any, device))
    window._poll_read(segments)
    assert not window._read_data and not window._ref_data
    assert window._read_item is None


def test_read_button_and_save_use_device_bytes(tmp_path, monkeypatch):
    from editor_gui.plugins.project.ui import memory_preview as module

    device = SimpleNamespace(node_id=1, individual_address="1.1.1", app=None)
    request = MemoryReadRequest("x", 0x100, 3)
    item = QueueItem(1, "1.1.1", "", None, ended=Future())
    asked = []
    window = MemoryPreviewWindow(
        lambda: [], read_memory=lambda *args: asked.append(args) or item
    )
    window.open(cast(Any, device))
    window._segments = {"x": b"abc"}
    context = imgui.create_context()
    clicked = [S.MEMORY_READ]
    monkeypatch.setattr(
        imgui, "button", lambda label: label.startswith(clicked[0] + "##")
    )
    path = tmp_path / "read.bin"
    dialog = SimpleNamespace(ready=lambda: True, result=lambda: str(path))
    monkeypatch.setattr(module.file_dialogs, "save_file", lambda *_args: dialog)
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(420, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        for _ in range(2):
            imgui.new_frame()
            imgui.begin("Preview")
            window._render_seg_toolbar("x", b"abc", 0)
            imgui.end()
            imgui.end_frame()
        assert asked == [(1, "x")]
        item.result = MemoryReadResult(request, 0x100, b"xyz")
        item.state = OperationState.FINISHED
        item.ended.set_result(item)
        window._poll_read({"x": PreviewSegment(request, b"abc")})
        clicked[0] = S.MEMORY_SAVE
        imgui.new_frame()
        imgui.begin("Preview")
        window._render_seg_toolbar("x", b"abc", 0)
        imgui.end()
        imgui.end_frame()
        window._segments = {"x": b"changed"}
        window._poll_dialogs()
        assert path.read_bytes() == b"xyz"
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize("change", ["generation", "address", "application", "layout"])
def test_queued_read_rejects_changed_device(change, monkeypatch):
    from editor_gui import memory_read
    from editor_gui.plugins.project.program_queue import ProgramQueue

    device = SimpleNamespace(
        node_id=1,
        individual_address="1.1.1",
        name="Device",
        app=SimpleNamespace(id="app"),
    )
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _node: device,
        prepared_download=lambda _device: None,
    )
    called = []
    connection = SimpleNamespace(read_memory=lambda *_args, **_kw: called.append(True))
    queue = ProgramQueue(is_busy=lambda: True, start=lambda _item: None)
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project, connection=connection),
        _program_queue=queue,
    )
    item = ProjectPlugin._queue_bus_action(
        cast(Any, plugin), 1, "read_memory", request=MemoryReadRequest("x", 0, 3)
    )
    if change == "generation":
        project.generation += 1
    elif change == "address":
        device.individual_address = "1.1.2"
    elif change == "application":
        device.app.id = "other"
    else:
        monkeypatch.setattr(
            memory_read,
            "preview_segments",
            lambda *_args: {"x": PreviewSegment(MemoryReadRequest("x", 1, 3), b"abc")},
        )
    with pytest.raises(ValueError, match="changed while"):
        item.action.start().result()
    assert not called


def test_preview_keeps_selected_device_after_lazy_rebuild(tmp_path):
    first = SimpleNamespace(node_id=1, individual_address="1.1.1", app=None)
    selected = SimpleNamespace(node_id=2, individual_address="1.1.2", app=None)
    devices = [first, selected]
    window = MemoryPreviewWindow(lambda: devices)
    window.open(cast(Any, selected))
    window._show = False
    request = MemoryReadRequest("x", 0, 3)
    window._read_data["x"] = MemoryReadResult(request, 0, b"abc")
    devices[1] = SimpleNamespace(
        node_id=2, individual_address="1.1.2", app=None, changed=True
    )
    path = tmp_path / "memory.bin"
    window._save_dialog = SimpleNamespace(ready=lambda: True, result=lambda: str(path))
    window._save_seg_id = "x"
    window._save_data = b"abc"
    window.render()
    assert window._device is devices[1]
    assert window._read_data["x"].data == b"abc"
    assert path.read_bytes() == b"abc"


@pytest.mark.parametrize("state", [OperationState.FAILED, OperationState.CANCELED])
def test_unsuccessful_read_shows_error_without_a_dump(state):
    window = MemoryPreviewWindow(lambda: [])
    item = QueueItem(1, "1.1.1", "", None, ended=Future())
    item.state = state
    item.message = "Read did not complete"
    item.ended.set_result(item)
    window._read_item = item
    window._poll_read({})
    assert window._read_item is None
    assert not window._read_data
    assert window._file_error == item.message


def test_read_result_reports_a_layout_change():
    window = MemoryPreviewWindow(lambda: [])
    request = MemoryReadRequest("x", 0, 3)
    item = QueueItem(1, "1.1.1", "", None, ended=Future())
    item.state = OperationState.FINISHED
    item.result = MemoryReadResult(request, 0, b"abc")
    item.ended.set_result(item)
    window._read_item = item
    window._poll_read({"x": PreviewSegment(MemoryReadRequest("x", 0, 4), b"abcd")})
    assert not window._read_data
    assert window._file_error == S.MEMORY_LAYOUT_CHANGED


def test_changed_queued_read_finishes_failed_and_allows_next_operation():
    from editor_gui.plugins.project.program_queue import BusAction, ProgramQueue

    device = SimpleNamespace(
        node_id=1, individual_address="1.1.1", name="Device", app=None
    )
    project = SimpleNamespace(generation=1, find_device_by_node_id=lambda _node: device)
    called = []
    connection = SimpleNamespace(read_memory=lambda *_args, **_kw: called.append(True))
    busy = [True]
    queue = ProgramQueue(
        is_busy=lambda: busy[0], start=lambda item: item.action.start()
    )
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project, connection=connection),
        _program_queue=queue,
    )
    item = ProjectPlugin._queue_bus_action(
        cast(Any, plugin), 1, "read_memory", request=MemoryReadRequest("x", 0, 3)
    )
    completed = Future()
    completed.set_result("next")
    next_item = QueueItem(
        2,
        "1.1.2",
        "",
        None,
        action=BusAction("next", "Next", lambda: completed),
        ended=Future(),
    )
    queue.enqueue(next_item)
    project.generation += 1
    busy[0] = False
    queue.tick()
    assert item.state is OperationState.FAILED
    assert "changed while" in item.message
    assert item.ended.done()
    assert next_item.state is OperationState.FINISHED
    assert not queue.active and not called
