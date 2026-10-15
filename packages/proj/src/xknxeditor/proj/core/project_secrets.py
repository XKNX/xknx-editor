"""The project's secrets (KNX Secure keys, passwords) encrypted with the project password.

A password-protected project never stores a secret in the clear. Each secret value is replaced in
place by a token ``enc1:<base64(nonce | AES-256-GCM ciphertext)>``, wherever it is kept: in its own
column (the group-address key), as a value of a JSON attribute map (``BCUKey``,
``IPRoutingBackboneKey``, ``ProjectTracingPassword``), as an attribute value inside a stored XML
subtree (``<Security ToolKey=…>``, ``<P2PLink Key=…>``, ``<DeviceCertificate FDSK=…>``) and in the
undo history, which snapshots those values. The key is derived from the project password, which is
not stored: the project keeps only the salt and a check value to detect a wrong password.

A token holds the value as written in an XML attribute (entities escaped), so the export decrypts
every token of the serialized XML in one pass (:meth:`ProjectCipher.reveal`).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from xml.sax.saxutils import escape, unescape

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import JSON, String, or_, select
from sqlalchemy.orm import Session

from xknxeditor.proj.models import (
    Base,
    Device,
    GroupAddress,
    Installation,
    Project,
)

#: Attributes whose values are secrets, on any element.
SECRET_ATTRIBUTES = frozenset(
    {
        "ToolKey",
        "LoadedToolKey",
        "LoadedIPRoutingBackboneKey",
        "DeviceAuthenticationCode",
        "DeviceManagementPassword",
        "Key",
        "Password",
        "FDSK",
        "BCUKey",
        "IPRoutingBackboneKey",
        "ProjectTracingPassword",
    }
)
#: The secrets that make a project a KNX Secure project, which then needs a password.
SECURE_ATTRIBUTES = SECRET_ATTRIBUTES - {"BCUKey", "ProjectTracingPassword"}

# Columns holding a secret value on its own.
_SECRET_COLUMNS = frozenset({("group_addresses", "security_key")})
# Keys of a JSON map (or of an undo-history snapshot) whose value is a secret.
_SECRET_FIELDS = SECRET_ATTRIBUTES | {"security_key"}

_PREFIX = "enc1:"
_TOKEN = re.compile(r"enc1:[A-Za-z0-9+/]+={0,2}")
_TOKEN_BYTES = re.compile(rb"enc1:[A-Za-z0-9+/]+={0,2}")
_NAMES = "|".join(sorted(SECRET_ATTRIBUTES))
_ATTRIBUTE = re.compile(rf'(?<=\s)({_NAMES})="([^"]*)"')
_SECURE_ATTRIBUTE = re.compile(
    rf'(?<=\s)({"|".join(sorted(SECURE_ATTRIBUTES))})="[^"]+"'
)
# A cheap filter for the rows that can hold a secret or a token at all.
_CANDIDATES = ("%Key%", "%Password%", "%FDSK%", "%enc1:%", "%security_key%")

_KDF = "pbkdf2-sha256"
_ITERATIONS = 600_000
_AAD = b"xknx-editor/enc1"
_CHECK = "xknx-editor project password"
_ATTRIBUTE_ENTITIES = {'"': "&quot;"}
_ATTRIBUTE_UNENTITIES = {"&quot;": '"', "&apos;": "'"}


class ProjectPasswordRequired(ValueError):
    """The project is password-protected (or must become so) and no password was given."""


class InvalidProjectPassword(ValueError):
    """The given password is not the project's password."""


def is_token(value: str) -> bool:
    return value.startswith(_PREFIX)


@dataclass(frozen=True)
class ProjectCipher:
    """AES-256-GCM with the key derived from the project password."""

    key: bytes

    @classmethod
    def derive(cls, password: str, salt: str) -> ProjectCipher:
        """The cipher for ``password`` under a stored ``salt`` (see :func:`new_salt`)."""
        kdf, iterations, raw = salt.split(":")
        if kdf != _KDF:
            raise ValueError(f"unknown key derivation {kdf!r}")
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(raw), int(iterations)
        )
        return cls(key)

    def seal(self, text: str) -> str:
        nonce = os.urandom(12)
        data = AESGCM(self.key).encrypt(nonce, text.encode("utf-8"), _AAD)
        return _PREFIX + base64.b64encode(nonce + data).decode("ascii")

    def unseal(self, token: str) -> str:
        """The text of ``token``; raises :class:`InvalidProjectPassword` for a foreign key."""
        raw = base64.b64decode(token[len(_PREFIX) :])
        try:
            data = AESGCM(self.key).decrypt(raw[:12], raw[12:], _AAD)
        except InvalidTag as exc:
            raise InvalidProjectPassword("wrong project password") from exc
        return data.decode("utf-8")

    def check_value(self) -> str:
        return self.seal(_CHECK)

    def verify(self, check: str) -> bool:
        try:
            return self.unseal(check) == _CHECK
        except (InvalidProjectPassword, ValueError):
            return False

    def reveal(self, data: bytes) -> bytes:
        """``data`` (serialized XML) with every token replaced by its text."""
        return _TOKEN_BYTES.sub(
            lambda m: self.unseal(m.group().decode("ascii")).encode("utf-8"), data
        )

    def value(self, stored: str | None) -> str | None:
        """A stored secret value in the clear (the inverse of sealing a single value)."""
        if stored is None or not is_token(stored):
            return stored
        return unescape(self.unseal(stored), _ATTRIBUTE_UNENTITIES)


