"""The project's ``CodePage``: set and removed as one undo step, kept across reopening, written on
export only when set."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService, export_knxproj


def _info(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/project.xml"))
        root = ET.fromstring(zf.read(name))
    return next(e for e in root.iter() if e.tag.endswith("ProjectInformation"))


def test_set_undo_redo_and_reopen(tmp_path: Path) -> None:
    src = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CP")
    assert svc.project_code_page(pid) is None

    svc.set_project_code_page(pid, "iso-8859-5")
    assert svc.project_code_page(pid) == "iso-8859-5"
    assert svc.undo(pid)
    assert svc.project_code_page(pid) is None
    assert svc.redo(pid)
    assert svc.project_code_page(pid) == "iso-8859-5"

    svc.set_project_code_page(pid, None)
    assert svc.project_code_page(pid) is None
    assert svc.undo(pid)
    svc.close(pid)

    again = ProjectService()
    pid = again.open(src)
    assert again.project_code_page(pid) == "iso-8859-5"
    again.close(pid)


def test_setting_the_same_code_page_is_no_step(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-CP")
    svc.set_project_code_page(pid, None)
    assert not svc.undo(pid)
    svc.close(pid)


def test_an_unknown_code_page_is_refused(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-CP")
    with pytest.raises(ValueError, match="cp1251"):
        svc.set_project_code_page(pid, "cp1251")
    svc.close(pid)


def test_the_export_writes_the_code_page_only_when_set(tmp_path: Path) -> None:
    src = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-CP")
    svc.close(pid)
    out = tmp_path / "none.knxproj"
    export_knxproj(src, out, schema="23")
    assert _info(out).get("CodePage") is None

    pid = svc.open(src)
    svc.set_project_code_page(pid, "iso-8859-2")
    svc.close(pid)
    for schema in ("14", "20", "23"):
        out = tmp_path / f"set{schema}.knxproj"
        export_knxproj(src, out, schema=schema)
        assert _info(out).get("CodePage") == "iso-8859-2"
