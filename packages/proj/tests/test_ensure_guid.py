"""A project always has a GUID once asked for: one made by an earlier version is given one,
kept from then on."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from xknxeditor.proj import ProjectService


def test_a_project_without_a_guid_is_given_one(tmp_path: Path) -> None:
    path = tmp_path / "old.xknx"
    svc = ProjectService()
    pid = svc.create(path, name="Old")
    created = svc.project(pid).guid
    assert svc.ensure_guid(pid) == created
    svc.close(pid)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE projects SET guid = ''")
        db.commit()

    pid = svc.open(path)
    given = svc.ensure_guid(pid)
    assert given
    assert given != created
    assert svc.ensure_guid(pid) == given
    svc.close(pid)

    pid = svc.open(path)
    assert svc.project(pid).guid == given
    svc.close(pid)
