"""The Operations panel also shows the bus operations the queue has no entry for (a device test, a
restart, a scan …), with what they do, where, and how far they are."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

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
    assert _unqueued(("test", "1.1.5")) == ("test", "1.1.5")
    assert _unqueued(("scan_progmode", "broadcast")) == ("scan_progmode", "broadcast")


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
    connection.busy_operation = ("test", "1.1.5")
    assert plugin.take_operations_focus()
    # Once per operation: the user may switch to another panel while it runs.
    assert not plugin.take_operations_focus()
    connection.busy_operation = ("restart", "1.1.5")
    assert plugin.take_operations_focus()
    # The queue shows its own operations, and brings the panel to the front itself.
    connection.busy_operation = ("program", "1.1.5")
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
    operation: list[tuple[str, str] | None] = [("test", "1.1.5")]
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
