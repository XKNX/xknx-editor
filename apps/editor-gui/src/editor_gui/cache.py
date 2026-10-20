"""The app's cache folder: parsed product data and documentation fetched for the AI tools.

Everything in it is made again when needed (product data is parsed again from the catalog's
product files, documentation downloaded again), so it can be cleared at any time; afterwards only
the first use of each product is slower. Not for anything that has to survive, like the downloaded
KNX master data an export needs offline (that stays in :func:`editor_gui.settings.config_dir`)."""

from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

import platformdirs

_APP_NAME = "xknx-editor"


def cache_dir() -> Path:
    """The per-user cache folder of the OS (platformdirs)."""
    return Path(platformdirs.user_cache_dir(_APP_NAME))


def product_cache_dir() -> Path:
    """Parsed application programs and master data, by the content they were parsed from."""
    return cache_dir() / "product-data"


def docs_cache_dir() -> Path:
    """Documentation (device manuals) the AI tools fetched."""
    return cache_dir() / "docs"


def move_product_cache(old: Path) -> None:
    """Move the product data cache from ``old`` (its former place in the data folder) into the
    cache folder, once, so nothing has to be parsed again. Best effort: a cache."""
    if not old.is_dir():
        return
    new = product_cache_dir()
    try:
        if not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(old, new)
            return
        for entry in old.iterdir():
            if not (new / entry.name).exists():
                shutil.move(entry, new / entry.name)
        shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass


def cache_size() -> int:
    """The bytes the files in the cache folder take."""
    total = 0
    for root, _dirs, files in os.walk(cache_dir()):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).stat().st_size
    return total


def clear_cache() -> int:
    """Delete the files in the cache folder; returns the bytes freed. A file in use (being written
    right now) stays and is deleted the next time."""
    freed = 0
    for root, _dirs, files in os.walk(cache_dir()):
        for name in files:
            path = Path(root) / name
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            freed += size
    return freed
