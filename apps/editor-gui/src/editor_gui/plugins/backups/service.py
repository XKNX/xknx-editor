"""Making, listing and restoring the backups of the open project (files: see ``store``).

Backups are written on a thread of their own, one at a time, so the editor keeps going while a
large project is compressed. They are made on request, when a project is closed and at an
interval while it changes (see ``settings``); a project whose state did not change since its
newest backup gets no automatic one."""

from __future__ import annotations

import contextlib
import queue
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from editor_gui import __version__
from editor_gui.plugins.backups import settings as settings_store
from editor_gui.plugins.backups.settings import BackupSettings
from editor_gui.plugins.backups.store import (
    KIND_CLOSE,
    KIND_INTERVAL,
    KIND_MANUAL,
    KIND_RESTORE,
    Backup,
    BackupError,
    BackupInfo,
    backups_of_file,
    check_backup,
    check_project_file,
    delete_backup,
    expired,
    extract_project,
    list_backups,
    machine_name,
    move_aside,
    project_folder,
    set_pinned,
    snapshot,
    user_name,
    write_backup,
)

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.project.service import ProjectService

# How often (seconds) the interval backup looks whether one is due.
_INTERVAL_CHECK = 60.0


@dataclass(frozen=True)
class ProjectMeta:
    """What a backup of the open project records, read while it is open."""

    guid: str
    name: str
    source: Path
    working: Path
    state: str


