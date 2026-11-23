"""Pausing the cyclic garbage collector while many long-lived objects are created, and leaving
them out of the later collections.

A parsed application program or the devices of a project are millions of objects that live
long. Each collection of the oldest generation looks at all of them again: with a few programs
in memory that takes seconds, in whichever thread happens to allocate then (the user interface,
mostly). They hold no reference cycles, so they need no collection at all: freed when they are
no longer used, as any object without cycles is."""

from __future__ import annotations

import gc
import threading
from collections.abc import Generator
from contextlib import contextmanager

_lock = threading.Lock()
_depth = 0
_was_enabled = False
# Whether the objects alive when a pause for long-lived objects ends are left out of the later
# collections (see keep_long_lived).
_keep = [False]


def keep_long_lived(enabled: bool) -> None:
    """With ``enabled``, the objects alive when a pause for long-lived objects ends (see
    :func:`gc_paused`) are left out of the later collections (``gc.freeze``) until
    :func:`release_long_lived`. They are still freed when no longer used, unless they are in a
    reference cycle: such a cycle stays until they are released."""
    _keep[0] = enabled


def release_long_lived() -> None:
    """Have the collections look at the objects left out of them again (``gc.unfreeze``), e.g.
    when a project closes: a cycle among them is collected then."""
    gc.unfreeze()


@contextmanager
def gc_paused(*, long_lived: bool = False) -> Generator[None]:
    """Pause the cyclic garbage collector for the duration of the block.

    Nested and concurrent pauses, also from other threads, share one pause: the collector is
    disabled when the first starts and its previous state is restored when the last ends.

    ``long_lived``: the block creates objects that live long (see :func:`keep_long_lived`)."""
    global _depth, _was_enabled
    with _lock:
        if _depth == 0:
            _was_enabled = gc.isenabled()
            gc.disable()
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if long_lived and _keep[0]:
                # Before the collector runs again: none of its collections looks at them.
                gc.freeze()
            if _depth == 0 and _was_enabled:
                gc.enable()
