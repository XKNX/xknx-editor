"""Confirming a full download, and the texts of failed downloads in the operations history."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

from xknx.exceptions import ManagementConnectionTimeout

from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
)
from editor_gui.plugins.project.strings import S
from xknxeditor.download.errors import PartialDownloadError
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


def _finish(
    error: BaseException,
    error_text: Any = None,
    item: QueueItem | None = None,
    describe: Any = None,
) -> QueueItem:
    futures: list[Future[Any]] = []

    def start(item: QueueItem) -> Future[Any]:
        futures.append(Future())
        return futures[-1]

    queue = ProgramQueue(
        is_busy=lambda: False,
        start=start,
        error_text=error_text,
        describe_error=describe,
    )
    queue.enqueue(item or _item())
    futures[0].set_exception(error)
    return queue.history[0]


def test_declining_the_full_download_cancels_it() -> None:
    item = _finish(FullDownloadDeclined("Aborting on user request"))
    assert item.state is OperationState.CANCELED
    assert item.message is None


def _error_text(error: BaseException) -> str | None:
    return ProjectPlugin._program_error_text(error)  # pyright: ignore[reportPrivateUsage]


def _describe(item: QueueItem, error: BaseException) -> str | None:
    plugin = SimpleNamespace(_api=SimpleNamespace(project=SimpleNamespace(devices=[])))
    return ProjectPlugin._describe_program_error(cast(Any, plugin), item, error)  # pyright: ignore[reportPrivateUsage]


def test_partial_download_errors_are_translated() -> None:
    item = _finish(DifferentProgramLoaded("load status=0"), _error_text)
    assert item.state is OperationState.FAILED
    assert item.message == S.DIFFERENT_PROGRAM_LOADED
    item = _finish(IncompatibleMaskVersion("MV-0705", "MV-07B0"), _error_text)
    assert item.message == S.INCOMPATIBLE_MASK_VERSION.format(
        found="MV-0705", expected="MV-07B0"
    )
    assert _finish(RuntimeError("other"), _error_text).message == "other"


def test_a_device_that_did_not_respond_says_what_to_check() -> None:
    timeout = "No ACK received for repeated telegram."
    not_responding = S.DEVICE_NOT_RESPONDING.format(detail=timeout)
    # A full download may be for a new device, which has to be in programming mode.
    item = _finish(ManagementConnectionTimeout(timeout), _error_text, None, _describe)
    assert item.message == f"{not_responding} {S.NO_RESPONSE_NEW_DEVICE_HINT}"
    others = [
        QueueItem(node_id=1, address="1.0.1", name="", scope=DownloadScope.PARAMETERS),
        QueueItem(
            node_id=1, address="1.0.1", name="", scope=DownloadScope.FULL, test=True
        ),
        QueueItem(node_id=1, address="1.0.1", name="", scope=None, device_info=False),
    ]
    for other in others:
        item = _finish(
            ManagementConnectionTimeout(timeout), _error_text, other, _describe
        )
        assert item.message == f"{not_responding} {S.NO_RESPONSE_HINT}"


def test_a_download_that_failed_after_writing_names_its_cause() -> None:
    timeout = ManagementConnectionTimeout("Timeout while waiting for MemoryResponse")
    error = PartialDownloadError("Downloading 1.0.1 failed after ...")
    error.__cause__ = timeout
    item = _finish(error, _error_text, None, _describe)
    cause = S.DEVICE_NOT_RESPONDING.format(detail=str(timeout))
    assert item.message == (
        f"{S.PARTIAL_DOWNLOAD_FAILED.format(cause=cause)} {S.NO_RESPONSE_HINT}"
    )
    other = PartialDownloadError("Downloading 1.0.1 failed after ...")
    other.__cause__ = RuntimeError("Verification failed")
    assert _finish(other, _error_text, None, _describe).message == (
        S.PARTIAL_DOWNLOAD_FAILED.format(cause="Verification failed")
    )
    # Without its cause the error's own text is shown.
    assert _finish(PartialDownloadError("wrapped"), _error_text).message == "wrapped"


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
