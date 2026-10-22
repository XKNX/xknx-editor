"""Engine and storage helpers for a project's SQLite document.

A project *is* a file on disk. No global engine, no in-memory mode: a caller builds one via
:func:`make_engine` and owns its lifetime (:class:`ProjectService` holds one per open project).
:func:`url_for` maps a project path to its SQLAlchemy URL.

Schema changes: ``create_all`` only creates missing *tables*, never adds columns to an existing one.
So when the model gains a column, an already-saved ``.xknx`` would break on the next full-entity
query ("no such column"). :func:`_migrate` closes that gap by additively adding any mapped column
that a pre-existing table is missing (``ALTER TABLE ... ADD COLUMN`` with the column's type and
default). This handles the common additive case automatically; a non-additive change (rename, drop,
type change, data backfill) needs an explicit numbered step keyed off ``PRAGMA user_version``.
"""

import contextlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import Column

from xknxeditor.proj.models import Base

logger = logging.getLogger(__name__)


class ProjectStorageError(RuntimeError):
    """A project database cannot be created/opened at its location.

    Almost always a **network share** (SMB/CIFS/NFS): a ``.xknx`` project is a live SQLite database,
    and SQLite needs POSIX file locking + journal sidecars that network filesystems do not provide,
    so it fails with "unable to open database file". Also covers read-only or permission-less
    directories. The fix is to keep the project on a local disk.
    """


def _sqlite_file(url: str) -> str:
    """The on-disk path from a ``sqlite:///<path>`` URL (for error messages)."""
    return url[len("sqlite:///") :] if url.startswith("sqlite:///") else url


def ensure_sqlite_writable(path: Path) -> None:
    """Fail fast (with a clear :class:`ProjectStorageError`) if a SQLite DB can't live at ``path``.

    Probes the *actual* capability that matters — creating a database and doing a transactional
    write (which forces a journal + file lock) in ``path``'s directory — with a throwaway file, so
    an unusable location (network share, read-only dir) is caught up front instead of as a raw
    ``OperationalError`` part-way through a long import.
    """
    directory = path.parent
    probe = directory / f".xknx-writeprobe-{os.getpid()}.tmp"
    engine: Engine | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url_for(probe))
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE _probe (x INTEGER)"))
            conn.execute(text("INSERT INTO _probe VALUES (1)"))
    except (OperationalError, OSError) as e:
        raise ProjectStorageError(
            f"Cannot create a project database in {directory}. This is usually a network drive "
            "(SMB/NFS) or a read-only folder; KNX project files use SQLite and must be on a local "
            "disk. Choose a local folder and copy the finished file to the share afterwards."
        ) from e
    finally:
        if engine is not None:
            engine.dispose()
        # Best-effort cleanup: the probe (and any journal sidecar) may not exist, and on an
        # unusable location even the unlink can raise (e.g. a non-directory parent) — never let
        # cleanup mask the real error.
        for suffix in ("", "-journal", "-wal", "-shm"):
            with contextlib.suppress(OSError):
                Path(f"{probe}{suffix}").unlink(missing_ok=True)


# Bump when adding a non-additive migration below. Additive column adds are handled generically and
# do not require a bump. Stamped into the file's ``PRAGMA user_version`` so future migrations can
# branch on the version a file was last written with.
SCHEMA_VERSION = 2


