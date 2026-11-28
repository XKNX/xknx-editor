import asyncio
import contextlib
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from concurrent.futures import Future
from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from imgui_bundle import imgui
from xknx import XKNX
from xknx.core.connection_state import XknxConnectionState
from xknx.exceptions import (
    CommunicationError,
    ConfirmationError,
    CouldNotParseAddress,
    RequestResponseError,
)
from xknx.io.connection import ConnectionConfig, ConnectionType
from xknx.io.const import DEFAULT_MCAST_GRP
from xknx.io.gateway_scanner import GatewayDescriptor, GatewayScanner
from xknx.io.self_description import request_description
from xknx.knxip.knxip_enum import KNXMedium
from xknx.telegram import IndividualAddress

from editor_gui.cemi_diagnostics import DiagnosticCEMIHandler
from editor_gui.color import color_u32
from editor_gui.device_info import application_version_text
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.connection.busmonitor import (
    DEFAULT_GATEWAY_PORT,
    BusmonitorConnection,
    rejection_reason,
)
from editor_gui.plugins.connection.configured import (
    ConfiguredConnection,
    host_valid,
    parse_fields,
    parse_parameter,
    unique_name,
)
from editor_gui.plugins.connection.configured_ui import (
    ConfiguredConnectionsDialog,
    connection_label,
)
from editor_gui.plugins.connection.interface import (
    ObservableKNXIPInterfaceThreaded,
    interface_apdu_limit,
)
from editor_gui.plugins.connection.ip_server import (
    IpServer,
    IpServerSettings,
    OpenTunnel,
    check_tunnel_addresses,
    parse_tunnel_addresses,
    suggest_tunnel_addresses,
    valid_port,
)
from editor_gui.plugins.connection.service import BusmonitorState, LinkInfo, LocalDevice
from editor_gui.plugins.connection.strings import S
from editor_gui.plugins.connection.usb import (
    ObservableUSBInterfaceThreaded,
    UsbBusmonitorConnection,
    UsbTarget,
    cancel_hidapi_atexit,
    emi_type_name,
    mask_version_text,
    medium_name,
    parse_rf_domain_address,
    rf_domain_address_text,
    search_usb_interfaces,
    supported_emi_type_names,
    usb_device_name,
)
from editor_gui.plugins.connection.write_guard import (
    parse_protected,
    parse_protected_entry,
    protected_patterns,
    set_protected,
)
from editor_gui.plugins.connection.xknx_usb import (
    USBDeviceInfo,
    USBInterfaceInfo,
)
from editor_gui.programming import (
    DEFAULT_INDIVIDUAL_ADDRESS,
    DeviceOverview,
    ScannedDevice,
)
from editor_gui.settings import load_settings, save_settings
from editor_gui.settings_window import Setting
from editor_gui.strings import S as app_strings
from editor_gui.theme import status_color
from editor_gui.widgets.animation import animation_time
from editor_gui.widgets.checkbox import checkbox, radio_button, wrapped_checkbox
from editor_gui.widgets.combo import begin_combo, end_combo
from editor_gui.widgets.dialog import (
    begin_dialog,
    begin_tool_window,
    dialog_buttons,
    end_dialog,
)
from editor_gui.widgets.dpi import label_column_width, px, px_vec2
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.spinner import loading, spinner
from editor_gui.widgets.tables import (
    begin_data_table,
    data_table_headers,
)
from editor_gui.widgets.text_util import (
    control_hint,
    text_clipped_tooltip,
)
from editor_gui.widgets.tree import collapsing_header
from xknxeditor.download.timeouts import BusTimeouts, bus_timeouts, set_bus_timeouts
from xknxeditor.prod.app_id import parse_app_id
from xknxeditor.proj.core.medium import MEDIUM_RF, format_domain_address

if TYPE_CHECKING:
    from xknxeditor.catalog import ProductSummary

_SETTINGS = "connection"
# The page of the Settings window with the connection settings.
SETTINGS_PAGE = "connection"
# How far the connected gateway's details are indented in the Gateway menu (unscaled pixels).
_DETAILS_INDENT = 10.0
# The waits of the bus exchanges in Settings > Connection (``BusTimeouts`` fields), kept as
# "<name>_timeout", and the seconds they may take.
_TIMEOUTS = ("confirmation", "acknowledgement", "response")
_TIMEOUT_BOUNDS = (1, 60)


def _timeout_seconds(value: object, default: float) -> float:
    """``value`` as a wait in seconds within ``_TIMEOUT_BOUNDS``; ``default`` when it is no
    number (a missing or broken setting)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(min(max(value, _TIMEOUT_BOUNDS[0]), _TIMEOUT_BOUNDS[1]))


def gateway_services(gateway: GatewayDescriptor) -> list[str]:
    """The KNXnet/IP services a gateway offers, as their names."""
    services: list[str] = []
    if gateway.supports_tunnelling:
        services.append("Tunneling")
    if gateway.supports_tunnelling_tcp:
        services.append("TCP Tunneling")
    if gateway.supports_routing:
        services.append("Routing")
    if gateway.supports_secure:
        services.append("Secure")
    return services


# Delay before the one-shot autostart re-scan (macOS Local Network permission grant window).
_AUTOSTART_RETRY_SECONDS = 30.0
# Starting value for the configured controller IP, before any save/scan touches it. Used to tell
# "the user has configured a controller IP" from "it is still the untouched placeholder".
_DEFAULT_CONTROLLER_IP = "192.168.1.1"
# Seconds between the attempts to reopen a lost connection.
_REOPEN_SECONDS = 5.0
# How long a lost connection is left to xknx's own in-place auto-reconnect before a full
# teardown and reconnect is forced (the recovery a manual disconnect/connect does). xknx retries
# every few seconds; a wedged tunnel (e.g. after a heartbeat timeout) never recovers on its own.
_RECONNECT_GRACE_SECONDS = 20.0
# How often an interface that was just downloaded is asked what connection it requires.
_REOPEN_ATTEMPTS = 12
# Seconds between the checks whether the credentials an interface requires are available now.
_CREDENTIALS_CHECK_SECONDS = 2.0


class MissingCredentialsError(ConnectionError):
    """The interface requires KNX IP Secure, and neither the project nor a keyring has the
    credentials."""

    def __init__(self, gateway: GatewayDescriptor | None) -> None:
        super().__init__(S.CONNECT_SECURE_NO_CREDENTIALS)
        self.gateway = gateway


def _progmode_label_width() -> float:
    return label_column_width(
        S.PROGMODE_STATUS,
        S.PROGMODE_MANUFACTURER,
        S.PROGMODE_PRODUCT,
        S.PROGMODE_APPLICATION,
        S.PROGMODE_APP_VERSION,
        S.PROGMODE_MASK,
        S.PROGMODE_SERIAL,
        S.PROGMODE_ORDER,
        S.PROGMODE_HARDWARE,
        minimum=160.0,
    )


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


def scan_addresses(adapters: Iterable[Any] | None = None) -> list[str]:
    """The local IPv4 addresses a gateway search goes out from: those of every network adapter
    (``ifaddr`` adapters), but loopback ones. Link-local (169.254.x.x) ones stay: a gateway
    connected directly to the computer, without a DHCP server, uses AutoIP as the computer
    does."""
    if adapters is None:
        import ifaddr

        adapters = ifaddr.get_adapters()
    found: list[str] = []
    for adapter in adapters:
        for ip in adapter.ips:
            address = ip.ip
            if (
                isinstance(address, str)
                and not address.startswith("127.")
                and address not in found
            ):
                found.append(address)
    return found


async def search_gateways(
    timeout: float,
    addresses: list[str] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
) -> list[GatewayDescriptor]:
    """The KNXnet/IP devices that answer a search within ``timeout`` seconds, each once. The
    search goes out from every local address (``addresses``, :func:`scan_addresses` by
    default): the one the system routes multicast through alone may be a virtual network (WSL,
    Hyper-V, VPN) without any interface. ``on_error`` hears of a search that failed."""
    xknx = XKNX()
    if addresses is None:
        addresses = scan_addresses()
    scans = [
        GatewayScanner(xknx, local_ip=address, timeout_in_seconds=timeout).scan()
        for address in addresses
    ] or [GatewayScanner(xknx, timeout_in_seconds=timeout).scan()]
    found: dict[tuple[str, int], GatewayDescriptor] = {}
    for result in await asyncio.gather(*scans, return_exceptions=True):
        if isinstance(result, BaseException):
            if on_error is not None:
                on_error(result)
            continue
        for gateway in result:
            found.setdefault((gateway.ip_addr, gateway.port), gateway)
    return list(found.values())


async def find_by_serial(
    serial: bytes,
    tries: int = 6,
    search: Callable[[float], Awaitable[list[GatewayDescriptor]]] = search_gateways,
) -> str | None:
    """The IP address of the KNXnet/IP device with ``serial``: up to ``tries`` searches of a
    second each. None when it does not answer."""
    for _ in range(tries):
        for gateway in await search(1.0):
            if bytes.fromhex(gateway.serial_number.replace(":", "")) == serial:
                return gateway.ip_addr
    return None


def split_endpoint(address: str) -> tuple[str, int]:
    """The host and port of an interface address as entered: ``host`` (port 3671) or
    ``host:port``."""
    text = address.strip()
    host, colon, port = text.rpartition(":")
    if colon and host and ":" not in host and port.isdigit() and 0 < int(port) < 65536:
        return host, int(port)
    return text, DEFAULT_GATEWAY_PORT


def join_endpoint(host: str, port: int) -> str:
    """An interface address as entered: the host, with ``:port`` when not 3671."""
    return host if port == DEFAULT_GATEWAY_PORT else f"{host}:{port}"


# The tunnelling transports, by their persisted/MCP name. Routing is handled on its own.
_TUNNEL_TYPES = (ConnectionType.TUNNELING, ConnectionType.TUNNELING_TCP)
_CONNECTION_TYPE_NAMES: dict[str, ConnectionType] = {
    "tunneling": ConnectionType.TUNNELING,
    "tunneling_tcp": ConnectionType.TUNNELING_TCP,
    "routing": ConnectionType.ROUTING,
}


def parse_connection_type(name: str) -> ConnectionType | None:
    """The ``ConnectionType`` for an MCP name ("tunneling"/"tunneling_tcp"/"routing"), or None."""
    return _CONNECTION_TYPE_NAMES.get(name.strip().lower())


def _row_button(label: str) -> bool:
    """A button beside the previous item, or below it where the row has no room (a narrow
    screen); returns whether it was clicked."""
    width = imgui.calc_text_size(label.split("##")[0]).x + 2 * (
        imgui.get_style().frame_padding.x
    )
    imgui.same_line()
    if imgui.get_content_region_avail().x < width:
        imgui.new_line()
    return bool(imgui.button(label))


def gateway_label(gateway: GatewayDescriptor) -> str:
    """A discovered gateway as the lists show it: its name, else its individual address (a
    gateway may answer the search without a name), then its IP address."""
    primary = gateway.name.strip() or (
        str(gateway.individual_address) if gateway.individual_address else ""
    )
    return f"{primary}  ({gateway.ip_addr})" if primary else gateway.ip_addr


def _usb_target_label(target: UsbTarget) -> str:
    """A KNX USB interface in the USB Interface setting: its name, and its serial number to tell
    identical interfaces apart."""
    if target.serial_number:
        return f"{target.name} ({target.serial_number})"
    return target.name


def connection_type_name(connection_type: ConnectionType) -> str:
    """The MCP name of a ``ConnectionType`` (the secure variants map to their plain transport)."""
    if connection_type in (ConnectionType.ROUTING, ConnectionType.ROUTING_SECURE):
        return "routing"
    if connection_type in (
        ConnectionType.TUNNELING_TCP,
        ConnectionType.TUNNELING_TCP_SECURE,
    ):
        return "tunneling_tcp"
    return "tunneling"


def default_transport(gateway: GatewayDescriptor) -> ConnectionType:
    """The connection type to use for ``gateway`` when none is chosen: TCP tunnelling when it is
    offered (ETS prefers it for v2 interfaces), else UDP tunnelling, else routing."""
    if gateway.supports_tunnelling_tcp:
        return ConnectionType.TUNNELING_TCP
    if gateway.supports_tunnelling:
        return ConnectionType.TUNNELING
    return ConnectionType.ROUTING


def tunnel_host(parameter: str) -> str | None:
    """The address a Bus Connection (a ``<BusAccess>`` ``Parameter``) tunnels to, for a
    KNXnet/IP tunnelling one (``Type=IpTunneling;HostAddress=…;IpPort=…``): its host, with
    ``:port`` when not 3671; None for any other kind."""
    tunnel = parse_parameter(parameter)
    return join_endpoint(tunnel.host, tunnel.port) if tunnel is not None else None


def tunnel_type(parameter: str) -> ConnectionType:
    """The tunnelling a Bus Connection (a ``<BusAccess>`` ``Parameter``) asks for: over TCP for
    ``ProtocolType=Tcp``, else over UDP."""
    if parse_fields(parameter).get("protocoltype", "").lower() == "tcp":
        return ConnectionType.TUNNELING_TCP
    return ConnectionType.TUNNELING


# The connection settings a connection through another interface replaces while it is open:
# the gateway's address, the connection type, the scanned gateway, the routing multicast group
# and the USB interface.
_KeptSettings = tuple[
    str, ConnectionType, "GatewayDescriptor | None", str, "UsbTarget | None"
]


class _OperationConnector:
    """The connection plugin as the connection service's ``OperationConnector``."""

    def __init__(self, plugin: "ConnectionPlugin") -> None:
        self._plugin = plugin

    def open(self) -> Future[None]:
        return self._plugin.open_for_operation()

    def release(self) -> None:
        self._plugin.release_operation()

    def opened_for_operations(self) -> bool:
        return self._plugin.opened_for_operations

    def submit(self, fn: Callable[[], None]) -> None:
        self._plugin.on_main_thread(fn)


