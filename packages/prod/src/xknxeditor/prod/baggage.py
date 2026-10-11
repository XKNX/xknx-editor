"""Context help and icons an application ships as baggage (``ContextHelpFile``/``IconFile``)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path

from .archive import Archive

_HELP_EXTENSIONS = ("", ".txt", ".md")
_ICON_EXTENSIONS = ("", ".png", ".svg", ".jpg", ".jpeg", ".bmp", ".ico")


class _Zip:
    def __init__(self, data: bytes) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))

    @cached_property
    def names(self) -> dict[str, str]:
        return {name.casefold(): name for name in self._zip.namelist()}

    def find(self, name: str, extensions: tuple[str, ...]) -> bytes | None:
        for ext in extensions:
            entry = self.names.get(f"{name}{ext}".casefold())
            if entry is not None:
                return self._zip.read(entry)
        return None


class Baggages:
    """Baggage files of one manufacturer, keyed by Baggage id."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        self._files = dict(files)
        self._zips: dict[str, _Zip | None] = {}

    @classmethod
    def from_archive(cls, path: str | Path, manufacturer_id: str) -> Baggages:
        # Baggages (help/icons/pictures) do not need Catalog.xml, so tolerate archives that omit it
        # (product data embedded in a .knxproj - see Archive require_catalog).
        with Archive(Path(path), require_catalog=False) as archive:
            return cls(archive.get_baggages(manufacturer_id))

    def file(self, baggage_id: str) -> bytes | None:
        return self._files.get(baggage_id)

    def _zip(self, baggage_id: str | None) -> _Zip | None:
        if not baggage_id:
            return None
        if baggage_id not in self._zips:
            data = self._files.get(baggage_id)
            try:
                self._zips[baggage_id] = _Zip(data) if data is not None else None
            except zipfile.BadZipFile:
                self._zips[baggage_id] = None
        return self._zips[baggage_id]

    def help_text(self, help_file: str | None, context: str) -> str | None:
        """The help page for a ``HelpContext`` from the ``ContextHelpFile`` zip."""
        archive = self._zip(help_file)
        data = archive.find(context, _HELP_EXTENSIONS) if archive else None
        return data.decode("utf-8-sig", errors="replace") if data is not None else None

    def icon(self, icon_file: str | None, name: str) -> bytes | None:
        """An image named by an ``Icon`` attribute from the ``IconFile`` zip."""
        archive = self._zip(icon_file)
        return archive.find(name, _ICON_EXTENSIONS) if archive else None