def _merge_duplicate_com_objects(conn: Any) -> int:
    """Version 2: a com object its product references at two places of the parameter tree was
    saved twice. Merge each such pair into the oldest row: it keeps its values and takes the
    other's where it has none, and every group-address link moves onto it (a link both carry stays
    once, sending if either sent). Returns how many rows were removed."""
    groups = conn.execute(
        text(
            "SELECT device_id, ref_id, instance_ref_id, GROUP_CONCAT(id) FROM com_objects "
            "GROUP BY device_id, ref_id, instance_ref_id HAVING COUNT(*) > 1"
        )
    ).all()
    if not groups:
        return 0
    columns = [
        row[1]
        for row in conn.execute(text('PRAGMA table_info("com_objects")'))
        if row[1] not in ("id", "device_id", "ref_id", "instance_ref_id")
    ]
    removed = 0
    for _device, _ref, _instance, ids in groups:
        keep, *others = sorted(int(i) for i in str(ids).split(","))
        for other in others:
            for column in columns:
                conn.execute(
                    text(
                        f'UPDATE com_objects SET "{column}" = (SELECT "{column}" FROM com_objects '
                        f'WHERE id = :other) WHERE id = :keep AND "{column}" IS NULL'
                    ),
                    {"keep": keep, "other": other},
                )
            for link_id, ga_id, sending in conn.execute(
                text(
                    "SELECT id, group_address_id, is_sending FROM com_object_links "
                    "WHERE com_object_id = :other"
                ),
                {"other": other},
            ).all():
                kept = conn.execute(
                    text(
                        "SELECT id FROM com_object_links "
                        "WHERE com_object_id = :keep AND group_address_id = :ga"
                    ),
                    {"keep": keep, "ga": ga_id},
                ).first()
                if kept is None:
                    conn.execute(
                        text(
                            "UPDATE com_object_links SET com_object_id = :keep WHERE id = :id"
                        ),
                        {"keep": keep, "id": link_id},
                    )
                else:
                    if sending:
                        conn.execute(
                            text(
                                "UPDATE com_object_links SET is_sending = 1 WHERE id = :id"
                            ),
                            {"id": kept[0]},
                        )
                    conn.execute(
                        text("DELETE FROM com_object_links WHERE id = :id"),
                        {"id": link_id},
                    )
            conn.execute(text("DELETE FROM com_objects WHERE id = :id"), {"id": other})
            removed += 1
    return removed


def url_for(path: Path) -> str:
    """SQLAlchemy URL for a project file at ``path``."""
    return f"sqlite:///{path}"


def _json_default(default: Any) -> str:
    """Serialize a JSON column's model default to a literal (``[]`` for ``default=list`` etc.).

    Handles a scalar default arg and a zero-arg factory (``list``/``dict``); anything unexpected
    falls back to JSON ``null``.
    """
    value: Any = None
    if default is not None:
        arg = getattr(default, "arg", None)
        if getattr(default, "is_callable", False) and callable(arg):
            # SQLAlchemy wraps a plain factory (``list``/``dict``) to take an ExecutionContext, so
            # try the zero-arg form first, then pass a dummy context.
            for call in (lambda: arg(), lambda: arg(None)):
                try:
                    value = call()
                    break
                except TypeError:
                    continue
        elif getattr(default, "is_scalar", False):
            value = arg
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return "null"


def _column_ddl(column: Column[Any], dialect: Any) -> str:
    """``ALTER TABLE ADD COLUMN`` body for ``column`` (type + NOT NULL + DEFAULT).

    SQLite requires a literal DEFAULT when adding a NOT NULL column, so a scalar model default is
    rendered; NOT NULL columns without one fall back to a type-appropriate empty value.
    """
    type_sql = column.type.compile(dialect)
    default = column.default
    literal: str | None = None
    is_json = isinstance(column.type, JSON)
    if is_json:
        # A JSON column must default to valid JSON: '' would crash json.loads on read. Render the
        # model default (scalar value or the result of a zero-arg factory like ``list``/``dict``),
        # falling back to JSON null.
        literal = "'" + _json_default(default).replace("'", "''") + "'"
    elif default is not None and getattr(default, "is_scalar", False):
        arg: object = getattr(default, "arg", None)
        if isinstance(arg, bool):
            literal = "1" if arg else "0"
        elif isinstance(arg, (int, float)):
            literal = str(arg)
        else:
            literal = "'" + str(arg).replace("'", "''") + "'"
    parts = [f'"{column.name}"', type_sql]
    if not column.nullable:
        if literal is None:
            # NOT NULL with no scalar default: pick a safe empty value by type family.
            literal = (
                "0" if "INT" in type_sql.upper() or "BOOL" in type_sql.upper() else "''"
            )
        parts.append("NOT NULL")
    if literal is not None:
        parts.append(f"DEFAULT {literal}")
    return " ".join(parts)


