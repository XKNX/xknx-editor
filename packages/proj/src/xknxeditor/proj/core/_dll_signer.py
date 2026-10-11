"""Sign a ``.knxproj`` folder through the vendor ``Knx.Ets.XmlSigning`` assembly.

The reference implementation of this mechanism is OpenKNXproducer
(https://github.com/OpenKNX/OpenKNXproducer, ``Signing/XmlSigner.cs``):
load the assembly with ``Assembly.LoadFrom(<ets-path>\\Knx.Ets.XmlSigning.dll)`` and invoke the
static, non-public ``Knx.Ets.XmlSigning.XmlSigning.SignDirectory(path,
useCasingOfBaggagesXml, excludeFileEndings)``. This module does the same from Python via
``pythonnet`` (which loads the .NET Framework CLR). It is the primary folder-signing path on a
machine that has an ETS installation; elsewhere it is a no-op so the export falls back to the
offline signer (:mod:`knxproj_signing`), which reproduces the identical signature.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from xknxeditor.proj.core.knxproj_signing import verify_directory_signature

# Candidate install directories for the signing assembly, mirroring OpenKNXproducer's gPathETS.
ETS_ROOTS: tuple[str, ...] = (
    r"C:\Program Files (x86)\ETS6",
    r"C:\Program Files (x86)\ETS5",
    r"C:\Program Files\ETS6",
    r"C:\Program Files\ETS5",
)

_signer: Any = None


def _load_signer() -> Any | None:
    """Return the ``Knx.Ets.XmlSigning.XmlSigning`` type from the first usable ETS path."""
    if _signer is not None:
        return _signer
    if sys.platform != "win32":
        return None
    try:
        import pythonnet  # type: ignore[import-not-found]
    except ImportError:
        return None
    pythonnet.load("netfx")  # type: ignore[no-untyped-call]
    import clr  # type: ignore[import-not-found]

    for root in ETS_ROOTS:
        dll = Path(root) / "Knx.Ets.XmlSigning.dll"
        if not dll.is_file():
            continue
        try:
            clr.AddReference(str(dll))  # type: ignore[no-untyped-call]
            mod: Any = importlib.import_module("Knx.Ets.XmlSigning")
            cls: Any = getattr(mod, "XmlSigning", None)
            if getattr(cls, "SignDirectory", None) is not None:
                return cls
        except Exception:
            continue
    return None


def _folder_map(members: Mapping[str, bytes]) -> dict[str, dict[str, bytes]]:
    """Group a member map into ``{folder: {relpath: bytes}}`` (mirrors the export's layout)."""
    folders: dict[str, dict[str, bytes]] = {}
    for path, data in members.items():
        if "/" not in path:
            continue  # root-level file (knx_master.xml, <folder>.signature/.certificate)
        top, rel = path.split("/", 1)
        folders.setdefault(top, {})[rel] = data
    return folders


def _sign_folder(signer: Any, folder: str, files: dict[str, bytes]) -> bytes | None:
    """Write ``files`` to a scratch folder, run ``SignDirectory``, read the signature back."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / folder
        root.mkdir()
        for rel, data in files.items():
            dest = root.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        try:
            signer.SignDirectory(str(root), False, None)
        except Exception:
            return None
        # The assembly writes the signature as a sibling of the folder (or, in some builds,
        # inside it); accept whichever location it chose.
        for candidate in (
            root.parent / f"{folder}.signature",
            root / f"{folder}.signature",
        ):
            if candidate.is_file():
                return candidate.read_bytes()
    return None


def sign_member_map(members: MutableMapping[str, bytes]) -> None:
    """Re-sign every folder lacking a valid signature via the vendor assembly, in place.

    Folders whose existing signature already verifies are left untouched; folders the assembly
    does not cover (non-Windows, no ETS install, or the call fails) are also left untouched so
    the caller's offline signer still fills them in. RSA over a fixed digest is deterministic, so
    a covered folder is signed byte-identically to the offline path.
    """
    signer = _load_signer()
    if signer is None:
        return
    for folder, files in _folder_map(members).items():
        sig_key = f"{folder}.signature"
        existing = members.get(sig_key)
        if existing is not None and verify_directory_signature(files, existing):
            continue
        produced = _sign_folder(signer, folder, files)
        if produced is not None:
            members[sig_key] = produced
