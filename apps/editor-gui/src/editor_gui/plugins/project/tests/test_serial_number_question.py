"""The question a download asks when the secured device in programming mode has another serial
number than the one stored for it: shown in Operations, a No cancels the download, a Yes takes
the serial number over and queues the download again."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

from imgui_bundle import imgui

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
)
from editor_gui.plugins.project.ui import program_queue as ui
from xknxeditor.download.errors import (
    SerialNumberDeclined,
    SerialNumberMismatchError,
    SerialNumberTakenOver,
)
from xknxeditor.download.scope import DownloadScope

_FOUND = bytes.fromhex("00FAD6B25D85")
_STORED = bytes.fromhex("00FAE70AA822")


def _item(**fields: Any) -> QueueItem:
    return QueueItem(
        node_id=7,
        address="1.0.58",
        name="IP-Interface",
        scope=DownloadScope.FULL,
        **fields,
    )


def _finish(error: BaseException) -> QueueItem:
    futures: list[Future[Any]] = []

    def start(item: QueueItem) -> Future[Any]:
        futures.append(Future())
        return futures[-1]

    queue = ProgramQueue(is_busy=lambda: False, start=start)
    queue.enqueue(_item())
    futures[0].set_exception(error)
    return queue.history[0]


def test_a_no_cancels_the_download_and_a_yes_ends_it_for_the_next_one() -> None:
    assert (
        _finish(SerialNumberDeclined("not taken over")).state is OperationState.CANCELED
    )
    assert _finish(SerialNumberTakenOver(_FOUND)).state is OperationState.CANCELED
    # without anyone to ask, the download fails with the reason
    failed = _finish(SerialNumberMismatchError("another serial number"))
    assert failed.state is OperationState.FAILED
    assert failed.message == "another serial number"


async def test_the_question_waits_for_the_answer() -> None:
    plugin = SimpleNamespace(
        _api=SimpleNamespace(log=SimpleNamespace(info=lambda *args, **kwargs: None))
    )
    item = _item()
    ask = asyncio.ensure_future(
        ProjectPlugin._confirm_serial_number(plugin, item, _FOUND, _STORED)  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if item.serial_number_question is not None:
            break
    question = item.serial_number_question
    assert question is not None and not ask.done()
    assert item.serial_number_asked == (_FOUND, _STORED)
    item.state = OperationState.RUNNING
    assert ui.state_text(item, connecting=False) == ui.S.OPERATION_CONFIRM
    question.set_result(False)
    assert await ask is False
    assert item.serial_number_question is None


def test_a_taken_over_serial_number_is_recorded_and_the_download_queued_again() -> None:
    device = SimpleNamespace(node_id=7)
    recorded: list[tuple[object, bytes | None]] = []
    queued: list[QueueItem] = []
    plugin: Any = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(
        project=SimpleNamespace(
            find_device_by_node_id=lambda node_id: device if node_id == 7 else None
        )
    )
    plugin._set_serial_number = lambda d, serial: recorded.append((d, serial))
    plugin._program_queue = SimpleNamespace(enqueue=queued.append)
    plugin._program_again_with(_item(serial_number=_STORED, origin="MCP"), _FOUND)
    assert recorded == [(device, _FOUND)]
    (again,) = queued
    assert (again.node_id, again.scope, again.origin) == (7, DownloadScope.FULL, "MCP")
    assert again.serial_number == _FOUND
    # a download that did not go by serial number does not start to
    queued.clear()
    plugin._program_again_with(_item(), _FOUND)
    assert queued[0].serial_number is None
    # nor for a device no longer in the project
    queued.clear()
    plugin._api.project.find_device_by_node_id = lambda node_id: None
    plugin._program_again_with(_item(), _FOUND)
    assert queued == []


def test_the_operations_panel_shows_the_question() -> None:
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.set_ini_filename("")
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        item = _item()
        item.serial_number_asked = (_FOUND, _STORED)
        item.serial_number_question = Future()
        shown: list[str] = []
        real = imgui.text_wrapped

        def text_wrapped(text: str) -> None:
            shown.append(text)
            real(text)

        imgui.text_wrapped = text_wrapped  # type: ignore[assignment]
        try:
            imgui.new_frame()
            imgui.begin("Operations")
            ui._render_serial_number_question(item)  # pyright: ignore[reportPrivateUsage]
            imgui.end()
            imgui.end_frame()
        finally:
            imgui.text_wrapped = real  # type: ignore[assignment]
        assert shown == [
            ui.S.SERIAL_NUMBER_DIFFERENT.format(
                found="00FA:D6B25D85", stored="00FA:E70AA822"
            ),
            ui.S.SERIAL_NUMBER_QUESTION,
        ]
    finally:
        imgui.destroy_context(context)
