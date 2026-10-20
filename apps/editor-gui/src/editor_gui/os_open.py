"""Open a local file or a web address in the OS default handler (exported HTML label sheets,
help pages). In the browser UI both open in the user's browser instead."""

from __future__ import annotations

import webbrowser
from pathlib import Path


def open_path(path: str) -> bool:
    """Open ``path`` in the OS default application. Returns whether the request was dispatched.

    Uses :func:`webbrowser.open` on the ``file://`` URI, which is cross-platform stdlib and the right
    handler for HTML output. Best-effort: never raises."""
    from editor_gui import web

    if web.active_hub is not None:
        web.active_hub.offer_file(Path(path), show=True)
        return True
    try:
        return webbrowser.open(Path(path).resolve().as_uri())
    except (OSError, ValueError):
        return False


def open_url(url: str) -> bool:
    """Open a web address in the default browser. Returns whether the request was dispatched."""
    from editor_gui import web

    if web.active_hub is not None:
        web.active_hub.open_url(url)
        return True
    return webbrowser.open(url)
