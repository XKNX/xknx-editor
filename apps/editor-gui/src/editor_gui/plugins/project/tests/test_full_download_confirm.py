"""Confirming a full download, and the texts of failed partial downloads."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
)
from editor_gui.plugins.project.strings import S
from xknxeditor.download.partial import (
    DifferentProgramLoaded,
    FullDownloadDeclined,
    IncompatibleMaskVersion,
)
from xknxeditor.download.scope import DownloadScope


def _item() -> QueueItem:
    return QueueItem(node_id=1, address="1.0.1", name="", scope=DownloadScope.FULL)


def _plugin() -> Any:
    return SimpleNamespace(
        _api=SimpleNamespace(log=SimpleNamespace(info=lambda *args, **kwargs: None))
    )


def _finish(error: BaseException, error_text: Any = None) -> QueueItem:
    futures: list[Future[Any]] = []

    def start(item: QueueItem) -> Future[Any]:
        futures.append(Future())
        return futures[-1]

    queue = ProgramQueue(is_busy=lambda: False, start=start, error_text=error_text)
    queue.enqueue(_item())
    futures[0].set_exception(error)
    return queue.history[0]


def test_declining_the_full_download_cancels_it() -> None:
    item = _finish(FullDownloadDeclined("Aborting on user request"))
    assert item.state is OperationState.CANCELED
    assert item.message is None


def test_partial_download_errors_are_translated() -> None:
    def error_text(error: BaseException) -> str | None:
        return ProjectPlugin._program_error_text(cast(Any, None), error)  # pyright: ignore[reportPrivateUsage]

    item = _finish(DifferentProgramLoaded("load status=0"), error_text)
    assert item.state is OperationState.FAILED
    assert item.message == S.DIFFERENT_PROGRAM_LOADED
    item = _finish(IncompatibleMaskVersion("MV-0705", "MV-07B0"), error_text)
    assert item.message == S.INCOMPATIBLE_MASK_VERSION.format(
        found="MV-0705", expected="MV-07B0"
    )
    assert _finish(RuntimeError("other"), error_text).message == "other"


async def test_the_question_waits_for_the_answer() -> None:
    item = _item()
    ask = asyncio.ensure_future(
        ProjectPlugin._confirm_full_download(_plugin(), item, "not loaded")  # pyright: ignore[reportPrivateUsage]
    )
    for _ in range(10):
        await asyncio.sleep(0)
        if item.full_download_question is not None:
            break
    question = item.full_download_question
    assert question is not None and not ask.done()
    question.set_result(True)
    assert await ask is True
    assert item.full_download_question is None
