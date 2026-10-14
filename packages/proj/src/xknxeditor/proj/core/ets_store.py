"""Write ETS6 internal project-store files ("FileProjectStore"), used by the dongle workaround.

A ``.knxproj`` is the interchange format (XML archive). ETS' local project store
(``%ProgramData%/KNX/ETS6/ProjectStore/P-XXXX/``) is a different, internal format: one XML stream
per object-model section (``P`` project, ``B`` buildings, ``I`` installation, ``G`` group addresses,
``PT`` traces, ``P-XXXX-0_A-N`` application data, …), each guarded by a ``<name>.ets5hash`` sidecar,
plus ``storeVersion`` and a ``P-XXXX.store`` descriptor.

Reverse-engineered from ``Knx.Ets.ObjectModel`` ``HashedFileStream``/``FileProjectStore`` and verified
against a genuine store:

* ``.ets5hash`` is reproducible without any secret -- see :func:`ets5hash`. ETS verifies it on load.
* ``P-XXXX.store`` is an HMAC keyed by the ETS *installation* GUID (``%ProgramData%/KNX/ETS6/Installer/
  installer.guid``). ETS verifies it on open ("The Project was not imported on this computer" if wrong),
  so it must be written with the local installer GUID -- see :func:`store_descriptor`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from pathlib import Path

#: ``storeVersion`` content: schema line + tool-version line (ETS 6.4.x).
STORE_VERSION = "23\n6.4.8718.0"


def project_mask(project_guid: str, project_id: str) -> int:
    """Return the per-project ushort mask that keys the ``.ets5hash`` transform.

    Mirrors ``FileProjectStore.GetProjectMaskFromGuid``: the sum of the project GUID's bytes (in
    .NET ``Guid.ToByteArray()`` order) plus the UTF-8 bytes of the project id string, mod 2**16.
    """
    guid_bytes = uuid.UUID(project_guid).bytes_le  # .NET Guid.ToByteArray() byte order
    return (sum(guid_bytes) + sum(project_id.encode("utf-8"))) & 0xFFFF


def ets5hash(data: bytes, mask: int) -> str:
    """Return the base64 ``.ets5hash`` sidecar value for ``data`` under ``mask``.

    Mirrors ``HashedFileStream.NyFdzJ13kUg``: ``SHA1(data)``, add 41 to each byte (mod 256), then XOR
    with the two mask bytes ``[mask >> 8, mask & 0xFF]`` cyclically. No secret involved.
    """
    digest = hashlib.sha1(data).digest()
    mask_bytes = (mask >> 8) & 0xFF, mask & 0xFF
    out = bytes(((b + 41) & 0xFF) ^ mask_bytes[i % 2] for i, b in enumerate(digest))
    return base64.b64encode(out).decode("ascii")


def write_store_stream(store_dir: Path, name: str, data: bytes, mask: int) -> None:
    """Write a store stream ``name`` plus its ``name.ets5hash`` sidecar into ``store_dir``."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / name).write_bytes(data)
    (store_dir / f"{name}.ets5hash").write_text(ets5hash(data, mask), encoding="ascii")


def write_store_version(store_dir: Path) -> None:
    """Write the ``storeVersion`` marker (no hash sidecar)."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "storeVersion").write_text(STORE_VERSION, encoding="utf-8")


def installer_guid_path() -> Path:
    """Path to the local ETS 6 installer GUID file (``%ProgramData%/KNX/ETS6/Installer/installer.guid``)."""
    base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / "KNX" / "ETS6" / "Installer" / "installer.guid"


def read_installer_guid() -> str | None:
    """Return the local ETS installer GUID (BOM-stripped), or ``None`` if ETS is not installed here."""
    path = installer_guid_path()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8-sig").strip()


def store_descriptor(project_id: str, installer_guid: str) -> str:
    """Return the ``P-XXXX.store`` value binding the project to this ETS installation.

    Mirrors ``InstallationGuidWriter.CreateHmacForStore``: base64 of
    ``HMAC-SHA256(key=UTF16LE(installer_guid), msg=UTF16LE("{id}:{id}"))``. ETS verifies this on
    open, so it must use the GUID from :func:`read_installer_guid` on the target machine.
    """
    key = installer_guid.encode("utf-16-le")
    msg = f"{project_id}:{project_id}".encode("utf-16-le")
    return base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode("ascii")


def write_store_descriptor(
    store_dir: Path, project_id: str, installer_guid: str
) -> None:
    """Write the ``{project_id}.store`` descriptor file (no hash sidecar)."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / f"{project_id}.store").write_text(
        store_descriptor(project_id, installer_guid), encoding="utf-8"
    )
