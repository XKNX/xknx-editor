"""The global KNX ``MasterData`` (mask-version load/unload procedures, manufacturers, DPTs).

A download resolves an application's Load/Unload procedure against the mask version's
default in the master data (see :mod:`xknxeditor.download.merge`).

The master data is proprietary KNX content, so it is NOT bundled with the app. We fetch the
signed copy from the official KNX update server (the same file ETS downloads, and the same one
shipped inside every ``.knxprod``) on first use and cache it per-user under
:func:`editor_gui.settings.config_dir`. Subsequent launches read the cache (offline-capable);
a first launch with no network and no cache simply leaves the master data unavailable, and the
callers degrade with a clear message rather than crashing.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from editor_gui.settings import config_dir
from xknxeditor.prod import MasterData, parse_master_xml
from xknxeditor.proj import fetch_master_xml, master_cache_path

# The project/23 master, matching the XML namespace used across the app (see online_catalog).
_SCHEMA = "23"
# Re-fetch the cached master once it is older than this; a failed refresh keeps the old cache.
_MAX_CACHE_AGE_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class MasterDataInfo:
    """Which master data is in use, for the status bar."""

    version: int
    date: str
    source: str  # "cached" | "fetched"


def _cache_path() -> Path:
    # Shared with the export, which refreshes it with every downloaded master.
    return master_cache_path(config_dir(), _SCHEMA)


def _is_stale(cache: Path) -> bool:
    """True when the cache is older than the max age (or its age cannot be read)."""
    try:
        return time.time() - cache.stat().st_mtime > _MAX_CACHE_AGE_SECONDS
    except OSError:
        return True


def _download_and_cache(cache: Path) -> bytes:
    """Fetch the signed master for this schema and refresh the cache (best effort)."""
    data = fetch_master_xml(
        schema=_SCHEMA
    )  # signed + namespace-verified; raises on failure
    # a read-only cache dir just means we re-fetch next time
    with contextlib.suppress(OSError):
        cache.write_bytes(data)
    return data


def _load_bytes() -> tuple[bytes, str]:
    """Return the master XML bytes and their source ("cached"/"fetched").

    Prefers a fresh per-user cache. A cache older than ``_MAX_CACHE_AGE_SECONDS`` is refreshed from
    the signed master; if that download fails the existing (stale) cache is used. With no cache at
    all, downloads and caches it. Raises ``OSError``/``ValueError`` when no cache exists and the
    download fails.
    """
    cache = _cache_path()
    if cache.exists():
        if not _is_stale(cache):
            return cache.read_bytes(), "cached"
        try:
            return _download_and_cache(cache), "fetched"
        except (OSError, ValueError):
            return (
                cache.read_bytes(),
                "cached",
            )  # keep the stale copy until a refresh succeeds
    return _download_and_cache(cache), "fetched"


def load_master() -> tuple[MasterData, MasterDataInfo]:
    """Load the global master data (cached or freshly fetched) and report its version and date."""
    data, source = _load_bytes()
    # Through the parse cache next to the catalog's: every start reads the same master XML.
    master = parse_master_xml(data, config_dir() / "ir_cache")
    version = master.raw.version if master.raw is not None else 0
    cache = _cache_path()
    try:
        stamp = (
            _date.fromtimestamp(cache.stat().st_mtime).isoformat()
            if cache.exists()
            else ""
        )
    except OSError:
        stamp = ""
    return master, MasterDataInfo(version=version, date=stamp, source=source)


def master_xml_bytes() -> bytes | None:
    """Raw master XML bytes (cached or fetched) for wrapping OpenKNX/monolithic product XML
    (which carries no ``<MasterData>``) into an importable ``.knxprod``.

    Returns ``None`` when the master data is unavailable (offline and never cached) so the caller
    can surface a clear error instead of failing deep in the import.
    """
    try:
        data, _ = _load_bytes()
        return data
    except (OSError, ValueError):
        return None
