"""A project import (group address export, .knxproj) can be cancelled until the new project replaces
the destination: then nothing changes, neither the destination nor the open project."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.project import service as project_service
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod.errors import ImportCancelled

_GA_EXPORT = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<GroupAddress-Export xmlns="http://knx.org/xml/ga-export/01">
  <GroupRange Name="Licht" RangeStart="2048" RangeEnd="4095">
    <GroupRange Name="EG" RangeStart="2048" RangeEnd="2303">
      <GroupAddress Name="Wohnen" Address="1/0/1" DPTs="DPST-1-1" />
    </GroupRange>
  </GroupRange>
</GroupAddress-Export>"""

_KNXPROJ = (
    Path(__file__).resolve().parents[7]
    / "packages/proj/tests/fixtures/xknx_test_project_no_password.knxproj"
)


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))

    def ignore(*_args: object, **_kwargs: object) -> None:
        pass

    log = SimpleNamespace(debug=ignore, info=ignore, warning=ignore, error=ignore)
    project.set_logger(log)  # pyright: ignore[reportArgumentType]
    return project


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if ".import-" in p.name)


def test_group_address_import(
    service: ProjectService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "ga.xml"
    source.write_text(_GA_EXPORT, encoding="utf-8")
    dest = tmp_path / "out" / "ga.xknx"
    dest.parent.mkdir()
    cancel = threading.Event()
    build = project_service._import_ga_export  # pyright: ignore[reportPrivateUsage]

    def build_then_cancel(*args: object, **kwargs: object) -> None:
        build(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        cancel.set()

    # Cancelled while building: no project, no temp file, committing never announced.
    monkeypatch.setattr(project_service, "_import_ga_export", build_then_cancel)
    with pytest.raises(ImportCancelled):
        service.import_ga_export(
            source, dest, cancel=cancel, committing=lambda: pytest.fail("committed")
        )
    assert not dest.exists()
    assert _leftovers(dest.parent) == []
    monkeypatch.undo()

    # Not cancelled: committing is announced once, before the project is installed.
    committed: list[bool] = []
    service.import_ga_export(
        source,
        dest,
        cancel=threading.Event(),
        committing=lambda: committed.append(dest.exists()),
    )
    assert committed == [False]
    assert dest.exists()
    assert service.is_open


def test_knxproj_import(service: ProjectService, tmp_path: Path) -> None:
    if not _KNXPROJ.exists():
        pytest.skip("knxproj fixture missing")
    dest = tmp_path / "p.xknx"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        service.import_knxproj(_KNXPROJ, dest, cancel=cancel)
    assert not dest.exists()
    assert _leftovers(tmp_path) == []
    assert not service.is_open
