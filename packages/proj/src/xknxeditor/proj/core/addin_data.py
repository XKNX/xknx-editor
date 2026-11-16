"""Address-bound app attachments in project archives."""

import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from xknxeditor.proj.models import Device, DeviceAddinData, Installation, Project

_ADDRESS_APPS = frozenset(("M0083-A0041", "M0083-A00D1"))
_SYMBOL_NAME = re.compile(r"(\d+)-(Lib|Symbol_\d+_(?:bmp|data))")


def packed_address(device: Device) -> int | None:
    if device.segment is None or device.address is None:
        return None
    line = device.segment.line
    return line.area.address * 4096 + line.address * 256 + device.address


def normalize_addin_data(session: Session, project: Project) -> bool:
    """Bind recognized address keys once, preserving all other metadata and files."""
    subtrees = dict(project.extra_subtrees or {})
    raw = subtrees.get("AddinData")
    if not raw or not project.imported_project_files:
        return False
    root = ET.fromstring(raw)
    by_address: dict[int, list[Device]] = defaultdict(list)
    for device in session.scalars(select(Device)):
        address = packed_address(device)
        if address is not None:
            by_address[address].append(device)
    with ZipFile(BytesIO(project.imported_project_files)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    removed: set[str] = set()
    counts = Counter((e.get("AddinId"), e.get("Name")) for e in root)
    for element in list(root):
        app_id = element.get("AddinId", "")
        name = element.get("Name", "")
        match = _SYMBOL_NAME.fullmatch(name)
        if app_id not in _ADDRESS_APPS or match is None:
            continue
        devices = by_address[int(match[1])]
        path = f"AddinData/{app_id}/{name}.dat"
        if len(devices) != 1 or path not in members or counts[app_id, name] != 1:
            continue
        device = devices[0]
        suffix = match[2]
        if any(
            row.app_id == app_id and row.suffix == suffix for row in device.addin_data
        ):
            continue
        device.addin_data.append(
            DeviceAddinData(
                app_id=app_id,
                suffix=suffix,
                data=members[path],
                metadata_xml=ET.tostring(element, encoding="unicode"),
            )
        )
        removed.add(path)
        root.remove(element)
    if not removed:
        return False
    subtrees["AddinData"] = ET.tostring(root, encoding="unicode")
    project.extra_subtrees = subtrees
    result = BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as archive:
        for name, data in members.items():
            if name not in removed:
                archive.writestr(name, data)
    project.imported_project_files = result.getvalue()
    session.flush()
    return True


def export_addin_data(
    project: Project, installation: Installation | None
) -> tuple[ET.Element, dict[str, bytes]]:
    """Return metadata and current attachment paths without changing project state."""
    raw = (project.extra_subtrees or {}).get("AddinData")
    root = ET.fromstring(raw) if raw else ET.Element("AddinData")
    files: dict[str, bytes] = {}
    keys = {(e.get("AddinId"), e.get("Name")) for e in root}
    session = object_session(project)
    if (
        session is not None
        and session.scalar(
            select(DeviceAddinData.id).join(Device).where(Device.segment_id.is_(None))
        )
        is not None
    ):
        raise ValueError("An app attachment needs an individual address for export")
    if installation is None:
        return root, files
    for area in installation.areas:
        for line in area.lines:
            for segment in line.segments:
                for device in segment.devices:
                    address = packed_address(device)
                    for row in device.addin_data:
                        validate_key(row.app_id, row.suffix)
                        if address is None:
                            raise ValueError(
                                "An app attachment needs an individual address for export"
                            )
                        name = f"{address}-{row.suffix}"
                        key = (row.app_id, name)
                        if key in keys:
                            raise ValueError(
                                f"Duplicate app attachment: {row.app_id}/{name}"
                            )
                        keys.add(key)
                        entry = (
                            ET.fromstring(row.metadata_xml)
                            if row.metadata_xml
                            else ET.Element("AddinData")
                        )
                        entry.set("Name", name)
                        entry.set("AddinId", row.app_id)
                        root.append(entry)
                        files[f"AddinData/{row.app_id}/{name}.dat"] = row.data
    return root, files


def validate_key(app_id: str, suffix: str) -> None:
    if not re.fullmatch(r"M[0-9A-F]{4}-A[0-9A-F]{4}", app_id):
        raise ValueError("Invalid app identifier")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", suffix):
        raise ValueError("Invalid app attachment key")
