"""Full-download commissioning of a virgin device.

A device that was never programmed still answers on the default address, not on its
configured one, so a point-to-point download to the configured address gets no ACK. A
``FULL`` download with ``commission=True`` first writes the configured address to the
single device in programming mode (only when nothing already answers there), then runs
the load procedure. Partial scopes never commission.
"""

from __future__ import annotations

import importlib
from typing import Any, cast

import pytest
from xknx import XKNX

import xknxeditor.download.commissioning as commissioning
from xknxeditor.download.scope import DownloadScope

# The package re-exports the ``download`` function, which shadows the submodule name on
# attribute access, so import the module object explicitly to monkeypatch its internals.
dl = importlib.import_module("xknxeditor.download.download")


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    present: bool,
    scope: DownloadScope,
    commission: bool,
) -> list[str]:
    """Run ``download`` with the network stack faked out, returning the event trace."""
    trace: list[str] = []

    async def fake_present(xknx: XKNX, address: Any, security: Any) -> bool:
        trace.append("probe")
        return present

    async def fake_program(
        xknx: XKNX, address: Any, *, serial_number: bytes | None = None
    ) -> None:
        trace.append(f"assign:{address}")

    async def fake_reset(xknx: XKNX) -> None:
        trace.append("reset")

    class FakeRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def run(self, progress: Any = None) -> None:
            trace.append("run")

    def fake_resolve(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def same_image(*args: Any) -> Any:
        return args[3]

    monkeypatch.setattr(dl, "_device_present", fake_present)
    monkeypatch.setattr(commissioning, "program_individual_address", fake_program)
    monkeypatch.setattr(commissioning, "reset_individual_address", fake_reset)
    monkeypatch.setattr(dl, "_resolve_controls", fake_resolve)
    monkeypatch.setattr(dl, "_device_association_format", same_image)
    monkeypatch.setattr(dl, "LoadProcedureRunner", FakeRunner)

    await dl.download(
        cast("XKNX", object()),
        "1.1.24",
        cast("Any", object()),
        image=cast("Any", object()),
        scope=scope,
        commission=commission,
    )
    return trace


async def test_full_download_assigns_address_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = await _run(
        monkeypatch, present=False, scope=DownloadScope.FULL, commission=True
    )
    assert trace == ["probe", "assign:1.1.24", "run"]


async def test_full_download_skips_assign_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = await _run(
        monkeypatch, present=True, scope=DownloadScope.FULL, commission=True
    )
    assert trace == ["probe", "run"]


async def test_partial_download_never_commissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = await _run(
        monkeypatch, present=False, scope=DownloadScope.PARAMETERS, commission=True
    )
    assert trace == ["run"]


async def test_commission_disabled_never_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = await _run(
        monkeypatch, present=False, scope=DownloadScope.FULL, commission=False
    )
    assert trace == ["run"]


async def test_unload_all_resets_address_after_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = await _run(
        monkeypatch, present=True, scope=DownloadScope.UNLOAD_ALL, commission=False
    )
    assert trace == ["run", "reset"]


async def test_plain_unload_leaves_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = await _run(
        monkeypatch, present=True, scope=DownloadScope.UNLOAD, commission=False
    )
    assert trace == ["run"]
