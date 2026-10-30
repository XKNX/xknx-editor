"""A device whose product has a second application program (a PEI program) is downloaded with
it: the program comes from the catalog, read once, and reaches the download."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import editor_gui.programming as programming
from editor_gui.plugins.connection.service import (
    _second_application,  # pyright: ignore[reportPrivateUsage]
)

from .test_delete_product import _project_with_device


def test_a_device_reads_its_products_second_application_once(tmp_path: Path) -> None:
    catalog, project, _ = _project_with_device(tmp_path)
    (device,) = project.devices
    assert device.second_application is not None
    # The fixture's product has one application program only.
    assert device.second_application() is None
    assert _second_application(device) is None

    reads: list[str] = []
    pei = cast(Any, SimpleNamespace(id="M-0008_A-7072-43-1234"))
    service = cast(Any, catalog)._service

    def get_second_application(program_id: str) -> Any:
        reads.append(program_id)
        return pei

    service.get_second_application = get_second_application
    cast(Any, catalog)._second_applications.clear()
    assert catalog.second_application("HP-1") is pei
    assert catalog.second_application("HP-1") is pei
    assert reads == ["HP-1"]


def test_a_device_without_a_source_has_no_second_application() -> None:
    assert (
        _second_application(cast(Any, SimpleNamespace(second_application=None))) is None
    )
    pei = cast(Any, object())
    assert (
        _second_application(cast(Any, SimpleNamespace(second_application=lambda: pei)))
        is pei
    )


@pytest.mark.parametrize(
    ("function", "target"),
    [("download_device", "download"), ("eval_device", "preflight")],
)
async def test_the_second_application_reaches_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, function: str, target: str
) -> None:
    _, project, _ = _project_with_device(tmp_path)
    (device,) = project.devices
    calls: list[dict[str, Any]] = []

    async def capture(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(programming, target, capture)
    pei = cast(Any, object())
    await getattr(programming, function)(
        cast(Any, None), device, second_application=pei
    )
    assert calls[0]["application2"] is pei
