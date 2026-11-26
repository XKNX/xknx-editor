"""The KNX IP Secure passwords a download writes and the user of each tunnel: from the project,
else the keyring. User 1 is the management user; the tunnels follow in the order of their
address index, and tunnels with the same password share one user."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.keyring.service import KeyringIpSecrets, KeyringService
from editor_gui.plugins.project.service import (
    ProjectService,
    ip_secure_users,
    tunnelling_users,
)
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
    ring = KeyringIpSecrets(
        "other", "other", [(2, "1.1.250", "taken"), (3, "1.1.250", "ring")]
    )
    authentication, passwords, tunnelling = ip_secure_users(
        project, ring, _TUNNELS, [250, 251]
    )
    assert authentication == "auth"
    # The first tunnel with a password is user 2; the keyring fills in the tunnel without one,
    # with a user the project leaves free.
    assert passwords == {1: "mgmt", 2: "two", 3: "ring"}
    assert tunnelling == [(2, 2), (3, 1)]


def test_a_routing_interface_has_no_user() -> None:
    project = IpSecrets(
        management_password="mgmt",
        bus_interfaces=[
            IpBusInterface("M-1_A-1_BI-0", "routing"),
            IpBusInterface("M-1_A-1_BI-1", "one"),
            IpBusInterface("M-1_A-1_BI-2", "two"),
        ],
    )
    _, passwords, tunnelling = ip_secure_users(project, None, _TUNNELS, [1, 2])
    assert passwords == {1: "mgmt", 2: "one", 3: "two"}
    assert tunnelling == [(2, 1), (3, 2)]


_FOUR = {f"M-1_A-1_BI-{n}": n for n in range(1, 5)}


def test_tunnels_with_the_same_password_share_a_user() -> None:
    passwords = {
        "M-1_A-1_BI-1": "same",
        "M-1_A-1_BI-2": "same",
        "M-1_A-1_BI-3": "three",
        "M-1_A-1_BI-4": "four",
    }
    users, by_ref, tunnelling = tunnelling_users(
        "mgmt", passwords, _FOUR, [251, 252, 253, 254]
    )
    assert users == ["mgmt", "same", "three", "four"]
    assert by_ref == {
        "M-1_A-1_BI-1": 2,
        "M-1_A-1_BI-2": 2,
        "M-1_A-1_BI-3": 3,
        "M-1_A-1_BI-4": 4,
    }
    assert tunnelling == [(2, 1), (2, 2), (3, 3), (4, 4)]
    # The management password as a tunnel's is user 1.
    _users, by_ref, _tunnelling = tunnelling_users(
        "same", passwords, _FOUR, [251, 252, 253, 254]
    )
    assert by_ref["M-1_A-1_BI-1"] == 1


def test_the_tunnels_follow_their_address_index() -> None:
    # Bus interfaces listed in another order, the address entries not ascending: the users
    # follow the AddressIndex.
    passwords = {
        "M-1_A-1_BI-4": "four",
        "M-1_A-1_BI-2": "two",
        "M-1_A-1_BI-1": "one",
        "M-1_A-1_BI-3": "three",
    }
    tunnels = {
        "M-1_A-1_BI-3": 3,
        "M-1_A-1_BI-1": 1,
        "M-1_A-1_BI-4": 4,
        "M-1_A-1_BI-2": 2,
    }
    users, by_ref, tunnelling = tunnelling_users(
        "mgmt", passwords, tunnels, [254, 251, 253, 252]
    )
    assert users == ["mgmt", "one", "two", "three", "four"]
    assert by_ref["M-1_A-1_BI-3"] == 4
    assert tunnelling == [(2, 1), (3, 2), (4, 3), (5, 4)]


def test_a_tunnel_without_an_address_or_a_password() -> None:
    passwords = {"M-1_A-1_BI-1": "one", "M-1_A-1_BI-2": "two", "M-1_A-1_BI-3": None}
    tunnels = {"M-1_A-1_BI-1": 1, "M-1_A-1_BI-2": 2, "M-1_A-1_BI-3": 3}
    # Entry 1 has no address: its tunnel takes the user of the first one with an address and
    # has no user of its own; tunnel 3 has no password and no user.
    users, by_ref, tunnelling = tunnelling_users(
        "mgmt", passwords, tunnels, [None, 252, 253]
    )
    assert users == ["mgmt", "two"]
    assert by_ref == {"M-1_A-1_BI-2": 2}
    assert tunnelling == [(2, 1), (2, 2)]
    # No tunnel with an address at all: no tunnelling users.
    assert tunnelling_users("mgmt", passwords, tunnels, [None, None]) == (
        ["mgmt"],
        {},
        [],
    )


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


def _secure_interface(order: list[str]) -> Any:
    """A project service with a KNX IP Secure interface at 1.1.250 with four tunnels (1.1.251 to
    1.1.254, the first two with the same password), its bus interfaces in the project in
    ``order``."""
    access = SimpleNamespace(value="Tunneling")
    app = SimpleNamespace(
        program=SimpleNamespace(
            static=SimpleNamespace(
                bus_interfaces=SimpleNamespace(
                    bus_interface=[
                        SimpleNamespace(id=ref, access_type=access, address_index=index)
                        for ref, index in _FOUR.items()
                    ]
                )
            )
        )
    )
    device = SimpleNamespace(node_id=7, individual_address="1.1.250", app=app)
    password = {1: "same", 2: "same", 3: "three", 4: "four"}
    secrets = IpSecrets(
        device_authentication_code="auth",
        management_password="mgmt",
        bus_interfaces=[IpBusInterface(ref, password[_FOUR[ref]]) for ref in order],
    )
    service = cast(Any, SimpleNamespace())
    service._pid = "P"
    service.ip_secure_device = lambda d: True
    service._svc = SimpleNamespace(
        secure_commissioning=lambda pid, node: True,
        ip_secrets=lambda pid, node: secrets,
        device_additional_addresses=lambda pid, node: [251, 252, 253, 254],
    )
    service.devices = [device]
    settings = ProjectService._read_ip_secure_settings(  # pyright: ignore[reportPrivateUsage]
        service, cast(Any, device)
    )
    service.ip_secure_settings = lambda d: settings
    return service


def test_the_project_tunnels_have_the_users_of_the_device() -> None:
    # #348: the gateway gives 1.1.253 to user 3, its bus interface the third in the project.
    order = ["M-1_A-1_BI-1", "M-1_A-1_BI-4", "M-1_A-1_BI-3", "M-1_A-1_BI-2"]
    service = _secure_interface(order)
    tunnels = ProjectService.secure_tunnels(service, "1.1.250")
    assert [(t.individual_address, t.user_id) for t in tunnels] == [
        ("1.1.251", 2),
        ("1.1.254", 4),
        ("1.1.253", 3),
        ("1.1.252", 2),
    ]
    settings = service.ip_secure_settings(None)
    assert [i.user_id for i in settings.interfaces] == [2, 4, 3, 2]


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
            IpSecureInterface("M-1_A-1_BI-0", "Routing", None, None, "route"),
            IpSecureInterface("M-1_A-1_BI-1", "Tunneling", 3, "1.1.250", "tunnel"),
            # without a user (its address entry has none): not in the keyring
            IpSecureInterface("M-1_A-1_BI-2", "Tunneling", None, None, "spare"),
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
    service._tunnel_senders = lambda: {}
    tunnels: list[tuple[int, int, list[int]]] = []
    service.get_project_metadata = lambda: SimpleNamespace(name="Haus")
    service._project_name_for_keyring = lambda: (
        ProjectService._project_name_for_keyring(  # pyright: ignore[reportPrivateUsage]
            service
        )
    )
    service._svc = SimpleNamespace(
        installation_attributes=lambda pid: {"IPRoutingLatencyTolerance": "1500"},
        # Every group address with a key, also one in the clear now (0x0802).
        group_keys=lambda pid: {0x0801: group_key, 0x0802: bytes(range(32, 48))},
        device_sequence_number=lambda pid, node: 42 if node == 2 else None,
        bus_interface_group_addresses=lambda pid, node: {"M-1_A-1_BI-1": [0x0801]},
        tunnel_group_addresses=lambda pid, node, tunnel, gas, can_send, senders: (
            tunnels.append((node, tunnel, list(gas))) or {0x0801: [0x1105]}
        ),
        ip_backbone_key=lambda pid: backbone,
    )
    path = tmp_path / "haus.knxkeys"
    assert ProjectService.export_keyring(service, path, "pw") == 2
    keyring = load_and_decrypt(path.read_bytes(), "pw")
    assert keyring.backbone_key == backbone
    assert keyring.group_keys == {0x0801: group_key, 0x0802: bytes(range(32, 48))}
    # The tunnel's group addresses: those assigned to it, decided where its device is.
    assert tunnels == [(1, 0x11FA, [0x0801])]
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