def check_project_password(path: Path, password: str | None) -> None:
    """Raise :class:`ProjectPasswordRequired`/:class:`InvalidProjectPassword` unless ``password``
    opens the project file at ``path`` (always fine for an unprotected one). Reads the file only."""
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as db:
            row = db.execute(
                "SELECT password_salt, password_check FROM projects"
            ).fetchone()
    except sqlite3.Error:
        return  # not (yet) a protected project file; opening it reports any other problem
    if row is None or not row[0]:
        return
    if not password:
        raise ProjectPasswordRequired("the project is password-protected")
    if not ProjectCipher.derive(password, row[0]).verify(row[1] or ""):
        raise InvalidProjectPassword("wrong project password")


def new_salt() -> str:
    return f"{_KDF}:{_ITERATIONS}:{base64.b64encode(os.urandom(16)).decode('ascii')}"


def _sealer(cipher: ProjectCipher) -> tuple[Callable[[str], str], Callable[[str], str]]:
    def value(text: str) -> str:
        if not text or is_token(text):
            return text
        return cipher.seal(escape(text, _ATTRIBUTE_ENTITIES))

    def xml(text: str) -> str:
        return _ATTRIBUTE.sub(
            lambda m: (
                m.group()
                if not m.group(2) or is_token(m.group(2))
                else f'{m.group(1)}="{cipher.seal(m.group(2))}"'
            ),
            text,
        )

    return value, xml


def _unsealer(
    cipher: ProjectCipher,
) -> tuple[Callable[[str], str], Callable[[str], str]]:
    def value(text: str) -> str:
        return cipher.value(text) or ""

    def xml(text: str) -> str:
        return _TOKEN.sub(lambda m: cipher.unseal(m.group()), text)

    return value, xml


def _walk(
    obj: Any, value: Callable[[str], str], xml: Callable[[str], str], key: object = None
) -> Any:
    if isinstance(obj, str):
        return value(obj) if key in _SECRET_FIELDS else xml(obj)
    if isinstance(obj, dict):
        items = cast(dict[str, Any], obj)
        return {k: _walk(v, value, xml, k) for k, v in items.items()}
    if isinstance(obj, list):
        return [_walk(v, value, xml) for v in cast(list[Any], obj)]
    return obj


def recrypt(
    session: Session, old: ProjectCipher | None, new: ProjectCipher | None
) -> None:
    """Re-encrypt every secret of the project in ``session`` from ``old`` to ``new``: ``old=None``
    seals the secrets stored in the clear, ``new=None`` stores them in the clear again. Runs inside
    the session's transaction (the caller commits) and covers every table, the undo history too."""
    session.flush()
    steps = [_unsealer(old)] if old is not None else []
    if new is not None:
        steps.append(_sealer(new))
    if not steps:
        return
    for table in Base.metadata.sorted_tables:
        columns = [c for c in table.columns if isinstance(c.type, (String, JSON))]
        keys = list(table.primary_key.columns)
        if not columns or not keys:
            continue
        candidates = or_(
            *(
                c.is_not(None)
                if (table.name, c.name) in _SECRET_COLUMNS
                else or_(*(c.cast(String).like(p) for p in _CANDIDATES))
                for c in columns
            )
        )
        rows = session.execute(select(*keys, *columns).where(candidates)).all()
        for row in rows:
            changes: dict[str, Any] = {}
            for index, column in enumerate(columns):
                current = row[len(keys) + index]
                if current is None:
                    continue
                updated = current
                for value, xml in steps:
                    if (table.name, column.name) in _SECRET_COLUMNS:
                        updated = value(updated)
                    elif isinstance(column.type, JSON):
                        updated = _walk(updated, value, xml)
                    else:
                        updated = xml(updated)
                if updated != current:
                    changes[column.name] = updated
            if changes:
                condition = [k == row[i] for i, k in enumerate(keys)]
                session.execute(table.update().where(*condition).values(**changes))
    session.expire_all()


def _has_secure(text: str | None) -> bool:
    return bool(text) and _SECURE_ATTRIBUTE.search(text or "") is not None


def has_secure_data(session: Session) -> bool:
    """Whether the project holds KNX Secure data: secured group addresses or their keys, a device's
    keys, authentication code or management password, a bus interface password, a point-to-point
    link key, a device certificate, or the IP backbone key. Such a project needs a password."""
    secure_group_address = session.query(GroupAddress.id).filter(
        (GroupAddress.data_secure.is_(True)) | (GroupAddress.security_key.is_not(None))
    )
    if session.query(secure_group_address.exists()).scalar():
        return True
    for installation in session.query(Installation):
        attrs = installation.extra_attributes or {}
        if attrs.get("IPRoutingBackboneKey") or _has_secure(installation.p2p_links_xml):
            return True
        if any(_has_secure(xml) for xml in installation.unassigned_devices_xml or []):
            return True
    for project in session.query(Project):
        if _has_secure((project.info_subtrees or {}).get("DeviceCertificates")):
            return True
    return any(
        _has_secure(xml)
        for (subtrees,) in session.query(Device.extra_subtrees)
        if subtrees
        for name, xml in subtrees.items()
        if name in ("Security", "BusInterfaces")
    )
