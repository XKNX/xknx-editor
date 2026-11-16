"""Shared project attachments and their metadata."""

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from xknxeditor.proj.models import Project


def validate_filename(filename: str) -> None:
    if (
        not filename
        or len(filename) > 255
        or filename in (".", "..")
        or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in filename)
    ):
        raise ValueError("Invalid project attachment filename")


def read_user_file(project: Project, filename: str) -> bytes | None:
    validate_filename(filename)
    if project.imported_project_files is None:
        return None
    with ZipFile(BytesIO(project.imported_project_files)) as archive:
        members = [
            i for i in archive.infolist() if i.filename == f"UserFiles/{filename}"
        ]
        if len(members) > 1:
            raise ValueError("Duplicate project attachment")
        return archive.read(members[0]) if members else None


def write_user_file(project: Project, filename: str, data: bytes | None) -> None:
    validate_filename(filename)
    name = f"UserFiles/{filename}"
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as target:
        if project.imported_project_files is not None:
            with ZipFile(BytesIO(project.imported_project_files)) as source:
                target.comment = source.comment
                for member in source.infolist():
                    if member.filename != name:
                        target.writestr(member, source.read(member))
        if data is not None:
            target.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
    subtrees = dict(project.extra_subtrees or {})
    root = ET.fromstring(subtrees.get("UserFiles", "<UserFiles />"))
    matches = [e for e in root if e.get("Filename") == filename]
    if data is None:
        for element in matches:
            root.remove(element)
    elif not matches:
        ET.SubElement(root, "UserFile", Filename=filename)
    subtrees["UserFiles"] = ET.tostring(root, encoding="unicode")
    project.imported_project_files = output.getvalue()
    project.extra_subtrees = subtrees
