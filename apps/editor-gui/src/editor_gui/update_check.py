"""Check GitHub for a newer XKNX Editor release.

The desktop app is published as GitHub releases tagged ``xknx-editor-v<version>``. On startup
(unless the user disabled it) we ask GitHub's REST API for the releases, pick the highest
``xknx-editor-v*`` version and, if it is newer than the running one, surface a prompt the user can
act on or skip. Network access is best-effort: the startup check ignores a failure (the app must never
depend on reaching GitHub).
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, cast

_RELEASES_API = "https://api.github.com/repos/xknx/xknx-editor/releases"
_TAG_PREFIX = "xknx-editor-v"
_RELEASE_PAGE = "https://github.com/xknx/xknx-editor/releases/tag/{tag}"


@dataclass(frozen=True)
class ReleaseAsset:
    """A downloadable file of a release."""

    name: str  # e.g. "XKNX-Editor-windows-installer-x64.exe"
    url: str  # its browser_download_url
    sha256: str | None = None  # hex digest GitHub publishes for the asset, if any
    size: int = 0


@dataclass(frozen=True)
class UpdateInfo:
    """A release newer than the running app."""

    version: str  # e.g. "0.2.0"
    tag: str  # e.g. "xknx-editor-v0.2.0"
    url: str  # release page to open in the browser
    notes: str = ""  # the release's notes/body (markdown text), if any
    assets: tuple[ReleaseAsset, ...] = ()


def _assets(release: dict[str, Any]) -> tuple[ReleaseAsset, ...]:
    """The release's assets with a name and a download URL; ``digest`` is ``"sha256:<hex>"``."""
    raw = release.get("assets")
    if not isinstance(raw, list):
        return ()
    assets: list[ReleaseAsset] = []
    for item in cast("list[Any]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        name, url = entry.get("name"), entry.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        digest = entry.get("digest")
        sha256 = (
            digest.removeprefix("sha256:")
            if isinstance(digest, str) and digest.startswith("sha256:")
            else None
        )
        size = entry.get("size")
        assets.append(
            ReleaseAsset(name, url, sha256, size if isinstance(size, int) else 0)
        )
    return tuple(assets)


def _parse_version(text: str) -> tuple[int, ...] | None:
    """Parse a dotted numeric version ("1.2.3") into a comparable tuple, padded to 3 parts, or
    ``None`` if it is not purely numeric (e.g. a release candidate suffix)."""
    text = text.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", text):
        return None
    parts = [int(p) for p in text.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def pick_newer(current: str, releases: list[dict[str, Any]]) -> UpdateInfo | None:
    """Return the newest ``xknx-editor-v*`` release strictly newer than ``current`` (a dotted
    version like "0.1.0"), or ``None``. Pure — unit-testable without network. Skips drafts,
    prereleases and non-app tags (e.g. library releases like ``xknxeditor-namespaces-v*``)."""
    cur = _parse_version(current)
    if cur is None:
        return None
    best: tuple[tuple[int, ...], str, str, tuple[ReleaseAsset, ...]] | None = None
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith(_TAG_PREFIX):
            continue
        ver = _parse_version(tag[len(_TAG_PREFIX) :])
        if ver is None:
            continue
        if best is None or ver > best[0]:
            body = rel.get("body")
            best = (ver, tag, body if isinstance(body, str) else "", _assets(rel))
    if best is None or best[0] <= cur:
        return None
    ver_tuple, tag, notes, assets = best
    version = ".".join(str(p) for p in ver_tuple)
    return UpdateInfo(
        version=version,
        tag=tag,
        url=_RELEASE_PAGE.format(tag=tag),
        notes=notes.strip(),
        assets=assets,
    )


class UpdateCheckError(Exception):
    """GitHub could not be asked for the releases, or its answer was not a release list."""


def fetch_update(current: str, *, timeout: float = 10.0) -> UpdateInfo | None:
    """Query GitHub and return an :class:`UpdateInfo` if a newer release exists, ``None`` when
    ``current`` is the newest. Raises :class:`UpdateCheckError` when the check fails. Call off the
    UI thread."""
    try:
        req = urllib.request.Request(
            _RELEASES_API,
            headers={
                "User-Agent": "xknx-editor",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except Exception as e:
        raise UpdateCheckError(str(e)) from e
    if not isinstance(payload, list):
        raise UpdateCheckError("unexpected answer from GitHub")
    typed = cast("list[Any]", payload)
    releases: list[dict[str, Any]] = [r for r in typed if isinstance(r, dict)]
    return pick_newer(current, releases)
