from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from xknxeditor.proj import ProjectService


def test_shared_attachment_undo_reopen_and_store_export(tmp_path: Path):
    service = ProjectService()
    path = tmp_path / "p.xknx"
    pid = service.create(path, "P-ABCD")
    try:
        service.set_project_user_file(pid, "symbols.zip", b"first", label="Symbols")
        assert service.project_user_file(pid, "symbols.zip") == b"first"
        cursor = service.cursor(pid)
        service.set_project_user_file(pid, "symbols.zip", b"first", label="Symbols")
        assert service.cursor(pid) == cursor
        service.undo(pid)
        assert service.project(pid).imported_project_files is None
        assert "UserFiles" not in (service.project(pid).extra_subtrees or {})
        service.redo(pid)
        service.set_project_user_file(pid, "unrelated.bin", b"keep", label="File")
        service.set_project_user_file(pid, "symbols.zip", b"second", label="Symbols")
        service.close(pid)
        pid = service.open(path)
        service.undo(pid)
        assert service.project_user_file(pid, "symbols.zip") == b"first"
        assert service.project_user_file(pid, "unrelated.bin") == b"keep"
        service.redo(pid)
        project = service.project(pid)
        assert service.project_user_file(pid, "symbols.zip") == b"second"
        with ZipFile(BytesIO(project.imported_project_files)) as z:
            assert z.namelist().count("UserFiles/symbols.zip") == 1
    finally:
        service.close(pid)


@pytest.mark.parametrize(
    "filename", ["../bad", "a/b", "a\\b", "", "..", "C:bad", "bad\x00"]
)
def test_attachment_names_cannot_escape_user_files(tmp_path, filename):
    service = ProjectService()
    pid = service.create(tmp_path / "p.xknx", "P-ABCD")
    try:
        with pytest.raises(ValueError):
            service.set_project_user_file(pid, filename, b"data", label="File")
    finally:
        service.close(pid)
