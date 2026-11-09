"""A download shows how far the memory write of its current load control is."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

import editor_gui.programming as programming
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.project.ui.program_queue import (
    download_progress_fraction,
    download_progress_text,
)
from xknxeditor.download.scope import DownloadScope

_DEVICE = SimpleNamespace(
    individual_address="1.3.39",
    name="RoundTouch",
    download_plugin_required=False,
    factory_image=None,
    second_application=None,
)


def test_the_download_reports_into_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_now(coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(asyncio.run(coro))
        return future

    seen: list[tuple[tuple[int, int] | None, tuple[int, int] | None]] = []
    service = ConnectionService()

    async def download(*args: Any, **kwargs: Any) -> None:
        kwargs["progress"](17, 28)
        kwargs["memory_progress"](6000, 14968)
        seen.append((service.busy_progress, service.busy_memory_progress))
        # The next control clears the memory progress of the one before.
        kwargs["progress"](18, 28)
        seen.append((service.busy_progress, service.busy_memory_progress))

    monkeypatch.setattr(programming, "download_device", download)
    service._xknx = MagicMock()  # pyright: ignore[reportPrivateUsage]
    service.set_logger(MagicMock())
    monkeypatch.setattr(service, "run_async", run_now)
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    assert seen == [((17, 28), (6000, 14968)), ((18, 28), None)]
    assert service.busy_memory_progress is None


async def test_the_memory_progress_reaches_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def capture(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    def report(done: int, total: int) -> None:
        pass

    monkeypatch.setattr(programming, "download", capture)
    monkeypatch.setattr(programming, "_image_for", lambda *a: None)
    monkeypatch.setattr(programming, "_application", lambda *a: None)
    await programming.download_device(
        cast(Any, None), cast(Any, _DEVICE), memory_progress=report
    )
    assert calls[0]["memory_progress"] is report


def test_progress_text_and_fraction() -> None:
    assert download_progress_text((17, 28), None) == "17/28"
    assert download_progress_text((17, 28), (6348, 14968)) == "17/28, 6.2 KB/14.6 KB"
    assert download_progress_fraction((17, 28), None) == pytest.approx(17 / 28)
    # Control 18 of 28 is half written.
    assert download_progress_fraction((17, 28), (7484, 14968)) == pytest.approx(
        17.5 / 28
    )
    assert download_progress_fraction((17, 28), (0, 0)) == pytest.approx(17 / 28)
