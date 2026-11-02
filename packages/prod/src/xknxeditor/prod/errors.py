from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading


class ArchiveError(Exception):
    """Invalid or unreadable knxprod archive."""


class PasswordRequired(ArchiveError):
    """A product database is encrypted and no password was available to read it."""


class VersionError(Exception):
    """KNX version undetectable or unsupported."""


class ParseError(Exception):
    """XML parsing failed."""


class EncodingError(Exception):
    """A parameter value could not be encoded into its memory/property image.

    Raised instead of silently skipping the write: a value that fails to encode against
    its own declared type is a clear sign of corrupt data (a hand-edited project, a UI
    bug bypassing widget validation, or a calculation producing a bad value), and a
    partial image must never be programmed onto a device.
    """


class ImportCancelled(Exception):
    """An import was cancelled before it stored anything."""


def check_cancelled(cancel: threading.Event | None) -> None:
    """Raise :class:`ImportCancelled` once ``cancel`` is set; long import steps call it between
    their parts."""
    if cancel is not None and cancel.is_set():
        raise ImportCancelled
