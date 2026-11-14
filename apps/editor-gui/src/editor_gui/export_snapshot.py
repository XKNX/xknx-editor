"""Capture the database and evaluated export inputs before starting a worker."""

from __future__ import annotations

import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from editor_gui.plugins.backups.store import snapshot as snapshot_project

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService


@dataclass
class ExportSnapshot:
    source: Path
    program_refs: set[str]
    module_arguments: dict[int, dict[str, list[tuple[str, str]]]]
    group_object_trees: dict[int, str]
    password: str | None = field(repr=False)
    directory: tempfile.TemporaryDirectory[str] = field(repr=False)

    def cleanup(self) -> None:
        self.directory.cleanup()


def capture_export(
    project: ProjectService, lock: AbstractContextManager[object]
) -> ExportSnapshot:
    """Read evaluated inputs and copy their database under the shared project lock."""
    directory = tempfile.TemporaryDirectory(prefix="xknx_export_")
    try:
        with lock:
            source = project.working_path
            if source is None:
                raise ValueError("No project open")
            refs = project.program_refs()
            arguments = project.module_arguments()
            trees = project.group_object_trees()
            password = project.project_password
            database = Path(directory.name) / "project.xknx"
            snapshot_project(source, database)
        return ExportSnapshot(database, refs, arguments, trees, password, directory)
    except BaseException:
        directory.cleanup()
        raise
