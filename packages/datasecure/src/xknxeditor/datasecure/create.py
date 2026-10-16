"""Create a keyring (``.knxkeys``) from plaintext key material, encrypted and signed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from xsdata.models.datatype import XmlDateTime

from xknxeditor.datasecure.crypto import derive_iv, hash_keyring_password
from xknxeditor.datasecure.files.knx_keyring import (
    Backbone,
    Devices,
    GroupAddresses,
    Interface,
    InterfaceType,
    Keyring,
)
from xknxeditor.datasecure.secure import encrypt_key, encrypt_password, sign_keyring

_PLACEHOLDER_SIGNATURE = "A" * 21 + "Q=="


@dataclass(frozen=True)
class KeyringTunnel:
    """A secure tunnel: its host device and tunnelling address (``x.y.z``), its user id and
    password, the host's device authentication code, and the group addresses (raw) it may
    receive secured, each with the addresses of its senders."""

    host: str
    individual_address: str
    user_id: int
    password: str
    authentication: str | None = None
    group_addresses: Mapping[int, Sequence[str]] = field(
        default_factory=dict[int, Sequence[str]]
    )


@dataclass(frozen=True)
class KeyringDevice:
    """A device commissioned securely: its address, Tool Key, last Sequence Number and, for a
    KNX IP Secure device, its commissioning password and authentication code."""

    individual_address: str
    tool_key: bytes | None = None
    sequence_number: int | None = None
    management_password: str | None = None
    authentication: str | None = None


def create_keyring(
    project: str,
    password: str,
    created: str,
    *,
    backbone_key: bytes | None = None,
    multicast_address: str = "224.0.23.12",
    latency: int | None = None,
    tunnels: Sequence[KeyringTunnel] = (),
    devices: Sequence[KeyringDevice] = (),
    group_keys: Mapping[int, bytes] | None = None,
) -> Keyring:
    """A keyring of the given key material, every secret encrypted under ``password`` (with the
    IV of ``created``, an ``xs:dateTime``) and signed."""
    key = hash_keyring_password(password.encode("utf-8"))
    iv = derive_iv(created)
    keyring = Keyring(
        project=project,
        created=XmlDateTime.from_string(created),
        created_by="XKNX Editor",
        signature=_PLACEHOLDER_SIGNATURE,
    )
    if backbone_key is not None:
        keyring.backbone = Backbone(
            multicast_address=multicast_address,
            latency=latency,
            key=encrypt_key(backbone_key, key, iv),
        )
    for tunnel in tunnels:
        keyring.interface.append(
            Interface(
                type_value=InterfaceType.TUNNELING,
                host=tunnel.host,
                individual_address=tunnel.individual_address,
                user_id=tunnel.user_id,
                password=encrypt_password(tunnel.password, key, iv),
                authentication=encrypt_password(tunnel.authentication, key, iv)
                if tunnel.authentication
                else None,
                group=[
                    Interface.Group(address=address, senders=list(senders))
                    for address, senders in sorted(tunnel.group_addresses.items())
                ],
            )
        )
    if group_keys:
        keyring.group_addresses = GroupAddresses(
            group=[
                GroupAddresses.Group(address=address, key=encrypt_key(raw, key, iv))
                for address, raw in sorted(group_keys.items())
            ]
        )
    if devices:
        keyring.devices = Devices(
            device=[
                Devices.Device(
                    individual_address=device.individual_address,
                    tool_key=encrypt_key(device.tool_key, key, iv)
                    if device.tool_key is not None
                    else None,
                    sequence_number=device.sequence_number,
                    management_password=encrypt_password(
                        device.management_password, key, iv
                    )
                    if device.management_password
                    else None,
                    authentication=encrypt_password(device.authentication, key, iv)
                    if device.authentication
                    else None,
                )
                for device in devices
            ]
        )
    return sign_keyring(keyring, password)
