"""New-project overwrite + Save as.

Regression: "New project" onto an existing .xknx (the save dialog lets you pick one to overwrite)
re-seeded into the old database and raised "UNIQUE constraint failed: installations.index". New now
starts from a fresh file. Save as snapshots the (auto-persisted) project to a chosen location.
"""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService


def _project(tmp_path: Path) -> ProjectService:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    return proj


def test_new_over_existing_file_starts_fresh(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    p = tmp_path / "p.xknx"
    proj.new(p)
    proj.create_group_address(address="1/2/3", name="old")
    proj.new(p)  # overwrite the same path — must not collide, must be empty
    assert [g.address for g in proj.group_addresses] == []


def test_save_as_preserves_data_and_switches_file(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    proj.new(tmp_path / "untitled.xknx")
    proj.create_group_address(address="1/2/3", name="keep")
    saved = proj.save_as(tmp_path / "final")  # no suffix -> .xknx appended
    assert saved == tmp_path / "final.xknx"
    assert proj.path == saved
    assert [g.address for g in proj.group_addresses] == ["1/2/3"]


def test_save_as_over_existing_target(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    # A pre-existing project at the target path must be cleanly overwritten by save as.
    other = _project(tmp_path)
    other.new(tmp_path / "target.xknx")
    other.create_group_address(address="7/7/7", name="stale")
    other.close()

    proj.new(tmp_path / "untitled.xknx")
    proj.create_group_address(address="1/1/1", name="new")
    proj.save_as(tmp_path / "target.xknx")
    assert [g.address for g in proj.group_addresses] == ["1/1/1"]


def test_save_as_onto_its_own_file_keeps_it(tmp_path: Path) -> None:
    # Regression: the file was deleted before the copy from it, so the project was lost.
    proj = _project(tmp_path)
    path = tmp_path / "p.xknx"
    proj.new(path)
    proj.create_group_address(address="1/2/3", name="keep")
    for target in (path, tmp_path / "p"):
        assert proj.save_as(target) is not None
        assert proj.path == path
    proj.close()
    proj.open(path)
    assert [g.address for g in proj.group_addresses] == ["1/2/3"]
