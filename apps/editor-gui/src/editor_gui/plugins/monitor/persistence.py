"""Persist a monitor's telegrams per project, reusing the CommunicationLog XML serializer.

The Group Monitor can keep its telegrams across restarts: they are written under the config dir in
a folder per project (so each project reloads its own log) and bounded by an optional size in MB.
"""

from __future__ import annotations

from pathlib import Path

from editor_gui.plugins.backups.store import project_folder
from editor_gui.plugins.monitor.records import MonitorRecord, TelegramLog
from editor_gui.plugins.monitor.xmlio import export_xml, import_xml
from editor_gui.settings import config_dir

_MB = 1024 * 1024


def monitor_dir() -> Path:
    """The folder holding the per-project persistence folders."""
    return config_dir() / "monitor"


def group_file(guid: str, name: str) -> Path:
    """The Group Monitor's persistence file for the project ``guid``/``name`` (readable name as a
    prefix, the short guid making it unique), under ``<config dir>/monitor/``."""
    return project_folder(monitor_dir(), guid, name) / "group.xml"


def load(path: Path) -> list[MonitorRecord]:
    """The telegrams persisted at ``path`` (empty when missing or unreadable)."""
    try:
        return import_xml(path.read_bytes()).records
    except (OSError, ValueError):
        return []


def _bounded(records: list[MonitorRecord], max_file_mb: int) -> bytes:
    """``records`` as a CommunicationLog, trimmed from the oldest until within ``max_file_mb``
    (0 = no limit). A single record larger than the limit is kept (it cannot be split)."""
    data = export_xml(records)
    limit = max_file_mb * _MB
    while limit and len(data) > limit and len(records) > 1:
        records = records[max(len(records) // 10, 1) :]
        data = export_xml(records)
    return data


def flush(log: TelegramLog, path: Path, max_file_mb: int) -> bool:
    """Write ``log`` to ``path`` (bounded by ``max_file_mb``) and mark it saved; return whether the
    write succeeded. The in-memory log keeps its own records (bounded by its count cap), so the file
    can hold a smaller recent slice.

    The write is atomic (a sibling temp file is replaced into place) so a crash or a full disk never
    leaves a half-written ``group.xml`` that would reload as an empty log. ``sequence`` is captured
    before the snapshot so a telegram arriving during the write is not wrongly marked saved."""
    token = log.sequence
    data = _bounded(log.snapshot(), max_file_mb)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        return False
    log.mark_saved(token)
    return True
