"""The Operations panel also shows the bus operations the queue has no entry for (a restart, a
scan …), with what they do, where, and how far they are."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.plugin import (
    ProjectPlugin,
    _unqueued,  # pyright: ignore[reportPrivateUsage]
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.program_queue import (
    ProgramQueuePanel,
    bus_operation_text,
)
from editor_gui.widgets.tabs import forget_shown_tab_bars


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)
    forget_shown_tab_bars()


def test_bus_operation_text_names_operation_and_address() -> None:
    assert bus_operation_text("test", "1.1.5") == f"{S.BTN_EVAL_DEVICE} 1.1.5"
    assert bus_operation_text("restart", "1.1.5") == f"{S.BTN_RESTART_DEVICE} 1.1.5"
    assert (
        bus_operation_text("recover", "1.0.1..1.0.20")
        == f"{S.OPERATION_RECOVER} 1.0.1..1.0.20"
    )


def test_bus_operation_text_leaves_out_step_names() -> None:
    # Steps of an operation are no address: only the operation is named.
    assert bus_operation_text("scan_progmode", "broadcast") == (
        S.OPERATION_PROGRAMMING_MODE
    )
    assert bus_operation_text("recover", "read-back") == S.OPERATION_RECOVER
    assert bus_operation_text("recover", "verify") == S.OPERATION_RECOVER


def test_bus_operation_text_of_unknown_kind_shows_the_kind() -> None:
    assert bus_operation_text("other", "1.1.5") == "other 1.1.5"


def test_queued_operations_are_left_to_their_queue_entries() -> None:
    assert _unqueued(None) is None
    assert _unqueued(("program", "1.1.5")) is None
    assert _unqueued(("script", "1.1.5")) is None
    assert _unqueued(("device_info", "1.1.5")) is None
    assert _unqueued(("test", "1.1.5")) is None
    for kind in ("restart", "reset", "dali", "siemens_dali", "siemens_dca"):
        assert _unqueued((kind, "1.1.5")) is None
    assert _unqueued(("scan_progmode", "broadcast")) == ("scan_progmode", "broadcast")
    assert _unqueued(("recover", "1.1.1..1.1.9")) == ("recover", "1.1.1..1.1.9")


def test_operations_come_to_front_when_a_bus_operation_starts() -> None:
    connection = SimpleNamespace(busy_operation=None)
    requests = [False]
    selected: list[int] = []
    plugin = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(connection=connection)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    plugin._shown_bus_operation = None  # pyright: ignore[reportPrivateUsage]
    plugin._program_queue = SimpleNamespace(  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        take_focus_request=lambda: requests.pop() if requests else False
    )
    plugin._program_queue_panel = SimpleNamespace(  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        select_active=lambda: selected.append(1)
    )
    assert not plugin.take_operations_focus()
    connection.busy_operation = ("scan_progmode", "broadcast")
    assert plugin.take_operations_focus()
    # Once per operation: the user may switch to another panel while it runs.
    assert not plugin.take_operations_focus()
    connection.busy_operation = ("recover", "1.1.1..1.1.9")
    assert plugin.take_operations_focus()
    # The queue shows its own operations, and brings the panel to the front itself.
    for kind in ("program", "restart"):
        connection.busy_operation = (kind, "1.1.5")
        assert not plugin.take_operations_focus()
    requests.append(True)
    assert plugin.take_operations_focus()
    assert len(selected) == 3


def _panel(
    bus_operation: list[tuple[str, str] | None], progress_reads: list[int]
) -> ProgramQueuePanel:
    def progress() -> tuple[int, int] | None:
        progress_reads.append(1)
        return (3, 20)

    return ProgramQueuePanel(
        get_active=lambda: [],
        get_history=lambda: [],
        get_progress=progress,
        on_cancel=lambda _item: None,
        on_cancel_all=lambda: None,
        on_clear_history=lambda: None,
        get_bus_operation=lambda: bus_operation[0],
    )


def _frames(panel: ProgramQueuePanel, count: int) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.begin("Operations")
        panel.render()
        imgui.end()
        imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_running_bus_operation_shows_with_its_progress() -> None:
    operation: list[tuple[str, str] | None] = [("restart", "1.1.5")]
    reads: list[int] = []
    _frames(_panel(operation, reads), 3)
    # The Active tab shows the operation with a progress bar every frame.
    assert len(reads) >= 2


@pytest.mark.usefixtures("context")
def test_no_progress_without_a_bus_operation() -> None:
    operation: list[tuple[str, str] | None] = [None]
    reads: list[int] = []
    panel = _panel(operation, reads)
    _frames(panel, 3)
    assert reads == []
    # One starts: the panel shows it, and stops once it has ended.
    operation[0] = ("restart", "1.1.5")
    _frames(panel, 3)
    assert reads
    reads.clear()
    operation[0] = None
    _frames(panel, 3)
    assert reads == []


def test_an_operation_connected_for_that_did_not_start_keeps_its_reason() -> None:
    from concurrent.futures import Future

    from editor_gui.plugins.connection.service import OperationNotStarted
    from editor_gui.plugins.project.program_queue import (
        OperationState,
        QueueItem,
        _outcome,  # pyright: ignore[reportPrivateUsage]
    )

    item = QueueItem(node_id=1, address="1.1.5", name="Dimmer", scope=None)
    item.message = "The device has no valid address."
    future: Future[object] = Future()
    future.set_exception(OperationNotStarted("program_device could not start"))
    assert _outcome(item, future) == (OperationState.FAILED, None)
    item.message = None
    assert _outcome(item, future) == (
        OperationState.FAILED,
        "program_device could not start",
    )


def test_a_certificate_found_missing_after_connecting_is_asked_for() -> None:
    from editor_gui.plugins.connection.service import DeviceCertificateRequired
    from editor_gui.plugins.project.program_queue import QueueItem

    asked: list[int] = []
    device = SimpleNamespace(node_id=1)
    plugin = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        project=SimpleNamespace(find_device_by_node_id=lambda _n: device)
    )
    plugin._ask_certificate = lambda d, _item: asked.append(d.node_id)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    error = DeviceCertificateRequired("1.1.5")
    item = QueueItem(node_id=1, address="1.1.5", name="Dimmer", scope=None)
    assert plugin._program_error_text(error) == S.DEVICE_CERTIFICATE_REQUIRED  # pyright: ignore[reportPrivateUsage]
    assert plugin._describe_program_error(item, error) is None  # pyright: ignore[reportPrivateUsage]
    assert asked == [1]
    # An operation an MCP client started gets the message instead of the dialog.
    item.origin = "MCP"
    plugin._describe_program_error(item, error)  # pyright: ignore[reportPrivateUsage]
    assert asked == [1]


def _test_plugin(
    busy: list[bool], evaluations: list[Future[object]], shown: list[tuple[object, ...]]
) -> ProjectPlugin:
    from editor_gui.plugins.project.program_queue import ProgramQueue

    device = SimpleNamespace(node_id=1, name="Dimmer", individual_address="1.1.5")

    def evaluate_device(*_args: object) -> Future[object]:
        future: Future[object] = Future()
        evaluations.append(future)
        return future

    plugin = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        connection=SimpleNamespace(
            can_reach_bus=lambda: True,
            evaluate_device=evaluate_device,
            xknx=object(),
        ),
        project=SimpleNamespace(
            edited_since_open=lambda: False,
            find_device_by_node_id=lambda _n: device,
            group_communication_for=lambda _d: None,
            ip_download_config=lambda _d: None,
        ),
    )
    plugin._preflight_result = SimpleNamespace(  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        submit_result=lambda *args: shown.append(("result", *args)),
        submit_error=lambda *args: shown.append(("error", *args)),
    )
    plugin._program_queue = ProgramQueue(  # pyright: ignore[reportPrivateUsage]
        is_busy=lambda: busy[0],
        start=plugin._start_program,  # pyright: ignore[reportPrivateUsage]
        # Like the connection, a canceled operation ends only once it stopped.
        cancel=lambda _future: None,
    )
    return plugin


def test_test_programming_waits_in_the_queue_while_the_bus_is_busy() -> None:
    from editor_gui.plugins.project.program_queue import OperationState
    from xknxeditor.download.scope import DownloadScope

    busy, evaluations, shown = [True], [], []
    plugin = _test_plugin(busy, evaluations, shown)
    queue = plugin.program_queue
    device: Any = plugin._api.project.find_device_by_node_id(1)  # pyright: ignore[reportPrivateUsage]
    plugin._eval_device(device, DownloadScope.PARAMETERS)  # pyright: ignore[reportPrivateUsage]
    # The bus is busy: it waits instead of failing.
    assert evaluations == [] and shown == []
    assert [(i.node_id, i.test, i.scope) for i in queue.queued] == [
        (1, True, DownloadScope.PARAMETERS)
    ]
    busy[0] = False
    queue.tick()
    assert len(evaluations) == 1
    evaluations[0].set_result("report")
    # The report shows in the result window, and the entry ends as finished.
    assert shown == [("result", "Dimmer", "PARAMETERS", "report", set(), {})]
    assert queue.history[0].state is OperationState.FINISHED


def test_a_canceled_test_shows_no_result_window() -> None:
    from xknxeditor.download.scope import DownloadScope

    busy, evaluations, shown = [False], [], []
    plugin = _test_plugin(busy, evaluations, shown)
    device: Any = plugin._api.project.find_device_by_node_id(1)  # pyright: ignore[reportPrivateUsage]
    plugin._eval_device(device, DownloadScope.FULL)  # pyright: ignore[reportPrivateUsage]
    plugin.program_queue.cancel_current()
    # The interrupted read raises; that is the cancellation, not an error to show.
    evaluations[0].set_exception(RuntimeError("interrupted"))
    assert shown == []


def test_test_programming_without_a_bus_connection_is_not_queued() -> None:
    from xknxeditor.download.scope import DownloadScope

    busy, evaluations, shown = [False], [], []
    plugin = _test_plugin(busy, evaluations, shown)
    plugin._api.connection.can_reach_bus = lambda: False  # pyright: ignore[reportPrivateUsage]
    device: Any = plugin._api.project.find_device_by_node_id(1)  # pyright: ignore[reportPrivateUsage]
    plugin._eval_device(device, DownloadScope.FULL)  # pyright: ignore[reportPrivateUsage]
    assert shown == [("error", "Dimmer", "FULL", S.PREFLIGHT_NO_CONNECTION)]
    assert plugin.program_queue.active == [] and evaluations == []


def test_a_test_names_its_scope_and_reads_while_it_runs() -> None:
    from editor_gui.plugins.project.program_queue import OperationState, QueueItem
    from editor_gui.plugins.project.ui.program_queue import operation_text, state_text
    from xknxeditor.download.scope import DownloadScope

    item = QueueItem(
        node_id=1,
        address="1.1.5",
        name="Dimmer",
        scope=DownloadScope.PARAMETERS,
        test=True,
    )
    assert operation_text(item) == f"{S.BTN_EVAL_DEVICE} ({S.SCOPE_PARAMETERS})"
    item.state = OperationState.RUNNING
    assert state_text(item) == S.OPERATION_READING


def test_a_running_operation_waiting_for_its_connection_shows_connecting() -> None:
    from editor_gui.plugins.project.program_queue import OperationState, QueueItem
    from editor_gui.plugins.project.ui.program_queue import state_text

    item = QueueItem(node_id=1, address="1.1.5", name="Dimmer", scope=None)
    item.state = OperationState.RUNNING
    assert state_text(item) == S.OPERATION_DOWNLOADING
    assert state_text(item, connecting=True) == S.OPERATION_CONNECTING
    # A waiting one is still waiting for its turn.
    item.state = OperationState.WAITING
    assert state_text(item, connecting=True) == S.OPERATION_WAITING


@pytest.mark.usefixtures("context")
def test_an_operation_waiting_for_its_connection_shows_no_bus_progress() -> None:
    from editor_gui.plugins.project.program_queue import OperationState, QueueItem

    item = QueueItem(node_id=1, address="1.1.5", name="Dimmer", scope=None)
    item.state = OperationState.RUNNING
    reads: list[int] = []
    connecting = [True]

    def progress() -> tuple[int, int]:
        reads.append(1)
        return (3, 20)

    panel = ProgramQueuePanel(
        get_active=lambda: [item],
        get_history=lambda: [],
        get_progress=progress,
        on_cancel=lambda _item: None,
        on_cancel_all=lambda: None,
        on_clear_history=lambda: None,
        is_connecting=lambda _item: connecting[0],
    )
    _frames(panel, 2)
    assert reads == []
    connecting[0] = False
    _frames(panel, 2)
    assert reads
