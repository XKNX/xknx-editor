"""The development commands ``generate-catalog`` and ``generate-demo`` (``editor_gui.scripts``)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from editor_gui.scripts import generate_catalog, generate_demo

from xknxeditor.catalog import CatalogService
from xknxeditor.proj import ProjectService

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)
# the application of the fixture, one of the demo devices
_APPLICATION = "M-0008_A-7072-21-5CC3-O000A"


def _applications(catalog_path: Path) -> list[str]:
    catalog = CatalogService(catalog_path)
    try:
        return [a.application_id for a in catalog.list_applications()]
    finally:
        catalog.close()


def _catalog(path: Path) -> Path:
    generate_catalog.generate_catalog(path, [_FIXTURE])
    return path


def _module_in(module: object, base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Let ``Path(__file__).parent`` x4 of a script module be ``base`` (its app folder)."""
    monkeypatch.setattr(
        module, "__file__", str(base / "src" / "editor_gui" / "scripts" / "x.py")
    )


def test_generate_catalog(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Imports the applications, skips missing files and reports broken ones."""
    broken = tmp_path / "broken.knxprod"
    broken.write_bytes(b"not a knxprod")
    catalog_path = tmp_path / "catalog.xknxcatalog"

    generate_catalog.generate_catalog(
        catalog_path, [_FIXTURE, tmp_path / "missing.knxprod", broken]
    )
    output = capsys.readouterr().out
    assert f"- {_APPLICATION}" in output
    assert "Skipping (not found)" in output
    assert "Error:" in output
    assert "1 application(s) added" in output
    assert _applications(catalog_path) == [_APPLICATION]

    generate_catalog.generate_catalog(catalog_path, [_FIXTURE])
    output = capsys.readouterr().out
    assert "No new applications (already in catalog)" in output
    assert "0 application(s) added" in output


def test_generate_catalog_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``generate-catalog`` with files, from internal/knxprod, and without either."""
    config = tmp_path / "config"
    monkeypatch.setattr("editor_gui.settings.config_dir", lambda: config)
    _module_in(generate_catalog, tmp_path / "app", monkeypatch)
    catalog_path = config / "catalog.xknxcatalog"

    monkeypatch.setattr(sys, "argv", ["generate-catalog"])
    generate_catalog.main()
    assert "Usage: generate-catalog" in capsys.readouterr().out
    assert not catalog_path.exists()

    internal = tmp_path / "app" / "internal" / "knxprod"
    internal.mkdir(parents=True)
    shutil.copy(_FIXTURE, internal)
    generate_catalog.main()
    assert f"Loading from: {internal}" in capsys.readouterr().out
    assert _applications(catalog_path) == [_APPLICATION]

    catalog_path.unlink()
    monkeypatch.setattr(sys, "argv", ["generate-catalog", str(_FIXTURE)])
    generate_catalog.main()
    assert "1 application(s) added" in capsys.readouterr().out


def test_generate_demo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Adds the demo devices found in the catalog to a new project, replacing an old one."""
    catalog_path = _catalog(tmp_path / "catalog.xknxcatalog")
    output_path = tmp_path / "demo.xknx"
    output_path.write_bytes(b"an older demo project")
    capsys.readouterr()

    generate_demo.generate_demo(output_path, catalog_path)
    output = capsys.readouterr().out
    assert "Added: Button Interface 2-gang (1.1.4)" in output
    # the other demo applications are not in this catalog
    assert (
        output.count("not in catalog, skipping") == len(generate_demo.DEMO_DEVICES) - 1
    )

    svc = ProjectService()
    pid = svc.open(output_path)
    try:
        assert [(d.name, d.address) for d in svc.devices(pid)] == [
            ("Button Interface 2-gang", 4)
        ]
    finally:
        svc.close(pid)


def test_generate_demo_without_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a catalog no project is created."""
    output_path = tmp_path / "demo.xknx"
    generate_demo.generate_demo(output_path, tmp_path / "missing.xknxcatalog")
    assert "Run 'uv run generate-catalog' first" in capsys.readouterr().out
    assert not output_path.exists()


def test_generate_demo_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``generate-demo`` writes demo.xknx into the app folder from the user's catalog."""
    config = tmp_path / "config"
    config.mkdir()
    _catalog(config / "catalog.xknxcatalog")
    monkeypatch.setattr("editor_gui.settings.config_dir", lambda: config)
    (tmp_path / "app").mkdir()
    _module_in(generate_demo, tmp_path / "app", monkeypatch)

    generate_demo.main()
    assert (tmp_path / "app" / "demo.xknx").exists()


def test_tp_segment_missing() -> None:
    """A project without a TP line has no segment for the demo devices."""

    class _NoLines:
        def topology(self, _pid: str, _installation: int) -> object:
            return type("Installation", (), {"areas": []})()

    with pytest.raises(RuntimeError, match="no TP line"):
        generate_demo._tp_segment(_NoLines(), "pid")  # type: ignore[arg-type]
