"""The KNX IP Secure passwords a download writes: from the project, else the keyring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.keyring.service import KeyringIpSecrets, KeyringService
from editor_gui.plugins.project.service import ip_secure_users
from xknxeditor.datasecure import DecryptedKeyring
from xknxeditor.datasecure.files.knx_keyring import InterfaceType
from xknxeditor.datasecure.secure import DecryptedDevice, DecryptedInterface
from xknxeditor.proj.core.service import IpBusInterface, IpSecrets

_TUNNELS = {"M-1_A-1_BI-1": 1, "M-1_A-1_BI-2": 2}


def test_project_passwords_come_first() -> None:
    project = IpSecrets(
        device_authentication_code="auth",
        management_password="mgmt",
        bus_interfaces=[
            IpBusInterface("M-1_A-1_BI-1", None),
            IpBusInterface("M-1_A-1_BI-2", "two"),
        ],
    )
    ring = KeyringIpSecrets("other", "other", [(2, "1.1.250", "ring")])
    authentication, passwords, tunnelling = ip_secure_users(
        project, ring, _TUNNELS, [250, 251]
    )
    assert authentication == "auth"
    # The second bus interface (tunnelling address 2) is user 3; the keyring adds user 2.
    assert passwords == {1: "mgmt", 3: "two", 2: "ring"}
    assert sorted(tunnelling) == [(2, 1), (3, 2)]


def test_a_routing_interface_takes_the_first_user() -> None:
    # A routing bus interface with a password is user 2, the tunnels follow.
    project = IpSecrets(
        management_password="mgmt",
        bus_interfaces=[
            IpBusInterface("M-1_A-1_BI-0", "routing"),
            IpBusInterface("M-1_A-1_BI-1", "one"),
            IpBusInterface("M-1_A-1_BI-2", "two"),
        ],
    )
    _, passwords, tunnelling = ip_secure_users(project, None, _TUNNELS, [1, 2])
    assert passwords == {1: "mgmt", 2: "routing", 3: "one", 4: "two"}
    assert tunnelling == [(3, 1), (4, 2)]


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
        device_authentication_code="auth",
        bus_interfaces=[IpBusInterface("M-1_A-1_BI-1", "one")],
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


def test_secured_service_families() -> None:
    from editor_gui.plugins.project.service import (
        _secured_families,  # pyright: ignore[reportPrivateUsage]
    )

    project = IpSecrets(
        bus_interfaces=[IpBusInterface("M-1_A-1_BI-1", "one", secure=True)]
    )
    both = {"Tunneling", "Routing"}
    assert _secured_families(both, project, {1: "m"}, bytes(16)) == {
        3: True,
        4: True,
        5: True,
    }
    # Without passwords neither device management nor tunnelling is touched.
    assert _secured_families(both, project, {}, bytes(16)) == {5: True}
    # Tunnels that are not secured; an interface without routing.
    plain = IpSecrets(
        bus_interfaces=[IpBusInterface("M-1_A-1_BI-1", "one", secure=False)]
    )
    assert _secured_families({"Tunneling"}, plain, {1: "m"}, bytes(16)) == {
        3: True,
        4: False,
    }


def test_export_keyring(tmp_path: Path) -> None:
    from contextlib import nullcontext

    from editor_gui.plugins.project.service import (
        IpSecureInterface,
        IpSecureSettings,
        ProjectService,
    )
    from xknxeditor.datasecure import load_and_decrypt
    from xknxeditor.proj.core.service import DeviceKeys

    tool_key, group_key, backbone = bytes(16), bytes(range(16)), bytes(range(16, 32))
    router = SimpleNamespace(node_id=1, individual_address="1.1.10")
    actuator = SimpleNamespace(node_id=2, individual_address="1.1.5")
    plain = SimpleNamespace(node_id=3, individual_address="1.1.6")
    settings = IpSecureSettings(
        authentication_code="auth",
        commissioning_password="mgmt",
        secure_tunnelling=True,
        interfaces=[
            IpSecureInterface("M-1_A-1_BI-0", "Routing", 2, None, "route"),
            IpSecureInterface("M-1_A-1_BI-1", "Tunneling", 3, "1.1.250", "tunnel"),
        ],
    )
    service = cast(Any, SimpleNamespace())
    service._pid = "P"
    service._io_lock = nullcontext()
    service._log = SimpleNamespace(info=lambda *a, **k: None)
    service.devices = [router, actuator, plain]
    service.device_keys = lambda d: (
        None if d is plain else DeviceKeys(tool_key=tool_key, loaded_tool_key=None)
    )
    service.ip_secure_settings = lambda d: settings if d is router else None
    service._com_object_can_send = lambda co: True
    service.get_project_metadata = lambda: SimpleNamespace(name="Haus")
    service._project_name_for_keyring = lambda: (
        ProjectService._project_name_for_keyring(  # pyright: ignore[reportPrivateUsage]
            service
        )
    )
    service._svc = SimpleNamespace(
        installation_attributes=lambda pid: {"IPRoutingLatencyTolerance": "1500"},
        secured_group_keys=lambda pid: {0x0801: group_key},
        device_sequence_number=lambda pid, node: 42 if node == 2 else None,
        bus_interface_group_addresses=lambda pid, node: {"M-1_A-1_BI-1": [0x0801]},
        group_address_senders=lambda pid, ga, can_send: [0x1105],
        ip_backbone_key=lambda pid: backbone,
    )
    path = tmp_path / "haus.knxkeys"
    assert ProjectService.export_keyring(service, path, "pw") == 2
    keyring = load_and_decrypt(path.read_bytes(), "pw")
    assert keyring.backbone_key == backbone
    assert keyring.group_keys == {0x0801: group_key}
    (tunnel,) = keyring.interfaces  # routing is the backbone, not an interface
    assert (tunnel.host, tunnel.individual_address, tunnel.user_id) == (
        "1.1.10",
        "1.1.250",
        3,
    )
    assert (tunnel.password, tunnel.authentication) == ("tunnel", "auth")
    devices = {d.individual_address: d for d in keyring.devices}
    assert set(devices) == {"1.1.10", "1.1.5"}
    assert devices["1.1.10"].management_password == "mgmt"
    assert devices["1.1.5"].sequence_number == 42
