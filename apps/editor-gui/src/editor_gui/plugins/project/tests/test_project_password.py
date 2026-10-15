"""Project name and password in the editor's project service."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.proj import InvalidProjectPassword, ProjectPasswordRequired


def _service(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    return service


def test_rename_and_password(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.new(tmp_path / "p.xknx", name="Haus")
    service.set_project_name("  Wohnung ")
    service.set_project_name("")  # an empty name keeps the current one
    service.set_project_password("geheim")
    info = service.get_project_metadata()
    assert info is not None
    assert (info.name, info.password_protected) == ("Wohnung", True)

    service.set_project_password(None)
    info = service.get_project_metadata()
    assert info is not None and not info.password_protected
    # A password change is no undo step.
    assert [entry.display_text for entry in service.history()][:1] == [
        "Rename project to 'Wohnung'"
    ]
    service.close()


def test_secure_project_keeps_its_password(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    service = _service(tmp_path)
    service.new(path)
    service.create_group_address("1/0/1", "Secure")
    service.set_project_password("geheim")
    service.close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE group_addresses SET data_secure = 1")

    service = _service(tmp_path)
    with pytest.raises(ProjectPasswordRequired):
        service.open(path)
    service.open(path, "geheim")
    assert service.project_needs_password()
    assert service.project_password == "geheim"
    with pytest.raises(ValueError):
        service.set_project_password(None)
    service.set_project_password("anders")  # changing it is fine
    info = service.get_project_metadata()
    assert info is not None and info.password_protected
    service.close()
    service.open(path, "anders")
    assert service.project_password == "anders"
    service.close()


def test_a_wrong_password_keeps_the_open_project(tmp_path: Path) -> None:
    protected = tmp_path / "protected.xknx"
    service = _service(tmp_path)
    service.new(protected)
    service.set_project_password("geheim")
    service.close()
    service.new(tmp_path / "other.xknx", name="Other")
    with pytest.raises(InvalidProjectPassword):
        service.open(protected, "falsch")
    info = service.get_project_metadata()
    assert info is not None and info.name == "Other"
    service.close()
