"""Reading the project row and its log stays cheap: the large blobs load only when read, and the
project log is read without the project row."""

from pathlib import Path

from sqlalchemy import inspect

from xknxeditor.proj import ProjectService
from xknxeditor.proj.models import ProjectTrace

_BLOBS = ("knx_master_xml", "imported_product_members", "imported_project_files")


def _project(tmp_path: Path) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-READ")
    session = svc._state(pid).session
    project = svc.project(pid)
    project.knx_master_xml = b"master"
    project.imported_product_members = b"members" * 1000
    project.imported_project_files = b"files"
    session.add_all(
        ProjectTrace(
            project_id=pid, date=f"2026-01-0{day}", user_name="u", comment=f"c{day}"
        )
        for day in (3, 1, 2)
    )
    session.commit()
    session.expire_all()
    return svc, pid


def test_the_blobs_load_only_when_read(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    project = svc.project(pid)
    assert project.name is not None
    assert set(_BLOBS) <= inspect(project).unloaded
    assert project.imported_product_members == b"members" * 1000


def test_the_project_log_in_document_order(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    assert svc.project_traces(pid) == [
        ("2026-01-03", "u", "c3"),
        ("2026-01-01", "u", "c1"),
        ("2026-01-02", "u", "c2"),
    ]
