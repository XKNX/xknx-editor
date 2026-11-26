"""Configured connections: KNXnet/IP tunnelling interfaces the user enters once (name, address,
port, protocol, NAT) and then uses from the Gateway menu, Select Connection and a project's Bus
Connection, also interfaces a gateway search cannot find (another network).

A connection is written as the connection string of a Bus Connection (the ``Parameter`` of a
``<BusAccess>``): ``Type=IpTunneling;HostAddress=…;IpPort=…;ProtocolType=…;UseNat=True;Name=…``
with the values at their defaults left out. Two connections are the same interface when their
address and port are; protocol, NAT and name don't count. They are exported and imported as a
``BusConnections`` XML file."""

from __future__ import annotations

import ipaddress
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

DEFAULT_PORT = 3671
Protocol = Literal["Auto", "Udp", "Tcp"]
PROTOCOLS: tuple[Protocol, ...] = ("Auto", "Udp", "Tcp")

_NAMESPACE = "http://knx.org/xml/busconnections/60"
_MAX_HOST = 255
_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
# A value is written in quotes when it holds any of these (or nothing at all).
_NEEDS_QUOTES = re.compile(r"""[\s"';=\x00-\x1f]""")


@dataclass(frozen=True, slots=True)
class ConfiguredConnection:
    """A KNXnet/IP tunnelling interface: its name, IP address or host name, port, protocol
    (``Auto``: TCP when the interface offers it, else UDP) and whether the tunnel goes through
    NAT (UDP only)."""

    name: str
    host: str
    port: int = DEFAULT_PORT
    protocol: Protocol = "Auto"
    use_nat: bool = False

    @property
    def endpoint(self) -> str:
        """``host``, with ``:port`` when it is not the default port."""
        return self.host if self.port == DEFAULT_PORT else f"{self.host}:{self.port}"

    def to_parameter(self) -> str:
        """The connection string, as a Bus Connection stores it."""
        fields: list[tuple[str, str]] = [
            ("Type", "IpTunneling"),
            ("HostAddress", self.host),
        ]
        if self.port != DEFAULT_PORT:
            fields.append(("IpPort", str(self.port)))
        if self.protocol != "Auto":
            fields.append(("ProtocolType", self.protocol))
        if self.use_nat and self.protocol != "Tcp":
            fields.append(("UseNat", "True"))
        fields.append(("Name", self.name))
        return ";".join(f"{key}={_quoted(value)}" for key, value in fields)

    def same_interface(self, other: ConfiguredConnection) -> bool:
        """Whether ``other`` is the same interface: the same address and port."""
        return self.host == other.host and self.port == other.port


def _quoted(value: str) -> str:
    if not value or _NEEDS_QUOTES.search(value) is None:
        return value
    if '"' in value and "'" not in value:
        return f"'{value}'"
    return '"' + value.replace('"', '""') + '"'


def parse_fields(text: str) -> dict[str, str]:
    """The ``key=value`` pairs of a connection string (keys in lower case); a value may be in
    double or single quotes, a quote doubled inside them."""
    fields: dict[str, str] = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in "; \t":
            i += 1
        start = i
        while i < n and text[i] not in "=;":
            i += 1
        key = text[start:i].strip().lower()
        if i >= n or text[i] == ";":
            continue
        i += 1  # "="
        while i < n and text[i] in " \t":
            i += 1
        if i < n and text[i] in "\"'":
            quote = text[i]
            i += 1
            value: list[str] = []
            while i < n:
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:
                        value.append(quote)
                        i += 2
                        continue
                    i += 1
                    break
                value.append(text[i])
                i += 1
            while i < n and text[i] != ";":
                i += 1
            found = "".join(value)
        else:
            start = i
            while i < n and text[i] != ";":
                i += 1
            found = text[start:i].strip()
        if key:
            fields[key] = found
    return fields


def parse_parameter(text: str) -> ConfiguredConnection | None:
    """The KNXnet/IP tunnelling connection of a connection string; None for any other kind or one
    without an address. ``Port`` is read like ``IpPort`` (older projects of this editor)."""
    fields = parse_fields(text)
    if fields.get("type", "").lower() != "iptunneling" or not fields.get("hostaddress"):
        return None
    port_text = fields.get("ipport") or fields.get("port") or ""
    port = int(port_text) if port_text.isdigit() else DEFAULT_PORT
    protocol: Protocol = "Auto"
    for candidate in PROTOCOLS:
        if fields.get("protocoltype", "").lower() == candidate.lower():
            protocol = candidate
    return ConfiguredConnection(
        name=fields.get("name", ""),
        host=fields["hostaddress"],
        port=port if 0 < port < 65536 else DEFAULT_PORT,
        protocol=protocol,
        use_nat=fields.get("usenat", "").lower() == "true",
    )


def host_valid(host: str) -> bool:
    """Whether ``host`` is an IPv4 address an interface can have (not 0.0.0.0, not
    255.255.255.255) or a valid host name."""
    text = host.strip()
    if not text or len(text) > _MAX_HOST:
        return False
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        return str(address) not in ("0.0.0.0", "255.255.255.255")
    if re.fullmatch(r"[0-9.]+", text):
        return False  # a broken IPv4 address, no host name
    labels = text.rstrip(".").split(".")
    return all(_LABEL.fullmatch(label) for label in labels)


def unique_name(base: str, taken: Iterable[str]) -> str:
    """``base``, or ``base (1)``, ``base (2)``, … when another connection has that name."""
    names = set(taken)
    if base not in names:
        return base
    number = 1
    while f"{base} ({number})" in names:
        number += 1
    return f"{base} ({number})"


def find_same(
    connections: Sequence[ConfiguredConnection], connection: ConfiguredConnection
) -> int | None:
    """The position of the first of ``connections`` that is the same interface, or None."""
    return next(
        (i for i, c in enumerate(connections) if c.same_interface(connection)), None
    )


def export_xml(connections: Sequence[ConfiguredConnection]) -> bytes:
    """The connections as a ``BusConnections`` XML file."""
    ET.register_namespace("", _NAMESPACE)
    root = ET.Element(f"{{{_NAMESPACE}}}BusConnections")
    for connection in connections:
        ET.SubElement(
            root,
            f"{{{_NAMESPACE}}}BusConnection",
            {"ConnectionString": connection.to_parameter()},
        )
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def import_xml(data: bytes) -> list[ConfiguredConnection]:
    """The KNXnet/IP tunnelling connections of a ``BusConnections`` XML file (any other kind is
    left out). Raises ``ValueError`` for a file that is no XML."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(str(error)) from error
    found: list[ConfiguredConnection] = []
    for element in root.iter():
        text = element.get("ConnectionString")
        if text is None:
            continue
        connection = parse_parameter(text)
        if connection is not None:
            found.append(connection)
    return found


def merge_imported(
    connections: Sequence[ConfiguredConnection],
    imported: Iterable[ConfiguredConnection],
) -> tuple[list[ConfiguredConnection], int]:
    """``connections`` with the ``imported`` ones added, and how many were added: one with the name
    and the interface of a connection there already is left out, one with only its name gets a
    new one."""
    merged = list(connections)
    added = 0
    for connection in imported:
        same_name = [c for c in merged if c.name == connection.name]
        if any(c.same_interface(connection) for c in same_name):
            continue
        if same_name:
            base = re.sub(r" \(\d+\)$", "", connection.name)
            connection = replace(
                connection, name=unique_name(base, (c.name for c in merged))
            )
        merged.append(connection)
        added += 1
    return merged, added
