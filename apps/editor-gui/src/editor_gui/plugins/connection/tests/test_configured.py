"""Configured connections: their connection strings, the address check, names, the same
interface and the BusConnections XML import and export (#351)."""

from __future__ import annotations

import pytest

from editor_gui.plugins.connection.configured import (
    ConfiguredConnection,
    export_xml,
    find_same,
    host_valid,
    import_xml,
    merge_imported,
    parse_fields,
    parse_parameter,
    unique_name,
)

# As a configured connection file of the reference holds them.
_TCP = (
    'Type=IpTunneling;HostAddress=192.168.16.103;ProtocolType=Tcp;Name="IP Tunneling"'
)
_UDP = (
    "Type=IpTunneling;HostAddress=192.168.16.103;ProtocolType=Udp;"
    'Name="IP Tunneling UDP"'
)


def test_the_connection_string_leaves_the_defaults_out() -> None:
    plain = ConfiguredConnection("Home", "192.168.0.221")
    assert (
        plain.to_parameter() == "Type=IpTunneling;HostAddress=192.168.0.221;Name=Home"
    )
    assert plain.endpoint == "192.168.0.221"
    other = ConfiguredConnection("Brücktal", "192.168.97.24", 3700, "Udp", use_nat=True)
    assert other.to_parameter() == (
        "Type=IpTunneling;HostAddress=192.168.97.24;IpPort=3700;ProtocolType=Udp;"
        "UseNat=True;Name=Brücktal"
    )
    assert other.endpoint == "192.168.97.24:3700"
    # NAT only for UDP
    tcp = ConfiguredConnection("A", "h", protocol="Tcp", use_nat=True)
    assert "UseNat" not in tcp.to_parameter()


def test_names_are_quoted_as_needed() -> None:
    assert (
        ConfiguredConnection("IP Tunneling", "h")
        .to_parameter()
        .endswith('Name="IP Tunneling"')
    )
    assert (
        ConfiguredConnection('Say "hi"', "h")
        .to_parameter()
        .endswith("Name='Say \"hi\"'")
    )
    both = ConfiguredConnection('It\'s "x"', "h").to_parameter()
    assert both.endswith('Name="It\'s ""x"""')
    assert parse_parameter(both) == ConfiguredConnection('It\'s "x"', "h")


@pytest.mark.parametrize(
    "connection",
    [
        ConfiguredConnection("IP Tunneling", "192.168.16.103"),
        ConfiguredConnection("a;b=c", "knx.example.com", 1234, "Tcp"),
        ConfiguredConnection("", "10.0.0.1", protocol="Udp", use_nat=True),
    ],
)
def test_a_connection_string_reads_back(connection: ConfiguredConnection) -> None:
    assert parse_parameter(connection.to_parameter()) == connection


def test_the_reference_strings_are_read() -> None:
    assert parse_parameter(_TCP) == ConfiguredConnection(
        "IP Tunneling", "192.168.16.103", protocol="Tcp"
    )
    assert parse_parameter(_UDP) == ConfiguredConnection(
        "IP Tunneling UDP", "192.168.16.103", protocol="Udp"
    )
    # Keys in any case, the port as this editor wrote it before.
    assert parse_parameter("type=iptunneling;hostaddress=h;Port=3672") == (
        ConfiguredConnection("", "h", 3672)
    )
    assert parse_fields('A=1; B = "x;y" ;C') == {"a": "1", "b": "x;y"}


@pytest.mark.parametrize(
    "text", ["Type=Usb;DevicePath=x", "Type=IpTunneling;Name=x", "", "garbage"]
)
def test_no_tunnelling_connection(text: str) -> None:
    assert parse_parameter(text) is None


@pytest.mark.parametrize(
    ("host", "valid"),
    [
        ("192.168.0.221", True),
        ("knx-gateway.local", True),
        ("gateway", True),
        ("0.0.0.0", False),
        ("255.255.255.255", False),
        ("192.168.0", False),
        ("192.168.0.300", False),
        ("-bad.example", False),
        ("bad_name", False),
        ("", False),
        ("a" * 256, False),
    ],
)
def test_host_check(host: str, valid: bool) -> None:
    assert host_valid(host) is valid


def test_unique_names() -> None:
    assert unique_name("IP Tunneling", []) == "IP Tunneling"
    assert unique_name("IP Tunneling", ["IP Tunneling"]) == "IP Tunneling (1)"
    assert (
        unique_name("IP Tunneling", ["IP Tunneling", "IP Tunneling (1)"])
        == "IP Tunneling (2)"
    )


def test_the_same_interface_is_address_and_port() -> None:
    tcp = ConfiguredConnection("A", "192.168.16.103", protocol="Tcp")
    udp = ConfiguredConnection("B", "192.168.16.103", protocol="Udp", use_nat=True)
    assert tcp.same_interface(udp)
    assert not tcp.same_interface(ConfiguredConnection("A", "192.168.16.103", 3672))
    assert find_same([udp], tcp) == 0
    assert find_same([], tcp) is None


def test_export_and_import() -> None:
    connections = [
        ConfiguredConnection("Home", "192.168.0.221", protocol="Tcp"),
        ConfiguredConnection("Brücktal", "192.168.97.24", 3700),
    ]
    data = export_xml(connections)
    assert data.startswith(b"<?xml")
    assert b'xmlns="http://knx.org/xml/busconnections/60"' in data
    assert import_xml(data) == connections
    reference = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<BusConnections xmlns="http://knx.org/xml/busconnections/60">\n'
        f'  <BusConnection ConnectionString="{_TCP.replace(chr(34), "&quot;")}" />\n'
        '  <BusConnection ConnectionString="Type=Usb;DevicePath=x" />\n'
        "</BusConnections>\n"
    ).encode()
    assert import_xml(reference) == [
        ConfiguredConnection("IP Tunneling", "192.168.16.103", protocol="Tcp")
    ]
    with pytest.raises(ValueError):
        import_xml(b"not xml")


def test_imported_connections_are_merged() -> None:
    home = ConfiguredConnection("Home", "192.168.0.221")
    existing = [home]
    imported = [
        ConfiguredConnection(
            "Home", "192.168.0.221", protocol="Tcp"
        ),  # the same: left out
        ConfiguredConnection("Home", "192.168.97.24"),  # another interface: a new name
        ConfiguredConnection("Other", "192.168.0.221"),  # another name: added
    ]
    merged, added = merge_imported(existing, imported)
    assert added == 2
    assert [c.name for c in merged] == ["Home", "Home (1)", "Other"]
    assert existing == [home]
