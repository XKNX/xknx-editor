"""A download that fails after it began writing the device is flagged as partial.

The Load Procedure unloads a device's tables before rewriting them, so a failure
partway through can leave the device without valid tables. ``download`` wraps such a
failure in a ``PartialDownloadError`` for load scopes so the caller can warn the user;
an unload is meant to clear the device, so its failures pass through unwrapped, as do
failures before any state was changed.
"""

from __future__ import annotations

import importlib
from typing import Any, cast

import pytest
from xknx import XKNX

from xknxeditor.download.errors import DownloadError, PartialDownloadError
from xknxeditor.download.scope import DownloadScope

dl = importlib.import_module("xknxeditor.download.download")


class _FailingRunner:
    """A runner whose procedure fails, reporting whether it had mutated state."""

    def __init__(self, *, state_mutated: bool) -> None:
        self.state_mutated = state_mutated

    async def run(self, progress: Any = None) -> None:
        raise DownloadError("object 1 did not reach LOADING, last state LOADED")


async def _same_image(*args: Any) -> Any:
    return args[3]


async def _run(
    monkeypatch: pytest.MonkeyPatch, *, scope: DownloadScope, state_mutated: bool
) -> None:
    runner = _FailingRunner(state_mutated=state_mutated)
    monkeypatch.setattr(dl, "_resolve_controls", lambda *a, **k: [])
    monkeypatch.setattr(dl, "_device_association_format", _same_image)
    monkeypatch.setattr(dl, "LoadProcedureRunner", lambda *a, **k: runner)

    await dl.download(
        cast("XKNX", object()),
        "1.1.3",
        cast("Any", object()),
        image=cast("Any", object()),
        scope=scope,
    )


async def test_failure_after_mutation_is_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(PartialDownloadError) as info:
        await _run(monkeypatch, scope=DownloadScope.FULL, state_mutated=True)
    # the original failure is preserved as the cause and in the message text, so it
    # survives logging even where the chained cause is not rendered
    assert isinstance(info.value.__cause__, DownloadError)
    assert "did not reach LOADING" in str(info.value)


async def test_failure_before_mutation_is_not_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DownloadError) as info:
        await _run(monkeypatch, scope=DownloadScope.FULL, state_mutated=False)
    assert not isinstance(info.value, PartialDownloadError)


async def test_unload_failure_is_not_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unload is meant to clear the device, so a failure is not a bricking surprise.
    with pytest.raises(DownloadError) as info:
        await _run(monkeypatch, scope=DownloadScope.UNLOAD, state_mutated=True)
    assert not isinstance(info.value, PartialDownloadError)
