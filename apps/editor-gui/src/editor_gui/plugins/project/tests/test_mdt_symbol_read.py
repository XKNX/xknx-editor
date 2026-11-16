from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from xknx.exceptions import ManagementConnectionError

from editor_gui.plugins.project.program_queue import (
    BusAction,
    OperationState,
    ProgramQueue,
    QueueItem,
)
from editor_gui.plugins.project.tests.test_mdt_dca import mdt_project
from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel, _SymbolState
from editor_gui.programming_mdt import read_symbols
from xknxeditor.recover.identify import AppId

__all__ = ["mdt_project"]


def test_empty_view_offers_read_once_without_starting_bus_access():
    queue = Mock(return_value=SimpleNamespace())
    connected = False
    panel = MdtDcaPanel(queue, connected=lambda: connected)
    device = SimpleNamespace(
        node_id=1, individual_address="1.1.76", app=SimpleNamespace(id="app")
    )
    state = _SymbolState()
    empty = {"symbols": [], "defaults": []}
    panel._offer_symbol_read(device, state, empty)
    queue.assert_not_called()
    connected = True
    panel._offer_symbol_read(device, state, {**empty, "symbols": [{}]})
    panel._offer_symbol_read(device, state, {**empty, "defaults": [{}]})
    queue.assert_not_called()
    assert not state.confirm_read
    panel._offer_symbol_read(device, state, empty)
    assert state.confirm_read
    queue.assert_not_called()
    state.confirm_read = False
    panel._offer_symbol_read(device, state, empty)
    assert not state.confirm_read
    panel._request_symbol_read(device, state)
    assert state.confirm_read
    queue.assert_not_called()


@pytest.mark.parametrize("choice", [None, 0, 1, "changed"])
def test_symbol_read_confirmation_starts_only_after_acceptance(monkeypatch, choice):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui import mdt_dca

    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        queue = Mock(return_value=SimpleNamespace())
        panel = MdtDcaPanel(queue, connected=lambda: True)
        device = SimpleNamespace(
            node_id=1, individual_address="1.1.76", app=SimpleNamespace(id="app")
        )
        state = _SymbolState()
        panel._request_symbol_read(device, state)
        if choice == "changed":
            device.individual_address = "1.1.77"
        panel._offer_symbol_read(device, state, {"symbols": [], "defaults": []})
        assert state.read_prompt == ("1.1.76", "app")
        monkeypatch.setattr(
            mdt_dca,
            "dialog_buttons",
            lambda *args: 0 if choice == "changed" else choice,
        )
        imgui.new_frame()
        panel._confirm_symbol_read(device, state)
        imgui.end_frame()
        if choice == 0:
            queue.assert_called_once_with(1, "read_mdt_symbols")
        else:
            queue.assert_not_called()
        assert state.confirm_read is (choice is None)
    finally:
        imgui.destroy_context(context)


def test_addressless_device_does_not_offer_or_open_read_confirmation():
    queue = Mock()
    panel = MdtDcaPanel(queue, connected=lambda: True)
    device = SimpleNamespace(
        node_id=1, individual_address="", app=SimpleNamespace(id="app")
    )
    state = _SymbolState()
    panel._offer_symbol_read(device, state, {"symbols": [], "defaults": []})
    panel._request_symbol_read(device, state)
    assert not state.confirm_read
    queue.assert_not_called()


def test_symbol_read_updates_only_symbols_and_undo_restores(mdt_project):
    project = mdt_project
    device = project.devices[2]
    project._svc.set_device_addin_data(
        project._pid, device.node_id, "M0083-A0041", {"Other": b"preserved"}
    )
    target = project.prepare_mdt_symbol_read(device.node_id)
    view = project.mdt_symbols(device.node_id)
    links = project._capture_links(device)
    symbols = project.mdt_symbol_payload(device.node_id, include_defaults=True)
    assert project.apply_mdt_symbol_read(target, symbols) == 36
    after = project.mdt_symbols(device.node_id)
    assert after["new_library"] is None
    assert (
        project._svc.device_addin_data(project._pid, device.node_id, "M0083-A0041")[
            "Other"
        ]
        == b"preserved"
    )
    assert {
        s["slot"]: bytes.fromhex(s["data_hex"]) for s in after["symbols"]
    } == symbols
    assert (
        project._capture_links(project.find_device_by_node_id(device.node_id)) == links
    )
    assert project.undo()
    assert project.mdt_symbols(device.node_id) == view


