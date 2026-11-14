"""MCP exports use the export format of the settings, as the GUI does."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


@pytest.fixture
def bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "editor_gui.plugins.project.knxproj_manufacturer.collect_manufacturer_bundle",
        lambda *_: SimpleNamespace(extra_files={}, master_xml=None),
    )


@pytest.mark.usefixtures("bundle")
@pytest.mark.parametrize("chosen", ["20", "23"])
async def test_export_uses_the_export_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chosen: str
) -> None:
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.new(tmp_path / "project.xknx")
    export = Mock(
        return_value=SimpleNamespace(unverifiable_folders=[], missing_references=[])
    )
    monkeypatch.setattr("xknxeditor.proj.export_knxproj", export)
    monkeypatch.setattr(
        "editor_gui.plugins.mcp.tools.project.export_schema", lambda: chosen
    )
    try:
        async with Client(build_server(ctx)) as client:
            result = await _data(
                client, "project_export_knxproj", dest=str(tmp_path / "out.knxproj")
            )
        assert result["status"] == "exported"
        assert export.call_args.kwargs["schema"] == chosen
    finally:
        project.close()


@pytest.mark.usefixtures("bundle")
@pytest.mark.parametrize(
    ("version_6", "chosen", "staged"),
    [
        (True, "23", "23"),
        (True, "20", "20"),
        # version 5 only reads project/20
        (False, "23", "20"),
    ],
)
async def test_local_export_uses_the_export_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version_6: bool,
    chosen: str,
    staged: str,
) -> None:
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.new(tmp_path / "project.xknx")
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
    monkeypatch.setattr("xknxeditor.proj.ets_local_available", lambda: version_6)
    monkeypatch.setattr("xknxeditor.proj.ets5_local_available", lambda: True)
    monkeypatch.setattr(
        "xknxeditor.proj.ets_local_sign"
        if version_6
        else "xknxeditor.proj.ets5_local_sign",
        signer,
    )
    monkeypatch.setattr("xknxeditor.proj.export_knxproj", stage)
    monkeypatch.setattr(
        "editor_gui.plugins.mcp.tools.project.export_schema", lambda: chosen
    )
    try:
        async with Client(build_server(ctx)) as client:
            result = await _data(
                client, "project_export_via_ets", dest=str(tmp_path / "out.knxproj")
            )
        assert result["status"] == "exported"
        signer.assert_called_once()
        assert stage.call_args.kwargs["schema"] == staged
    finally:
        project.close()
