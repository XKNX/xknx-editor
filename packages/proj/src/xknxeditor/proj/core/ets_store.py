"""Write ETS internal project-store files ("FileProjectStore"), used by the dongle workaround.

A ``.knxproj`` is the interchange format (XML archive). ETS' local project store
(``%ProgramData%/KNX/ETS{5,6}/ProjectStore/P-XXXX/``) is a different, internal format: one XML stream
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
from enum import Enum
from pathlib import Path


class StoreFormat(Enum):
    """Which ETS generation's store layout to write.

    The two are *not* interchangeable. The integrity primitives are identical (same ``.ets5hash``
    recipe, same ``.store`` HMAC), but the object-model streams differ in shape and each ETS rejects
    the other's: ETS 5 discards every line of an ETS 6 topology (it wants ``MediumTypeRefId`` on the
    ``Line``, not on a ``Segment`` it knows nothing about), which drops all devices and leaves the
    project open-able but empty. The ``storeVersion`` marker is the declared discriminator; see
    ets_store_serialize for the per-format stream shapes.
    """

    ETS5 = "ets5"
    ETS6 = "ets6"

    @property
    def store_version(self) -> str:
        """``storeVersion`` content: schema line + tool-version line, both CRLF-terminated."""
        if self is StoreFormat.ETS5:
            return "20\r\n5.7.597.39777\r\n"
        return "23\r\n6.4.8718.0\r\n"

    @property
    def app_dir(self) -> str:
        """The ``%ProgramData%/KNX/<this>/ProjectStore`` folder name for this generation."""
        return "ETS5" if self is StoreFormat.ETS5 else "ETS6"


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
    """Write a store stream ``name`` plus its ``name.ets5hash`` sidecar into ``store_dir``.

    An empty stream (``data == b""``, e.g. a project with no unassigned devices or no traces)
    gets **no base file at all** -- only the ``.ets5hash`` sidecar. Verified against a genuine,
    licensed ETS 5.7.7 ProjectStore: every optional section a project never touched (``U``, ``PT``,
    and the legacy ``AI``/``BA``/``D``/``HE``/``IPSD``/``S``/``T``/``UF`` streams) has only its
    ``.ets5hash`` on disk, computed over zero bytes -- the section file itself is absent, not a
    present-but-empty file. Writing a present-but-empty (or non-empty-but-wrong-shape) file for an
    unused section does not match this and has been observed to make ETS treat the project as
    damaged. Cross-checked against the independent `etssign <https://github.com/daimoniac/etssign>`_
    C implementation, which confirms the same recipe against both ETS 5.7.7 and ETS 6.4.1.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    if data:
        (store_dir / name).write_bytes(data)
    else:
        (store_dir / name).unlink(missing_ok=True)
    (store_dir / f"{name}.ets5hash").write_text(ets5hash(data, mask), encoding="ascii")


def write_store_version(
    store_dir: Path, store_format: StoreFormat = StoreFormat.ETS6
) -> None:
    """Write the ``storeVersion`` marker for ``store_format`` (no hash sidecar)."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "storeVersion").write_text(
        store_format.store_version, encoding="utf-8", newline=""
    )


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
    """Return the ``{id}.store`` value binding a store folder to this ETS installation.

    Mirrors ``InstallationGuidWriter.CreateHmacForStore``: base64 of
    ``HMAC-SHA256(key=UTF16LE(installer_guid), msg=UTF16LE("{id}:{id}"))``. ETS verifies this on
    open, so it must use the GUID from :func:`read_installer_guid` on the target machine.

    Despite the name, this is not project-specific: a genuine ProjectStore has one of these per
    folder that needs one, keyed by that folder's own id -- the project folder (``{pid}.store``,
    ``id=pid``) *and* every manufacturer folder inside it (``M-XXXX/M-XXXX.store``, ``id="M-XXXX"``).
    Verified byte-for-byte against a real ETS 5.7.7 ProjectStore for both: a manufacturer folder
    without its own ``.store`` is apparently treated as missing/untrusted by ETS even though every
    file in it and its ``.ets5hash`` sidecars are otherwise correct (:func:`write_project_store`
    writes one per folder in ``product_members``).
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