@pytest.mark.parametrize("change", ["symbols", "generation", "incomplete"])
def test_failed_or_stale_symbol_read_changes_nothing(mdt_project, change):
    project = mdt_project
    node = project.devices[2].node_id
    target = project.prepare_mdt_symbol_read(node)
    symbols = project.mdt_symbol_payload(node, include_defaults=True)
    if change == "symbols":
        project.set_mdt_symbol(node, 2, "00" * 4096)
    elif change == "generation":
        from dataclasses import replace

        target = replace(target, generation=-1)
    else:
        symbols.pop(2)
    before = project.mdt_symbols(node)
    cursor = project.cursor
    with pytest.raises(ValueError):
        project.apply_mdt_symbol_read(target, symbols)
    assert project.cursor == cursor
    assert project.mdt_symbols(node) == before


@pytest.mark.parametrize("failure", [None, "mismatch", "timeout", "denied"])
async def test_symbol_read_session_identity_retry_and_read_only(monkeypatch, failure):
    from editor_gui import programming_mdt
    from xknxeditor.recover import identify

    managers = [
        SimpleNamespace(open=AsyncMock(return_value=object()), close=AsyncMock())
        for _ in range(2)
    ]
    manager = Mock(side_effect=managers)
    programmers = []

    def programmer(*args, **kwargs):
        value = SimpleNamespace(
            max_apdu_length=15, read_max_apdu_length=AsyncMock(return_value=254)
        )
        programmers.append(value)
        return value

    monkeypatch.setattr(programming_mdt, "management_session", manager)
    monkeypatch.setattr(programming_mdt, "DeviceProgrammer", programmer)
    identity = AppId("M-0083", 0x008A, 0x25 if failure != "mismatch" else 0x24)
    monkeypatch.setattr(
        identify, "read_application_id", AsyncMock(return_value=identity)
    )
    captures = []

    async def capture(connection, profile):
        captures.append(connection.max_apdu_length)
        if failure == "denied":
            raise ValueError("Pixels are not readable")
        if failure == "timeout" and len(captures) == 1:
            raise ManagementConnectionError("Pixel timeout")
        return {0: b"pixels"}

    monkeypatch.setattr(programming_mdt, "recover_symbols", capture)
    if failure in ("mismatch", "denied"):
        with pytest.raises(ValueError):
            await read_symbols(
                object(),
                "1.1.76",
                None,
                "M-0083_A-008A-25-83B3",
                interface_max_apdu_length=55,
            )
    else:
        assert await read_symbols(
            object(),
            "1.1.76",
            None,
            "M-0083_A-008A-25-83B3",
            interface_max_apdu_length=55,
        ) == {0: b"pixels"}
    assert captures == (
        [] if failure == "mismatch" else [55, 15] if failure == "timeout" else [55]
    )
    for value in managers[: manager.call_count]:
        value.close.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_queue_applies_before_finishing_and_reports_apply_errors(outcome):
    future = Future()
    applied = []

    def apply(value):
        if outcome == "error":
            raise ValueError("Device changed")
        applied.append(value)
        return 36

    queue = ProgramQueue(
        is_busy=lambda: False,
        start=lambda item: item.action.start(),
        cancel=lambda _: None,
    )
    item = QueueItem(
        1,
        "1.1.76",
        "",
        None,
        action=BusAction("read_mdt_symbols", "Read", lambda: future, finish=apply),
        ended=Future(),
    )
    queue.enqueue(item)
    if outcome == "cancel":
        queue.cancel_current()
    future.set_result({0: b"pixels"})
    assert item.ended.done()
    assert (
        item.state
        is {
            "success": OperationState.FINISHED,
            "error": OperationState.FAILED,
            "cancel": OperationState.CANCELED,
        }[outcome]
    )
    assert len(applied) == (1 if outcome == "success" else 0)
    if outcome == "error":
        assert item.message == "Device changed"
        assert item.result is None
    assert queue.current is None


def test_project_queue_symbol_read_persists_before_result(mdt_project):
    from editor_gui.plugins.project.plugin import ProjectPlugin, _unqueued

    project = mdt_project
    node = project.devices[2].node_id
    original = project.mdt_symbols(node)
    future = Future()
    connection = SimpleNamespace(
        read_mdt_symbols=Mock(return_value=future), xknx=object()
    )
    queue = ProgramQueue(is_busy=lambda: False, start=lambda item: item.action.start())
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project, connection=connection),
        _program_queue=queue,
    )
    item = ProjectPlugin._queue_bus_action(plugin, node, "read_mdt_symbols")
    future.set_result(project.mdt_symbol_payload(node, include_defaults=True))
    assert item.ended.result().result == 36
    assert project.mdt_symbols(node)["new_library"] is None
    assert project.undo() and project.mdt_symbols(node) == original
    assert _unqueued(("mdt_symbols", "1.1.3")) is None
