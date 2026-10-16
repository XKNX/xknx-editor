"""KNX IP Secure configuration a tool writes into a KNXnet/IP router or interface.

The backbone (routing) key, the device authentication code, the tunnelling user password hashes and
the tunnelling-user table live in the KNXnet/IP Parameter Object (object type 11), KNX Standard
v3.0.0, 3/8/9 §2.3.1. They are written over the Tool-Key secured connection as ordinary property
writes (no load-state machine) before the final restart.

The device authentication code and the password hashes are the PBKDF2-HMAC-SHA256 derivations of
§2.3.1.3.1 / §2.3.1.4.1, as xknx computes them for its secure tunnelling connections.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from xknx.secure.security_primitives import (
    derive_device_authentication_password,
    derive_user_password,
)

from xknxeditor.namespaces.intermediate import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_invoke_function_prop_t import (
    LdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType

IP_PARAMETER_OBJECT: Final = 11
PID_BACKBONE_KEY: Final = 91
PID_DEVICE_AUTHENTICATION_CODE: Final = 92
PID_PASSWORD_HASHES: Final = 93
PID_SECURED_SERVICE_FAMILIES: Final = 94
PID_MULTICAST_LATENCY_TOLERANCE: Final = 95
PID_SYNC_LATENCY_FRACTION: Final = 96
PID_TUNNELLING_USERS: Final = 97

#: The password hash of an empty passphrase (3/8/9 §2.3.1.4.3), the value of an unconfigured user.
EMPTY_PASSWORD_HASH: Final = bytes.fromhex("E9C304B914A35175FD7D1C673AB52FE1")
#: KNXnet/IP service families whose security a tool sets (3/8/9 2.3.1.5.4.1).
SERVICE_FAMILY_DEVICE_MANAGEMENT: Final = 0x03
SERVICE_FAMILY_TUNNELLING: Final = 0x04
SERVICE_FAMILY_ROUTING: Final = 0x05

#: User ID 1 is the management client (full access); limited users are 2..127.
MANAGEMENT_USER_ID: Final = 1
MAX_USER_ID: Final = 127
# The whole object type is written on a full or application download of an IP device.
_APPLIES: Final = LdCtrlProcType.FULL


def device_authentication_code(passphrase: str) -> bytes:
    """The 16-octet device authentication code derived from ``passphrase`` (3/8/9 §2.3.1.3.1)."""
    return derive_device_authentication_password(passphrase)


def password_hash(passphrase: str) -> bytes:
    """The 16-octet tunnelling-user password hash of ``passphrase`` (3/8/9 §2.3.1.4.1)."""
    return derive_user_password(passphrase)


@dataclass(frozen=True, slots=True)
class IpSecureConfig:
    """The KNX IP Secure material a tool writes to an IP device.

    ``backbone_key`` is the 16-octet routing/multicast key. ``device_authentication_passphrase`` and
    the ``user_passwords`` (by user id, 1 = management) are passphrases; their PBKDF2 hashes are
    written, never the passphrase. ``tunnelling_users`` maps a user id to its tunnelling-address
    index (3/8/9 §2.3.1.8); user id 1 is implicit and must not appear. The latencies default to
    the standard's values (2000 ms, 10.2 %, 3/8/9 §2.3.1.6, §2.3.1.7).

    ``secured_families`` sets whether each KNXnet/IP service family (device management,
    tunnelling, routing) requires security (3/8/9 §2.3.1.5); a family not listed keeps its
    setting. Ex factory none does (§2.3.1.5.3).
    """

    backbone_key: bytes | None = None
    device_authentication_passphrase: str | None = None
    user_passwords: Mapping[int, str] = field(default_factory=dict[int, str])
    tunnelling_users: Sequence[tuple[int, int]] = ()
    multicast_latency_tolerance_ms: int = 2000
    sync_latency_fraction: int = 0x1A
    secured_families: Mapping[int, bool] = field(default_factory=dict[int, bool])


def _write(
    prop_id: int, data: bytes, count: int = 1, start: int = 1
) -> LdCtrlWriteProp:
    return LdCtrlWriteProp(
        obj_type=IP_PARAMETER_OBJECT,
        prop_id=prop_id,
        start_element=start,
        count=count,
        verify=True,
        inline_data=data,
        applies_to=_APPLIES,
    )


def ip_secure_controls(config: IpSecureConfig) -> list[object]:
    """The load controls writing ``config`` to the KNXnet/IP Parameter Object. Empty when the
    config carries nothing. The password hashes are written before the tunnelling-user table it
    references (3/8/9 §2.3.1.8)."""
    controls: list[object] = []
    if config.backbone_key is not None:
        if len(config.backbone_key) != 16:
            raise ValueError("the backbone key must be 16 octets")
        controls.append(_write(PID_BACKBONE_KEY, config.backbone_key))
    if config.device_authentication_passphrase is not None:
        controls.append(
            _write(
                PID_DEVICE_AUTHENTICATION_CODE,
                device_authentication_code(config.device_authentication_passphrase),
            )
        )
    if config.user_passwords:
        highest = max(config.user_passwords)
        if highest > MAX_USER_ID or min(config.user_passwords) < MANAGEMENT_USER_ID:
            raise ValueError("user ids run from 1 (management) to 127")
        # The array is written whole from index 1. A user id without a password gets a random hash
        # no passphrase matches, never the well-known empty-password one.
        table = bytearray(secrets.token_bytes(16 * highest))
        for user_id, passphrase in config.user_passwords.items():
            table[(user_id - 1) * 16 : user_id * 16] = password_hash(passphrase)
        controls.append(_write(PID_PASSWORD_HASHES, bytes(table), count=highest))
    controls.append(
        _write(
            PID_MULTICAST_LATENCY_TOLERANCE,
            config.multicast_latency_tolerance_ms.to_bytes(2, "big"),
        )
    )
    controls.append(
        _write(PID_SYNC_LATENCY_FRACTION, bytes([config.sync_latency_fraction & 0xFF]))
    )
    users = sorted(u for u in config.tunnelling_users if u[0] != MANAGEMENT_USER_ID)
    if users:
        data = b"".join(bytes([user_id, index]) for user_id, index in users)
        controls.append(_write(PID_TUNNELLING_USERS, data, count=len(users)))
    # Last: a family requires security only once its passwords are in place.
    for family, secured in sorted(config.secured_families.items()):
        controls.append(
            LdCtrlInvokeFunctionProp(
                obj_type=IP_PARAMETER_OBJECT,
                prop_id=PID_SECURED_SERVICE_FAMILIES,
                # reserved, service "write the security requirement", family, version
                inline_data=bytes([0, 0, family, 1 if secured else 0]),
                applies_to=_APPLIES,
            )
        )
    return controls