class BackupJob:
    """A backup task on the backup thread: ``step`` says what it does now and ``done`` of
    ``total`` how far it is; ``future`` ends with the backup made (None when none was due) or
    the error. ``kind`` is the kind of the backup."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.step = ""
        self.done = 0
        self.total = 0
        self.future: Future[Backup | None] = Future()

    def progress(self, step: str) -> Callable[[int, int], None]:
        def report(done: int, total: int) -> None:
            self.step, self.done, self.total = step, done, total

        return report


class BackupService:
    def __init__(
        self,
        project: ProjectService,
        io_lock: threading.RLock,
        log: Logger,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``io_lock`` is the lock that guards the project (see ``CatalogService.io_lock``)."""
        self._project = project
        self._io_lock = io_lock
        self._log = log
        self._now = now
        self._clock = clock
        self._settings = settings_store.load()
        self._queue: queue.Queue[
            tuple[BackupJob, Callable[[BackupJob], Backup | None]]
        ] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._current: BackupJob | None = None
        self._pending = 0
        self._idle = threading.Event()
        self._idle.set()
        # Jobs that ended and were not taken by the UI yet (see take_finished).
        self._finished: list[BackupJob] = []
        # The project being closed, when a backup is to be made of it (see _on_closing).
        self._closing: ProjectMeta | None = None
        # Set while a restore replaces the project: its closing needs no backup.
        self._restoring = False
        self._last_interval_check: float | None = None
        # When the open project was opened (first seen by tick), by its generation.
        self._opened: tuple[int, datetime] = (-1, datetime.min.replace(tzinfo=UTC))
        # The listed backups of the open project: (its GUID, the backups), and whether the
        # list is to be read again.
        self._listing: tuple[str, list[Backup]] = ("", [])
        self._listing_stale = True
        # The open project's GUID, by the generation of the project it was read from.
        self._guid_of: tuple[int, str] = (-1, "")
        project.subscribe("closing", self._on_closing)
        project.subscribe("closed", self._on_closed)

    # --- settings ---------------------------------------------------------

    @property
    def settings(self) -> BackupSettings:
        return self._settings

    def set_settings(self, settings: BackupSettings) -> BackupSettings:
        self._settings = settings_store.save(settings)
        self._listing_stale = True
        return self._settings

    @property
    def root(self) -> Path:
        return self._settings.root

    # --- the open project ---------------------------------------------------

    def meta(self) -> ProjectMeta | None:
        """What a backup of the open project records; None with no project open (or while a
        background open holds it). Called where the project may be read (the UI thread)."""
        project = self._project
        source, working = project.path, project.working_path
        if not project.is_open or project.loading or source is None or working is None:
            return None
        with self._io_lock:
            state = project.state_key()
            info = project.get_project_metadata()
            if not state or info is None:
                return None
            return ProjectMeta(project.ensure_guid(), info.name, source, working, state)

    def _guid(self) -> str:
        """The open project's GUID, read once per opened project; empty with none open."""
        project = self._project
        if not project.is_open:
            return ""
        if self._guid_of[0] != project.generation and not project.loading:
            self._guid_of = (project.generation, project.ensure_guid())
        return self._guid_of[1] if self._guid_of[0] == project.generation else ""

    def backups(self) -> list[Backup]:
        """The open project's backups as last read, the newest first; reads them again in the
        background when they may have changed."""
        guid = self._guid()
        if guid != self._listing[0]:
            self._listing = (guid, [])
            self._listing_stale = True
        if self._listing_stale and guid:
            self._listing_stale = False
            root = self.root
            self._submit(BackupJob("list"), lambda _job: self._read_listing(root, guid))
        return self._listing[1]

    def folder(self) -> Path:
        """The folder of the open project's backups once it has some, else the backup
        folder."""
        listed = self._listing[1] if self._listing[0] == self._guid() else []
        return listed[0].path.parent if listed else self.root

    def _read_listing(self, root: Path, guid: str) -> None:
        found = list_backups(root, guid)
        if self._listing[0] == guid:
            self._listing = (guid, found)

    def list_now(self) -> list[Backup]:
        """The open project's backups, read now (for a caller that may wait)."""
        meta = self.meta()
        return [] if meta is None else list_backups(self.root, meta.guid)

    def refresh(self) -> None:
        self._listing_stale = True

    # --- jobs ---------------------------------------------------------------

    @property
    def current(self) -> BackupJob | None:
        """The backup being made now (not a list being read)."""
        job = self._current
        return job if job is not None and job.kind != "list" else None

    def take_finished(self) -> list[BackupJob]:
        """The backup jobs that ended since the last call."""
        with self._lock:
            finished, self._finished = self._finished, []
        return finished

    def _submit(
        self, job: BackupJob, work: Callable[[BackupJob], Backup | None]
    ) -> BackupJob:
        with self._lock:
            self._pending += 1
            self._idle.clear()
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="backups", daemon=True
                )
                self._thread.start()
        self._queue.put((job, work))
        return job

    def _run(self) -> None:
        while True:
            job, work = self._queue.get()
            self._current = job
            try:
                job.future.set_result(work(job))
            except Exception as e:
                if not isinstance(e, BackupError):
                    self._log.error("backup failed", error=f"{type(e).__name__}: {e}")
                job.future.set_exception(e)
            finally:
                self._current = None
                with self._lock:
                    if job.kind != "list":
                        self._finished.append(job)
                    self._pending -= 1
                    if self._pending == 0:
                        self._idle.set()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until every backup asked for is written (at exit); False on a timeout."""
        return self._idle.wait(timeout)

    # --- making backups -------------------------------------------------------

    def create(self, comment: str = "", kind: str = KIND_MANUAL) -> BackupJob:
        """Make a backup of the open project as it is now, in the background (raises
        :class:`BackupError` with no project open)."""
        meta = self.meta()
        if meta is None:
            raise BackupError("no project is open")
        root = self.root
        return self._submit(
            BackupJob(kind),
            lambda job: self._make_live(meta, root, kind, comment, job),
        )

    def _info(self, meta: ProjectMeta, kind: str, comment: str) -> BackupInfo:
        return BackupInfo(
            project_guid=meta.guid,
            project_name=meta.name,
            created=self._now(),
            kind=kind,
            comment=comment,
            user=user_name(),
            machine=machine_name(),
            source=str(meta.source),
            state=meta.state,
            app_version=__version__,
        )

    def _make_live(
        self, meta: ProjectMeta, root: Path, kind: str, comment: str, job: BackupJob
    ) -> Backup:
        """A backup of the open project's file, taken as one consistent state while it stays
        open."""
        with tempfile.TemporaryDirectory(prefix="xknx-backup-") as tmp:
            copy = Path(tmp) / "project.xknx"
            snapshot(meta.working, copy, job.progress("copy"))
            return self._write(meta, root, copy, kind, comment, job)

    def _write(
        self,
        meta: ProjectMeta,
        root: Path,
        copy: Path,
        kind: str,
        comment: str,
        job: BackupJob,
    ) -> Backup:
        """Check the project file ``copy`` and write it as a backup; then delete the automatic
        backups that are due."""
        job.progress("check")(0, 0)
        problem = check_project_file(copy)
        if problem is not None:
            self._log.error(
                "the project file failed its check, no backup was made",
                project=str(meta.source),
                error=problem,
            )
            raise BackupError(problem)
        folder = project_folder(root, meta.guid, meta.name)
        backup = write_backup(
            copy, folder, self._info(meta, kind, comment), job.progress("write")
        )
        self._log.info(
            "backup created", kind=kind, path=str(backup.path), size=backup.size
        )
        self._expire(root, meta.guid)
        self._listing_stale = True
        return backup

    def _expire(self, root: Path, guid: str) -> None:
        settings = self._settings
        if not settings.expire:
            return
        due = expired(
            list_backups(root, guid),
            days=settings.expire_days,
            keep_newest=settings.keep_newest,
            now=self._now(),
        )
        for backup in due:
            try:
                delete_backup(backup)
                self._log.info("old backup deleted", path=str(backup.path))
            except BackupError as e:
                self._log.warning(
                    "old backup not deleted", path=str(backup.path), error=str(e)
                )

    def _unchanged(self, root: Path, meta: ProjectMeta) -> bool:
        """Whether the newest backup holds the project's state already."""
        found = list_backups(root, meta.guid)
        return bool(found) and found[0].info.state == meta.state

    def _on_closing(self) -> None:
        self._closing = None
        if self._restoring or not self._settings.on_close:
            return
        try:
            self._closing = self.meta()
        except Exception as e:
            self._log.warning("no backup on close", error=f"{type(e).__name__}: {e}")

    def _on_closed(self, working: Path) -> None:
        """Copy the closed project's file (now no longer written to) and write the backup of it
        in the background, unless its newest backup holds the same state."""
        meta, self._closing = self._closing, None
        if meta is None:
            return
        tmp = Path(tempfile.mkdtemp(prefix="xknx-backup-"))
        copy = tmp / "project.xknx"
        try:
            shutil.copyfile(working, copy)
        except OSError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            self._log.error("no backup on close", error=str(e))
            return
        root = self.root

        def work(job: BackupJob) -> Backup | None:
            try:
                if self._unchanged(root, meta):
                    return None
                return self._write(meta, root, copy, KIND_CLOSE, "", job)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        self._submit(BackupJob(KIND_CLOSE), work)

    def tick(self) -> None:
        """Called every frame: start an interval backup once one is due and the project changed
        since its newest backup."""
        settings = self._settings
        if not settings.interval or not self._project.is_open:
            return
        now = self._clock()
        last = self._last_interval_check
        if last is not None and now - last < _INTERVAL_CHECK:
            return
        self._last_interval_check = now
        if self._pending:
            return
        meta = self.meta()
        if meta is None:
            return
        root, minutes = self.root, settings.interval_minutes
        opened = self._opened_at()

        def work(job: BackupJob) -> Backup | None:
            found = list_backups(root, meta.guid)
            if found and found[0].info.state == meta.state:
                return None
            # The interval counts from the newest backup, but not from before the project was
            # opened.
            since = max(found[0].info.created, opened) if found else opened
            if self._now() - since < timedelta(minutes=minutes):
                return None
            return self._make_live(meta, root, KIND_INTERVAL, "", job)

        self._submit(BackupJob(KIND_INTERVAL), work)

    def _opened_at(self) -> datetime:
        """When the open project was opened (first seen by :meth:`tick`)."""
        generation = self._project.generation
        if self._opened[0] != generation:
            self._opened = (generation, self._now())
        return self._opened[1]

    # --- using backups ----------------------------------------------------------

    def restore(
        self,
        backup: Backup,
        password: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Replace the open project by the state of ``backup``, after saving its current state
        as a backup (so the restore can be taken back). ``password`` opens a backup protected
        with another password than the project's now (raises ``ProjectPasswordRequired`` /
        ``InvalidProjectPassword`` before anything changes). Runs in the caller's thread, which
        holds the project meanwhile."""
        from xknxeditor.proj.core.project_secrets import check_project_password

        with (
            self._io_lock,
            tempfile.TemporaryDirectory(prefix="xknx-restore-") as tmp,
        ):
            meta = self.meta()
            if meta is None:
                raise BackupError("no project is open")
            restored = Path(tmp) / "restored.xknx"
            extract_project(backup.path, restored, progress)
            problem = check_project_file(restored)
            if problem is not None:
                raise BackupError(problem)
            password = password or self._project.project_password
            check_project_password(restored, password)
            if not self._unchanged(self.root, meta):
                self._make_live(
                    meta, self.root, KIND_RESTORE, "", BackupJob(KIND_RESTORE)
                )
            self._restoring = True
            try:
                self._project.replace_with(restored, password)
            finally:
                self._restoring = False
        self._listing_stale = True
        self._log.info("project restored", backup=str(backup.path))

    def restore_file(self, backup: Backup, path: Path) -> Path | None:
        """Write the project of ``backup`` to ``path`` (a project file that is damaged or gone);
        a file there is renamed first, and its new path returned."""
        kept = move_aside(path, self._now()) if path.exists() else None
        extract_project(backup.path, path)
        self._log.info(
            "project file restored",
            path=str(path),
            backup=str(backup.path),
            kept=str(kept) if kept else None,
        )
        return kept

    def backups_of_file(self, path: Path) -> list[Backup]:
        """The backups made from the project file ``path``, the newest first."""
        return backups_of_file(self.root, path)

    def export(self, backup: Backup, dest: Path) -> None:
        """Write the project of ``backup`` as a project file ``dest``."""
        for own in (self._project.path, self._project.working_path):
            if own is not None and dest.resolve() == own.resolve():
                raise BackupError("the backup cannot replace the open project's file")
        extract_project(backup.path, dest)
        self._log.info("backup exported", backup=str(backup.path), path=str(dest))

    def check(self, backup: Backup) -> str | None:
        """Why ``backup`` cannot be restored, or None when it is intact."""
        return check_backup(backup.path)

    def set_pinned(self, backup: Backup, pinned: bool) -> Backup:
        changed = set_pinned(backup, pinned)
        self._listing_stale = True
        return changed

    def delete(self, backup: Backup) -> None:
        delete_backup(backup)
        self._log.info("backup deleted", path=str(backup.path))
        self._listing_stale = True

    def find(self, path: str) -> Backup:
        """The open project's backup at ``path`` (raises :class:`BackupError` for another
        file)."""
        for backup in self.list_now():
            with contextlib.suppress(OSError):
                if backup.path.resolve() == Path(path).resolve():
                    return backup
        raise BackupError(f"no backup of the open project at {path}")
