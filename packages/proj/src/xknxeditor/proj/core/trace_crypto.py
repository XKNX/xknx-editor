"""Decrypt/encrypt project-log comments (``ProjectInformation/ProjectTraces``).

The KNX authoring tool encrypts each ``ProjectTrace/@Comment`` as ``MARKER + base64(AES-256-CBC(text))``,
where the AES key/IV and the plaintext marker prefix are constants baked into the tool's assemblies
(identical everywhere, not project- or user-specific). By design this source ships **no** key
material: the key/IV/marker are extracted at runtime from the user's own ``Knx.Ets.Common.dll`` (see
:func:`xknxeditor.proj.extract_trace_key`) and injected via :func:`set_trace_key`. Until a key is
set, :func:`decrypt_comment` returns ``None`` and comments stay opaque (as stored).

The crypto is pure Python (``cryptography``): AES-256-CBC with PKCS#7 padding. The marker is treated
as opaque text — we never assume its exact characters.
"""

from __future__ import annotations

import base64
import re

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

#: Fixed probe plaintext the extractor encrypts to recover the marker prefix (shared with _extract).
TRACE_SENTINEL = "xknx-trace-probe"

#: AES block size in bits, for PKCS#7 padding.
_BLOCK_BITS = 128

#: The base64 an encrypted comment ends in: at least one AES block (16 bytes, 24 characters).
_BASE64_TAIL = re.compile(r"[A-Za-z0-9+/]{22,}={0,2}$")

_key: bytes | None = None
_iv: bytes | None = None
_marker: str | None = None


def set_trace_key(key: bytes, iv: bytes, marker: str) -> None:
    """Install the trace key/IV and plaintext marker prefix (e.g. extracted from the tool's DLL)."""
    if len(key) != 32 or len(iv) != 16:
        raise ValueError("trace key must be 32 bytes and iv 16 bytes")
    global _key, _iv, _marker
    _key, _iv, _marker = key, iv, marker


def reset_trace_key() -> None:
    """Forget the installed key so comments are treated as opaque again."""
    global _key, _iv, _marker
    _key = _iv = _marker = None


def trace_key_available() -> bool:
    """Whether a key/IV/marker is installed and comments can be decrypted."""
    return _key is not None and _iv is not None and _marker is not None


def current_marker() -> str | None:
    """The installed plaintext marker prefix, or ``None`` if no key is set."""
    return _marker


def is_encrypted(comment: str) -> bool:
    """Whether ``comment`` looks like an encrypted comment (needs an installed marker)."""
    return _marker is not None and comment.startswith(_marker)


def looks_encrypted(comment: str) -> bool:
    """Whether ``comment`` has the stored form of an encrypted comment, also without a key: some
    marker text followed by the base64 of whole AES blocks. With a key installed, its marker
    decides (see :func:`is_encrypted`)."""
    if _marker is not None:
        return is_encrypted(comment)
    tail = _BASE64_TAIL.search(comment)
    if tail is None or tail.start() == 0:
        return False
    # The marker may end in characters base64 also uses: try the tail from each later start.
    for start in range(tail.start(), len(comment) - 15):
        if (len(comment) - start) % 4:
            continue
        try:
            data = base64.b64decode(comment[start:], validate=True)
        except ValueError:
            continue
        if data and len(data) % 16 == 0:
            return True
    return False


def decrypt_comment(comment: str) -> str | None:
    """Return the plaintext of an encrypted ``comment``, or ``None`` if it cannot be decrypted.

    ``None`` covers: no key installed, ``comment`` not marker-prefixed, or any crypto/decoding error
    (so a stray non-conforming value never raises into the UI).
    """
    if _key is None or _iv is None or _marker is None:
        return None
    if not comment.startswith(_marker):
        return None
    try:
        ciphertext = base64.b64decode(comment[len(_marker) :], validate=True)
        return _decrypt(_key, _iv, ciphertext).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def encrypt_comment(text: str) -> str:
    """Encrypt ``text`` into the on-disk ``MARKER + base64(ciphertext)`` form (deterministic)."""
    if _key is None or _iv is None or _marker is None:
        raise RuntimeError("no trace key installed")
    return _marker + base64.b64encode(
        encrypt_bytes(_key, _iv, text.encode("utf-8"))
    ).decode("ascii")


def derive_marker(output: str, key: bytes, iv: bytes) -> str:
    """Recover the marker prefix from the encryptor's :data:`TRACE_SENTINEL` output.

    ``output`` is ``marker + base64(ciphertext)``; we reproduce the ciphertext from the recovered
    key/IV and strip it, leaving the marker. Raises :class:`ValueError` if the tail does not match.
    """
    tail = base64.b64encode(
        encrypt_bytes(key, iv, TRACE_SENTINEL.encode("utf-8"))
    ).decode("ascii")
    if not output.endswith(tail):
        raise ValueError("could not derive the marker prefix from the output")
    return output[: -len(tail)]


def encrypt_bytes(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC encrypt ``data`` with PKCS#7 padding (used by the marker-recovery path)."""
    padder = PKCS7(_BLOCK_BITS).padder()
    padded = padder.update(data) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(padded) + enc.finalize()


def _decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = dec.update(data) + dec.finalize()
    unpadder = PKCS7(_BLOCK_BITS).unpadder()
    return unpadder.update(padded) + unpadder.finalize()
