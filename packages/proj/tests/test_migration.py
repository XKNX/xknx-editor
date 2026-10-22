"""Additive schema migration: reopening a project written by an older schema must add the columns
the model has since gained, so full-entity queries keep working (no "no such column")."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from xknxeditor.proj.core.service import ProjectService
from xknxeditor.proj.db import make_engine, url_for


def test_reopen_adds_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "proj.xknx"
    svc = ProjectService()
    pid = svc.create(path)
    svc.close(pid)

    # Simulate a file written before these columns existed.
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE devices DROP COLUMN serial_number")
    con.execute("ALTER TABLE devices DROP COLUMN parameters_loaded")
    con.execute("ALTER TABLE group_addresses DROP COLUMN unfiltered")
    con.execute("ALTER TABLE lines DROP COLUMN additional_group_addresses")
    con.execute("ALTER TABLE com_objects DROP COLUMN instance_ref_id")
    con.commit()
    con.close()

    # Reopening must migrate (add) them back and leave the project queryable.
    svc2 = ProjectService()
    pid2 = svc2.open(path)
    assert (
        svc2.devices(pid2) == []
    )  # full-entity query works again (no OperationalError)

    cols = inspect(make_engine(url_for(path)))
    device_cols = {c["name"] for c in cols.get_columns("devices")}
    assert {"serial_number", "parameters_loaded"} <= device_cols
    assert "unfiltered" in {c["name"] for c in cols.get_columns("group_addresses")}
    assert "additional_group_addresses" in {
        c["name"] for c in cols.get_columns("lines")
    }
    # Projects imported before instance_ref_id existed keep their (module-stripped) ref_id; the
    # column is added empty so export falls back to it rather than failing to open the project.
    assert "instance_ref_id" in {c["name"] for c in cols.get_columns("com_objects")}


def test_reopen_migrates_json_column_to_valid_default(tmp_path: Path) -> None:
    """A JSON column added to an existing table must default to valid JSON (``[]`` for
    ``default=list``), not ``''`` — otherwise reading a migrated row crashes in ``json.loads``."""
    from sqlalchemy.orm import Session

    from xknxeditor.proj.models import Device, ModuleInstance

    path = tmp_path / "proj.xknx"
    svc = ProjectService()
    pid = svc.create(path)
    seg = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == seg
    )
    device_id = svc.add_device(pid, segment_id, "M-1_H-1_P-1", address=5, name="D")
    with Session(make_engine(url_for(path))) as session:
        device = session.get(Device, device_id)
        assert device is not None
        device.module_instances.append(
            ModuleInstance(instance_id="MI-1", ref_id="MD-1")
        )
        session.commit()
    svc.close(pid)

    # Simulate a file written before the module-instance JSON/text columns existed.
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE module_instances DROP COLUMN arguments")
    con.execute("ALTER TABLE module_instances DROP COLUMN repeat_index")
    con.commit()
    con.close()

    ProjectService().open(path)  # triggers the additive migration
    with Session(make_engine(url_for(path))) as session:
        mi = session.query(ModuleInstance).one()
        assert (
            mi.arguments == []
        )  # valid JSON default, not None/'' -> no json.loads crash
        assert mi.repeat_index == ""


def test_reopen_stamps_user_version(tmp_path: Path) -> None:
    from xknxeditor.proj.db import SCHEMA_VERSION

    path = tmp_path / "proj.xknx"
    svc = ProjectService()
    svc.close(svc.create(path))
    con = sqlite3.connect(path)
    try:
        (version,) = con.execute("PRAGMA user_version").fetchone()
    finally:
        con.close()
    assert version == SCHEMA_VERSION


def test_reopen_up_to_date_db_takes_no_write_lock(tmp_path: Path) -> None:
    """An already-current open must stay read-only: no ALTER and no ``user_version`` stamp.

    The export path opens a second engine on the live working-copy file while the UI reads it per
    frame; a bare ``PRAGMA user_version`` write there escalated to an EXCLUSIVE lock and raised
    ``database is locked`` out of the render loop. The first open stamps the version, the second must
    not write at all."""
    from sqlalchemy import event

    path = tmp_path / "proj.xknx"
    svc = ProjectService()
    svc.close(svc.create(path))

    engine = make_engine(url_for(path))
    writes: list[str] = []

    @event.listens_for(engine, "after_cursor_execute")
    def _record(  # pyright: ignore[reportUnusedFunction]
        _c: object, _cur: object, statement: str, _p: object, _ctx: object, _many: bool
    ) -> None:
        normalized = " ".join(statement.strip().upper().split())
        if normalized.startswith("ALTER") or "USER_VERSION =" in normalized:
            writes.append(statement.strip())

    from xknxeditor.proj.db import _migrate

    _migrate(engine)
    engine.dispose()
    assert writes == []


def test_reopen_merges_duplicate_com_objects(tmp_path: Path) -> None:
    # Version 2: a com object saved twice (referenced at two places of the parameter tree) becomes
    # one row with both rows' links and the overrides the kept row lacks.
    path = tmp_path / "proj.xknx"
    svc = ProjectService()
    pid = svc.create(path)
    segment = svc.topology(pid, 0).areas[0].lines[0].segments[0].id
    dev = svc.add_device(
        pid,
        segment,
        "M-0001_H-x-1_P-1",
        address=1,
        com_objects=[("O-1_R-1", None), ("O-1_R-1", None), ("O-2_R-1", None)],
    )
    first, second = sorted(
        co.id
        for co in svc.devices(pid, with_contents=True)[0].com_objects
        if co.ref_id == "O-1_R-1"
    )
    ga1 = svc.create_group_address(pid, 0, 0x0801, "a")
    ga2 = svc.create_group_address(pid, 0, 0x0802, "b")
    svc.link_com_object(pid, first, ga1)
    svc.link_com_object(pid, second, ga1, sending=True)
    svc.link_com_object(pid, second, ga2)
    svc.close(pid)
    con = sqlite3.connect(path)
    con.execute("UPDATE com_objects SET text_override = 'Kept' WHERE id = ?", (second,))
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()

    svc2 = ProjectService()
    pid2 = svc2.open(path)
    (device,) = svc2.devices(pid2, with_contents=True)
    assert device.id == dev
    merged = [co for co in device.com_objects if co.ref_id == "O-1_R-1"]
    assert [co.id for co in merged] == [first]
    assert merged[0].text_override == "Kept"
    assert sorted(
        (link.group_address_id, link.is_sending) for link in merged[0].links
    ) == [
        (ga1, True),
        (ga2, False),
    ]
    assert len(device.com_objects) == 2
