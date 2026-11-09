"""A download is limited to the APDU length the project's couplers on the route pass."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from xknx.telegram import IndividualAddress

import editor_gui.programming as programming
from editor_gui.plugins.connection.service import ConnectionService
from xknxeditor.download import route
from xknxeditor.download.scope import DownloadScope

# Issue #245: tunnel 1.0.100 on the main line, device 1.3.39 behind line coupler 1.3.0.
_DEVICE = SimpleNamespace(
    individual_address="1.3.39",
    name="RoundTouch",
    download_plugin_required=False,
    factory_image=None,
    second_application=None,
)


def _service(
    monkeypatch: pytest.MonkeyPatch, project_devices: set[str]
) -> tuple[ConnectionService, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    async def capture(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    def run_now(coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(asyncio.run(coro))
        return future

    monkeypatch.setattr(programming, "download_device", capture)
    monkeypatch.setattr(programming, "eval_device", capture)
    service = ConnectionService()
    service._xknx = MagicMock(current_address=IndividualAddress("1.0.100"))  # pyright: ignore[reportPrivateUsage]
    service.set_logger(MagicMock())
    service.project_device = lambda address: (
        object() if address in project_devices else None
    )
    monkeypatch.setattr(service, "run_async", run_now)
    return service, calls


@pytest.mark.parametrize("evaluate", [False, True])
def test_the_projects_couplers_on_the_route_reach_the_download(
    monkeypatch: pytest.MonkeyPatch, evaluate: bool
) -> None:
    service, calls = _service(monkeypatch, {"1.3.0", "1.3.39"})
    device = cast(Any, _DEVICE)
    if evaluate:
        service.evaluate_device(device, DownloadScope.FULL)
    else:
        service.program_device(device, DownloadScope.FULL)
    (kwargs,) = calls
    assert kwargs["route_couplers"] == [IndividualAddress("1.3.0")]


def test_a_coupler_not_in_the_project_is_not_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, calls = _service(monkeypatch, {"1.3.39"})
    service.program_device(cast(Any, _DEVICE), DownloadScope.FULL)
    (kwargs,) = calls
    assert kwargs["route_couplers"] == []


@pytest.mark.parametrize(
    ("interface", "route_limit", "expected"),
    [(210, 55, 55), (None, 55, 55), (50, 55, 50), (210, None, 210), (None, None, None)],
)
@pytest.mark.parametrize(
    ("function", "target"),
    [("download_device", "download"), ("eval_device", "preflight")],
)
async def test_the_route_limits_the_interfaces_apdu_length(
    monkeypatch: pytest.MonkeyPatch,
    function: str,
    target: str,
    interface: int | None,
    route_limit: int | None,
    expected: int | None,
) -> None:
    calls: list[dict[str, Any]] = []
    asked: list[list[IndividualAddress]] = []

    async def capture(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    async def limit(couplers: list[IndividualAddress], connect: Any) -> int | None:
        asked.append(list(couplers))
        return route_limit

    monkeypatch.setattr(programming, target, capture)
    monkeypatch.setattr(programming, "_image_for", lambda *a: None)
    monkeypatch.setattr(programming, "_application", lambda *a: None)
    monkeypatch.setattr(route, "route_max_apdu_length", limit)
    couplers = [IndividualAddress("1.3.0")]
    await getattr(programming, function)(
        cast(Any, None),
        cast(Any, _DEVICE),
        interface_max_apdu_length=interface,
        route_couplers=couplers,
    )
    assert asked == [couplers]
    assert calls[0]["interface_max_apdu_length"] == expected


async def test_no_coupler_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def capture(*args: Any, **kwargs: Any) -> None:
        pass

    async def limit(couplers: Any, connect: Any) -> int | None:
        raise AssertionError("no coupler to ask")

    monkeypatch.setattr(programming, "download", capture)
    monkeypatch.setattr(programming, "_image_for", lambda *a: None)
    monkeypatch.setattr(programming, "_application", lambda *a: None)
    monkeypatch.setattr(route, "route_max_apdu_length", limit)
    await programming.download_device(cast(Any, None), cast(Any, _DEVICE))
