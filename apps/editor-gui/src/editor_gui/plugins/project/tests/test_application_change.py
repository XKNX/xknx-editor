"""Change Application Program and the local Update target of a device."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.catalog.tests.test_delete_product import _fixture
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService

_HW = "M-0008_H-8.20.2F.2F.205192.2000-0-O000A"
_APP = "M-0008_A-7072-21-5CC3-O000A"


def _program(
    db: Path, suffix: str, number: int, version: int, status: str = "Registered"
) -> str:
    """Add another program of the fixture's hardware with an application of its own."""
    program = f"{_HW}_HP-{suffix}"
    app = f"M-0008_A-{suffix}"
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO applications (id, name, application_number, application_version)"
            " VALUES (?, ?, ?, ?)",
            (app, f"App {suffix}", number, version),
        )
        con.execute(
            "INSERT INTO hardware_programs"
            " (id, hardware_id, application_id, knxprod_path, registration_status)"
            " VALUES (?, ?, ?, '', ?)",
            (program, _HW, app, status),
        )
    return program


def _setup(tmp_path: Path) -> tuple[CatalogService, ProjectService, Path]:
    db = tmp_path / "c.xknxcatalog"
    cat = CatalogService(db)
    cat.import_knxprod(_fixture())
    (product,) = cat.get_products()
    app = cat.get_application(_APP)
    assert app is not None
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    assert proj.add_device(
        product.product_ref_id, product.hardware2program_ref_id, "Button", app
    )
    return cat, proj, db


def test_targets_and_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cat, proj, db = _setup(tmp_path)
    other = _program(db, "7099-10-AAAA", 0x7099, 0x10)
    newer = _program(db, "7072-22-BBBB", 0x7072, 0x22)
    newest = _program(db, "7072-23-CCCC", 0x7072, 0x23)
    _program(db, "7072-24-DDDD", 0x7072, 0x24, status="Unregistered")
    cat.refresh()
    replaces = {newer: [0x21], newest: [0x20]}
    monkeypatch.setattr(cat, "replaces_versions", lambda p: replaces.get(p, []))
    (device,) = proj.devices

    assert {p.id for p in proj.application_change_targets(device)} == {
        other,
        newer,
        newest,
    }
    target = proj.local_update_target(device)
    assert target is not None and target.id == newer
    assert proj.newer_application_version(device) == 0x22

    replaces[newest] = [0x21]
    assert proj.newer_application_version(device) == 0x23
    proj.close()


def test_change_resets_the_device(tmp_path: Path) -> None:
    cat, proj, db = _setup(tmp_path)
    (device,) = proj.devices
    defaults = sorted(co.id for co in device.com_objects)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO hardware_programs"
            " (id, hardware_id, application_id, knxprod_path, registration_status)"
            " VALUES (?, ?, ?, '', 'Registered')",
            (f"{_HW}_HP-copy", _HW, _APP),
        )
    cat.refresh()
    (program,) = proj.application_change_targets(device)

    assert proj.change_application(device, program)

    (changed,) = proj.devices
    info = proj.get_device_info(changed.node_id)
    assert info is not None and info.hardware2program_ref_id == program.id
    assert sorted(co.id for co in changed.com_objects) == defaults
    assert proj.undo()
    info = proj.get_device_info(changed.node_id)
    assert info is not None and info.hardware2program_ref_id != program.id
    proj.close()
