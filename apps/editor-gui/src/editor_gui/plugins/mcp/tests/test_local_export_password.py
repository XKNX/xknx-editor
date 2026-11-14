"""Local export stages the project for the selected ETS version."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


@pytest.mark.parametrize("password", [None, "project-password"])
@pytest.mark.parametrize("change_after_staging", [False, True])
@pytest.mark.parametrize("ets6", [False, True])
async def test_local_export_forwards_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    password: str | None,
    change_after_staging: bool,
    ets6: bool,
) -> None:
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.new(tmp_path / "project.xknx")
    if password:
        project.set_project_password(password)
    run_locked = ctx.run_locked

    def stage_then_change(fn: Callable[[], Any], *, timeout: float) -> Any:
        result = run_locked(fn, timeout=timeout)
        if change_after_staging:
            project.set_project_password("changed-password")
        return result

    monkeypatch.setattr(ctx, "run_locked", stage_then_change)
    bundle = SimpleNamespace(extra_files={}, master_xml=None)
    stage = Mock()
    signer = Mock(
        return_value=SimpleNamespace(
            certificate_written=True,
            validation_ok=True,
            license_name="Owner",
            licensed_devices=1,
            notes=(),
        )
    )
    monkeypatch.setattr("xknxeditor.proj.ets_local_available", lambda: ets6)
    monkeypatch.setattr("xknxeditor.proj.ets5_local_available", lambda: True)
    monkeypatch.setattr(
        "xknxeditor.proj.ets_local_sign" if ets6 else "xknxeditor.proj.ets5_local_sign",
        signer,
    )
    monkeypatch.setattr("xknxeditor.proj.export_knxproj", stage)
    monkeypatch.setattr(
        "editor_gui.plugins.project.knxproj_manufacturer.collect_manufacturer_bundle",
        lambda *_: bundle,
    )
    dest = str(tmp_path / "export.knxproj")
    try:
        async with Client(build_server(ctx)) as client:
            result = await _data(client, "project_export_via_ets", dest=dest)
        assert result["status"] == "exported"
        stage.assert_called_once()
        signer.assert_called_once()
        assert stage.call_args.kwargs["schema"] == ("23" if ets6 else "20")
        assert stage.call_args.kwargs["password"] == password
        assert signer.call_args.kwargs["password"] == password
        assert signer.call_args.args == (stage.call_args.args[1], dest)
        assert not stage.call_args.args[1].exists()
    finally:
        project.close()
