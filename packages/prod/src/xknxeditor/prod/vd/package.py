"""Turn a ``.vd*`` product database into ``.knxprod`` bytes for the loader."""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

from .archive import read_dump
from .database import Database

if TYPE_CHECKING:
    from pathlib import Path


def vd_to_knxprod(source: str | Path | bytes, master_xml: bytes) -> bytes:
    """Convert a ``.vd1`` … ``.vd5`` file (path or bytes) into an in-memory ``.knxprod``.

    The database is brought to the newest schema, converted to the XML product data (catalog,
    hardware, application programs, translations, baggages) and signed with the content hashes the
    ids carry. ``master_xml`` is the ``knx_master.xml`` the conversion reads (manufacturers,
    languages, mask versions, datapoint types) and the package ships.
    """
    from .convert import convert
    from .signing import sign
    from .upgrade import upgrade

    db = Database.from_dump(read_dump(source))
    upgrade(db)
    files = sign(convert(db, master_xml))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("knx_master.xml", master_xml)
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()