def _migrate(engine: Engine) -> None:
    """Additively add any mapped column missing from a pre-existing table, then stamp the version.

    Opening a project must not take a write lock when nothing has to change. A bare
    ``PRAGMA user_version`` write escalates to an EXCLUSIVE lock on the database header, and the
    export path opens a second engine on the *live* working-copy file while the UI reads it every
    frame -- an unconditional stamp there raised ``database is locked`` straight out of the render
    loop (no ``busy_timeout``, so a contending read fails instead of waiting). So the version is
    stamped only when a column was actually added or the stored version differs; an already-current
    database stays read-only and never contends with concurrent readers.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue  # create_all just made it fresh with every column
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                conn.execute(
                    text(
                        f'ALTER TABLE "{table.name}" '
                        f"ADD COLUMN {_column_ddl(column, engine.dialect)}"
                    )
                )
                added.append(f"{table.name}.{column.name}")
        current = conn.execute(text("PRAGMA user_version")).scalar()
        if (current or 0) < 2 and "com_objects" in tables:
            merged = _merge_duplicate_com_objects(conn)
            if merged:
                logger.info("merged %d duplicate com-object rows", merged)
        if added or current != SCHEMA_VERSION:
            conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
            logger.debug(
                "migrated project db (write lock taken): added=%s user_version=%s->%s",
                added or "none",
                current,
                SCHEMA_VERSION,
            )


class _FrameDbProfiler:
    """Dev-only watchdog for uncached per-frame DB reads.

    In the immediate-mode GUI a panel's ``render()`` runs ~60x/s; a DB read there that is not cached
    by revision becomes a 60 Hz SQLite query + ORM rebuild that pins the CPU and keeps the app off
    idling. This profiler counts queries issued on the *render thread* and, when the project has not
    changed across several consecutive frames yet queries keep firing, returns a one-off warning that
    names the offending statements. Off unless ``XKNX_DEBUG_DB_FRAMES=1``; adds no cost otherwise.
    """

    # Consecutive no-change frames with queries before we warn: a legitimate lazy cache miss touches
    # the DB for a single frame, an uncached per-frame read touches it every frame (streak grows).
    _THRESHOLD = 3

    def __init__(self) -> None:
        self.enabled = os.environ.get("XKNX_DEBUG_DB_FRAMES") == "1"
        self._render_thread: int | None = None
        self._count = 0
        self._stmts: set[str] = set()
        self._last_revision: int | None = None
        self._streak = 0

    def note(self, statement: str) -> None:
        """Record a query. Ignores queries off the render thread (background import/IO), so only
        per-frame reads that actually stall the UI are counted."""
        if self._render_thread is None or threading.get_ident() != self._render_thread:
            return
        self._count += 1
        if len(self._stmts) < 8:
            self._stmts.add(statement.split("\n", 1)[0][:80])

    def end_frame(self, revision: int) -> str | None:
        """Close the current frame (call once per rendered frame from the render thread). Returns a
        warning string the first time a query-without-change streak crosses the threshold, else None."""
        self._render_thread = threading.get_ident()
        msg: str | None = None
        if self._count > 0 and revision == self._last_revision:
            self._streak += 1
            if self._streak == self._THRESHOLD:
                stmts = "; ".join(sorted(self._stmts))
                msg = (
                    f"{self._count} DB query/queries per frame with no project change over "
                    f"{self._THRESHOLD} frames (uncached read in a panel render?): {stmts}"
                )
        else:
            self._streak = 0
        self._last_revision = revision
        self._count = 0
        self._stmts.clear()
        return msg


frame_db_profiler = _FrameDbProfiler()


def make_engine(url: str) -> Engine:
    """Build a configured SQLAlchemy engine (SQLite pragmas + schema auto-create/migrate).

    The caller owns it. Existing files are migrated additively to the current model (see module doc).
    """
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_conn: Any, _: Any) -> None:  # pyright: ignore[reportUnusedFunction]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute("PRAGMA foreign_keys=ON")
        # Overwrite deleted content with zeros, so a secret replaced by its encrypted form (see
        # core.project_secrets) leaves no readable copy in the file's free pages.
        cursor.execute("PRAGMA secure_delete=ON")
        cursor.close()

    if frame_db_profiler.enabled:

        @event.listens_for(engine, "after_cursor_execute")
        def _count_query(  # pyright: ignore[reportUnusedFunction]
            _conn: Any,
            _cursor: Any,
            statement: str,
            _params: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            frame_db_profiler.note(statement)

    # create_all/_migrate are the first DB writes; on a network share (SMB/NFS) or a read-only dir
    # SQLite fails here with "unable to open database file". Translate that into a clear, typed
    # error for every caller (create/open/import) instead of a raw OperationalError stacktrace.
    try:
        Base.metadata.create_all(engine)
        _migrate(engine)
    except OperationalError as e:
        engine.dispose()
        raise ProjectStorageError(
            f"Cannot open the project database {_sqlite_file(url)}. This is usually a network drive "
            "(SMB/NFS) or a read-only folder; KNX project files use SQLite and must be on a local "
            "disk."
        ) from e
    return engine
