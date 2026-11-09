from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from xknxeditor.prod.errors import ArchiveError

if TYPE_CHECKING:
    from collections.abc import Iterator

MANUFACTURER_PATTERN = re.compile(r"^M-[0-9A-Fa-f]{4}$")
APPLICATION_PATTERN = re.compile(r"^M-[0-9A-Fa-f]{4}_A-.+\.xml$")


class Archive:
    """Reads .knxprod archives holding KNX device definitions."""

    __slots__ = (
        "_application_paths",
        "_entries",
        "_manufacturer_ids",
        "_require_catalog",
        "_source_holder",
        "_zipfile",
    )

    _zipfile: zipfile.ZipFile
    _source_holder: io.BytesIO | None
    _entries: set[str]  # every path in the archive
    _manufacturer_ids: set[str]
    _require_catalog: bool
    # manufacturer id -> app id -> path of its application XML, filled when first asked for
    _application_paths: dict[str, dict[str, str]]

    def __init__(
        self, source: str | Path | bytes, *, require_catalog: bool = True
    ) -> None:
        """Open an archive from a path or raw bytes.

        ``require_catalog`` enforces a ``Catalog.xml`` per manufacturer (true for a genuine
        ``.knxprod``). Product data embedded in a ``.knxproj`` omits ``Catalog.xml`` (only
        ``Hardware.xml`` + the used application XMLs), so pass ``False`` to ingest that.
        """
        self._source_holder = None
        self._require_catalog = require_catalog
        self._application_paths = {}

        if isinstance(source, bytes):
            self._source_holder = io.BytesIO(source)
            try:
                self._zipfile = zipfile.ZipFile(self._source_holder, "r")
            except zipfile.BadZipFile as e:
                raise ArchiveError(f"Invalid ZIP archive: {e}") from e
        else:
            path = Path(source)
            if not path.exists():
                raise ArchiveError(f"File not found: {path}")
            try:
                self._zipfile = zipfile.ZipFile(path, "r")
            except zipfile.BadZipFile as e:
                raise ArchiveError(f"Invalid ZIP archive: {e}") from e

        self._entries = set(self._zipfile.namelist())
        self._manufacturer_ids = self._find_manufacturer_ids()
        self._validate_structure()

    def _find_manufacturer_ids(self) -> set[str]:
        root_dirs = {entry.split("/")[0] for entry in self._entries if "/" in entry}
        manufacturer_ids = {d for d in root_dirs if MANUFACTURER_PATTERN.match(d)}

        if not manufacturer_ids:
            raise ArchiveError(
                "No manufacturer directory found (expected M-XXXX pattern)"
            )

        return manufacturer_ids

    def _validate_manufacturer_id(self, manufacturer_id: str) -> None:
        if manufacturer_id not in self._manufacturer_ids:
            raise ArchiveError(f"Unknown manufacturer ID: {manufacturer_id}")

    def _validate_structure(self) -> None:
        if "knx_master.xml" not in self._entries:
            raise ArchiveError("Missing required file: knx_master.xml")

        for manufacturer_id in self._manufacturer_ids:
            # A manufacturer may contribute only application programs or baggages that the
            # hardware of another manufacturer in the archive uses: no Hardware.xml, no catalog.
            if not self.has_hardware(manufacturer_id):
                continue
            if self._require_catalog:
                catalog_path = f"{manufacturer_id}/Catalog.xml"
                if catalog_path not in self._entries:
                    raise ArchiveError(f"Missing required file: {catalog_path}")

    def has_hardware(self, manufacturer_id: str) -> bool:
        """Whether this manufacturer ships a ``Hardware.xml`` (one with only application programs
        does not)."""
        self._validate_manufacturer_id(manufacturer_id)
        return f"{manufacturer_id}/Hardware.xml" in self._entries

    def has_catalog(self, manufacturer_id: str) -> bool:
        """Whether this manufacturer ships a ``Catalog.xml`` (project-embedded data omits it)."""
        self._validate_manufacturer_id(manufacturer_id)
        return f"{manufacturer_id}/Catalog.xml" in self._entries

    @property
    def manufacturer_ids(self) -> set[str]:
        """All manufacturer IDs in the archive."""
        return self._manufacturer_ids

    def get_master_xml(self) -> bytes:
        """knx_master.xml bytes."""
        return self._zipfile.read("knx_master.xml")

    def get_catalog_xml(self, manufacturer_id: str) -> bytes:
        """Catalog.xml bytes for a manufacturer."""
        self._validate_manufacturer_id(manufacturer_id)
        return self._zipfile.read(f"{manufacturer_id}/Catalog.xml")

    def get_hardware_xml(self, manufacturer_id: str) -> bytes:
        """Hardware.xml bytes for a manufacturer."""
        self._validate_manufacturer_id(manufacturer_id)
        return self._zipfile.read(f"{manufacturer_id}/Hardware.xml")

    def _application_entries(self, manufacturer_id: str) -> dict[str, str]:
        """The archive paths of a manufacturer's application XMLs, keyed by app ID, in the order
        of their IDs."""
        self._validate_manufacturer_id(manufacturer_id)
        known = self._application_paths.get(manufacturer_id)
        if known is not None:
            return known
        result: dict[str, str] = {}
        prefix = f"{manufacturer_id}/"

        for entry in sorted(self._entries):
            if not entry.startswith(prefix):
                continue
            filename = entry[len(prefix) :]
            if APPLICATION_PATTERN.match(filename):
                result[filename.removesuffix(".xml")] = entry

        self._application_paths[manufacturer_id] = result
        return result

    def application_ids(self, manufacturer_id: str) -> list[str]:
        """The IDs of a manufacturer's application XMLs, sorted, without reading them."""
        return list(self._application_entries(manufacturer_id))

    def application_xml_size(self, manufacturer_id: str, app_id: str) -> int:
        """The size of an application XML (as listed by :meth:`application_ids`) in bytes,
        without reading it."""
        entry = self._application_entries(manufacturer_id)[app_id]
        return self._zipfile.getinfo(entry).file_size

    def read_application_xml(self, manufacturer_id: str, app_id: str) -> bytes:
        """The bytes of one application XML listed by :meth:`application_ids`."""
        entry = self._application_entries(manufacturer_id).get(app_id)
        if entry is None:
            raise ArchiveError(f"No application XML {app_id} of {manufacturer_id}")
        return self._zipfile.read(entry)

    def get_application_xmls(self, manufacturer_id: str) -> dict[str, bytes]:
        """App XMLs for a manufacturer, keyed by app ID.

        Holds all of them in memory at once: to go through them one by one, read each with
        :meth:`read_application_xml` instead."""
        return {
            app_id: self._zipfile.read(entry)
            for app_id, entry in self._application_entries(manufacturer_id).items()
        }

    def get_application_xml(self, manufacturer_id: str, app_id: str) -> bytes | None:
        """The XML of one application, or None if the archive has no such application.

        Reads only that entry, unlike :meth:`get_application_xmls`.
        """
        self._validate_manufacturer_id(manufacturer_id)
        filename = f"{app_id}.xml"
        if not APPLICATION_PATTERN.match(filename) or "/" in app_id:
            return None
        entry = f"{manufacturer_id}/{filename}"
        if entry not in self._entries:
            return None
        return self._zipfile.read(entry)

    def get_baggages(self, manufacturer_id: str) -> dict[str, bytes]:
        """Baggage file contents for a manufacturer, keyed by Baggage id."""
        self._validate_manufacturer_id(manufacturer_id)
        index = f"{manufacturer_id}/Baggages.xml"
        if index not in self._entries:
            return {}
        root = ET.fromstring(self._zipfile.read(index))
        result: dict[str, bytes] = {}
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] != "Baggage":
                continue
            baggage_id = element.get("Id")
            name = element.get("Name")
            if not baggage_id or not name:
                continue
            target = (element.get("TargetPath") or "").replace("\\", "/").strip("/")
            path = "/".join(
                part for part in (manufacturer_id, "Baggages", target, name) if part
            )
            if path in self._entries:
                result[baggage_id] = self._zipfile.read(path)
        return result

    def get_signature(self, manufacturer_id: str) -> bytes | None:
        """Signature bytes for a manufacturer, or None if absent."""
        self._validate_manufacturer_id(manufacturer_id)
        sig_path = f"{manufacturer_id}.signature"
        if sig_path in self._entries:
            return self._zipfile.read(sig_path)
        return None

    def list_entries(self) -> list[str]:
        """All paths in the archive."""
        return list(self._entries)

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the archive, freeing resources."""
        self._zipfile.close()
        if self._source_holder is not None:
            self._source_holder.close()

    def __iter__(self) -> Iterator[str]:
        return iter(self.list_entries())
