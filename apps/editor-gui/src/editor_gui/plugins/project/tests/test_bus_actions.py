"""A restart, a master reset and the DALI runs are operations of the Operations queue: they wait
behind the others, are named there and end in its history."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.program_queue import operation_text, state_text


def _done(result: Any = None) -> Future[Any]:
    future: Future[Any] = Future()
    future.set_result(result)
    return future


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.busy = False
        self.xknx: object | None = object()
        self.futures: list[Future[Any]] = []

    def _future(self) -> Future[Any]:
        self.futures.append(Future())
        return self.futures[-1]

    def restart_device(self, device: Any) -> Future[Any] | None:
        self.calls.append(("restart", device.node_id))
        return self._future() if self.xknx is not None else None

    def master_reset_device(self, device: Any, erase_code: int) -> Future[Any]:
        self.calls.append(("reset", device.node_id, erase_code))
        return self._future()

    def run_dali(self, device: Any, op: Any, *, channel: int = 0) -> Future[Any]:
        self.calls.append(("dali", device.node_id, channel))
        return self._future()

    def run_siemens_dali(self, device: Any, op: Any) -> Future[Any]:
        self.calls.append(("siemens_dali", device.node_id))
        return self._future()

    def run_siemens_dca_program(self, device: Any, blob_xml: bytes) -> Future[Any]:
        self.calls.append(("siemens_dca", device.node_id, blob_xml))
        return self._future()


def _plugin(order_number: str = "SCN-DA641.03") -> tuple[Any, _Connection, list[int]]:
    connection = _Connection()
    device = SimpleNamespace(node_id=1, individual_address="1.1.5", name="Gateway")
    recorded: list[int] = []
    plugin: Any = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(
        connection=connection,
        main_thread=None,
        project=SimpleNamespace(
            find_device_by_node_id=lambda node_id: device if node_id == 1 else None,
            get_device_info=lambda node_id: SimpleNamespace(order_number=order_number),
            record_factory_reset=lambda d: recorded.append(d.node_id),
        ),
    )
    plugin._program_queue = ProgramQueue(
        is_busy=lambda: connection.busy, start=plugin._start_program
    )
    return plugin, connection, recorded


def _queue(plugin: Any) -> ProgramQueue:
    return cast(ProgramQueue, plugin._program_queue)


def test_a_restart_runs_in_the_queue_and_ends_in_its_history() -> None:
    plugin, connection, _ = _plugin()
    plugin._restart_device(SimpleNamespace(node_id=1))
    item = _queue(plugin).current
    assert item is not None and connection.calls == [("restart", 1)]
    assert (operation_text(item), item.address, item.name) == (
        S.BTN_RESTART_DEVICE,
        "1.1.5",
        "Gateway",
    )
    assert state_text(item) == S.OPERATION_RUNNING
    connection.futures[0].set_result(None)
    assert _queue(plugin).history == [item]
    assert item.state is OperationState.FINISHED


def test_a_master_reset_names_its_reset_and_records_a_factory_reset() -> None:
    plugin, connection, recorded = _plugin()
    plugin._master_reset_device(SimpleNamespace(node_id=1), 5)
    connection.futures[0].set_result(None)
    plugin._master_reset_device(SimpleNamespace(node_id=1), 2)
    connection.futures[1].set_result(None)
    first, second = reversed(_queue(plugin).history)
    assert operation_text(first) == f"{S.RESET_POPUP_TITLE} ({S.RESET_PARAM})"
    assert operation_text(second) == f"{S.RESET_POPUP_TITLE} ({S.RESET_FACTORY})"
    assert connection.calls == [("reset", 1, 5), ("reset", 1, 2)]
    # Only a factory reset gives a secure device its FDSK back.
    assert recorded == [1]


def test_a_failed_factory_reset_records_nothing() -> None:
    plugin, connection, recorded = _plugin()
    plugin._master_reset_device(SimpleNamespace(node_id=1), 2)
    connection.futures[0].set_exception(RuntimeError("no ack"))
    assert _queue(plugin).history[0].state is OperationState.FAILED
    assert recorded == []


def test_operations_wait_behind_the_running_one() -> None:
    plugin, connection, _ = _plugin()
    connection.busy = True
    plugin._restart_device(SimpleNamespace(node_id=1))
    plugin._restart_device(SimpleNamespace(node_id=1))
    # Two restarts stay two operations.
    assert len(_queue(plugin).queued) == 2 and connection.calls == []
    connection.busy = False
    _queue(plugin).tick()
    assert connection.calls == [("restart", 1)]


def test_a_dali_run_is_named_by_its_button_and_tells_its_end() -> None:
    plugin, connection, _ = _plugin(order_number="SCN-DA642.03")
    ended: list[int] = []
    connection.busy = True
    plugin._run_dali(
        SimpleNamespace(node_id=1),
        None,
        1,
        S.DALI_PROGRAM_GROUPS,
        lambda: ended.append(1),
    )
    item = _queue(plugin).queued[0]
    channel = S.DALI_CHANNEL_N.format(n=2)
    assert operation_text(item) == (
        f"{S.EDITOR_TAB_DALI}: {S.DALI_PROGRAM_GROUPS.removesuffix('…')} ({channel})"
    )
    # Canceled while it waits: it never ran, and the tab is told it has ended.
    _queue(plugin).cancel_item(item)
    assert ended == [1] and connection.calls == []
    connection.busy = False
    plugin._run_dali(
        SimpleNamespace(node_id=1), None, 0, S.DALI_SCAN, lambda: ended.append(2)
    )
    assert connection.calls == [("dali", 1, 0)]
    connection.futures[0].set_result(None)
    assert ended == [1, 2]


def test_one_channel_is_not_named() -> None:
    plugin, _, _ = _plugin()
    plugin._run_dali(SimpleNamespace(node_id=1), None, 0, S.DALI_SCAN, lambda: None)
    item = _queue(plugin).current
    assert item is not None
    assert operation_text(item) == f"{S.EDITOR_TAB_DALI}: {S.DALI_SCAN}"


def test_siemens_dali_runs_are_queued_too() -> None:
    plugin, connection, _ = _plugin()
    plugin._run_siemens_dali(
        SimpleNamespace(node_id=1), None, S.SIEMENS_DALI_SCAN_BUS, lambda: None
    )
    connection.futures[0].set_result("scan")
    device = SimpleNamespace(
        node_id=1,
        individual_address="1.1.5",
        binary_data=[SimpleNamespace(name="BLOB_PARA", data=b"<DaliDevice/>")],
    )
    plugin._handle_program_dca(device)
    connection.futures[1].set_result(3)
    dca, scan = _queue(plugin).history
    assert operation_text(scan) == (
        f"{S.EDITOR_TAB_SIEMENS_DALI}: {S.SIEMENS_DALI_SCAN_BUS}"
    )
    assert scan.result == "scan"
    assert (operation_text(dca), dca.result) == (S.PLUGIN_PARAMS_PROGRAM_DCA, 3)
    assert connection.calls[1] == ("siemens_dca", 1, b"<DaliDevice/>")


def test_without_a_connection_the_history_says_so() -> None:
    plugin, connection, _ = _plugin()
    connection.xknx = None
    plugin._restart_device(SimpleNamespace(node_id=1))
    item = _queue(plugin).history[0]
    assert (item.state, item.message) == (
        OperationState.FAILED,
        S.SCRIPT_NOT_CONNECTED,
    )


def test_a_device_not_in_the_project_is_not_queued() -> None:
    plugin, _, _ = _plugin()
    assert plugin._queue_bus_action(2, "restart") is None
    with pytest.raises(ValueError, match="unknown bus operation"):
        plugin._queue_bus_action(1, "teleport")


def test_the_device_is_looked_up_when_it_starts() -> None:
    plugin, connection, _ = _plugin()
    connection.busy = True
    item = plugin._queue_bus_action(1, "restart")
    assert isinstance(item, QueueItem)
    # Removed from the project while it waited: it fails without touching the bus.
    plugin._api.project.find_device_by_node_id = lambda node_id: None
    connection.busy = False
    _queue(plugin).tick()
    assert item.state is OperationState.FAILED and connection.calls == []
