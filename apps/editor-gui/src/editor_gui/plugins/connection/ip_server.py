"""The KNXnet/IP server of the editor ("USB to IP bridge"): other tools use the KNX USB
interface the editor is connected through as a KNXnet/IP interface.

``IpServerSettings`` are its settings as kept, ``check_tunnel_addresses()`` validates the tunnel
addresses, ``suggest_tunnel_addresses()`` proposes free ones, and ``IpServer`` runs the server
(the copy of ``xknx.io.server`` in ``xknx_server``) on the connection's event loop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, cast

from xknx.exceptions import ConversionError, CouldNotParseAddress
from xknx.knxip.knxip_enum import HostProtocol
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.strings import S
from editor_gui.plugins.connection.xknx_server import (
    KNXIPTunnellingServer,
    TunnelInfo,
)

if TYPE_CHECKING:
    from xknx import XKNX

logger = logging.getLogger(__name__)

# The KNXnet/IP port.
DEFAULT_PORT = 3671
# The name the server answers searches with, unless one is set.
DEFAULT_NAME = "XKNX Editor"
# Where it listens: every network interface, or this computer only.
Listen = Literal["all", "localhost"]
_LISTEN_IPS: dict[str, str] = {"all": "0.0.0.0", "localhost": "127.0.0.1"}
# How many tunnel addresses are suggested, and from which device number down.
_SUGGESTED = 2
_SUGGEST_FROM = 250


@dataclass(frozen=True, slots=True)
class IpServerSettings:
    """The settings of the KNXnet/IP server. Off unless the user turns it on: it opens a network
    listener."""

    enabled: bool = False
    port: int = DEFAULT_PORT
    listen: Listen = "all"
    discovery: bool = True
    tcp: bool = True
    tunnel_addresses: tuple[str, ...] = field(default_factory=tuple[str, ...])
    name: str = DEFAULT_NAME

    @property
    def local_ip(self) -> str:
        return _LISTEN_IPS[self.listen]

    def to_settings(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "port": self.port,
            "listen": self.listen,
            "discovery": self.discovery,
            "tcp": self.tcp,
            "tunnel_addresses": list(self.tunnel_addresses),
            "name": self.name,
        }

    @classmethod
    def from_settings(cls, data: object) -> IpServerSettings:
        """The stored settings; a value that is missing or unreadable gets its default."""
        if not isinstance(data, dict):
            return cls()
        fields = cast(dict[str, object], data)
        default = cls()
        port = fields.get("port")
        listen = fields.get("listen")
        addresses = fields.get("tunnel_addresses")
        name = fields.get("name")
        return cls(
            enabled=fields.get("enabled") is True,
            port=port
            if isinstance(port, int) and not isinstance(port, bool) and valid_port(port)
            else default.port,
            listen="localhost" if listen == "localhost" else "all",
            discovery=fields.get("discovery") is not False,
            tcp=fields.get("tcp") is not False,
            tunnel_addresses=tuple(
                str(address)
                for address in cast(list[object], addresses)
                if isinstance(address, str)
            )
            if isinstance(addresses, list)
            else (),
            name=name if isinstance(name, str) and name.strip() else default.name,
        )


def valid_port(port: int) -> bool:
    return 0 < port < 65536


def parse_tunnel_addresses(text: str) -> list[str]:
    """The addresses in ``text``, separated by spaces or commas, as typed."""
    return text.replace(",", " ").split()


def check_tunnel_addresses(
    addresses: Sequence[str],
    interface_address: IndividualAddress | None,
    device_at: Callable[[str], str | None],
) -> list[IndividualAddress]:
    """The tunnel addresses ``addresses`` as individual addresses. Raises ``ValueError`` with the
    reason for one that is none, one twice, the interface's own, one of another line than the
    interface's (``interface_address``; unchecked while unknown) or one of a device of the project
    (``device_at`` names the project's device at an address, None for none)."""
    if not addresses:
        raise ValueError(S.IP_SERVER_NO_ADDRESSES)
    checked: list[IndividualAddress] = []
    for text in addresses:
        try:
            address = IndividualAddress(text.strip())
        except CouldNotParseAddress:
            raise ValueError(
                S.USB_ADDRESS_INVALID.format(address=text.strip())
            ) from None
        if address in checked:
            raise ValueError(S.IP_SERVER_ADDRESS_TWICE.format(address=address))
        if interface_address is not None:
            if address == interface_address:
                raise ValueError(S.IP_SERVER_ADDRESS_INTERFACE.format(address=address))
            # xknx names the parts area.main.line, the last one being the device's
            if (address.area, address.main) != (
                interface_address.area,
                interface_address.main,
            ):
                raise ValueError(
                    S.IP_SERVER_ADDRESS_LINE.format(
                        address=address,
                        line=f"{interface_address.area}.{interface_address.main}",
                    )
                )
        if address.line == 0:
            # the line's coupler
            raise ValueError(S.USB_ADDRESS_INVALID.format(address=address))
        if (name := device_at(str(address))) is not None:
            raise ValueError(
                S.IP_SERVER_ADDRESS_USED.format(address=address, name=name)
            )
        checked.append(address)
    return checked


def suggest_tunnel_addresses(
    interface_address: IndividualAddress | None,
    device_at: Callable[[str], str | None],
    count: int = _SUGGESTED,
) -> list[str]:
    """``count`` free addresses of the interface's line for the tunnels: from device 250 down,
    none of the project's devices and not the interface's own. None while its address is
    unknown."""
    if interface_address is None:
        return []
    found: list[str] = []
    for device in range(_SUGGEST_FROM, 0, -1):
        address = IndividualAddress(
            f"{interface_address.area}.{interface_address.main}.{device}"
        )
        if address != interface_address and device_at(str(address)) is None:
            found.append(str(address))
            if len(found) == count:
                break
    return found


@dataclass(frozen=True, slots=True)
class OpenTunnel:
    """An open tunnel of the server, as listed."""

    channel: int
    client: str
    protocol: str
    address: str

    @classmethod
    def of(cls, tunnel: TunnelInfo) -> OpenTunnel:
        host, port = tunnel.client
        return cls(
            channel=tunnel.communication_channel,
            client=f"{host}:{port}" if host else "",
            protocol="TCP" if tunnel.protocol is HostProtocol.IPV4_TCP else "UDP",
            address=str(tunnel.individual_address),
        )


class IpServer:
    """The KNXnet/IP server in front of the editor's connection: started on the connection's
    event loop when it is open, stopped when it closes."""

    def __init__(self) -> None:
        self._server: KNXIPTunnellingServer | None = None
        # why the last start failed, None when it did not
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int | None:
        """The port it listens on while it runs."""
        return self._server.port if self._server is not None else None

    def tunnels(self) -> list[OpenTunnel]:
        """The open tunnels, by communication channel."""
        if self._server is None:
            return []
        return sorted(
            (OpenTunnel.of(tunnel) for tunnel in self._server.tunnels),
            key=lambda tunnel: tunnel.channel,
        )

    async def start(
        self,
        xknx: XKNX,
        settings: IpServerSettings,
        addresses: Sequence[IndividualAddress],
    ) -> bool:
        """Start the server with ``settings`` on the connection of ``xknx`` (on its event loop);
        whether it runs. Why it does not is ``error``."""
        await self.stop()
        self.error = None
        try:
            server = KNXIPTunnellingServer(
                xknx,
                individual_addresses=list(addresses),
                local_ip=settings.local_ip,
                port=settings.port,
                name=settings.name,
                discovery=settings.discovery,
                tcp=settings.tcp,
            )
            await server.start()
        except (OSError, ConversionError, TypeError) as err:
            self.error = S.IP_SERVER_FAILED.format(error=err)
            logger.warning("KNXnet/IP server could not start: %s", err)
            return False
        self._server = server
        return True

    async def stop(self) -> None:
        """Stop the server: every client is disconnected."""
        server, self._server = self._server, None
        if server is not None:
            await server.stop()

    def fail(self, error: str) -> None:
        """Keep why it does not run (e.g. it may not with this connection)."""
        self.error = error


def with_addresses(
    settings: IpServerSettings, addresses: Sequence[str]
) -> IpServerSettings:
    return replace(settings, tunnel_addresses=tuple(addresses))