class ConnectionPlugin:
    name = "connection"
    # Set in __init__ (see there); the class has them for a plugin made without it (tests).
    _reopen_when_idle = False
    _operation_open = False
    _operation_pending: Future[None] | None = None
    _operation_restore: _KeptSettings | None = None
    _use_project_bus = False
    _project_bus_seen: tuple[int, int] | None = None
    _project_bus: tuple[str, ConnectionType] | None = None
    _project_restore: _KeptSettings | None = None
    _configured: tuple[ConfiguredConnection, ...] = ()
    _configured_dialog: ConfiguredConnectionsDialog | None = None
    _configured_in_settings = False

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        api.connection.set_logger(Logger(api.log, "connection"))
        self._log = Logger(api.log, "connection")
        self._state = ConnectionState.DISCONNECTED
        self._error_message: str | None = None
        self._controller_ip: str = _DEFAULT_CONTROLLER_IP
        self._connection_type: ConnectionType = ConnectionType.TUNNELING
        self._multicast_group: str = DEFAULT_MCAST_GRP
        self._selected_gateway: GatewayDescriptor | None = None
        # The gateway the user last connected to; preferred on startup auto-connect.
        self._preferred_gateway_ip: str | None = None
        # The KNX USB interface connected through instead of a gateway (None: a gateway).
        self._usb_target: UsbTarget | None = None
        # The individual addresses of the USB interfaces that forget theirs when unplugged
        # (UsbTarget.keeps_address), by UsbTarget.key: set again on connect.
        self._usb_addresses: dict[str, str] = {}
        # The USB Interface dialog: whether it is open, the address in its field, the
        # address the next connect writes (set_usb_interface_address), whether that or an address
        # check runs, and the outcome of the last one.
        self._usb_interface_open = False
        self._usb_interface_shown = False
        self._usb_address_text = ""
        self._usb_address_write: IndividualAddress | None = None
        self._usb_address_busy = False
        self._usb_address_checking = False
        self._usb_address_error: str | None = None
        self._usb_address_free: tuple[str, bool] | None = None
        # The same for the domain address of a KNX RF interface (set_usb_interface_domain_address).
        self._usb_domain_text = ""
        self._usb_domain_busy = False
        self._usb_domain_error: str | None = None
        # Local device access (Settings > Connection, the Gateway menu, the USB Interface
        # dialog): a USB connection reaches the interface's own device through its cEMI
        # Transport Layer instead of the bus; whether the dialog switches it, and why it failed.
        self._usb_local_device = False
        self._usb_local_busy = False
        self._usb_local_error: str | None = None
        # The KNXnet/IP server in front of a USB connection ("USB to IP bridge"): its settings as
        # kept (off unless the user turns it on), the server, and the fields of its section of
        # the USB Interface dialog as typed until applied, with why they were not.
        self._ip_settings = IpServerSettings()
        self._ip_server = IpServer()
        self._ip_busy = False
        self._ip_port_text = ""
        self._ip_addresses_text = ""
        self._ip_name_text = ""
        self._ip_draft: IpServerSettings | None = None
        self._ip_error: str | None = None
        # Whether the editor looks for interfaces at start and connects to one (Settings >
        # Connection); off, it connects only when the user asks.
        self._connect_at_startup = True
        # This start only (``--no-connect``): no connection at start, whatever the setting says.
        self.skip_startup_connect = False
        # Block Bus Writes as stored (Settings > Connection), and this start only
        # (``--no-bus-writes``), which keeps it on whatever the setting says.
        self._block_bus_writes_setting = False
        self._block_bus_writes_forced = False
        # Protected Group Addresses as typed in the Settings window, until applied.
        self._protected_draft: str | None = None
        # "Use the Project's Bus Connection" (Settings > Connection): the open project's backbone
        # Bus Connection (a KNXnet/IP tunnel) replaces the gateway set here while the project is
        # open. Set before the saved settings are read, which restore it.
        self._use_project_bus = False
        self._panels: list[PanelDefinition] = []
        self._load_saved_settings()

        self._xknx: XKNX | None = None
        self._interface: ObservableKNXIPInterfaceThreaded | None = None
        # What the connected USB interface reported about itself.
        self._usb_info: USBInterfaceInfo | None = None
        # The KNX USB interfaces plugged in, as the last scan found them.
        self._usb_devices: list[USBDeviceInfo] = []
        self._gateway_info: GatewayDescriptor | None = None
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_thread: threading.Thread | None = None
        # Unregisters the lost/re-established callback of the open connection.
        self._unregister_link_state: Callable[[], None] | None = None
        # The configuration of the open connection, and whether its link is up.
        self._config: ConnectionConfig | None = None
        self._link_up = False
        self._watching_lost = False
        # When the link first went down in the current outage (reset on reconnect), so the grace
        # before a forced reconnect is measured per outage, not from the first ever loss.
        self._lost_since: float | None = None
        # The interface a connection failed to for missing credentials, and the next check.
        self._awaiting_credentials: GatewayDescriptor | None = None
        self._retry_on_credentials = False
        self._credentials_check_at = 0.0
        # The connection made at startup: missing credentials are no error while no project has
        # opened yet (the one being opened may have them). The error then waits for a project.
        self._startup_connect = False
        self._credentials_error: str | None = None
        # Counts the connects and disconnects the user asked for; a reopen of an older one stops.
        self._session = 0
        # The bus monitor connection while it runs (the normal connection is closed then).
        self._busmonitor: BusmonitorConnection | UsbBusmonitorConnection | None = None
        api.connection.set_busmonitor_controller(
            start=self.start_busmonitor,
            stop=self.stop_busmonitor,
            supported=lambda: not self.is_routing and not self.local_device_chosen,
            local_device=lambda: self.local_device_chosen,
        )
        api.connection.set_reopen_controller(self.reopen)
        api.connection.set_local_address_writer(self.set_usb_interface_address)
        api.connection.set_device_ip_finder(
            lambda serial: asyncio.run_coroutine_threadsafe(
                find_by_serial(serial), self._ensure_async_loop()
            )
        )

        self._gateways: list[GatewayDescriptor] = []
        # A manually entered interface's TCP support, from a one-shot description probe: the endpoint
        # probed and its result (True/False, or None while unknown / still checking).
        self._manual_probe_ip = ""
        self._manual_tcp: bool | None = None
        # The project revision the connection's Data Secure tables were built from.
        self._data_secure_revision = -1
        self._scanning = False
        # One-shot re-scan after autostart found nothing: on macOS the first discovery triggers the
        # "Local Network" permission prompt and returns no gateways; a delayed retry picks them up
        # once the user has allowed it. Guarded so it fires at most once per autostart.
        self._autostart_retry_done = False

        # "Devices in programming mode" window state (opened from the Diagnostics menu).
        self._progmode_open = False
        # Opens the Settings window on a page (set by the app).
        self.open_settings: Callable[[str], None] | None = None
        self._progmode_scanning = False
        self._progmode_scanned = (
            False  # a scan has completed at least once (empty vs. not-yet-run)
        )
        self._progmode_results: list[ScannedDevice] = []
        self._progmode_error: str | None = (
            None  # last scan's failure, shown in the window
        )

        # A connection opened for online operations while none was open: through the backbone's
        # Bus Connection, else the one chosen in "Select Connection". How many operations use
        # it, whether it is open, the open() all of them wait for, the connection settings it
        # replaces while it is open, the name it is opened as, and the dialog with the failed
        # attempts.
        self._operation_users = 0
        self._operation_open = False
        self._operation_pending: Future[None] | None = None
        self._operation_restore: _KeptSettings | None = None
        # Changed connection settings wait for the running bus operation before the open
        # connection opens anew with them.
        self._reopen_when_idle = False
        # The project state "Use the Project's Bus Connection" was last looked at for
        # (generation, revision), the tunnel applied (host, connection type) and the settings it
        # replaces. The setting itself is read with the saved settings above.
        self._project_bus_seen: tuple[int, int] | None = None
        self._project_bus: tuple[str, ConnectionType] | None = None
        self._project_restore: _KeptSettings | None = None
        self._operation_name = ""
        self._select_open = False
        self._select_shown = False
        self._select_failures: list[tuple[str, str]] = []
        self._select_ip = ""
        # Configured Connections: the interfaces entered by their address (see configured.py),
        # their dialog, and whether it was opened from the Settings window (it is drawn there).
        self._configured: tuple[ConfiguredConnection, ...] = ()
        self._configured_dialog = ConfiguredConnectionsDialog(
            self._apply_configured,
            notify=api.notify or (lambda _text: None),
            warn=api.tell_refused,
        )
        self._configured_in_settings = False
        api.connection.operation_connector = _OperationConnector(self)

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED

    @property
    def controller_ip(self) -> str:
        return self._controller_ip

    @property
    def multicast_group(self) -> str:
        return self._multicast_group

    @property
    def is_routing(self) -> bool:
        return (
            self._usb_target is None and self._connection_type == ConnectionType.ROUTING
        )

    @property
    def is_usb(self) -> bool:
        """Whether the connection goes through a KNX USB interface instead of a gateway."""
        return self._usb_target is not None

    @property
    def usb_target(self) -> UsbTarget | None:
        """The KNX USB interface connected (or to connect) through, None for a gateway."""
        return self._usb_target

    @property
    def usb_devices(self) -> list[USBDeviceInfo]:
        """The KNX USB interfaces found by the most recent scan."""
        return list(self._usb_devices)

    @property
    def usb_info(self) -> USBInterfaceInfo | None:
        """What the connected KNX USB interface reported about itself, its bus connection as it
        is now."""
        if isinstance(self._interface, ObservableUSBInterfaceThreaded):
            return self._interface.usb_info() or self._usb_info
        return self._usb_info

    @property
    def local_device_chosen(self) -> bool:
        """Whether the connection goes through a KNX USB interface to its own device (local
        device access) instead of to the bus, as chosen."""
        return self._usb_target is not None and self._usb_local_device

    @property
    def usb_local_device(self) -> bool:
        """The choice of local device access for USB connections (Settings > Connection)."""
        return self._usb_local_device

    @property
    def local_device_connected(self) -> bool:
        """Whether the open connection reaches only the USB interface's own device."""
        info = self.usb_info
        return (
            self.connected
            and self._usb_target is not None
            and info is not None
            and info.transport_layer
        )

    @property
    def usb_local_busy(self) -> bool:
        """Whether the connection is being switched to or from local device access."""
        return self._usb_local_busy

    @property
    def connection_type(self) -> str:
        """The configured transport as an MCP name: "tunneling", "tunneling_tcp", "routing" or
        "usb"."""
        if self._usb_target is not None:
            return "usb"
        return connection_type_name(self._connection_type)

    @property
    def scanning(self) -> bool:
        """Whether a gateway discovery scan is currently running."""
        return self._scanning

    @property
    def gateways(self) -> list[GatewayDescriptor]:
        """The gateways found by the most recent scan."""
        return list(self._gateways)

    def connect_to_gateway_by_ip(
        self, ip: str, connection_type: ConnectionType | None = None
    ) -> bool:
        """Connect to a previously discovered gateway by IP, over ``connection_type`` (its best
        when None). Returns ``False`` if none matches."""
        gateway = next((g for g in self._gateways if g.ip_addr == ip), None)
        if gateway is None:
            return False
        self.connect_to_gateway(gateway, connection_type=connection_type)
        return True

    # --- configured connections ------------------------------------------------------------

    @property
    def configured_connections(self) -> list[ConfiguredConnection]:
        """The configured connections (Gateway menu > Configured Connections…), in order."""
        return list(self._configured)

    def set_configured_connections(
        self, connections: Iterable[ConfiguredConnection]
    ) -> None:
        """Replace the configured connections, and keep them for the next start."""
        self._configured = tuple(connections)
        self._log.info("configured connections", count=len(self._configured))
        self._save_current_settings()

    def add_configured_connection(
        self, connection: ConfiguredConnection
    ) -> ConfiguredConnection:
        """Add a connection (an empty name: the next free default one); the one added. Raises
        ``ValueError`` for an address no interface can have."""
        if not host_valid(connection.host):
            raise ValueError(S.CONFIGURED_HOST_INVALID)
        if not connection.name:
            connection = replace(
                connection,
                name=unique_name(
                    S.CONFIGURED_DEFAULT_NAME, (c.name for c in self._configured)
                ),
            )
        self.set_configured_connections([*self._configured, connection])
        return connection

    def project_connection(self) -> ConfiguredConnection | None:
        """The open project's connection: its backbone's Bus Connection, when that is a
        KNXnet/IP tunnel; None otherwise."""
        project = self._api.project
        bus = project.backbone_bus_connection() if project.is_open else None
        return parse_parameter(bus[0]) if bus is not None else None

    def set_project_connection(self, connection: ConfiguredConnection | None) -> None:
        """Make ``connection`` the open project's connection (its backbone's Bus Connection;
        None: Automatic), one undo step."""
        if not self._api.project.is_open:
            return
        self._api.project.set_backbone_bus_connection(
            connection.to_parameter() if connection is not None else ""
        )

    def open_configured_connections(self, *, in_settings: bool = False) -> None:
        """Open the Configured Connections dialog (``in_settings``: from the Settings window,
        which then draws it)."""
        dialog = self._configured_dialog
        if dialog is None or dialog.is_open:
            return
        self._configured_in_settings = in_settings
        dialog.open(
            self._configured,
            self.project_connection(),
            project_open=self._api.project.is_open,
        )

    def _apply_configured(
        self,
        connections: list[ConfiguredConnection],
        project: ConfiguredConnection | None,
        changed: bool,
    ) -> None:
        """The Configured Connections dialog's OK: the connections, and the project's
        connection when its marking changed."""
        self.set_configured_connections(connections)
        if changed:
            self.set_project_connection(project)

    def tunnel_type_of(self, connection: ConfiguredConnection) -> ConnectionType:
        """The tunnelling a configured connection uses: its protocol; for Automatic TCP when a
        discovered gateway at its address offers it, else UDP."""
        if connection.protocol == "Tcp":
            return ConnectionType.TUNNELING_TCP
        if connection.protocol == "Udp":
            return ConnectionType.TUNNELING
        gateway = next(
            (
                g
                for g in self._gateways
                if g.ip_addr == connection.host and g.port == connection.port
            ),
            None,
        )
        if gateway is not None and gateway.supports_tunnelling_tcp:
            return ConnectionType.TUNNELING_TCP
        return ConnectionType.TUNNELING

    def connect_to_configured(self, connection: ConfiguredConnection) -> None:
        """Connect through a configured connection, as the connection the user chose."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        self._controller_ip = connection.endpoint
        self.connect(connection_type=self.tunnel_type_of(connection))

    def _uses_nat(self, host: str, port: int) -> bool:
        """Whether the tunnel to ``host``:``port`` goes through NAT: a configured connection or
        the project's connection to it says so."""
        project = self.project_connection()
        known = [*self._configured, *([project] if project is not None else [])]
        target = ConfiguredConnection("", host, port)
        return any(
            c.use_nat and c.protocol != "Tcp" and c.same_interface(target)
            for c in known
        )

    def configure(
        self,
        controller_ip: str,
        multicast_group: str,
        routing: bool,
        *,
        connection_type: ConnectionType | None = None,
    ) -> None:
        """Apply and persist connection settings (take effect on next connect). ``connection_type``
        sets the transport directly; without it, ``routing`` chooses routing or UDP tunnelling."""
        self._controller_ip = controller_ip.strip() or self._controller_ip
        self._multicast_group = multicast_group.strip() or self._multicast_group
        self._connection_type = connection_type or (
            ConnectionType.ROUTING if routing else ConnectionType.TUNNELING
        )
        self._save_current_settings()
        self._log.info(
            "connection settings saved",
            controller_ip=self._controller_ip,
            connection_type=self._connection_type.name.lower(),
        )

    def _ensure_async_loop(self) -> asyncio.AbstractEventLoop:
        if self._async_loop is not None and self._async_loop.is_running():
            return self._async_loop

        loop_ready = threading.Event()

        def run_loop() -> None:
            self._async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._async_loop)
            loop_ready.set()
            self._async_loop.run_forever()

        self._async_thread = threading.Thread(
            target=run_loop, daemon=True, name="KNX-Async"
        )
        self._async_thread.start()
        loop_ready.wait()
        return self._async_loop  # type: ignore

    def _run_async(self, coro: Coroutine[Any, Any, None]) -> None:
        loop = self._ensure_async_loop()
        asyncio.run_coroutine_threadsafe(coro, loop)

    def _load_saved_settings(self) -> None:
        data = load_settings(_SETTINGS)
        ip = data.get("controller_ip")
        if isinstance(ip, str) and ip:
            self._controller_ip = ip
        mcast = data.get("multicast_group")
        if isinstance(mcast, str) and mcast:
            self._multicast_group = mcast
        ctype = data.get("connection_type")
        if ctype is not None:
            with contextlib.suppress(ValueError):
                self._connection_type = ConnectionType(ctype)
        pref = data.get("preferred_gateway_ip")
        if isinstance(pref, str) and pref:
            self._preferred_gateway_ip = pref
        self._usb_target = UsbTarget.from_settings(data.get("usb_interface"))
        self._usb_local_device = data.get("usb_local_device") is True
        self._ip_settings = IpServerSettings.from_settings(data.get("ip_server"))
        addresses = data.get("usb_interface_addresses")
        if isinstance(addresses, dict):
            self._usb_addresses = {
                str(key): str(value)
                for key, value in cast(dict[object, object], addresses).items()
            }
        self._api.connection.use_serial_number = bool(
            data.get("serial_number_downloads", True)
        )
        self._connect_at_startup = bool(data.get("connect_at_startup", True))
        self._use_project_bus = bool(data.get("use_project_bus_connection", False))
        stored = data.get("configured_connections")
        if isinstance(stored, list):
            self._configured = tuple(
                connection
                for text in cast(list[object], stored)
                if isinstance(text, str)
                and (connection := parse_parameter(text)) is not None
            )
        self._block_bus_writes_setting = bool(data.get("block_bus_writes", False))
        self._apply_block_bus_writes()
        self._load_protected(str(data.get("protected_group_addresses", "")))
        defaults = BusTimeouts()
        set_bus_timeouts(
            BusTimeouts(
                **{
                    name: _timeout_seconds(
                        data.get(f"{name}_timeout"), getattr(defaults, name)
                    )
                    for name in _TIMEOUTS
                }
            )
        )

    def _save_current_settings(self) -> None:
        # The settings of their own, not those a connection through the project's Bus
        # Connection or one opened for operations puts in place while it is open.
        kept = self._project_restore or self._operation_restore
        if kept is not None:
            controller_ip, connection_type, _gateway, multicast_group, usb_target = kept
        else:
            controller_ip, connection_type, multicast_group, usb_target = (
                self._controller_ip,
                self._connection_type,
                self._multicast_group,
                self._usb_target,
            )
        save_settings(
            _SETTINGS,
            {
                "controller_ip": controller_ip,
                "multicast_group": multicast_group,
                "connection_type": connection_type.value,
                "preferred_gateway_ip": self._preferred_gateway_ip,
                "usb_interface": (
                    usb_target.to_settings() if usb_target is not None else None
                ),
                "usb_interface_addresses": dict(self._usb_addresses),
                "usb_local_device": self._usb_local_device,
                "ip_server": self._ip_settings.to_settings(),
                "serial_number_downloads": self._api.connection.use_serial_number,
                "connect_at_startup": self._connect_at_startup,
                "use_project_bus_connection": self._use_project_bus,
                "configured_connections": [c.to_parameter() for c in self._configured],
                "block_bus_writes": self._block_bus_writes_setting,
                "protected_group_addresses": " ".join(protected_patterns()),
                **{
                    f"{name}_timeout": getattr(bus_timeouts(), name)
                    for name in _TIMEOUTS
                },
            },
        )

    def connect(
        self,
        *,
        connection_type: ConnectionType | None = None,
        startup: bool = False,
    ) -> None:
        """Manual IP connect over ``connection_type`` (UDP or TCP tunnelling); without one, the
        saved tunnelling preference, else UDP. ``startup``: the connection made at startup."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        if self._busmonitor_blocks("connect"):
            return
        self._chosen_connection(startup)
        if connection_type in _TUNNEL_TYPES:
            self._connection_type = connection_type
        elif self._connection_type not in _TUNNEL_TYPES:
            self._connection_type = ConnectionType.TUNNELING
        self._selected_gateway = None
        self._usb_target = None
        self._session += 1
        self._retry_on_credentials = False
        self._startup_connect = startup
        self._credentials_error = None
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    def connect_to_gateway(
        self,
        gateway: GatewayDescriptor,
        *,
        connection_type: ConnectionType | None = None,
        startup: bool = False,
    ) -> None:
        """Connect to a scanned gateway over ``connection_type`` (one the gateway offers); without
        one, TCP tunnelling when available, else UDP tunnelling, else routing. ``startup``: the
        connection made at startup."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        if self._busmonitor_blocks("connect"):
            return
        self._chosen_connection(startup)
        self._connection_type = connection_type or default_transport(gateway)
        if self._connection_type == ConnectionType.ROUTING:
            self._multicast_group = gateway.multicast_address or DEFAULT_MCAST_GRP
        else:
            self._controller_ip = join_endpoint(gateway.ip_addr, gateway.port)
        self._selected_gateway = gateway
        self._preferred_gateway_ip = gateway.ip_addr
        self._usb_target = None
        self._session += 1
        self._retry_on_credentials = False
        self._startup_connect = startup
        self._credentials_error = None
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    def connect_to_usb(
        self,
        device: USBDeviceInfo | UsbTarget,
        *,
        startup: bool = False,
        local_device: bool | None = None,
    ) -> None:
        """Connect through a KNX USB interface. ``startup``: the connection made at startup.
        ``local_device`` chooses local device access (kept as the setting is); None connects as
        chosen."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        if self._busmonitor_blocks("connect"):
            return
        self._chosen_connection(startup)
        self._usb_target = (
            device if isinstance(device, UsbTarget) else UsbTarget.of(device)
        )
        if local_device is not None:
            self._usb_local_device = local_device
        self._selected_gateway = None
        self._session += 1
        self._retry_on_credentials = False
        self._startup_connect = startup
        self._credentials_error = None
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    def connect_to_usb_by_serial(
        self, serial_number: str, *, local_device: bool | None = None
    ) -> bool:
        """Connect through the plugged-in KNX USB interface with ``serial_number`` (the first one
        found when empty), with ``local_device`` as in ``connect_to_usb``. ``False`` if the last
        scan found none that matches."""
        device = next(
            (
                d
                for d in self._usb_devices
                if not serial_number or d.serial_number == serial_number
            ),
            None,
        )
        if device is None:
            return False
        self.connect_to_usb(device, local_device=local_device)
        return True

    @property
    def usb_address_busy(self) -> bool:
        """Whether the connected USB interface's individual address is being set."""
        return self._usb_address_busy

    def set_usb_interface_address(self, text: str) -> Future[None]:
        """Set the individual address of the connected KNX USB interface to ``text``: the
        connection is opened again and sets it on the interface, which keeps it. Nothing is written
        when the interface has that address already. Raises ``ValueError`` with the reason when it
        can't start; the future fails with the reason when the address could not be set, and the
        interface is then connected again as it is."""
        address = _parse_individual_address(text)
        if not self.connected or self._usb_target is None:
            raise ValueError(S.USB_NOT_CONNECTED_HERE)
        if self._usb_address_busy or self._usb_address_checking:
            raise ValueError(S.USB_ADDRESS_BUSY)
        if self._usb_domain_busy:
            raise ValueError(S.USB_DOMAIN_ADDRESS_BUSY)
        if self._usb_local_busy:
            raise ValueError(S.USB_LOCAL_DEVICE_BUSY)
        self._usb_address_error = None
        self._usb_address_free = None
        info = self.usb_info
        if info is not None and info.individual_address == address:
            unchanged: Future[None] = Future()
            unchanged.set_result(None)
            return unchanged
        self._usb_address_busy = True
        return asyncio.run_coroutine_threadsafe(
            self._write_usb_address(self._session, address), self._ensure_async_loop()
        )

    async def _write_usb_address(
        self, session: int, address: IndividualAddress
    ) -> None:
        """Open the USB connection again with ``address`` set on the interface (see
        ``_usb_address_to_set``); when that fails, open it again without."""
        try:
            self._usb_address_write = address
            error = await self._reopen_usb_writing(session)
            if error is None:
                self._log.info(
                    "USB interface individual address set", address=str(address)
                )
                return
            self._usb_address_error = S.USB_ADDRESS_FAILED.format(error=error)
            raise ConnectionError(self._usb_address_error)
        finally:
            self._usb_address_write = None
            self._usb_address_busy = False

    async def _reopen_usb_writing(self, session: int) -> str | None:
        """Open the USB connection again, writing the individual address ``_usb_address_write``
        holds on connect; ``None`` when it is connected, else the reason it is not, and then it is
        opened again as the interface is, without writing."""
        await self._disconnect_async(reopen=True)
        self._api.connection.dispatch_link_state(False)
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        try:
            await self._connect_async(reopen=True)
        finally:
            self._usb_address_write = None
        if self.connected:
            return None
        if session == self._session:
            self._run_async(self._reopen(session))
        return self._error_message or ""

    def set_usb_local_device(self, local_device: bool) -> Future[None]:
        """Choose local device access for USB connections (kept as the setting is). While a USB
        interface is connected the connection is opened again that way; when that fails, it is
        opened again as it was, the choice unchanged, and the future fails with the reason.
        Raises ``ValueError`` with the reason when it can't start."""
        if (
            self._usb_address_busy
            or self._usb_address_checking
            or self._usb_domain_busy
            or self._usb_local_busy
        ):
            raise ValueError(S.USB_LOCAL_DEVICE_BUSY)
        self._usb_local_error = None
        info = self.usb_info
        if (
            not self.connected
            or self._usb_target is None
            or (info is not None and info.transport_layer == local_device)
        ):
            if local_device != self._usb_local_device:
                self._usb_local_device = local_device
                self._save_current_settings()
            unchanged: Future[None] = Future()
            unchanged.set_result(None)
            return unchanged
        self._usb_local_busy = True
        return asyncio.run_coroutine_threadsafe(
            self._switch_usb_local_device(self._session, local_device),
            self._ensure_async_loop(),
        )

    async def _switch_usb_local_device(self, session: int, local_device: bool) -> None:
        """Open the USB connection again with local device access on or off; when that fails,
        open it again as it was."""
        previous = self._usb_local_device
        try:
            self._usb_local_device = local_device
            error = await self._reopen_usb_writing(session)
            if error is None:
                self._save_current_settings()
                self._log.info("local device access", on=local_device)
                return
            # the reopen started on the failure uses the choice as it was
            self._usb_local_device = previous
            self._usb_local_error = S.USB_LOCAL_DEVICE_FAILED.format(error=error)
            raise ConnectionError(self._usb_local_error)
        finally:
            self._usb_local_busy = False

    # --- the KNXnet/IP server ---------------------------------------------------

    @property
    def ip_server_settings(self) -> IpServerSettings:
        """The settings of the KNXnet/IP server, as kept."""
        return self._ip_settings

    @property
    def ip_server_running(self) -> bool:
        return self._ip_server.running

    @property
    def ip_server_port(self) -> int | None:
        """The port the KNXnet/IP server listens on while it runs."""
        return self._ip_server.port

    @property
    def ip_server_error(self) -> str | None:
        """Why the KNXnet/IP server, turned on, does not run (None: it does, or it is off)."""
        return self._ip_server.error if self._ip_settings.enabled else None

    @property
    def ip_server_busy(self) -> bool:
        """Whether the KNXnet/IP server is being started or stopped for new settings."""
        return self._ip_busy

    def ip_server_tunnels(self) -> list[OpenTunnel]:
        """The open tunnels of the KNXnet/IP server."""
        return self._ip_server.tunnels()

    def ip_server_refusal(self) -> str | None:
        """Why the KNXnet/IP server can't run with the connection chosen: only in front of a KNX
        USB interface's connection to the bus. None when it can."""
        if self._usb_target is None:
            return S.IP_SERVER_NEEDS_USB
        if self._usb_local_device:
            return S.IP_SERVER_NOT_LOCAL
        return None

    def _project_device_at(self, address: str) -> str | None:
        """The name of the open project's device at ``address``, None for none."""
        project = self._api.project
        if not project.is_open:
            return None
        device = project.find_device_by_address(address)
        return device.name if device is not None else None

    def _interface_address(self) -> IndividualAddress | None:
        info = self.usb_info
        return info.individual_address if info is not None else None

    def suggest_ip_server_addresses(self) -> list[str]:
        """Free addresses of the interface's line for the tunnels (none while its individual
        address is unknown)."""
        return suggest_tunnel_addresses(
            self._interface_address(), self._project_device_at
        )

    def check_ip_server_settings(
        self, settings: IpServerSettings
    ) -> list[IndividualAddress]:
        """The tunnel addresses of ``settings``. Raises ``ValueError`` with the reason for a port
        that is none, or tunnel addresses that can't be used (see ``check_tunnel_addresses``)."""
        if not valid_port(settings.port):
            raise ValueError(S.IP_SERVER_PORT_INVALID.format(port=settings.port))
        return check_tunnel_addresses(
            settings.tunnel_addresses,
            self._interface_address(),
            self._project_device_at,
        )

    def configure_ip_server(self, settings: IpServerSettings) -> Future[None]:
        """Keep ``settings`` of the KNXnet/IP server (checked when it is turned on) and, while
        connected, start it again with them or stop it. Raises ``ValueError`` with the reason
        when they can't be used; the future fails with the reason when it does not start."""
        if self._ip_busy:
            raise ValueError(S.IP_SERVER_BUSY)
        if settings.enabled:
            self.check_ip_server_settings(settings)
        elif not valid_port(settings.port):
            raise ValueError(S.IP_SERVER_PORT_INVALID.format(port=settings.port))
        changed = settings != self._ip_settings
        self._ip_settings = settings
        if changed:
            self._save_current_settings()
            self._log.info(
                "KNXnet/IP server settings",
                enabled=settings.enabled,
                port=settings.port,
                listen=settings.listen,
                discovery=settings.discovery,
                tcp=settings.tcp,
                tunnel_addresses=" ".join(settings.tunnel_addresses),
            )
        if not self.connected or (
            not changed and settings.enabled == self._ip_server.running
        ):
            done: Future[None] = Future()
            done.set_result(None)
            return done
        self._ip_busy = True
        return asyncio.run_coroutine_threadsafe(
            self._restart_ip_server(), self._ensure_async_loop()
        )

    async def _restart_ip_server(self) -> None:
        """Start the KNXnet/IP server again with its settings, or stop it when it is off."""
        try:
            await self._ip_server.stop()
            await self._start_ip_server()
            if self._ip_settings.enabled and not self._ip_server.running:
                raise ConnectionError(self._ip_server.error or "")
        finally:
            self._ip_busy = False

    async def _start_ip_server(self) -> None:
        """Start the KNXnet/IP server on the open connection, when it is on and can run; why it
        does not is its ``error``."""
        settings = self._ip_settings
        xknx = self._xknx
        if not settings.enabled or xknx is None:
            return
        if (reason := self.ip_server_refusal()) is not None:
            self._ip_server.fail(reason)
            self._log.warning("KNXnet/IP server not started", reason=reason)
            return
        try:
            addresses = await self._on_main_thread(
                lambda: self.check_ip_server_settings(settings)
            )
        except ValueError as err:
            self._ip_server.fail(str(err))
            self._log.warning("KNXnet/IP server not started", reason=str(err))
            return
        if await self._ip_server.start(xknx, settings, addresses):
            self._log.info(
                "KNXnet/IP server started",
                port=self._ip_server.port,
                listen=settings.local_ip,
                tunnel_addresses=" ".join(str(a) for a in addresses),
            )
        else:
            self._log.error(
                "KNXnet/IP server not started", reason=self._ip_server.error or ""
            )

    def ip_server_status_text(self) -> str | None:
        """The KNXnet/IP server as the status bar and the Gateway menu show it while it runs."""
        port = self._ip_server.port
        if port is None:
            return None
        count = len(self._ip_server.tunnels())
        return S.IP_SERVER_RUNNING(count).format(port=port, count=count)

    @property
    def usb_domain_address_busy(self) -> bool:
        """Whether the connected USB interface's domain address is being set."""
        return self._usb_domain_busy

    def set_usb_interface_domain_address(self, text: str) -> Future[None]:
        """Set the domain address of the connected KNX RF USB interface to ``text`` (as lines show
        it, ``0001:23456789``) on the open connection; the interface keeps it, and it applies at
        once. Nothing is written when the interface has that address already. Raises
        ``ValueError`` with the reason when it can't start; the future fails with the reason when
        the address could not be set."""
        try:
            domain_address = parse_rf_domain_address(text)
        except ValueError:
            raise ValueError(
                S.USB_DOMAIN_ADDRESS_INVALID.format(address=text.strip())
            ) from None
        info = self.usb_info
        if not self.connected or self._usb_target is None or info is None:
            raise ValueError(S.USB_NOT_CONNECTED_HERE)
        if info.medium is not KNXMedium.RF:
            raise ValueError(S.USB_NOT_RF)
        if self._usb_address_busy or self._usb_address_checking:
            raise ValueError(S.USB_ADDRESS_BUSY)
        if self._usb_domain_busy:
            raise ValueError(S.USB_DOMAIN_ADDRESS_BUSY)
        if self._usb_local_busy:
            raise ValueError(S.USB_LOCAL_DEVICE_BUSY)
        self._usb_domain_error = None
        if info.rf_domain_address == domain_address:
            unchanged: Future[None] = Future()
            unchanged.set_result(None)
            return unchanged
        self._usb_domain_busy = True
        return asyncio.run_coroutine_threadsafe(
            self._write_usb_domain_address(domain_address), self._ensure_async_loop()
        )

    async def _write_usb_domain_address(self, domain_address: bytes) -> None:
        """Write ``domain_address`` to the connected interface; the reason it could not be set is
        kept for the dialog."""
        try:
            interface = self._interface
            try:
                if not isinstance(interface, ObservableUSBInterfaceThreaded):
                    raise CommunicationError(S.USB_NOT_CONNECTED_HERE)
                await interface.set_rf_domain_address(domain_address)
            except CommunicationError as err:
                self._usb_domain_error = S.USB_DOMAIN_ADDRESS_FAILED.format(error=err)
                raise ConnectionError(self._usb_domain_error) from err
            self._log.info(
                "USB interface domain address set",
                domain_address=rf_domain_address_text(domain_address),
            )
        finally:
            self._usb_domain_busy = False

    def check_usb_interface_address(self, text: str) -> Future[Any]:
        """Check whether a device on the bus answers at the individual address ``text`` (the
        "Address Free?" button), over the open connection; the future's result is whether one
        does. Raises ``ValueError`` with the reason when it can't start."""
        address = _parse_individual_address(text)
        if self._usb_address_busy or self._usb_address_checking:
            raise ValueError(S.USB_ADDRESS_BUSY)
        if self._usb_local_busy:
            raise ValueError(S.USB_LOCAL_DEVICE_BUSY)
        if reason := self._api.connection.local_device_refusal(str(address)):
            raise ValueError(reason)
        future = self._api.connection.individual_address_occupied(str(address))
        if future is None:
            raise ValueError(S.USB_NOT_CONNECTED_HERE)
        self._usb_address_checking = True
        self._usb_address_error = None
        self._usb_address_free = None

        def checked(done: Future[Any]) -> None:
            self._usb_address_checking = False
            try:
                occupied = bool(done.result())
            except Exception as err:
                self._log.warning(
                    "individual address check failed",
                    address=str(address),
                    error=str(err),
                )
                self._usb_address_error = S.USB_ADDRESS_CHECK_FAILED.format(error=err)
                return
            self._log.info(
                "individual address checked", address=str(address), used=occupied
            )
            self._usb_address_free = (str(address), not occupied)

        future.add_done_callback(checked)
        return future

    def _usb_address_to_set(self, usb: UsbTarget) -> IndividualAddress | None:
        """The individual address the next connect sets on ``usb``: the one being set, else for an
        interface that forgets its address the one it had (``None``: only read it)."""
        if self._usb_address_write is not None:
            return self._usb_address_write
        kept = None if usb.keeps_address else self._usb_addresses.get(usb.key)
        if kept is None:
            return None
        try:
            return IndividualAddress(kept)
        except CouldNotParseAddress:
            return None

    def _keep_usb_address(self) -> None:
        """Keep the individual address of a connected USB interface that forgets it when
        unplugged, to set it again on the next connect."""
        usb, info = self._usb_target, self.usb_info
        if (
            usb is None
            or usb.keeps_address
            or info is None
            or info.individual_address is None
        ):
            return
        address = str(info.individual_address)
        if self._usb_addresses.get(usb.key) != address:
            self._usb_addresses[usb.key] = address
            self._save_current_settings()

    def switch_connection_type(self, connection_type: ConnectionType) -> None:
        """Reconnect the live connection over a different type (same gateway/IP). No-op when not
        connected or already on it."""
        if (
            self._state != ConnectionState.CONNECTED
            or connection_type == self._connection_type
            or self.is_usb
        ):
            return
        if self._busmonitor_blocks("switch connection type"):
            return
        self._connection_type = connection_type
        self._save_current_settings()
        self._session += 1
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._run_async(self._reopen(self._session, retry=False))

    @property
    def shown_target(self) -> str:
        """The connection target as the status bar and the Gateway menu show it: with local
        device access through a USB interface, also that and the device's address."""
        target = self._connection_target
        if not self.local_device_connected:
            return target
        info = self.usb_info
        address = (
            str(info.individual_address)
            if info is not None and info.individual_address is not None
            else S.LOCAL_DEVICE_ADDRESS_UNKNOWN
        )
        return S.STATUS_LOCAL_DEVICE.format(target=target, address=address)

    @property
    def _connection_target(self) -> str:
        if self._usb_target is not None:
            return self._usb_target.label
        if self._connection_type == ConnectionType.ROUTING:
            return f"{self._multicast_group} (routing)"
        return self._controller_ip

    async def _connect_async(self, reopen: bool = False) -> None:
        """Open the connection; ``reopen`` replaces a lost or outdated one, so the open
        recordings go on."""
        usb = self._usb_target
        self._log.info(
            "connecting",
            target=self._connection_target,
            mode=self._mode_name,
        )
        try:
            self._xknx = XKNX()
            gateway: GatewayDescriptor | None = None
            if usb is not None:
                usb_interface = ObservableUSBInterfaceThreaded(
                    self._xknx,
                    usb,
                    raw_cemi_callback=self._api.connection.dispatch_raw_cemi,
                    sent_cemi_callback=self._api.connection.dispatch_sent_cemi,
                    data_secure=await self._on_main_thread(
                        self._api.connection.data_secure
                    ),
                    individual_address=self._usb_address_to_set(usb),
                    writes_blocked=self._api.connection.writes_blocked,
                    transport_layer=self._usb_local_device,
                )
                self._interface = usb_interface
                config = usb_interface.connection_config
            else:
                description = (
                    await self._gateway_description()
                    if self._connection_type in _TUNNEL_TYPES
                    else None
                )
                gateway = description or self._selected_gateway
                config = await self._on_main_thread(
                    lambda: self._connection_config(gateway)
                )
                self._interface = ObservableKNXIPInterfaceThreaded(
                    xknx=self._xknx,
                    connection_config=config,
                    raw_cemi_callback=self._api.connection.dispatch_raw_cemi,
                    sent_cemi_callback=self._api.connection.dispatch_sent_cemi,
                    writes_blocked=self._api.connection.writes_blocked,
                )
            self._xknx.cemi_handler = DiagnosticCEMIHandler(
                self._xknx,
                mode="usb" if usb is not None else config.connection_type.name.lower(),
                target=self._connection_target,
                writes_blocked=self._api.connection.writes_blocked,
            )
            # Replace the inert default interface so all internal send paths use ours.
            self._xknx.knxip_interface = self._interface
            await self._interface.start()
            self._usb_info = (
                self._interface.usb_info()
                if isinstance(self._interface, ObservableUSBInterfaceThreaded)
                else None
            )
            # Secured group communication with the project's (or the keyring's) keys, sent
            # secured where the connection's own line secures it.
            local_address = self._xknx.current_address.raw
            rf_domain = (
                self._usb_info.rf_domain_address if self._usb_info is not None else None
            )
            rf_domain_address = (
                int.from_bytes(rf_domain, "big") if rf_domain is not None else None
            )
            self._xknx.cemi_handler.data_secure = await self._on_main_thread(
                lambda: self._api.connection.data_secure(
                    local_address, rf_domain_address
                )
            )
            self._data_secure_revision = self._api.project.revision
            self._gateway_info = gateway
            self._config = config
            self._link_up = True
            interface_apdu: int | None = None
            try:
                interface_apdu = await self._interface.read_max_apdu_length()
            except Exception as feature_err:
                self._log.debug(
                    "interface max APDU length unavailable",
                    error=f"{type(feature_err).__name__}: {feature_err}",
                )
            if interface_apdu is not None:
                self._log.info("interface max APDU length", length=interface_apdu)
            else:
                interface_apdu = interface_apdu_limit(
                    None,
                    tunnelling=usb is not None
                    or self._connection_type in _TUNNEL_TYPES,
                )
                if interface_apdu is not None:
                    self._log.info(
                        "interface max APDU length not reported, using standard frames",
                        length=interface_apdu,
                    )
            self._api.connection.interface_max_apdu_length = interface_apdu
            self._api.connection.interface_name = (
                self._gateway_info.name
                if self._gateway_info
                else usb.name
                if usb is not None
                else self._connection_target
            )
            # Before the connection is offered: an operation then finds only the own device.
            self._api.connection.local_device = (
                LocalDevice(
                    str(self._usb_info.individual_address)
                    if self._usb_info.individual_address is not None
                    else None
                )
                if self._usb_info is not None and self._usb_info.transport_layer
                else None
            )
            self._api.connection.set_connection(self._xknx, asyncio.get_running_loop())
            # Connected only now: the interface's APDU length query above can take seconds on
            # a slow link, and a caller seeing the state must find the connection in place.
            self._state = ConnectionState.CONNECTED
            self._api.connection.link_info = self._link_info()
            usb_address = (
                self._usb_info.individual_address
                if self._usb_info is not None
                else None
            )
            interface_address = (
                usb_address
                if usb_address is not None
                else gateway.individual_address
                if gateway is not None
                else None
            )
            self._api.connection.interface_address = (
                str(interface_address) if interface_address is not None else None
            )
            # Lost/re-established events of the open connection (xknx reconnects by itself).
            self._unregister_link_state = (
                self._xknx.connection_manager.register_connection_state_changed_cb(
                    self._on_link_state
                )
            )
            if reopen:
                self._api.connection.dispatch_link_state(True)
            else:
                self._api.connection.dispatch_connected()
            if (
                usb is not None
                and self._xknx.connection_manager.state
                is not XknxConnectionState.CONNECTED
            ):
                # The interface is open but reports no bus connection: the link is down until
                # the bus is back.
                self._on_link_state(XknxConnectionState.DISCONNECTED)
            self._startup_connect = False
            self._log.info(
                "connected",
                target=self._connection_target,
                mode="usb" if usb is not None else config.connection_type.name.lower(),
            )
            if self._usb_info is not None:
                self._log.info(
                    "USB interface info",
                    knx_address=str(usb_address) if usb_address is not None else "",
                    max_apdu_length=self._usb_info.max_apdu_length,
                    bus_connected=self._usb_info.bus_connected,
                    serial_number=self._usb_info.device.serial_number,
                    emi_type=emi_type_name(self._usb_info.emi_type),
                    supported_emi_types=supported_emi_type_names(
                        self._usb_info.supported_emi_types
                    ),
                    mask_version=mask_version_text(self._usb_info.mask_version),
                    medium=medium_name(self._usb_info.medium),
                    domain_address=rf_domain_address_text(
                        self._usb_info.rf_domain_address
                    ),
                    local_device=self._usb_info.transport_layer,
                )
                self._keep_usb_address()
            if self._gateway_info:
                services = [
                    s
                    for s, enabled in [
                        ("tunneling", self._gateway_info.supports_tunnelling),
                        ("tunneling_tcp", self._gateway_info.supports_tunnelling_tcp),
                        ("routing", self._gateway_info.supports_routing),
                        ("secure", self._gateway_info.supports_secure),
                    ]
                    if enabled
                ]
                self._log.info(
                    "gateway info",
                    name=self._gateway_info.name,
                    knx_address=str(self._gateway_info.individual_address or ""),
                    core_version=str(self._gateway_info.core_version),
                    services=",".join(services),
                )
            await self._start_ip_server()
        except Exception as e:
            startup, self._startup_connect = self._startup_connect, False
            if isinstance(e, MissingCredentialsError):
                self._awaiting_credentials = e.gateway
                self._retry_on_credentials = True
            self._state = ConnectionState.ERROR
            self._error_message = str(e)
            self._interface = None
            self._gateway_info = None
            self._usb_info = None
            self._xknx = None
            self._report_failure(e, startup=startup)

    def _report_failure(self, error: Exception, *, startup: bool) -> None:
        """Log a failed connect as an error (shown as a toast); missing credentials at startup
        only once a project has opened without them (see _connect_with_credentials)."""
        text = f"{type(error).__name__}: {error}"
        if (
            isinstance(error, MissingCredentialsError)
            and startup
            and self._no_project_yet()
        ):
            self._credentials_error = text
            self._log.info(
                "connection waits for a project with the credentials",
                target=self._connection_target,
            )
            return
        self._log_failure(text)

    def _log_failure(self, error: str) -> None:
        self._log.error(
            "connection failed",
            target=self._connection_target,
            mode=self._mode_name,
            error=error,
        )

    @property
    def _mode_name(self) -> str:
        """The kind of connection, as logged."""
        if self._usb_target is not None:
            return "usb"
        return self._connection_type.name.lower()

    def _no_project_yet(self) -> bool:
        """Whether no project has opened yet, or one is still opening."""
        project = self._api.project
        return project.loading or not project.is_open

    async def _on_main_thread[T](self, fn: Callable[[], T]) -> T:
        """``fn`` run on the main thread, where the project's data is read (its reads elsewhere
        find nothing while the main thread reads it)."""
        executor = getattr(self._api, "main_thread", None)
        if executor is None:
            return fn()
        return await asyncio.wrap_future(executor.submit(fn))

    async def _gateway_description(self) -> GatewayDescriptor | None:
        """The tunnelling interface's description as it is now: its download changes the services
        it secures."""
        host, port = self._endpoint()
        try:
            return await request_description(host, port)
        except Exception as err:
            self._log.debug(
                "gateway description unavailable",
                ip=self._controller_ip,
                error=f"{type(err).__name__}: {err}",
            )
            return None

    def _connection_config(
        self,
        gateway: GatewayDescriptor | None,
        *,
        active_tunnel: IndividualAddress | None = None,
    ) -> ConnectionConfig:
        """The connection to open to ``gateway``: KNX IP Secure tunnelling (over TCP) when the
        interface requires it, KNX IP Secure routing when the backbone does, with the credentials
        of the project or the loaded keyring; plain otherwise."""
        from xknx.io.connection import SecureConfig

        connection = self._api.connection
        if self._connection_type == ConnectionType.ROUTING:
            backbone = connection.secure_backbone()
            if backbone is not None and (
                gateway is None or gateway.routing_requires_secure is not False
            ):
                key, latency = backbone
                return ConnectionConfig(
                    connection_type=ConnectionType.ROUTING_SECURE,
                    multicast_group=self._multicast_group,
                    secure_config=SecureConfig(
                        backbone_key=key.hex(), latency_ms=latency
                    ),
                    threaded=True,
                )
            if gateway is not None and gateway.routing_requires_secure:
                raise MissingCredentialsError(gateway)
            return ConnectionConfig(
                connection_type=ConnectionType.ROUTING,
                multicast_group=self._multicast_group,
                threaded=True,
            )
        host, port = self._endpoint()
        if gateway is not None and gateway.tunnelling_requires_secure:
            # UDP discovery cannot authorize a secure tunnel. The gateway checks the selected
            # credentials when establishing the secure session.
            candidates = [
                str(address)
                for address, slot in gateway.tunnelling_slots.items()
                if slot.usable and slot.free
            ]
            if active_tunnel is not None:
                active_slot = gateway.tunnelling_slots.get(active_tunnel)
                if active_slot is not None and active_slot.usable:
                    # Our open tunnel is reported occupied. Compare its credentials first.
                    candidates = [str(active_tunnel)]
            tunnel = (
                connection.secure_tunnel(str(gateway.individual_address), candidates)
                if gateway.individual_address is not None
                else None
            )
            if tunnel is None:
                raise MissingCredentialsError(gateway)
            return ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP_SECURE,
                gateway_ip=host,
                gateway_port=port,
                individual_address=tunnel.individual_address,
                secure_config=SecureConfig(
                    user_id=tunnel.user_id,
                    user_password=tunnel.password,
                    device_authentication_password=tunnel.authentication,
                ),
                threaded=True,
            )
        tcp = self._connection_type == ConnectionType.TUNNELING_TCP
        return ConnectionConfig(
            connection_type=(
                ConnectionType.TUNNELING_TCP if tcp else ConnectionType.TUNNELING
            ),
            gateway_ip=host,
            gateway_port=port,
            route_back=not tcp and self._uses_nat(host, port),
            threaded=True,
        )

    def _connect_with_credentials(self) -> None:
        """Connect once the project or a keyring has the credentials the interface requires, e.g.
        after the project opened that the startup connection came too early for."""
        if not self._retry_on_credentials or self._state != ConnectionState.ERROR:
            return
        now = time.monotonic()
        if now < self._credentials_check_at:
            return
        self._credentials_check_at = now + _CREDENTIALS_CHECK_SECONDS
        try:
            self._connection_config(self._awaiting_credentials)
        except ConnectionError:
            # The project opened at startup has no credentials either: now it is an error.
            if self._credentials_error is not None and not self._no_project_yet():
                self._log_failure(self._credentials_error)
                self._credentials_error = None
            return
        self._credentials_error = None
        self._retry_on_credentials = False
        self._log.info("the credentials the interface requires are available now")
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._run_async(self._connect_async())

    def _refresh_data_secure(self) -> None:
        """Hand the open connection the current Data Secure tables after a project change."""
        revision = self._api.project.revision
        if self._xknx is None or revision == self._data_secure_revision:
            return
        if self._api.connection.busy_operation is not None:
            return
        # Not while a background task holds the project (an open, a language switch): reading the
        # tables would wait for it on the UI thread. A frame after it tries again.
        if self._api.project.loading:
            return
        self._data_secure_revision = revision
        self._api.connection.refresh_data_secure()

    # --- connections for online operations --------------------------------------------

    @property
    def opened_for_operations(self) -> bool:
        """Whether the open connection was opened for online operations (it closes after the
        last of them)."""
        return self._operation_open

    def on_main_thread(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the main thread (at once without one, e.g. in tests)."""
        executor = getattr(self._api, "main_thread", None)
        if executor is None:
            fn()
        else:
            executor.submit(fn)

    def open_for_operation(self) -> Future[None]:
        """A connection for one more online operation: the open one, else one through the
        backbone's Bus Connection; when there is none or it fails, the user selects one. Done
        once it is open, canceled when the user canceled the selection. Each call is balanced
        by ``release_operation``."""
        self._operation_users += 1
        if self._state == ConnectionState.CONNECTED:
            done: Future[None] = Future()
            done.set_result(None)
            return done
        if self._operation_pending is not None:
            return self._operation_pending
        pending: Future[None] = Future()
        self._operation_pending = pending
        self._select_failures = []
        project = self._api.project
        bus = project.backbone_bus_connection() if project.is_open else None
        if bus is None:
            self._open_select_connection()
            return pending
        parameter, name = bus
        tunnel = parse_parameter(parameter)
        if tunnel is None:
            self._select_failures.append((name, S.BUS_CONNECTION_UNSUPPORTED))
            self._open_select_connection()
        else:
            self._connect_for_operation(
                name,
                tunnel.endpoint,
                None,
                connection_type=self.tunnel_type_of(tunnel),
            )
        return pending

    def release_operation(self) -> None:
        """An online operation has ended: the connection opened for operations closes after
        the last one, and the connection settings it replaced return."""
        self._operation_users = max(0, self._operation_users - 1)
        self._close_when_unused()

    def _close_when_unused(self) -> None:
        if self._operation_users or not self._operation_open:
            return
        self._operation_open = False
        self._session += 1
        self._state = ConnectionState.DISCONNECTING
        self._log.info("closing the connection opened for the operations")
        self._run_async(self._close_for_operations())

    async def _close_for_operations(self) -> None:
        await self._disconnect_async()
        await self._on_main_thread(self._restore_connection_settings)

    def _connect_for_operation(
        self,
        name: str,
        host: str,
        gateway: GatewayDescriptor | None,
        *,
        connection_type: ConnectionType = ConnectionType.TUNNELING,
    ) -> None:
        """Connect to ``host`` (or the discovered ``gateway``) for the waiting operations, without
        making it the connection the user chose (its settings return when it closes); tunnelled
        to over ``connection_type``."""
        self._keep_settings_for_operation()
        self._usb_target = None
        if gateway is not None and not gateway.supports_tunnelling:
            self._connection_type = ConnectionType.ROUTING
            self._multicast_group = gateway.multicast_address or DEFAULT_MCAST_GRP
        else:
            self._connection_type = connection_type
            self._controller_ip = host
        self._selected_gateway = gateway
        self._operation_name = name
        self._session += 1
        self._retry_on_credentials = False
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._log.info("connecting for an operation", connection=name)
        self._run_async(self._connect_for_operation_async())

    def _connect_usb_for_operation(self, name: str, device: USBDeviceInfo) -> None:
        """Connect through the KNX USB interface ``device`` for the waiting operations, without
        making it the connection the user chose."""
        self._keep_settings_for_operation()
        self._usb_target = UsbTarget.of(device)
        self._selected_gateway = None
        self._operation_name = name
        self._session += 1
        self._retry_on_credentials = False
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._log.info("connecting for an operation", connection=name)
        self._run_async(self._connect_for_operation_async())

    def _keep_settings_for_operation(self) -> None:
        """Keep the connection settings a connection for operations replaces, to restore them
        when it closes."""
        if self._operation_restore is None:
            self._operation_restore = (
                self._controller_ip,
                self._connection_type,
                self._selected_gateway,
                self._multicast_group,
                self._usb_target,
            )

    async def _connect_for_operation_async(self) -> None:
        await self._connect_async()
        # Not retried later on: the operation asks for another connection instead.
        self._retry_on_credentials = False
        await self._on_main_thread(self._operation_connected)

    def _operation_connected(self) -> None:
        pending = self._operation_pending
        if self._state == ConnectionState.CONNECTED:
            self._operation_pending = None
            self._select_open = False
            self._operation_open = True
            if pending is None:
                # The selection was canceled while it connected: it closes unless used.
                self._close_when_unused()
            else:
                pending.set_result(None)
            return
        self._select_failures.append(
            (self._operation_name, self._error_message or S.STATUS_CONNECTION_FAILED)
        )
        self._restore_connection_settings()
        self._state = ConnectionState.DISCONNECTED
        self._error_message = None
        if pending is not None:
            self._open_select_connection()

    def _restore_connection_settings(self) -> None:
        restore, self._operation_restore = self._operation_restore, None
        if restore is not None:
            (
                self._controller_ip,
                self._connection_type,
                self._selected_gateway,
                self._multicast_group,
                self._usb_target,
            ) = restore

    def _open_select_connection(self) -> None:
        """Ask the user for the connection of the waiting operations ("Select Connection")."""
        self._select_open = True
        self._select_ip = self._controller_ip
        if not self._scanning:
            self.scan()

    def _cancel_select_connection(self) -> None:
        pending, self._operation_pending = self._operation_pending, None
        self._select_open = False
        if pending is not None:
            pending.cancel()

    def _render_select_connection(self) -> None:
        """The "Select Connection" dialog: why no connection could be opened (the attempts that
        failed) and the interfaces found, or one entered, to use instead."""
        title = f"{S.SELECT_CONNECTION_TITLE}###select_connection"
        if self._select_open and not self._select_shown:
            imgui.open_popup(title)
            self._select_shown = True
        if not self._select_shown:
            return
        if not begin_dialog(title, width=460.0):
            self._select_shown = False
            return
        if not self._select_open:
            imgui.close_current_popup()
            end_dialog()
            return
        failures = self._select_failures
        imgui.text_wrapped(
            S.SELECT_CONNECTION_SPECIFIC.format(name=failures[0][0])
            if failures
            else S.SELECT_CONNECTION_AUTOMATIC
        )
        for name, error in failures:
            imgui.push_style_color(imgui.Col_.text, status_color("error"))
            imgui.text_wrapped(
                S.SELECT_CONNECTION_FAILED.format(name=name, error=error)
            )
            imgui.pop_style_color()
        imgui.text_wrapped(S.SELECT_CONNECTION_INSTEAD)
        imgui.separator()
        connecting = self._state == ConnectionState.CONNECTING
        imgui.begin_disabled(connecting)
        imgui.text_disabled(S.SECTION_DISCOVERED)
        if self._scanning or connecting:
            imgui.same_line()
            spinner("##select-spinner")
        for gateway in self._gateways:
            label = gateway_label(gateway)
            if imgui.selectable(f"{label}##select_{gateway.ip_addr}", False)[0]:
                self._connect_for_operation(
                    label, join_endpoint(gateway.ip_addr, gateway.port), gateway
                )
        if not self._gateways and not self._scanning:
            imgui.text_disabled(S.NO_GATEWAYS_FOUND)
        if self._configured:
            imgui.text_disabled(S.CONFIGURED_TITLE)
            for index, connection in enumerate(self._configured):
                label = connection_label(connection)
                if imgui.selectable(f"{label}##select_configured_{index}", False)[0]:
                    self._connect_for_operation(
                        label,
                        connection.endpoint,
                        None,
                        connection_type=self.tunnel_type_of(connection),
                    )
        imgui.text_disabled(S.SECTION_USB)
        for device in self._usb_devices:
            label = self._usb_option_label(device)
            if imgui.selectable(label, False)[0]:
                self._connect_usb_for_operation(label.split("##")[0], device)
        if not self._usb_devices and not self._scanning:
            imgui.text_disabled(S.NO_USB_INTERFACES)
        imgui.text_disabled(S.SECTION_MANUAL)
        imgui.set_next_item_width(px(180))
        _, self._select_ip = imgui.input_text("IP##select_ip", self._select_ip)
        imgui.same_line()
        if (
            imgui.button(f"{S.MENU_CONNECT}##select_connect")
            and self._select_ip.strip()
        ):
            host = self._select_ip.strip()
            self._connect_for_operation(host, host, None)
        imgui.end_disabled()
        if dialog_buttons(S.SELECT_CONNECTION_CANCEL) == 0:
            self._cancel_select_connection()
            imgui.close_current_popup()
        end_dialog()

    def disconnect(self) -> None:
        if self._api.connection.busmonitor_state is BusmonitorState.RUNNING:
            self.stop_busmonitor(restore=False)
            return
        if self._state in (ConnectionState.DISCONNECTED, ConnectionState.DISCONNECTING):
            return
        if self._operation_open:
            # Closed by the user: the operations using it fail, and it is not closed again.
            self._operation_open = False
            self._restore_connection_settings()
        self._session += 1
        self._retry_on_credentials = False
        self._credentials_error = None
        self._state = ConnectionState.DISCONNECTING
        self._run_async(self._disconnect_async())

    async def _disconnect_async(self, reopen: bool = False) -> None:
        """Close the connection; ``reopen`` closes it for a new one, so the open recordings go
        on."""
        if self._unregister_link_state is not None:
            self._unregister_link_state()
            self._unregister_link_state = None
        was_connected = self._xknx is not None
        self._keep_usb_address()
        try:
            # its clients first, while the connection they use is still open
            await self._ip_server.stop()
            if self._interface is not None:
                await self._interface.stop()
        finally:
            self._interface = None
            self._gateway_info = None
            self._usb_info = None
            self._xknx = None
            self._config = None
            self._link_up = False
            self._state = ConnectionState.DISCONNECTED
            self._api.connection.set_connection(None, None)
            if was_connected and not reopen:
                self._api.connection.dispatch_disconnected()
            self._log.info("disconnected")

    def _on_link_state(self, state: XknxConnectionState) -> None:
        if state is XknxConnectionState.CONNECTED:
            self._link_up = True
            self._lost_since = (
                None  # the outage ended; the next loss starts a fresh grace
            )
            self._api.connection.dispatch_link_state(True)
        elif state is XknxConnectionState.DISCONNECTED:
            self._link_up = False
            if self._lost_since is None:
                self._lost_since = time.monotonic()
            self._api.connection.dispatch_link_state(False)
            self._run_async(self._reopen_while_lost(self._session))

    def reopen(self) -> None:
        """Open the connection anew when the interface now requires another one, e.g. secure
        tunnelling after its download."""
        if self._state == ConnectionState.CONNECTED:
            self._run_async(self._reopen_when_answered(self._session))

    async def _reopen_when_answered(self, session: int) -> None:
        """Check the connection once the interface answers again (it may still restart)."""
        for _ in range(_REOPEN_ATTEMPTS):
            if await self._reopen_if_changed(session):
                return
            await asyncio.sleep(_REOPEN_SECONDS)

    async def _reopen_if_changed(self, session: int) -> bool:
        """Replace the open connection when the interface now requires another configuration
        (secure or plain, other credentials). Without the credentials it requires, the connection
        is tried once and shows why it fails. ``False`` when the interface did not answer."""
        current = self._open_config(session)
        if current is None:
            return True
        if self._usb_target is not None:
            # A USB interface takes no other connection; it is reopened when plugged in again.
            return True
        gateway = self._selected_gateway
        if self._connection_type == ConnectionType.TUNNELING:
            gateway = await self._gateway_description()
            if gateway is None:
                return False
        active_tunnel = (
            current.individual_address
            if self._link_up
            and current.connection_type is ConnectionType.TUNNELING_TCP_SECURE
            else None
        )
        try:
            config = await self._on_main_thread(
                lambda: self._connection_config(gateway, active_tunnel=active_tunnel)
            )
        except ConnectionError:
            config = None
        current = self._open_config(session)
        if current is None:
            return True
        if config is not None and _config_key(config) == _config_key(current):
            return True
        self._log.info("the interface requires another connection, reconnecting")
        await self._reopen(session, retry=config is not None)
        return True

    def _open_config(self, session: int) -> ConnectionConfig | None:
        """The configuration of the open connection while it is the one of ``session``."""
        return self._config if session == self._session else None

    async def _reopen_while_lost(self, session: int) -> None:
        """While the connection is lost, let xknx's own auto-reconnect restore it; replace a
        connection the interface no longer accepts. When xknx has not recovered the link within
        the grace period (a wedged tunnel after a heartbeat timeout never does), force a full
        teardown and reconnect, the same recovery a manual disconnect/connect performs."""
        if self._watching_lost:
            return
        self._watching_lost = True
        try:
            while (
                session == self._session
                and not self._link_up
                and self._config is not None
            ):
                await self._reopen_if_changed(session)
                # Re-check after the await: the link may be back, the session may have moved on,
                # or the config may be gone (a failed credentialed reopen, or bus monitor mode
                # taking over the connection) — in any case stop rather than force a reconnect.
                if self._link_up or self._open_config(session) is None:
                    break
                if (
                    self._usb_target is None
                    and self._lost_since is not None
                    and time.monotonic() - self._lost_since >= _RECONNECT_GRACE_SECONDS
                ):
                    self._log.info(
                        "connection still lost, forcing a reconnect",
                        target=self._connection_target,
                    )
                    await self._reopen(session)
                    return
                await asyncio.sleep(_REOPEN_SECONDS)
        finally:
            self._watching_lost = False

    async def _reopen(self, session: int, retry: bool = True) -> None:
        """Close the connection and open it anew; with ``retry`` until it is open or the user
        connects or disconnects otherwise."""
        await self._disconnect_async(reopen=True)
        self._api.connection.dispatch_link_state(False)
        while session == self._session:
            self._state = ConnectionState.CONNECTING
            self._error_message = None
            await self._connect_async(reopen=True)
            if self.connected:
                return
            if not retry:
                break
            await asyncio.sleep(_REOPEN_SECONDS)
        if not self.connected:
            # Not reopened: the recordings of the connection end.
            self._api.connection.dispatch_disconnected()

    def _link_info(self) -> LinkInfo:
        """How the open connection is described in recordings."""
        if self._usb_target is not None:
            usb = self.usb_info
            return LinkInfo(
                name=self._usb_target.name,
                host=usb.device.path.decode(errors="replace")
                if usb is not None
                else "",
                port=0,
                connector="Usb",
                medium="Rf" if usb is not None and usb.medium is KNXMedium.RF else "Tp",
                local_device=(
                    str(usb.individual_address or "")
                    if usb is not None and usb.transport_layer
                    else None
                ),
            )
        name = ""
        if self._gateway_info is not None and self._gateway_info.name:
            name = "".join(
                c for c in self._gateway_info.name if c.isprintable()
            ).strip()
        if self._connection_type == ConnectionType.ROUTING:
            return LinkInfo(
                name=name or "IP Routing",
                host=self._multicast_group,
                port=DEFAULT_GATEWAY_PORT,
                connector="IpRouting",
            )
        host, port = self._endpoint()
        return LinkInfo(
            name=name or "IP Tunneling",
            host=host,
            port=port,
            connector="IpTunneling",
            protocol=(
                "Tcp"
                if self._connection_type == ConnectionType.TUNNELING_TCP
                else "Udp"
            ),
        )

    def _endpoint(self) -> tuple[str, int]:
        """The host and port of the tunnelling interface: the controller address as entered,
        ``host`` (port 3671) or ``host:port``."""
        return split_endpoint(self._controller_ip)

    # --- bus monitor mode -----------------------------------------------------

    @property
    def busmonitor_active(self) -> bool:
        return self._api.connection.busmonitor_active

    def _busmonitor_blocks(self, op: str) -> bool:
        """Refuse ``op`` while the bus monitor connection runs."""
        if not self.busmonitor_active:
            return False
        self._log.warning(f"{op} refused: the bus monitor is running")
        return True

    def start_busmonitor(self) -> None:
        """Close the normal connection and connect the gateway in bus monitor mode."""
        service = self._api.connection
        if service.busmonitor_active:
            return
        if self.is_routing:
            service.set_busmonitor_state(BusmonitorState.OFF, error="routing")
            return
        if self.local_device_chosen:
            # The interface's own device is all the connection reaches.
            service.set_busmonitor_state(BusmonitorState.OFF, error="local_device")
            return
        service.set_busmonitor_state(BusmonitorState.STARTING)
        self._run_async(self._start_busmonitor_async())

    async def _start_busmonitor_async(self) -> None:
        service = self._api.connection
        gateway_ip, port = self._endpoint()
        link = self._link_info()
        target = self._busmonitor_target
        self._log.info("starting bus monitor", target=target, port=port)
        if self._interface is not None or self._xknx is not None:
            await self._disconnect_async()
        monitor: BusmonitorConnection | UsbBusmonitorConnection
        if self._usb_target is not None:
            monitor = UsbBusmonitorConnection(
                self._usb_target,
                on_frame=service.dispatch_busmonitor_cemi,
                on_state=service.dispatch_link_state,
            )
        else:
            monitor = BusmonitorConnection(
                gateway_ip,
                port,
                on_frame=service.dispatch_busmonitor_cemi,
                on_state=service.dispatch_link_state,
            )
        try:
            await monitor.connect()
        except Exception as err:
            status = err.error_code if isinstance(err, RequestResponseError) else None
            reason = rejection_reason(status) or "failed"
            service.busmonitor_error_detail = str(err)
            self._log.error(
                "bus monitor connection rejected",
                target=target,
                error=f"{type(err).__name__}: {err}",
            )
            service.set_busmonitor_state(BusmonitorState.OFF, error=reason)
            await self._restore_connection()
            return
        self._busmonitor = monitor
        service.busmonitor_link = link
        service.set_busmonitor_state(BusmonitorState.RUNNING)
        self._log.info("bus monitor running", target=target)

    @property
    def _busmonitor_target(self) -> str:
        """The interface the bus monitor connects to, as shown."""
        if self._usb_target is not None:
            return self._usb_target.label
        return self._controller_ip

    def stop_busmonitor(self, restore: bool = True) -> None:
        """Close the bus monitor connection; ``restore`` reconnects the normal connection."""
        service = self._api.connection
        if service.busmonitor_state is not BusmonitorState.RUNNING:
            return
        service.set_busmonitor_state(BusmonitorState.STOPPING)
        self._run_async(self._stop_busmonitor_async(restore))

    async def _stop_busmonitor_async(self, restore: bool) -> None:
        service = self._api.connection
        monitor, self._busmonitor = self._busmonitor, None
        if monitor is not None:
            try:
                await monitor.disconnect()
            except Exception as err:
                self._log.warning("bus monitor disconnect failed", error=str(err))
        service.busmonitor_link = None
        service.set_busmonitor_state(BusmonitorState.OFF)
        self._log.info("bus monitor stopped")
        if restore:
            await self._restore_connection()

    async def _restore_connection(self) -> None:
        """Reconnect the normal connection to the same gateway."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        await self._connect_async()

    def scan(self, auto_connect: bool = False) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._run_async(self._scan_async(auto_connect))

    def autostart(self) -> None:
        """Discover gateways at startup and connect to the preferred (last-used) or first one,
        unless the connection at startup is turned off (the setting, or ``--no-connect``): then
        nothing is searched or connected until the user asks."""
        if not self._connect_at_startup or self.skip_startup_connect:
            self._log.info(
                "no connection at startup",
                reason="command line" if self.skip_startup_connect else "setting",
            )
            return
        if self._state != ConnectionState.DISCONNECTED or self.busmonitor_active:
            return
        self._log.info("auto-discovering gateways")
        self.scan(auto_connect=True)

    async def _scan_async(self, auto_connect: bool = False) -> None:
        try:
            self._usb_devices = await search_usb_interfaces(
                lambda error: self._log.debug(
                    "USB interface search failed", error=str(error)
                )
            )
            for device in self._usb_devices:
                self._log.debug(
                    "USB interface found",
                    name=usb_device_name(device),
                    serial_number=device.serial_number,
                )
            if auto_connect and self._usb_target is not None:
                # The last connection went through a USB interface: only that one is
                # connected at startup, a gateway is not chosen in its place.
                auto_connect = False
                self._autostart_usb(self._usb_target)
            addresses = scan_addresses()
            self._gateways = await search_gateways(
                3.0,
                addresses,
                lambda error: self._log.debug(
                    "gateway search failed", error=str(error)
                ),
            )
            self._log.info(
                "gateway scan complete",
                found=len(self._gateways),
                addresses=",".join(addresses),
            )
            for g in self._gateways:
                self._log.debug(
                    "gateway found",
                    name=g.name,
                    ip=g.ip_addr,
                    knx_address=str(g.individual_address or ""),
                )
            if auto_connect and not self._gateways:
                self._log.warning("no gateways found on the network")
                # Fallback: try the configured IP as a tunneling connect right away. Either the
                # user typed one in the settings or we connected to one before
                # (``_preferred_gateway_ip``, written on every scan connect).
                fallback_ip = self._preferred_gateway_ip or (
                    self._controller_ip
                    if self._controller_ip != _DEFAULT_CONTROLLER_IP
                    else None
                )
                if (
                    fallback_ip
                    and self._state == ConnectionState.DISCONNECTED
                    and not self.busmonitor_active
                ):
                    self._log.info(
                        "no gateways discovered, trying configured IP",
                        ip=fallback_ip,
                    )
                    self._controller_ip = fallback_ip
                    self.connect(startup=True)
                if (
                    not self._autostart_retry_done
                    and self._state == ConnectionState.DISCONNECTED
                ):
                    # macOS: the first discovery only triggers the "Local Network" permission
                    # prompt and returns nothing. Retry once after a delay so a granted permission
                    # takes effect without restarting the app. The fallback above may already have
                    # connected by then; the retry's own state guard then skips the scan.
                    self._autostart_retry_done = True
                    self._run_async(self._delayed_rescan(_AUTOSTART_RETRY_SECONDS))
            if (
                auto_connect
                and self._gateways
                and self._state == ConnectionState.DISCONNECTED
                and not self.busmonitor_active
            ):
                target = next(
                    (
                        g
                        for g in self._gateways
                        if g.ip_addr == self._preferred_gateway_ip
                    ),
                    self._gateways[0],
                )
                self._log.info(
                    "auto-connecting gateway",
                    name=target.name,
                    ip=target.ip_addr,
                    preferred=target.ip_addr == self._preferred_gateway_ip,
                )
                self.connect_to_gateway(target, startup=True)
        except Exception as e:
            self._log.error("gateway scan failed", error=f"{type(e).__name__}: {e}")
        finally:
            self._scanning = False

    def _autostart_usb(self, target: UsbTarget) -> None:
        """Connect the USB interface last used at startup, when it is plugged in."""
        device = next((d for d in self._usb_devices if target.matches(d)), None)
        if device is None:
            self._log.warning("USB interface not plugged in", interface=target.label)
            return
        if self._state != ConnectionState.DISCONNECTED or self.busmonitor_active:
            return
        self._log.info("auto-connecting USB interface", interface=target.label)
        self.connect_to_usb(device, startup=True)

    async def _delayed_rescan(self, delay: float) -> None:
        """Re-run autostart discovery once after ``delay`` seconds. Skips if the user connected or a
        scan is already running in the meantime. Runs on the async loop, so the wait never blocks the
        UI thread."""
        await asyncio.sleep(delay)
        if self._state != ConnectionState.DISCONNECTED or self._scanning:
            return
        self._log.info("retrying gateway discovery after delay", delay=delay)
        self._scanning = True
        await self._scan_async(auto_connect=True)

    def shutdown(self) -> None:
        if self._busmonitor is not None:
            self._run_async(self._stop_busmonitor_async(restore=False))
        if self._interface is not None:
            self._run_async(self._disconnect_async())
        if self._async_loop is not None:
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        # hidapi crashes on quit if its exit-time teardown runs after the GUI run loop is gone.
        cancel_hidapi_atexit()

    def render_status_indicator(self) -> None:
        self._refresh_data_secure()
        self._connect_with_credentials()
        self._follow_project_bus_connection()
        self._reopen_once_idle()
        self._render_status_indicator()

    def _render_status_indicator(self) -> None:
        draw_list = imgui.get_window_draw_list()
        cursor = imgui.get_cursor_screen_pos()
        text_height = imgui.get_text_line_height()
        center = imgui.ImVec2(cursor.x + px(5), cursor.y + text_height / 2)

        if self.busmonitor_active:
            draw_list.add_circle_filled(center, px(4), color_u32(0.95, 0.6, 0.15, 1.0))
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            imgui.text_colored(
                status_color("warning"),
                S.STATUS_BUSMONITOR.format(ip=self._busmonitor_target),
            )
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.STATUS_BUSMONITOR_HINT)
        elif self._state == ConnectionState.CONNECTED:
            # A steady dot: a connection that lasts is no reason to redraw the status bar (in the
            # browser view, to send it again) all the time.
            draw_list.add_circle_filled(center, px(4), color_u32(0.2, 0.8, 0.3, 1.0))
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            target = self.shown_target
            if self._gateway_info and self._gateway_info.name:
                name = "".join(
                    c for c in self._gateway_info.name if c.isprintable()
                ).strip()
                if name:
                    target = f"{name} @ {target}"
            imgui.text(S.STATUS_CONNECTED.format(ip=target))
            if (server := self.ip_server_status_text()) is not None:
                imgui.same_line()
                imgui.text_disabled(f"|  {server}")
        elif self._state == ConnectionState.CONNECTING:
            spin = (animation_time() * 4) % 1.0
            draw_list.add_circle_filled(
                center, px(4), color_u32(0.8, 0.7, 0.2, 0.5 + 0.5 * spin)
            )
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            imgui.text_disabled(S.STATUS_CONNECTING)
        elif self._state == ConnectionState.ERROR:
            draw_list.add_circle_filled(center, px(4), color_u32(0.8, 0.2, 0.2, 1.0))
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            error_short = (
                self._error_message[:60]
                if self._error_message
                else S.STATUS_UNKNOWN_ERROR
            )
            imgui.text_colored(
                status_color("error"),
                S.STATUS_ERROR.format(error=error_short),
            )
            if self._error_message and imgui.is_item_hovered():
                imgui.set_tooltip(self._error_message)
        else:
            draw_list.add_circle_filled(center, px(4), color_u32(0.5, 0.5, 0.5, 1.0))
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            imgui.text_disabled(S.STATUS_DISCONNECTED)
        if self._api.connection.block_bus_writes:
            # Clickable (touch has no hover): opens the setting.
            imgui.same_line()
            imgui.text_colored(status_color("warning"), S.STATUS_WRITES_BLOCKED)
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.STATUS_WRITES_BLOCKED_HINT)
            if imgui.is_item_clicked() and self.open_settings is not None:
                self.open_settings(SETTINGS_PAGE)

    def _open_progmode_window(self) -> None:
        """Open the 'Devices in programming mode' window and kick off a first scan."""
        self._progmode_open = True
        self._scan_progmode()

    def _scan_progmode(self) -> None:
        """Broadcast for devices in programming mode and read each one's dossier. Results and the
        scanning flag are updated from the async done-callback and read by ``render_window`` each
        frame (the same direct cross-thread assignment the gateway scan uses)."""
        if self._progmode_scanning:
            return
        future = self._api.connection.scan_programming_mode()
        if future is None:
            return  # not connected / bus busy — the service already logged why
        self._progmode_scanning = True
        self._progmode_error = None

        def _done(f: "Future[Any]") -> None:
            self._progmode_scanning = False
            self._progmode_scanned = True
            try:
                self._progmode_results = f.result()
            except ConfirmationError as e:  # interface did not ack the broadcast
                self._log.error("programming-mode scan failed", error=str(e))
                self._progmode_results = []
                self._progmode_error = S.PROGMODE_NO_CONFIRM
            except (
                Exception
            ) as e:  # bus timeout / xknx error (also logged by the service)
                self._log.error("programming-mode scan failed", error=str(e))
                self._progmode_results = []
                self._progmode_error = S.PROGMODE_SCAN_FAILED.format(error=str(e))

        future.add_done_callback(_done)

    def render_window(self) -> None:
        """The 'Devices in programming mode' window and the "Select Connection" dialog; called
        from the overlay pass."""
        self._render_select_connection()
        self._render_usb_interface()
        if not self._configured_in_settings:
            self._render_configured_dialog()
        if not self._progmode_open:
            return
        expanded, open_state = begin_tool_window(
            f"{S.PROGMODE_TITLE}###progmode_window", True, width=30 * 16, height=24 * 16
        )
        self._progmode_open = bool(open_state)
        if expanded:
            self._render_progmode_contents()
        imgui.end()

    def open_usb_interface(self) -> None:
        """Open the USB Interface dialog of the connected USB interface."""
        info = self.usb_info
        address = info.individual_address if info is not None else None
        self._usb_address_text = str(address) if address is not None else ""
        self._usb_address_error = None
        self._usb_address_free = None
        self._usb_domain_text = rf_domain_address_text(
            info.rf_domain_address if info is not None else None
        )
        self._usb_domain_error = None
        self._reset_ip_server_fields()
        self._usb_interface_open = True

    def _reset_ip_server_fields(self) -> None:
        """The fields of the KNXnet/IP Server section as kept; free addresses of the interface's
        line suggested while none are."""
        settings = self._ip_settings
        self._ip_draft = settings
        self._ip_port_text = str(settings.port)
        self._ip_addresses_text = " ".join(
            settings.tunnel_addresses or self.suggest_ip_server_addresses()
        )
        self._ip_name_text = settings.name
        self._ip_error = None

    def _ip_server_draft(self) -> IpServerSettings | None:
        """The settings of the KNXnet/IP Server section as entered; None when the port is no
        number."""
        draft = self._ip_draft or self._ip_settings
        try:
            port = int(self._ip_port_text.strip())
        except ValueError:
            return None
        return replace(
            draft,
            port=port,
            tunnel_addresses=tuple(parse_tunnel_addresses(self._ip_addresses_text)),
            name=self._ip_name_text.strip() or draft.name,
        )

    def _render_ip_server(self) -> None:
        """The "KNXnet/IP Server" section of the USB Interface dialog: its settings, applied
        with Apply (which starts or stops it while connected), its state and its tunnels."""
        imgui.push_id("ip_server")
        if not collapsing_header(f"{S.IP_SERVER_HEADER}###ip_server"):
            imgui.pop_id()
            return
        draft = self._ip_draft or self._ip_settings
        imgui.begin_disabled(self._ip_busy)
        changed, enabled = wrapped_checkbox(
            f"{S.IP_SERVER_ENABLED}##ip_enabled", draft.enabled
        )
        if changed:
            draft = replace(draft, enabled=enabled)
        control_hint(S.IP_SERVER_HINT)
        imgui.align_text_to_frame_padding()
        imgui.text(S.IP_SERVER_PORT)
        imgui.same_line()
        imgui.set_next_item_width(px(70))
        _, self._ip_port_text = imgui.input_text("##ip_port", self._ip_port_text)
        same_line_if_fits(px(190) + imgui.calc_text_size(S.IP_SERVER_LISTEN).x)
        imgui.align_text_to_frame_padding()
        imgui.text(S.IP_SERVER_LISTEN)
        imgui.same_line()
        listen_names = {
            "all": S.IP_SERVER_LISTEN_ALL,
            "localhost": S.IP_SERVER_LISTEN_LOCAL,
        }
        imgui.set_next_item_width(px(180))
        if begin_combo("##ip_listen", listen_names[draft.listen]):
            for key, name in listen_names.items():
                if imgui.selectable(f"{name}##ip_listen_{key}", key == draft.listen)[0]:
                    draft = replace(
                        draft, listen="localhost" if key == "localhost" else "all"
                    )
            end_combo()
        changed, discovery = checkbox(
            f"{S.IP_SERVER_DISCOVERY}##ip_discovery", draft.discovery
        )
        if changed:
            draft = replace(draft, discovery=discovery)
        same_line_if_fits(button_width(S.IP_SERVER_TCP) + px(24))
        changed, tcp = checkbox(f"{S.IP_SERVER_TCP}##ip_tcp", draft.tcp)
        if changed:
            draft = replace(draft, tcp=tcp)
        imgui.align_text_to_frame_padding()
        imgui.text(S.IP_SERVER_ADDRESSES)
        imgui.same_line()
        imgui.set_next_item_width(
            max(
                px(120),
                imgui.get_content_region_avail().x
                - button_width(S.IP_SERVER_SUGGEST)
                - imgui.get_style().item_spacing.x,
            )
        )
        _, self._ip_addresses_text = imgui.input_text(
            "##ip_addresses", self._ip_addresses_text
        )
        if _row_button(f"{S.IP_SERVER_SUGGEST}##ip_suggest"):
            suggested = self.suggest_ip_server_addresses()
            if suggested:
                self._ip_addresses_text = " ".join(suggested)
        imgui.align_text_to_frame_padding()
        imgui.text(S.IP_SERVER_NAME)
        imgui.same_line()
        imgui.set_next_item_width(px(200))
        _, self._ip_name_text = imgui.input_text("##ip_name", self._ip_name_text)
        self._ip_draft = draft
        entered = self._ip_server_draft()
        imgui.begin_disabled(entered is None or entered == self._ip_settings)
        applied = imgui.button(f"{S.IP_SERVER_APPLY}##ip_apply")
        imgui.end_disabled()
        imgui.end_disabled()
        if applied and entered is not None:
            self._apply_ip_server(entered)
        error = (
            S.IP_SERVER_PORT_INVALID.format(port=self._ip_port_text.strip())
            if entered is None
            else self._ip_error
        )
        if self._ip_busy:
            loading("##ip_spinner", S.IP_SERVER_BUSY)
        elif error:
            imgui.push_style_color(imgui.Col_.text, status_color("error"))
            imgui.text_wrapped(error)
            imgui.pop_style_color()
        self._render_ip_server_state()
        imgui.pop_id()

    def _apply_ip_server(self, settings: IpServerSettings) -> None:
        """Apply the section's settings; a reason they can't be is shown below them."""
        self._ip_error = None
        try:
            future = self.configure_ip_server(settings)
        except ValueError as err:
            self._ip_error = str(err)
            return

        def done(result: Future[None]) -> None:
            error = result.exception() if not result.cancelled() else None
            if error is not None:
                self._ip_error = str(error)

        future.add_done_callback(done)

    def _render_ip_server_state(self) -> None:
        """Whether the KNXnet/IP server runs, on which port, and its open tunnels."""
        status = self.ip_server_status_text()
        if status is None:
            error = self.ip_server_error
            if error and not self._ip_error:
                imgui.push_style_color(imgui.Col_.text, status_color("error"))
                imgui.text_wrapped(error)
                imgui.pop_style_color()
            else:
                imgui.text_disabled(S.IP_SERVER_STOPPED)
            return
        imgui.text_colored(status_color("success"), status)
        tunnels = self.ip_server_tunnels()
        if not tunnels:
            imgui.text_disabled(S.IP_SERVER_NO_TUNNELS)
            return
        flags = (
            imgui.TableFlags_.borders_inner_v
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        if not begin_data_table("##ip_tunnels", 4, flags):
            return
        fixed = imgui.TableColumnFlags_.width_fixed
        imgui.table_setup_column(S.IP_SERVER_CHANNEL, fixed)
        imgui.table_setup_column(
            S.IP_SERVER_CLIENT, imgui.TableColumnFlags_.width_stretch
        )
        imgui.table_setup_column(S.IP_SERVER_PROTOCOL, fixed)
        imgui.table_setup_column(S.IP_SERVER_ADDRESS, fixed)
        data_table_headers()
        for tunnel in tunnels:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text(str(tunnel.channel))
            imgui.table_set_column_index(1)
            text_clipped_tooltip(tunnel.client)
            imgui.table_set_column_index(2)
            imgui.text(tunnel.protocol)
            imgui.table_set_column_index(3)
            imgui.text(tunnel.address)
        imgui.end_table()

    def _render_usb_interface(self) -> None:
        """The "USB Interface" dialog: what the connected USB interface reported, and its
        individual address, set on the interface with Apply (or Enter in the field), with a check
        whether a device on the bus uses an address; a KNX RF interface's domain address the
        same way, also taken from one of the project's RF lines."""
        title = f"{S.USB_INTERFACE_TITLE}###usb_interface"
        if self._usb_interface_open and not self._usb_interface_shown:
            imgui.open_popup(title)
            self._usb_interface_shown = True
        if not self._usb_interface_shown:
            return
        # wide enough for the frames and tunnel tables (data tables need 560 not to scroll sideways)
        if not begin_dialog(title, width=640.0):
            self._usb_interface_shown = False
            self._usb_interface_open = False
            return
        target = self._usb_target
        if not self._usb_interface_open or target is None:
            self._usb_interface_open = False
            imgui.close_current_popup()
            end_dialog()
            return
        info = self.usb_info
        imgui.text(S.GATEWAY_NAME.format(name=target.name))
        if target.serial_number:
            imgui.text(S.USB_SERIAL_NUMBER.format(serial=target.serial_number))
        if info is not None:
            imgui.text(S.USB_MAX_APDU_LENGTH.format(length=info.max_apdu_length))
            imgui.text(S.USB_EMI_TYPE.format(emi=emi_type_name(info.emi_type)))
            imgui.text(
                S.USB_SUPPORTED_EMI_TYPES.format(
                    types=", ".join(supported_emi_type_names(info.supported_emi_types))
                )
            )
            if info.mask_version is not None:
                imgui.text(
                    S.USB_MASK_VERSION.format(mask=mask_version_text(info.mask_version))
                )
            imgui.text(S.USB_MEDIUM.format(medium=medium_name(info.medium)))
        busy = (
            self._usb_address_busy
            or self._usb_address_checking
            or self._usb_domain_busy
            or self._usb_local_busy
        )
        imgui.separator()
        self._render_usb_local_device(busy)
        local = info is not None and info.transport_layer
        imgui.separator()
        imgui.begin_disabled(busy or not self.connected)
        imgui.align_text_to_frame_padding()
        imgui.text(S.USB_ADDRESS_LABEL)
        imgui.same_line()
        imgui.set_next_item_width(px(110))
        entered, self._usb_address_text = imgui.input_text(
            "##usb_address",
            self._usb_address_text,
            imgui.InputTextFlags_.enter_returns_true,
        )
        current = (
            str(info.individual_address)
            if info is not None and info.individual_address is not None
            else ""
        )
        # The address the interface has: nothing to apply, and the interface uses it (a check
        # could be answered by the interface itself).
        changed = self._usb_address_text.strip() != current
        imgui.begin_disabled(not changed)
        applied = _row_button(f"{S.USB_ADDRESS_APPLY}##usb_address_apply")
        # the own device is all the connection reaches: no other one can be asked
        checked = not local and _row_button(f"{S.USB_ADDRESS_FREE}##usb_address_free")
        imgui.end_disabled()
        # Enter in the field applies it too
        if applied or (entered and changed):
            self._start_usb_address(self.set_usb_interface_address)
        if checked:
            self._start_usb_address(self.check_usb_interface_address)
        imgui.end_disabled()
        if self._usb_local_busy:
            pass  # shown at the local device access
        elif busy or (self._state == ConnectionState.CONNECTING):
            loading(
                "##usb_address_spinner",
                S.USB_ADDRESS_CHECKING
                if self._usb_address_checking
                else S.USB_ADDRESS_SETTING,
            )
        elif self._usb_address_error:
            imgui.push_style_color(imgui.Col_.text, status_color("error"))
            imgui.text_wrapped(self._usb_address_error)
            imgui.pop_style_color()
        elif self._usb_address_free is not None:
            address, free = self._usb_address_free
            imgui.push_style_color(
                imgui.Col_.text, status_color("success" if free else "warning")
            )
            imgui.text_wrapped(
                (S.USB_ADDRESS_IS_FREE if free else S.USB_ADDRESS_NOT_FREE).format(
                    address=address
                )
            )
            imgui.pop_style_color()
        if not target.keeps_address:
            # wrapped: a line of its own would widen the dialog past a narrow screen
            imgui.push_style_color(
                imgui.Col_.text, imgui.get_style().color_(imgui.Col_.text_disabled)
            )
            imgui.text_wrapped(S.USB_ADDRESS_HINT_VOLATILE)
            imgui.pop_style_color()
        if info is not None and info.medium is KNXMedium.RF:
            self._render_usb_domain_address(info.rf_domain_address, busy)
        imgui.separator()
        self._render_ip_server()
        if dialog_buttons(S.USB_INTERFACE_CLOSE) == 0:
            self._usb_interface_open = False
            imgui.close_current_popup()
        end_dialog()

    def _render_usb_domain_address(self, current: bytes | None, busy: bool) -> None:
        """The domain address row of the USB Interface dialog, for a KNX RF interface."""
        imgui.separator()
        imgui.begin_disabled(busy or not self.connected)
        imgui.align_text_to_frame_padding()
        imgui.text(S.USB_DOMAIN_ADDRESS_LABEL)
        imgui.same_line()
        imgui.set_next_item_width(px(130))
        entered, self._usb_domain_text = imgui.input_text(
            "##usb_domain",
            self._usb_domain_text,
            imgui.InputTextFlags_.enter_returns_true,
        )
        changed = self._usb_domain_text.strip().upper() != rf_domain_address_text(
            current
        )
        imgui.begin_disabled(not changed)
        applied = _row_button(f"{S.USB_ADDRESS_APPLY}##usb_domain_apply")
        imgui.end_disabled()
        if applied or (entered and changed):
            self._start_usb_domain_address()
        lines = self.project_rf_domain_addresses()
        if lines:
            imgui.align_text_to_frame_padding()
            imgui.text(S.USB_DOMAIN_ADDRESS_FROM_LINE)
            imgui.same_line()
            imgui.set_next_item_width(min(px(260), imgui.get_content_region_avail().x))
            if begin_combo("##usb_domain_line", ""):
                for index, (label, text) in enumerate(lines):
                    item = f"{label}  ({text})##usb_domain_line_{index}"
                    if imgui.selectable(item, text == self._usb_domain_text)[0]:
                        self._usb_domain_text = text
                end_combo()
        imgui.end_disabled()
        if self._usb_domain_busy:
            loading("##usb_domain_spinner", S.USB_DOMAIN_ADDRESS_SETTING)
        elif self._usb_domain_error:
            imgui.push_style_color(imgui.Col_.text, status_color("error"))
            imgui.text_wrapped(self._usb_domain_error)
            imgui.pop_style_color()
        control_hint(S.USB_DOMAIN_ADDRESS_HINT)

    def _start_usb_domain_address(self) -> None:
        """Set the domain address in the dialog's field; a reason it can't is shown there."""
        try:
            self.set_usb_interface_domain_address(self._usb_domain_text)
        except ValueError as err:
            self._usb_domain_error = str(err)

    def project_rf_domain_addresses(self) -> list[tuple[str, str]]:
        """The RF lines of the open project with a domain address, as (line, domain address)
        pairs: the line's address and name, and its domain address as lines show it."""
        project = self._api.project
        result: list[tuple[str, str]] = []
        for area in project.get_areas():
            for line in project.get_lines(area.id):
                if line.medium_type != MEDIUM_RF or line.domain_address is None:
                    continue
                address = f"{area.area_number}.{line.line_number}"
                label = f"{address} {line.name}".strip()
                result.append(
                    (label, format_domain_address(MEDIUM_RF, line.domain_address))
                )
        return result

    def _start_usb_address(self, start: Callable[[str], Future[Any]]) -> None:
        """Set or check the address in the dialog's field; a reason it can't is shown there."""
        try:
            start(self._usb_address_text)
        except ValueError as err:
            self._usb_address_error = str(err)

    def _render_usb_local_device(self, busy: bool) -> None:
        """The local device access row of the USB Interface dialog: switches the open
        connection between the bus and the interface's own device."""
        info = self.usb_info
        connected_local = info is not None and info.transport_layer
        imgui.begin_disabled(busy or not self.connected)
        changed, local_device = wrapped_checkbox(
            f"{S.USB_LOCAL_DEVICE_OPTION}##usb_interface_local", connected_local
        )
        imgui.end_disabled()
        if changed:
            try:
                self.set_usb_local_device(local_device)
            except ValueError as err:
                self._usb_local_error = str(err)
        if self._usb_local_busy:
            loading("##usb_local_spinner", S.USB_LOCAL_DEVICE_SWITCHING)
        elif self._usb_local_error:
            imgui.push_style_color(imgui.Col_.text, status_color("error"))
            imgui.text_wrapped(self._usb_local_error)
            imgui.pop_style_color()
        control_hint(S.SETTING_USB_LOCAL_DEVICE_HINT)

    def _render_progmode_contents(self) -> None:
        imgui.begin_disabled(self._progmode_scanning)
        if imgui.button(S.PROGMODE_RESCAN):
            self._scan_progmode()
        imgui.end_disabled()
        if self._progmode_scanning:
            imgui.same_line()
            loading("##progmode-spinner", S.PROGMODE_SEARCHING)
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.PROGMODE_HINT)
        imgui.pop_text_wrap_pos()
        imgui.separator()

        if not self._progmode_results:
            if self._progmode_error is not None:
                imgui.push_text_wrap_pos(0.0)
                imgui.text_colored(status_color("error"), self._progmode_error)
                imgui.pop_text_wrap_pos()
            elif self._progmode_scanned and not self._progmode_scanning:
                imgui.text_disabled(S.PROGMODE_NONE)
            return
        count = len(self._progmode_results)
        imgui.text_disabled(S.PROGMODE_COUNT(count).format(count=count))
        imgui.spacing()
        for index, found in enumerate(self._progmode_results):
            self._render_progmode_device(index, found)

    def _render_progmode_device(self, index: int, found: ScannedDevice) -> None:
        # A factory-fresh device that was never programmed answers with the default address; it is
        # not a project device, so it gets its own label rather than a "not in this project" note.
        unprogrammed = found.address == DEFAULT_INDIVIDUAL_ADDRESS
        device = (
            None
            if unprogrammed
            else self._api.project.find_device_by_address(found.address)
        )
        header = found.address
        if device is not None:
            header = f"{found.address}  —  {device.name}"
        imgui.set_next_item_open(True, imgui.Cond_.first_use_ever)
        if not collapsing_header(f"{header}###progmode_{index}"):
            return
        imgui.indent()
        if unprogrammed:
            imgui.text_colored(status_color("warning"), S.PROGMODE_UNPROGRAMMED)
        elif device is not None:
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(S.PROGMODE_IN_PROJECT.format(name=device.name))
            imgui.same_line()
            if imgui.button(f"{S.PROGMODE_SELECT}##progmode_sel_{index}"):
                self._api.project.selected_device = device
                self._api.project.focus_editor()
        else:
            imgui.text_disabled(S.PROGMODE_NOT_IN_PROJECT)

        overview = found.overview
        if overview is None:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(
                status_color("error"),
                S.PROGMODE_READ_FAILED.format(error=found.error or ""),
            )
            imgui.pop_text_wrap_pos()
            imgui.unindent()
            return

        self._render_progmode_status(overview)
        mask = (
            f"{overview.mask_version:#06x}" if overview.mask_version is not None else ""
        )
        app_v = (
            application_version_text(overview.application_version)
            if overview.application_version is not None
            else ""
        )
        app_no = (
            f"{overview.application_number:#06x}"
            if overview.application_number is not None
            else ""
        )
        product = self._catalog_product(overview)
        rows: list[tuple[str, str]] = [
            (S.PROGMODE_MANUFACTURER, self._manufacturer_label(overview.manufacturer)),
        ]
        if product is not None:
            rows.append((S.PROGMODE_PRODUCT, self._product_label(product)))
        rows += [
            (S.PROGMODE_APPLICATION, app_no),
            (S.PROGMODE_APP_VERSION, app_v),
            (S.PROGMODE_MASK, mask),
            (S.PROGMODE_SERIAL, overview.serial_number or ""),
            (S.PROGMODE_ORDER, overview.order_info or ""),
            (S.PROGMODE_HARDWARE, overview.hardware_type or ""),
        ]
        label_w = _progmode_label_width()
        for label, value in rows:
            imgui.text_disabled(label)
            imgui.same_line(label_w)
            imgui.text(value or "-")
        imgui.unindent()

    def _manufacturer_label(self, manufacturer_id: str | None) -> str:
        """Resolve the raw ``M-xxxx`` manufacturer id to its name (from the global master data) if
        available, e.g. ``Robert Bosch (M-0002)``; fall back to the bare id."""
        if not manufacturer_id:
            return ""
        master = self._api.connection.master
        name = master.manufacturers.get(manufacturer_id) if master is not None else None
        return f"{name} ({manufacturer_id})" if name else manufacturer_id

    def _catalog_product(self, overview: DeviceOverview) -> "ProductSummary | None":
        """Match the device read off the bus to a product in the local catalog by manufacturer +
        application number (the parts of the application-program id that identify the product's
        application, independent of the concrete version). ``None`` if the catalog has no match."""
        manufacturer = overview.manufacturer
        app_number = overview.application_number
        if not manufacturer or app_number is None:
            return None
        manufacturer = manufacturer.upper()
        for product in self._api.catalog.get_products():
            if not product.application_id:
                continue
            parsed = parse_app_id(product.application_id)
            if (
                parsed is not None
                and parsed.manufacturer_id == manufacturer
                and parsed.application_number == app_number
            ):
                return product
        return None

    def _product_label(self, product: "ProductSummary") -> str:
        name = product.name or product.product_ref_id
        if product.order_number:
            return f"{name}  ({product.order_number})"
        return name

    def _render_progmode_status(self, overview: DeviceOverview) -> None:
        """Programming-mode flag + device error class, coloured like the editor's diagnosis."""
        imgui.text_disabled(S.PROGMODE_STATUS)
        imgui.same_line(_progmode_label_width())
        if overview.error_code is None or overview.error_code == 0:
            imgui.text_colored(status_color("success"), S.PROGMODE_STATUS_OK)
        else:
            imgui.text_colored(status_color("error"), overview.error_text or "")
        if overview.programming_mode:
            imgui.same_line()
            imgui.text_colored(status_color("warning"), S.PROGMODE_FLAG_ON)

    @property
    def menu_label(self) -> str:
        return S.MENU_GATEWAY

    @property
    def disconnect_label(self) -> str:
        return S.MENU_DISCONNECT

    def render_menu(self) -> None:
        """The Gateway menu's content (the app draws the menu itself, see ``menu_label``)."""
        if self.busmonitor_active:
            imgui.text(S.STATUS_BUSMONITOR.format(ip=self._busmonitor_target))
            imgui.text_disabled(S.STATUS_BUSMONITOR_HINT)
            imgui.separator()
            running = self._api.connection.busmonitor_state is BusmonitorState.RUNNING
            if imgui.menu_item(S.MENU_STOP_BUSMONITOR, "", False, running)[0]:
                self.stop_busmonitor()
        elif self._state == ConnectionState.CONNECTED:
            # The gateway connected to: its address as the heading, what it is below it, and the
            # connection types it offers to switch between.
            imgui.text(S.STATUS_CONNECTED_TO.format(ip=self.shown_target))
            imgui.indent(px(_DETAILS_INDENT))
            self._render_gateway_details()
            self._render_connected_transport()
            imgui.unindent(px(_DETAILS_INDENT))
            imgui.separator()
            if self.is_usb and imgui.menu_item(S.MENU_USB_INTERFACE, "", False)[0]:
                self.open_usb_interface()
            if imgui.menu_item(S.MENU_READ_PROGMODE, "", self._progmode_open)[0]:
                self._open_progmode_window()
            imgui.separator()
            if imgui.menu_item(S.MENU_DISCONNECT, "", False)[0]:
                self.disconnect()
        elif self._state == ConnectionState.CONNECTING:
            imgui.text_disabled(S.STATUS_CONNECTING)
            imgui.separator()
            if imgui.menu_item(S.SELECT_CONNECTION_CANCEL, "", False)[0]:
                self.disconnect()
        elif self._state == ConnectionState.ERROR:
            imgui.text_colored(status_color("error"), S.STATUS_CONNECTION_FAILED)
            if self._error_message:
                imgui.text_wrapped(self._error_message)
            imgui.separator()
            self._render_gateway_picker(retry_label=S.MENU_RETRY)
        else:
            self._render_gateway_picker(retry_label=S.MENU_CONNECT)
        imgui.separator()
        if imgui.menu_item(S.MENU_CONFIGURED_CONNECTIONS, "", False)[0]:
            self.open_configured_connections()
        if (
            self.open_settings is not None
            and imgui.menu_item(app_strings.MENU_SETTINGS, "", False)[0]
        ):
            self.open_settings(SETTINGS_PAGE)

    def _render_gateway_details(self) -> None:
        """The connected gateway's name, individual address, KNXnet/IP version and services; for
        a USB interface its individual address, serial number and bus connection."""
        usb = self.usb_info
        if usb is not None:
            if usb.individual_address is not None:
                imgui.text(S.GATEWAY_ADDRESS.format(address=usb.individual_address))
            if usb.device.serial_number:
                imgui.text(S.USB_SERIAL_NUMBER.format(serial=usb.device.serial_number))
            if usb.transport_layer:
                # the bus connection doesn't matter then
                imgui.text_colored(status_color("warning"), S.USB_LOCAL_DEVICE_DETAILS)
            elif not usb.bus_connected:
                imgui.text_colored(status_color("warning"), S.USB_NOT_ON_BUS)
            if (server := self.ip_server_status_text()) is not None:
                imgui.text(server)
            elif (error := self.ip_server_error) is not None:
                imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + px(320))
                imgui.text_colored(status_color("error"), error)
                imgui.pop_text_wrap_pos()
            return
        gateway = self._gateway_info
        if gateway is None:
            return
        if gateway.name.strip():
            imgui.text(S.GATEWAY_NAME.format(name=gateway.name))
        if gateway.individual_address:
            imgui.text(S.GATEWAY_ADDRESS.format(address=gateway.individual_address))
        imgui.text(S.GATEWAY_CORE_VERSION.format(version=gateway.core_version))
        services = gateway_services(gateway)
        if services:
            imgui.text(S.GATEWAY_SERVICES.format(services=", ".join(services)))

    def settings(self) -> list[Setting]:
        """The connection settings, for the Connection page of the Settings window (and MCP)."""
        return [
            Setting(
                "connection_type",
                lambda: S.SETTING_CONNECTION_TYPE,
                "choice",
                get=lambda: self.connection_type,
                set=self._set_connection_type_name,
                choices=self._connection_type_choices,
                hint=lambda: S.SETTING_CONNECTION_TYPE_HINT,
                # the connection open through the USB interface is no gateway to switch to
                read_only=lambda: (
                    self._usb_target is not None and self._connection_open
                ),
            ),
            Setting(
                "usb_interface",
                lambda: S.SETTING_USB_INTERFACE,
                "choice",
                get=lambda: (
                    self._usb_target.key if self._usb_target is not None else ""
                ),
                set=self._set_usb_interface_key,
                choices=lambda: [
                    (target.key, _usb_target_label(target))
                    for target in self._usb_interface_targets()
                ],
                shown=lambda: self._usb_target is not None,
                read_only=lambda: self._connection_open,
            ),
            Setting(
                "usb_local_device",
                lambda: S.SETTING_USB_LOCAL_DEVICE,
                "bool",
                get=lambda: self._usb_local_device,
                set=self._set_usb_local_device,
                hint=lambda: S.SETTING_USB_LOCAL_DEVICE_HINT,
                shown=lambda: self._usb_target is not None,
                # switched on the open connection in the USB Interface dialog
                read_only=lambda: self._connection_open,
            ),
            Setting(
                "gateway_ip",
                lambda: S.SETTING_GATEWAY_IP,
                "text",
                get=self._gateway_ip_setting,
                set=self._set_controller_ip,
                hint=lambda: S.SETTING_GATEWAY_IP_HINT,
                shown=lambda: self._usb_target is None,
                applied_when_done=True,
            ),
            Setting(
                "use_project_bus_connection",
                lambda: S.SETTING_USE_PROJECT_BUS_CONNECTION,
                "bool",
                get=lambda: self._use_project_bus,
                set=self._set_use_project_bus,
                hint=lambda: S.SETTING_USE_PROJECT_BUS_CONNECTION_HINT,
            ),
            Setting(
                "configured_connections",
                lambda: S.CONFIGURED_TITLE,
                "custom",
                get=lambda: [c.to_parameter() for c in self._configured],
                hint=lambda: S.SETTING_CONFIGURED_CONNECTIONS_HINT,
                render=self._render_configured_setting,
            ),
            Setting(
                "connect_at_startup",
                lambda: S.SETTING_CONNECT_AT_STARTUP,
                "bool",
                get=lambda: self._connect_at_startup,
                set=self._set_connect_at_startup,
                hint=lambda: S.SETTING_CONNECT_AT_STARTUP_HINT,
            ),
            Setting(
                "block_bus_writes",
                lambda: S.SETTING_BLOCK_BUS_WRITES,
                "bool",
                get=lambda: self._api.connection.block_bus_writes,
                set=self._set_block_bus_writes,
                hint=lambda: S.SETTING_BLOCK_BUS_WRITES_HINT,
                read_only=lambda: self._block_bus_writes_forced,
                # An MCP client may turn it on, never off.
                mcp_may_set=bool,
            ),
            Setting(
                "protected_group_addresses",
                lambda: S.SETTING_PROTECTED_GAS,
                "custom",
                get=lambda: " ".join(protected_patterns()),
                set=self._set_protected,
                hint=lambda: S.SETTING_PROTECTED_GAS_HINT,
                render=self._render_protected,
                # An MCP client may add to the list, never take anything out of it.
                mcp_may_set=_only_adds_protected,
            ),
            Setting(
                "use_serial_number",
                lambda: S.SETTING_USE_SERIAL_NUMBER,
                "bool",
                get=lambda: self._api.connection.use_serial_number,
                set=self._set_use_serial_number,
                hint=lambda: S.SETTING_USE_SERIAL_NUMBER_HINT,
            ),
            self._timeout_setting(
                "confirmation",
                lambda: S.SETTING_CONFIRMATION_TIMEOUT,
                lambda: S.SETTING_CONFIRMATION_TIMEOUT_HINT,
            ),
            self._timeout_setting(
                "acknowledgement",
                lambda: S.SETTING_ACKNOWLEDGEMENT_TIMEOUT,
                lambda: S.SETTING_ACKNOWLEDGEMENT_TIMEOUT_HINT,
            ),
            self._timeout_setting(
                "response",
                lambda: S.SETTING_RESPONSE_TIMEOUT,
                lambda: S.SETTING_RESPONSE_TIMEOUT_HINT,
            ),
        ]

    def _render_configured_dialog(self) -> None:
        if self._configured_dialog is not None:
            self._configured_dialog.render()
            if not self._configured_dialog.is_open:
                self._configured_in_settings = False

    def _render_configured_setting(self) -> None:
        """How many connections are configured, and the button opening their dialog, which the
        Settings window then draws (a dialog opened from it stays above it)."""
        imgui.align_text_to_frame_padding()
        imgui.text(S.SETTING_CONFIGURED_COUNT(len(self._configured)))
        same_line_if_fits(button_width(S.MENU_CONFIGURED_CONNECTIONS))
        if imgui.button(f"{S.MENU_CONFIGURED_CONNECTIONS}##configured_setting"):
            self.open_configured_connections(in_settings=True)
        if self._configured_in_settings:
            self._render_configured_dialog()

    def _timeout_setting(
        self, name: str, label: Callable[[], str], hint: Callable[[], str]
    ) -> Setting:
        """A wait of the bus exchanges (``BusTimeouts``) in whole seconds."""
        return Setting(
            f"{name}_timeout",
            label,
            "int",
            get=lambda: round(getattr(bus_timeouts(), name)),
            set=lambda value: self._set_timeout(name, value),
            bounds=_TIMEOUT_BOUNDS,
            hint=hint,
        )

    def _set_timeout(self, name: str, value: object) -> None:
        seconds = _timeout_seconds(value, getattr(bus_timeouts(), name))
        set_bus_timeouts(replace(bus_timeouts(), **{name: seconds}))
        self._save_current_settings()

    @property
    def _connection_open(self) -> bool:
        return self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED)

    def _connection_type_choices(self) -> list[tuple[str, str]]:
        """The transports the Connection Type setting offers: the gateway's, and "USB" while a
        KNX USB interface is stored or was found (not while a gateway connection is open, which
        would then go through another interface)."""
        choices = [
            ("tunneling", S.CONN_TYPE_TUNNELING),
            ("tunneling_tcp", S.CONN_TYPE_TCP_TUNNELING),
            ("routing", S.CONN_TYPE_ROUTING),
        ]
        usb_known = self._usb_target is not None or bool(self._usb_devices)
        if usb_known and not (self._connection_open and self._usb_target is None):
            choices.append(("usb", S.CONN_TYPE_USB))
        return choices

    def _set_connection_type_name(self, value: object) -> None:
        """Set the stored transport from an MCP name ("tunneling"/"tunneling_tcp"/"routing" or
        "usb": the stored KNX USB interface, else the first one found). An open gateway connection
        opens anew with it (see ``_reopen_with_settings``); the USB interface takes effect on the
        next connect. Nothing changes while the connection is open through another kind of
        interface."""
        name = str(value).strip().lower()
        if name == "usb":
            if self._usb_target is not None or self._connection_open:
                return
            if not self._usb_devices:
                return
            self._usb_target = UsbTarget.of(self._usb_devices[0])
            self._save_current_settings()
            return
        connection_type = parse_connection_type(name)
        if connection_type is None:
            return
        if self._usb_target is not None:
            if self._connection_open:
                return
            self._usb_target = None
        elif connection_type == self._connection_type:
            return
        self._connection_type = connection_type
        self._save_current_settings()
        self._reopen_with_settings()

    def _usb_interface_targets(self) -> list[UsbTarget]:
        """The KNX USB interfaces the USB Interface setting offers: the stored one, then those the
        last scan found."""
        targets: list[UsbTarget] = []
        if self._usb_target is not None:
            targets.append(self._usb_target)
        for device in self._usb_devices:
            target = UsbTarget.of(device)
            if all(target.key != known.key for known in targets):
                targets.append(target)
        return targets

    def _set_usb_interface_key(self, value: object) -> None:
        """Store the KNX USB interface with the key ``value`` (see ``UsbTarget.key``) for the
        next connect; nothing changes while a connection is open."""
        if self._connection_open:
            return
        key = str(value).strip()
        target = next(
            (t for t in self._usb_interface_targets() if t.key == key),
            None,
        )
        if target is not None and target != self._usb_target:
            self._usb_target = target
            self._save_current_settings()

    def _set_usb_local_device(self, value: object) -> None:
        """Store the choice of local device access for the next connect; nothing changes while
        a connection is open (the USB Interface dialog switches that one)."""
        if self._connection_open or bool(value) == self._usb_local_device:
            return
        self._usb_local_device = bool(value)
        self._save_current_settings()

    def _gateway_ip_setting(self) -> str:
        """The gateway's address as set here (not the one of the project's Bus Connection or of a
        connection opened for operations, in use for a while)."""
        kept = self._project_restore or self._operation_restore
        return kept[0] if kept is not None else self._controller_ip

    def _set_controller_ip(self, value: object) -> None:
        """Set the gateway's address; an open tunnelling connection opens anew with it (see
        ``_reopen_with_settings``). While the project's Bus Connection or a connection opened for
        operations is in use, the address is kept for afterwards."""
        address = str(value).strip()
        if not address or address == self._gateway_ip_setting():
            return
        kept = self._project_restore or self._operation_restore
        if kept is not None:
            changed = (address, *kept[1:])
            if self._project_restore is not None:
                self._project_restore = changed
            else:
                self._operation_restore = changed
            self._save_current_settings()
            return
        self._controller_ip = address
        self._selected_gateway = None
        self._save_current_settings()
        if self._connection_type != ConnectionType.ROUTING:
            self._reopen_with_settings()

    def _reopen_with_settings(self) -> None:
        """The settings of the open gateway connection changed: it opens anew with them, at once
        or once the running bus operation has ended. Not a connection through a USB interface or
        the bus monitor, nor one opened for operations."""
        if (
            not self._connection_open
            or self._usb_target is not None
            or self._operation_open
            or self._operation_pending is not None
            or self.busmonitor_active
        ):
            self._reopen_when_idle = False
            return
        if self._api.connection.busy_operation is not None:
            self._reopen_when_idle = True
            return
        self._reopen_when_idle = False
        self._session += 1
        self._log.info("the connection settings changed, connecting again")
        self._run_async(self._reopen(self._session))

    def _reopen_once_idle(self) -> None:
        """Open the connection anew with changed settings once the bus operation that held it
        back has ended (every frame)."""
        if self._reopen_when_idle and self._api.connection.busy_operation is None:
            self._reopen_with_settings()

    def _set_use_project_bus(self, value: object) -> None:
        if bool(value) == self._use_project_bus:
            return
        self._use_project_bus = bool(value)
        self._project_bus_seen = None
        self._save_current_settings()

    def _wanted_project_bus(self) -> tuple[str, ConnectionType] | None:
        """The tunnel of the open project's backbone Bus Connection to connect through (host,
        connection type), with the setting on; None otherwise."""
        project = self._api.project
        if not self._use_project_bus or not project.is_open:
            return None
        bus = project.backbone_bus_connection()
        if bus is None:
            return None
        parameter, _name = bus
        tunnel = parse_parameter(parameter)
        if tunnel is None:
            return None
        return tunnel.endpoint, self.tunnel_type_of(tunnel)

    def _follow_project_bus_connection(self) -> None:
        """Connect through the open project's Bus Connection with "Use the Project's Bus
        Connection" on, and through the gateway set here again once the project has none (closed,
        another project, set to Automatic, the setting off). Looked at again when the project
        changed (every frame); an open connection opens anew, not while a bus operation, the bus
        monitor or a connection opened for operations holds it."""
        project = self._api.project
        seen = (project.generation, project.revision) if project.is_open else (-1, -1)
        if seen == self._project_bus_seen:
            return
        if (
            self._api.connection.busy_operation is not None
            or self.busmonitor_active
            or self._operation_open
            or self._operation_pending is not None
        ):
            return
        self._project_bus_seen = seen
        wanted = self._wanted_project_bus()
        if wanted == self._project_bus and (
            wanted is not None or self._project_restore is None
        ):
            return
        if self._project_restore is None and wanted is not None:
            self._project_restore = (
                self._controller_ip,
                self._connection_type,
                self._selected_gateway,
                self._multicast_group,
                self._usb_target,
            )
        if wanted is not None:
            host, connection_type = wanted
            self._controller_ip = host
            self._connection_type = connection_type
            self._selected_gateway = None
            self._usb_target = None
            self._log.info("connecting through the project's Bus Connection", host=host)
        else:
            restore, self._project_restore = self._project_restore, None
            if restore is not None:
                (
                    self._controller_ip,
                    self._connection_type,
                    self._selected_gateway,
                    self._multicast_group,
                    self._usb_target,
                ) = restore
            self._log.info("connecting through the gateway set in the settings again")
        self._project_bus = wanted
        if self._connection_open:
            if self._usb_target is not None or wanted is None:
                # back to a USB interface, or anywhere else: closed and opened anew
                self._session += 1
                self._run_async(self._reopen(self._session))
            else:
                self._reopen_with_settings()

    def _chosen_connection(self, startup: bool) -> None:
        """A connect to an interface: the user's choice ends the connection through the project's
        Bus Connection (what it chose is kept as set here) until the project changes; at startup
        the project's Bus Connection, if any, takes over again once connected."""
        if startup:
            self._project_bus_seen = None
            self._project_bus = None
        else:
            self._project_restore = None

    def block_bus_writes_this_start(self) -> None:
        """``--no-bus-writes``: Block Bus Writes stays on until the editor ends, whatever the
        setting says (the setting itself is kept as it is)."""
        self._block_bus_writes_forced = True
        self._apply_block_bus_writes()

    def _load_protected(self, text: str) -> None:
        """The saved list; an entry that is no group address (saved by an older version) is
        left out and logged."""
        kept: list[str] = []
        for item in text.replace(",", " ").split():
            try:
                kept.append(parse_protected_entry(item).text)
            except ValueError:
                self._log.warning("protected group address left out", entry=item)
        set_protected(kept)

    def _render_protected(self) -> None:
        """The list as a text field with Apply: an entry that is no group address or group is
        named below it, and Apply stays off until every entry is one."""
        current = " ".join(protected_patterns())
        draft = current if self._protected_draft is None else self._protected_draft
        imgui.set_next_item_width(px(260.0))
        changed, draft = imgui.input_text("##protected_gas", draft)
        if changed:
            self._protected_draft = draft
        try:
            patterns = parse_protected(draft)
            error = ""
        except ValueError:
            patterns = []
            invalid = [
                item for item in draft.replace(",", " ").split() if not _parses(item)
            ]
            error = S.SETTING_PROTECTED_GAS_INVALID.format(entries=", ".join(invalid))
        same_line_if_fits(button_width(S.SETTING_PROTECTED_GAS_APPLY))
        imgui.begin_disabled(bool(error) or patterns == protected_patterns())
        if imgui.button(f"{S.SETTING_PROTECTED_GAS_APPLY}##protected_gas_apply"):
            self._set_protected(" ".join(patterns))
            self._protected_draft = None
        imgui.end_disabled()
        if error:
            imgui.text_colored(status_color("error"), error)

    def _set_protected(self, value: object) -> None:
        """Raises :class:`ValueError` naming the entries that are no group address or group."""
        patterns = parse_protected(str(value))
        if patterns != protected_patterns():
            self._log.info("protected group addresses", patterns=" ".join(patterns))
        set_protected(patterns)
        self._save_current_settings()

    def _set_block_bus_writes(self, value: object) -> None:
        self._block_bus_writes_setting = bool(value)
        self._save_current_settings()
        self._apply_block_bus_writes()

    def _apply_block_bus_writes(self) -> None:
        blocked = self._block_bus_writes_setting or self._block_bus_writes_forced
        if blocked != self._api.connection.block_bus_writes:
            self._log.info("bus writes blocked" if blocked else "bus writes allowed")
        self._api.connection.block_bus_writes = blocked

    def _set_connect_at_startup(self, value: object) -> None:
        self._connect_at_startup = bool(value)
        self._save_current_settings()

    def _set_use_serial_number(self, value: object) -> None:
        self._api.connection.use_serial_number = bool(value)
        self._save_current_settings()

    def _render_gateway_picker(self, retry_label: str) -> None:
        if imgui.is_window_appearing() and not self._scanning:
            self.scan()

        imgui.text_disabled(S.SECTION_DISCOVERED)
        if self._scanning:
            imgui.same_line()
            spinner("##scan-spinner")
        if self._gateways:
            for gw in self._gateways:
                self._render_gateway_option(gw)
        elif not self._scanning:
            imgui.text_disabled(S.NO_GATEWAYS_FOUND)

        if self._configured:
            imgui.separator()
            imgui.text_disabled(S.CONFIGURED_TITLE)
            for index, connection in enumerate(self._configured):
                label = f"{connection_label(connection)}##configured_{index}"
                if imgui.menu_item(label, "", False)[0]:
                    self.connect_to_configured(connection)

        imgui.separator()
        imgui.text_disabled(S.SECTION_USB)
        for device in self._usb_devices:
            if imgui.menu_item(self._usb_option_label(device), "", False)[0]:
                self.connect_to_usb(device)
        if not self._usb_devices and not self._scanning:
            imgui.text_disabled(S.NO_USB_INTERFACES)

        imgui.separator()
        imgui.text_disabled(S.SECTION_MANUAL)
        imgui.set_next_item_width(px(180))
        _, self._controller_ip = imgui.input_text("IP##manual", self._controller_ip)
        self._maybe_probe_manual(editing=imgui.is_item_active())
        tcp_support = self._manual_tcp_support()
        manual_type = (
            self._connection_type
            if self._connection_type in _TUNNEL_TYPES
            else ConnectionType.TUNNELING
        )
        if tcp_support is False and manual_type == ConnectionType.TUNNELING_TCP:
            manual_type = ConnectionType.TUNNELING
        # TCP first, as a discovered gateway offers it; unless the interface is known not to
        # support it (a KNXnet/IP v1 device).
        if tcp_support is not False:
            if radio_button(
                f"{S.CONN_TYPE_TCP_TUNNELING}##manual_tcp",
                manual_type == ConnectionType.TUNNELING_TCP,
            ):
                self._connection_type = ConnectionType.TUNNELING_TCP
            imgui.same_line()
        if radio_button(
            f"{S.CONN_TYPE_TUNNELING}##manual_udp",
            manual_type == ConnectionType.TUNNELING,
        ):
            self._connection_type = ConnectionType.TUNNELING
        if imgui.menu_item(retry_label, "", False)[0]:
            self.connect(connection_type=manual_type)

    def _usb_option_label(self, device: USBDeviceInfo) -> str:
        """A plugged-in USB interface as offered: its name, and its serial number to tell
        several apart."""
        name = usb_device_name(device)
        serial = f"  ({device.serial_number})" if device.serial_number else ""
        return f"{name}{serial}##usb_{device.path.hex()}"

    def _manual_tcp_support(self) -> bool | None:
        """Whether the manually entered interface supports TCP tunnelling: True/False from a
        matching discovered gateway or a finished probe, None while unknown (not yet probed or the
        device did not answer)."""
        endpoint = self._controller_ip.strip()
        if not endpoint:
            return None
        host = split_endpoint(endpoint)[0]
        for gateway in self._gateways:
            if gateway.ip_addr == host:
                return gateway.supports_tunnelling_tcp
        return self._manual_tcp if endpoint == self._manual_probe_ip else None

    def _maybe_probe_manual(self, editing: bool) -> None:
        """Probe the manually entered interface's capabilities once it is settled (not mid-edit),
        so the TCP option can hide for a v1 device. Discovered gateways are authoritative and need
        no probe."""
        endpoint = self._controller_ip.strip()
        if editing or not endpoint or endpoint == self._manual_probe_ip:
            return
        host = split_endpoint(endpoint)[0]
        if any(gateway.ip_addr == host for gateway in self._gateways):
            return
        self._manual_probe_ip = endpoint
        self._manual_tcp = None
        self._run_async(self._probe_manual_tcp(endpoint))

    async def _probe_manual_tcp(self, endpoint: str) -> None:
        host, port = split_endpoint(endpoint)
        supported: bool | None = None
        try:
            description = await request_description(host, port)
            supported = description.supports_tunnelling_tcp
        except Exception as err:
            self._log.debug(
                "manual interface description unavailable",
                endpoint=endpoint,
                error=f"{type(err).__name__}: {err}",
            )
        if self._manual_probe_ip == endpoint:
            self._manual_tcp = supported

    def _render_connected_transport(self) -> None:
        """While connected: a radio per connection type the interface offers, side by side, so
        the type in use is visible and switchable. Picking a different one reconnects over it.
        Shown only when the interface offers more than one."""
        transports = self.offered_transports()
        if len(transports) < 2:
            return
        for index, (ctype, label) in enumerate(transports):
            if index:
                imgui.same_line()
            if radio_button(
                f"{label}##connected_{ctype.name}",
                self._connection_type == ctype,
            ):
                self.switch_connection_type(ctype)

    def offered_transports(self) -> list[tuple[ConnectionType, str]]:
        """The connection types the connected gateway offers, with their labels."""
        gateway = self._gateway_info
        if gateway is None:
            return []
        transports: list[tuple[ConnectionType, str]] = []
        if gateway.supports_tunnelling_tcp:
            transports.append((ConnectionType.TUNNELING_TCP, S.CONN_TYPE_TCP_TUNNELING))
        if gateway.supports_tunnelling:
            transports.append((ConnectionType.TUNNELING, S.CONN_TYPE_TUNNELING))
        if gateway.supports_routing:
            transports.append((ConnectionType.ROUTING, S.CONN_TYPE_ROUTING))
        return transports

    @property
    def gateway_info(self) -> GatewayDescriptor | None:
        """The description of the gateway connected to, while connected over KNXnet/IP."""
        return self._gateway_info

    def _render_gateway_option(self, gw: GatewayDescriptor) -> None:
        """A discovered gateway: its name, then below it a radio for each connection type it
        offers, side by side and not indented, as in the manual connection. Picking one connects
        to the gateway over that type (the picker shows only while not connected, so a pick
        always starts a fresh connection)."""
        imgui.text(gateway_label(gw))
        transports: list[tuple[ConnectionType, str]] = []
        if gw.supports_tunnelling_tcp:
            transports.append((ConnectionType.TUNNELING_TCP, S.CONN_TYPE_TCP_TUNNELING))
        if gw.supports_tunnelling:
            transports.append((ConnectionType.TUNNELING, S.CONN_TYPE_TUNNELING))
        if gw.supports_routing:
            transports.append((ConnectionType.ROUTING, S.CONN_TYPE_ROUTING))
        preferred = gw.ip_addr == self._preferred_gateway_ip
        for index, (ctype, label) in enumerate(transports):
            if index:
                imgui.same_line()
            if radio_button(
                f"{label}##gw_{gw.ip_addr}_{ctype.name}",
                preferred and self._connection_type == ctype,
            ):
                self.connect_to_gateway(gw, connection_type=ctype)

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        self.shutdown()


def _config_key(config: ConnectionConfig) -> tuple[object, ...]:
    """What tells two connection configurations apart for the interface."""
    secure = config.secure_config
    return (
        config.connection_type,
        config.individual_address,
        None
        if secure is None
        else (
            secure.user_id,
            secure.user_password,
            secure.device_authentication_password,
            secure.backbone_key,
        ),
    )


def _parse_individual_address(text: str) -> IndividualAddress:
    """``text`` as an individual address (area.line.device). Raises ``ValueError`` with the
    message shown when it is none."""
    try:
        return IndividualAddress(text.strip())
    except CouldNotParseAddress:
        raise ValueError(S.USB_ADDRESS_INVALID.format(address=text.strip())) from None


def _parses(item: str) -> bool:
    try:
        parse_protected_entry(item)
    except ValueError:
        return False
    return True


def _only_adds_protected(value: object) -> bool:
    """Whether an MCP client may set Protected Group Addresses to ``value``: only when it keeps
    every entry (it may add, never take out). A list with an entry that is no group address
    passes here, so the setting refuses it with the entries named."""
    try:
        new = set(parse_protected(str(value)))
    except ValueError:
        return True
    return new >= set(protected_patterns())
