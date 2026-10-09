"""Pausing the cyclic garbage collector while many long-lived objects are created."""

from __future__ import annotations

import gc
import threading
from collections.abc import Generator
from contextlib import contextmanager

_lock = threading.Lock()
_depth = 0
_was_enabled = False


@contextmanager
def gc_paused() -> Generator[None]:
    """Pause the cyclic garbage collector for the duration of the block.

    Nested and concurrent pauses, also from other threads, share one pause: the collector is
    disabled when the first starts and its previous state is restored when the last ends."""
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
            if _depth == 0 and _was_enabled:
                gc.enable()
