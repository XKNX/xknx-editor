"""The KNX IP Secure passwords of an IP device in the project.

A secure IP device has a device authentication code, a commissioning (management, user 1)
password and a password for each of its bus interfaces (tunnels, routing), stored on its
``<Security>`` (``@DeviceAuthenticationCode``, ``@DeviceManagementPassword``) and its
``<BusInterfaces>/<BusInterface @Password>``, each next to its hash (``…Hash``,
``@PasswordHash``): the base64 of the 16-octet derivation of KNX Standard 3/8/9 §2.3.1.3.1 and
§2.3.1.4.1. The bus interfaces are users 2, 3, … in the device's order.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import xml.etree.ElementTree as ET
from collections.abc import Iterable

#: The passwords and codes are this long (as in exported projects), at most 20 characters.
PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 20
#: The bus interface kinds that take a password.
PASSWORD_ACCESS_TYPES = frozenset({"Tunneling", "Routing"})

_PRINTABLE = "".join(chr(c) for c in range(0x20, 0x7F))
_AUTHENTICATION_SALT = b"device-authentication-code.1.secure.ip.knx.org"
_USER_SALT = b"user-password.1.secure.ip.knx.org"
_ITERATIONS = 65536


def new_password() -> str:
    """A random password of printable ASCII characters."""
    return "".join(secrets.choice(_PRINTABLE) for _ in range(PASSWORD_LENGTH))


def check_password(value: str) -> None:
    """Raise ``ValueError`` unless ``value`` can be a KNX IP Secure password or code."""
    if not value or len(value) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"1 to {MAX_PASSWORD_LENGTH} characters are needed")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError("only Latin-1 characters are possible") from exc


def authentication_code_hash(code: str) -> bytes:
    """The device authentication code's 16-octet derivation (3/8/9 §2.3.1.3.1)."""
    return hashlib.pbkdf2_hmac(
        "sha256", code.encode("latin-1"), _AUTHENTICATION_SALT, _ITERATIONS, 16
    )


def password_hash(password: str) -> bytes:
    """A user password's 16-octet derivation (3/8/9 §2.3.1.4.1)."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("latin-1"), _USER_SALT, _ITERATIONS, 16
    )


def encoded(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def bus_interfaces(raw: str | None) -> ET.Element:
    """The stored ``<BusInterfaces>`` (a new empty one when absent or malformed)."""
    if raw:
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            pass
    return ET.Element("BusInterfaces")


def bus_interface(root: ET.Element, ref_id: str) -> ET.Element | None:
    return next(
        (bi for bi in root.iter("BusInterface") if bi.get("RefId") == ref_id), None
    )


def add_bus_interfaces(root: ET.Element, ref_ids: Iterable[str]) -> None:
    """Append a ``<BusInterface>`` for each of ``ref_ids`` the device has none for, in order."""
    for ref_id in ref_ids:
        if bus_interface(root, ref_id) is None:
            ET.SubElement(root, "BusInterface", RefId=ref_id)
