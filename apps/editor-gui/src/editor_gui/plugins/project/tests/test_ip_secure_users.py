"""The KNX IP Secure passwords a download writes: from the project, else the keyring."""

from __future__ import annotations

from typing import Any, cast

from editor_gui.plugins.keyring.service import KeyringIpSecrets, KeyringService
from editor_gui.plugins.project.service import ip_secure_users
from xknxeditor.datasecure import DecryptedKeyring
from xknxeditor.datasecure.files.knx_keyring import InterfaceType
from xknxeditor.datasecure.secure import DecryptedDevice, DecryptedInterface
from xknxeditor.proj.core.service import IpSecrets

_TUNNELS = {"M-1_A-1_BI-1": 1, "M-1_A-1_BI-2": 2}


def test_project_passwords_come_first() -> None:
    project = IpSecrets(
        device_authentication_code="auth",
        management_password="mgmt",
        tunnel_passwords={"M-1_A-1_BI-2": "two"},
    )
    ring = KeyringIpSecrets("other", "other", [(2, "1.1.250", "ring")])
    authentication, passwords, tunnelling = ip_secure_users(
        project, ring, _TUNNELS, [250, 251]
    )
    assert authentication == "auth"
    # The bus interface on tunnelling address 2 is user 3; the keyring adds user 2 on address 1.
    assert passwords == {1: "mgmt", 3: "two", 2: "ring"}
    assert sorted(tunnelling) == [(2, 1), (3, 2)]


def test_the_keyring_fills_in() -> None:
    ring = KeyringIpSecrets("auth", "mgmt", [(4, "1.1.251", "pw"), (5, "1.1.9", "x")])
    authentication, passwords, tunnelling = ip_secure_users(
        IpSecrets(), ring, _TUNNELS, [250, 251]
    )
    assert authentication == "auth"
    assert passwords == {1: "mgmt", 4: "pw"}  # 1.1.9 is none of the device's addresses
    assert tunnelling == [(4, 2)]


def test_no_passwords_without_the_management_password() -> None:
    project = IpSecrets(
        device_authentication_code="auth", tunnel_passwords={"M-1_A-1_BI-1": "one"}
    )
    assert ip_secure_users(project, None, _TUNNELS, [250]) == ("auth", {}, [])


def test_keyring_ip_secrets() -> None:
    service = KeyringService()
    cast(Any, service)._decrypted = DecryptedKeyring(
        backbone_key=None,
        interfaces=[
            DecryptedInterface(
                InterfaceType.TUNNELING, "1.1.250", "1.1.10", 2, "pw", None
            ),
            DecryptedInterface(
                InterfaceType.TUNNELING, "1.1.240", "1.1.20", 2, "no", None
            ),
        ],
        devices=[DecryptedDevice("1.1.10", None, "mgmt", "auth", None, None, None)],
        group_keys={},
    )
    secrets = service.ip_secrets("1.1.10")
    assert secrets == KeyringIpSecrets("auth", "mgmt", [(2, "1.1.250", "pw")])
    assert service.ip_secrets("1.1.99") is None
