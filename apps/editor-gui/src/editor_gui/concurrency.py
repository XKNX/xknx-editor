"""A tiny guard for service reads during a background import.

A long ``.knxproj`` import runs on a worker thread that mutates the catalog and project databases.
The GUI panels read those same services every frame on the UI thread. To keep the UI responsive
without racing the writer, the importer holds a shared *re-entrant* lock for the whole import, and
every per-frame service read is wrapped with :func:`io_guarded`: it acquires the lock without
blocking and, if the importer holds it, returns an empty placeholder instead of touching the
database. Because the lock is re-entrant, the importing thread itself (which already holds it) still
reads real data while building the project view.
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from concurrent.futures import Future
from functools import wraps
from typing import Any, cast


class MainThreadExecutor:
    """Run callables on the imgui main thread and hand their result back to any thread.

    The GUI's ``project``/``catalog`` services wrap non-thread-safe SQLAlchemy sessions and per-frame
    caches, so a background caller (the embedded MCP server on its own thread) must not touch them
    directly. Instead it submits a callable here and blocks on the returned :class:`Future`; the main
    thread drains the queue once per frame via :meth:`drain` and fulfils each future. This serialises
    background access with the GUI's own per-frame reads and writes on a single thread.

    ``drain`` must be called from the main thread each frame; ``submit`` is safe from any thread.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Callable[[], Any], Future[Any]]] = queue.Queue()

    def submit[T](self, fn: Callable[[], T]) -> Future[T]:
        """Queue ``fn`` for execution on the main thread; return a future for its result."""
        future: Future[T] = Future()
        self._queue.put((fn, future))
        return future

    def run[T](self, fn: Callable[[], T], *, timeout: float = 30.0) -> T:
        """Submit ``fn`` and block until the main thread has produced its result (or it raises).

        On timeout the task is cancelled so it cannot still run on a later frame after the caller has
        given up — otherwise a "failed" mutation could silently take effect afterwards. Cancellation
        is best-effort: if the main thread has already started the task, it runs to completion."""
        future = self.submit(fn)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise

    def drain(self) -> None:
        """Execute all queued callables on the calling (main) thread. Call once per frame."""
        while True:
            try:
                fn, future = self._queue.get_nowait()
            except queue.Empty:
                return
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn())
                except BaseException as exc:
                    future.set_exception(exc)


def io_guarded[T](
    default_factory: Callable[[], object],
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Return the wrapped read's result, or ``default_factory()`` if a background import is running.

    The instance must expose a re-entrant ``self._io_lock`` (``threading.RLock``). ``default_factory``
    is typed loosely (``Callable[[], object]``) so the wrapped function's own return type is
    preserved rather than being erased to the factory's type (e.g. ``io_guarded(list)`` on a method
    returning ``list[Foo]`` keeps ``list[Foo]``). The empty placeholder must still be a valid stand-in
    at runtime."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> T:
            if not self._io_lock.acquire(blocking=False):
                return cast(T, default_factory())
            try:
                return fn(self, *args, **kwargs)
            finally:
                self._io_lock.release()

        return wrapper

    return decorator


def revision_cached[T](fn: Callable[[Any], T]) -> Callable[[Any], T]:
    """Cache a zero-arg service read by ``self.revision`` so a per-frame caller (a panel's ``render``)
    does not re-hit the database while nothing changed.

    In the immediate-mode GUI a panel's ``render()`` runs ~60x/s; an uncached read there is a 60 Hz
    SQLite query + ORM rebuild that pins the CPU and keeps the app off idling. Making caching the
    default for such reads removes that footgun: annotate the reader and it is memoised until the next
    edit. The instance must expose an ``int`` ``revision`` (bumped on every mutation/undo/redo) and a
    ``self._revision_cache`` dict that is cleared when the project closes/opens (so a cache built at
    revision 0 for one project is not reused for the next, which also starts at revision 0). The value
    is returned by shared reference; callers must treat it as read-only, as they already do for the
    other cached reads. Stack ``@io_guarded`` above this so a cache-miss rebuild stays import-safe."""
    key = fn.__name__

    @wraps(fn)
    def wrapper(self: Any) -> T:
        cache: dict[str, tuple[int, Any]] = self._revision_cache
        hit = cache.get(key)
        if hit is not None and hit[0] == self.revision:
            return cast(T, hit[1])
        value = fn(self)
        cache[key] = (self.revision, value)
        return value

    return wrapper


def revision_cached_by_args[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Like :func:`revision_cached` but for a read that takes hashable positional args (e.g. an id).

    A panel's per-row ``render`` often calls such a read once per row per frame (the group-objects
    table resolving each com-object's links, each link's group address). Caching per ``args`` by
    ``self.revision`` turns that back into a dict lookup; the whole per-method bucket is dropped when
    the revision moves, so a later edit is picked up. Same instance contract as ``revision_cached``
    (an ``int`` ``revision`` and a ``self._revision_cache`` dict); stack ``@io_guarded`` above it."""
    key = fn.__name__

    @wraps(fn)
    def wrapper(self: Any, *args: Any) -> T:
        cache: dict[str, tuple[int, dict[tuple[Any, ...], Any]]] = self._revision_cache
        hit = cache.get(key)
        if hit is None or hit[0] != self.revision:
            bucket: dict[tuple[Any, ...], Any] = {}
            cache[key] = (self.revision, bucket)
        else:
            bucket = hit[1]
        if args not in bucket:
            bucket[args] = fn(self, *args)
        return cast(T, bucket[args])

    return wrapper
