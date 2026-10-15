"""Project name and password: the name is an undoable edit, the password protects the project and
the project folder of an export."""

import io
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import pyzipper
from xknxproject.exceptions import InvalidPasswordException

from xknxeditor.proj import (
    InvalidProjectPassword,
    ProjectPasswordRequired,
    ProjectService,
    export_knxproj,
    import_knxproj,
)
from xknxeditor.proj.core.knxproj_protect import (
    ets6_zip_password,
    is_password_protected,
    protected_folder_zip,
)
from xknxeditor.proj.core.knxproj_signing import verify_directory_signature

_FILES = {"project.xml": b"<KNX/>", "0.xml": b"<KNX>" + b"x" * 5000 + b"</KNX>"}


def _project(tmp_path: Path, password: str | None) -> Path:
    path = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-1234", name="Haus")
    area_id = svc.create_area(pid, 0, 2, "Area 2")
    svc.create_line(pid, area_id, 1, "Line 1")
    svc.create_group_address(pid, 0, 0x0801, "GA One")
    if password is not None:
        svc.set_project_password(pid, password)
    svc.close(pid)
    return path


def test_zipcrypto_folder() -> None:
    data = protected_folder_zip(_FILES, "Pässwort", schema=20)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert all(info.flag_bits & 0x1 for info in archive.infolist())
        with pytest.raises(RuntimeError):
            archive.read("0.xml", pwd=b"wrong")
        archive.setpassword("Pässwort".encode())
        assert {name: archive.read(name) for name in archive.namelist()} == _FILES


def test_aes_folder() -> None:
    data = protected_folder_zip(_FILES, "Pässwort", schema=23)
    with pyzipper.AESZipFile(io.BytesIO(data)) as archive:
        archive.setpassword(ets6_zip_password("Pässwort"))
        assert {name: archive.read(name) for name in archive.namelist()} == _FILES
    with zipfile.ZipFile(io.BytesIO(data)) as raw:
        # WinZip AES (method 99) with a 256-bit key.
        assert {info.compress_type for info in raw.infolist()} == {99}


@pytest.mark.parametrize("schema", ["20", "23"])
def test_protected_export_round_trip(tmp_path: Path, schema: str) -> None:
    out = tmp_path / "out.knxproj"
    export_knxproj(_project(tmp_path, "geheim"), out, schema=schema, password="geheim")

    assert is_password_protected(out)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        assert not any(name.startswith("P-1234/") for name in names)
        assert archive.getinfo("P-1234.zip").compress_type == zipfile.ZIP_STORED
        info = json.loads(archive.read("P-1234.info").decode("utf-8-sig"))
        signature = archive.read("P-1234.signature")
        inner = archive.read("P-1234.zip")
    assert info["IsPasswordProtected"] is True
    assert info["ProjectName"] == "Haus"
    if int(schema) >= 21:
        folder = pyzipper.AESZipFile(io.BytesIO(inner))
        folder.setpassword(ets6_zip_password("geheim"))
    else:
        folder = zipfile.ZipFile(io.BytesIO(inner))
        folder.setpassword(b"geheim")
    files = {name: folder.read(name) for name in folder.namelist()}
    # The signature covers the decrypted folder.
    assert verify_directory_signature(files, signature)

    for wrong in ("wrong", None):
        with pytest.raises(InvalidPasswordException):
            import_knxproj(out, tmp_path / "wrong.xknx", password=wrong)
    again = tmp_path / "again.xknx"
    pid = import_knxproj(out, again, password="geheim")
    svc = ProjectService()
    with pytest.raises(ProjectPasswordRequired):
        svc.open(again)
    # The import keeps the password, so the next export is protected again.
    svc.open(again, "geheim")
    assert svc.project_password(pid) == "geheim"
    assert svc.project(pid).name == "Haus"
    svc.close(pid)


def test_unprotected_export(tmp_path: Path) -> None:
    out = tmp_path / "out.knxproj"
    export_knxproj(_project(tmp_path, None), out)
    assert not is_password_protected(out)
    with zipfile.ZipFile(out) as archive:
        assert "P-1234/0.xml" in archive.namelist()
        info = json.loads(archive.read("P-1234.info").decode("utf-8-sig"))
    assert info["IsPasswordProtected"] is False
    pid = import_knxproj(out, tmp_path / "again.xknx")
    svc = ProjectService()
    svc.open(tmp_path / "again.xknx")
    assert not svc.is_password_protected(pid)
    svc.close(pid)


def test_name_is_undoable_and_password_is_not(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-1234", name="Haus")
    svc.set_project_name(pid, "Wohnung")
    svc.set_project_password(pid, "geheim")
    assert (svc.project(pid).name, svc.project_password(pid)) == ("Wohnung", "geheim")

    assert svc.undo(pid)
    assert svc.project(pid).name == "Haus"
    assert svc.project_password(pid) == "geheim"
    assert not svc.undo(pid)
    assert svc.redo(pid)
    assert svc.project(pid).name == "Wohnung"
    svc.set_project_password(pid, None)
    assert not svc.is_password_protected(pid)
    svc.close(pid)


def test_open_needs_the_password(tmp_path: Path) -> None:
    path = _project(tmp_path, "geheim")
    svc = ProjectService()
    with pytest.raises(ProjectPasswordRequired):
        svc.open(path)
    with pytest.raises(InvalidProjectPassword):
        svc.open(path, "falsch")
    pid = svc.open(path, "geheim")
    svc.set_project_password(pid, "anders")
    svc.close(pid)
    with pytest.raises(InvalidProjectPassword):
        svc.open(path, "geheim")
    pid = svc.open(path, "anders")
    assert svc.project_password(pid) == "anders"
    svc.close(pid)
    with pytest.raises(InvalidProjectPassword):
        export_knxproj(path, tmp_path / "out.knxproj", password="geheim")


def test_a_password_kept_in_the_clear_is_taken_over(tmp_path: Path) -> None:
    path = _project(tmp_path, None)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE projects ADD COLUMN password TEXT")
        db.execute("UPDATE projects SET password = 'geheim'")
        db.execute(
            "INSERT INTO events (type, data, timestamp, reverted) VALUES "
            "('SetProjectPassword', '{\"password\": \"geheim\"}', '2026-01-01', 0)"
        )
    svc = ProjectService()
    pid = svc.open(path)
    assert svc.project_password(pid) == "geheim"
    svc.close(pid)
    assert b"geheim" not in path.read_bytes()
    pid = svc.open(path, "geheim")
    svc.close(pid)


def test_has_secure_data(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-1234")
    svc.create_group_address(pid, 0, 0x0801, "GA One")
    assert not svc.has_secure_data(pid)
    svc.close(pid)

    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE group_addresses SET data_secure = 1")
    pid = svc.open(path)
    assert svc.has_secure_data(pid)
    svc.close(pid)
