"""Loading product data without a ``Catalog.xml`` (as embedded in a ``.knxproj``).

A genuine ``.knxprod`` carries a ``Catalog.xml`` per manufacturer, but the product data ETS embeds
inside a ``.knxproj`` has only ``Hardware.xml`` + the used application XMLs. ``load`` must still
ingest that (hardware + applications) when ``require_catalog=False`` so the project's devices
resolve; strict loading keeps requiring ``Catalog.xml`` (issue #52).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from xknxeditor.prod import load
from xknxeditor.prod.errors import ArchiveError

_FIXTURE = Path(__file__).parent / "fixtures" / "gira_2gang_button_interface.knxprod"


def _without_catalog(path: Path) -> bytes:
    """The fixture archive re-zipped without any ``*/Catalog.xml`` (the ETS-``.knxproj`` shape)."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(path) as src,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out,
    ):
        for name in src.namelist():
            if name.endswith("/Catalog.xml"):
                continue
            out.writestr(name, src.read(name))
    return buffer.getvalue()


def test_strict_load_requires_catalog() -> None:
    with pytest.raises(ArchiveError, match=r"Catalog\.xml"):
        load(_without_catalog(_FIXTURE))


def test_load_without_catalog_still_ingests_hardware_and_applications() -> None:
    full = load(_FIXTURE.read_bytes())
    lenient = load(_without_catalog(_FIXTURE), require_catalog=False)

    # Hardware and applications ingest exactly as with the catalog present...
    assert lenient.applications.keys() == full.applications.keys()
    assert lenient.hardware.keys() == full.hardware.keys()
    assert lenient.programs.keys() == full.programs.keys()
    assert lenient.applications  # non-empty: the device view can resolve

    # ...only the catalog browse index is absent for the manufacturer without a Catalog.xml.
    assert not lenient.catalog_items
    assert not lenient.manufacturer_to_section
