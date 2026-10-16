"""A keyring created from plaintext key material verifies and decrypts back to it."""

from pathlib import Path

from xknx.secure.keyring import sync_load_keyring

from xknxeditor.datasecure import (
    KeyringDevice,
    KeyringTunnel,
    create_keyring,
    load_and_decrypt,
    serialize_keyring,
)
from xknxeditor.datasecure.files.knx_keyring import InterfaceType

_TOOL_KEY = bytes(range(16))
_BACKBONE = bytes(range(16, 32))
_GROUP_KEY = bytes(range(32, 48))


def _keyring() -> bytes:
    return serialize_keyring(
        create_keyring(
            "Project",
            "secret",
            "2026-10-17T08:00:00",
            backbone_key=_BACKBONE,
            latency=2000,
            tunnels=[
                KeyringTunnel(
                    host="1.1.10",
                    individual_address="1.1.250",
                    user_id=2,
                    password='a"<b> &c',
                    authentication="auth",
                    group_addresses={0x0801: ["1.1.5", "1.1.6"]},
                )
            ],
            devices=[
                KeyringDevice("1.1.5", tool_key=_TOOL_KEY, sequence_number=42),
                KeyringDevice(
                    "1.1.10",
                    tool_key=_TOOL_KEY,
                    management_password="mgmt",
                    authentication="auth",
                ),
            ],
            group_keys={0x0801: _GROUP_KEY},
        )
    )


def test_round_trip() -> None:
    decrypted = load_and_decrypt(_keyring(), "secret")
    assert decrypted.backbone_key == _BACKBONE
    assert decrypted.group_keys == {0x0801: _GROUP_KEY}
    (tunnel,) = decrypted.interfaces
    assert tunnel.type is InterfaceType.TUNNELING
    assert (tunnel.host, tunnel.individual_address, tunnel.user_id) == (
        "1.1.10",
        "1.1.250",
        2,
    )
    assert (tunnel.password, tunnel.authentication) == ('a"<b> &c', "auth")
    devices = {d.individual_address: d for d in decrypted.devices}
    assert devices["1.1.5"].tool_key == _TOOL_KEY
    assert devices["1.1.5"].sequence_number == 42
    assert devices["1.1.10"].management_password == "mgmt"


def test_xknx_reads_it(tmp_path: Path) -> None:
    path = tmp_path / "project.knxkeys"
    path.write_bytes(_keyring())
    keyring = sync_load_keyring(path, "secret")
    (interface,) = keyring.interfaces
    assert interface.user_id == 2
    assert interface.decrypted_password == 'a"<b> &c'
    assert keyring.get_data_secure_group_keys(
        receiver=interface.individual_address
    ) == {next(iter(interface.group_addresses)): _GROUP_KEY}
