import asyncio
import contextlib
import math
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from enum import Enum
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui, imspinner
from xknx import XKNX
from xknx.core.connection_state import XknxConnectionState
from xknx.exceptions import ConfirmationError, RequestResponseError
from xknx.io.connection import ConnectionConfig, ConnectionType
from xknx.io.const import DEFAULT_MCAST_GRP
from xknx.io.gateway_scanner import GatewayDescriptor, GatewayScanner
from xknx.io.self_description import request_description

from editor_gui.color import color_u32
from editor_gui.device_info import application_version_text
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.connection.busmonitor import (
    DEFAULT_GATEWAY_PORT,
    BusmonitorConnection,
    rejection_reason,
)
from editor_gui.plugins.connection.interface import ObservableKNXIPInterfaceThreaded
from editor_gui.plugins.connection.service import BusmonitorState, LinkInfo
from editor_gui.plugins.connection.strings import S
from editor_gui.programming import (
    DEFAULT_INDIVIDUAL_ADDRESS,
    DeviceOverview,
    ScannedDevice,
)
from editor_gui.settings import load_settings, save_settings
from editor_gui.widgets.dialog import begin_tool_window
from editor_gui.widgets.dpi import label_column_width, px, px_vec2
from xknxeditor.prod.app_id import parse_app_id

if TYPE_CHECKING:
    from xknxeditor.catalog import ProductSummary

_SETTINGS = "connection"

# Delay before the one-shot autostart re-scan (macOS Local Network permission grant window).
_AUTOSTART_RETRY_SECONDS = 30.0
# Seconds between the attempts to reopen a lost connection.
_REOPEN_SECONDS = 5.0
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


class ConnectionPlugin:
    name = "connection"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        api.connection.set_logger(Logger(api.log, "connection"))
        self._log = Logger(api.log, "connection")
        self._state = ConnectionState.DISCONNECTED
        self._error_message: str | None = None
        self._controller_ip: str = "192.168.1.1"
        self._connection_type: ConnectionType = ConnectionType.TUNNELING
        self._multicast_group: str = DEFAULT_MCAST_GRP
        self._selected_gateway: GatewayDescriptor | None = None
        # The gateway the user last connected to; preferred on startup auto-connect.
        self._preferred_gateway_ip: str | None = None
        self._panels: list[PanelDefinition] = []
        self._load_saved_settings()

        self._xknx: XKNX | None = None
        self._interface: ObservableKNXIPInterfaceThreaded | None = None
        self._gateway_info: GatewayDescriptor | None = None
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_thread: threading.Thread | None = None
        # Unregisters the lost/re-established callback of the open connection.
        self._unregister_link_state: Callable[[], None] | None = None
        # The configuration of the open connection, and whether its link is up.
        self._config: ConnectionConfig | None = None
        self._link_up = False
        self._watching_lost = False
        # The interface a connection failed to for missing credentials, and the next check.
        self._awaiting_credentials: GatewayDescriptor | None = None
        self._retry_on_credentials = False
        self._credentials_check_at = 0.0
        # Counts the connects and disconnects the user asked for; a reopen of an older one stops.
        self._session = 0
        # The bus monitor connection while it runs (the normal connection is closed then).
        self._busmonitor: BusmonitorConnection | None = None
        api.connection.set_busmonitor_controller(
            start=self.start_busmonitor,
            stop=self.stop_busmonitor,
            supported=lambda: not self.is_routing,
        )
        api.connection.set_reopen_controller(self.reopen)

        self._gateways: list[GatewayDescriptor] = []
        # The project revision the connection's Data Secure tables were built from.
        self._data_secure_revision = -1
        self._scanning = False
        # One-shot re-scan after autostart found nothing: on macOS the first discovery triggers the
        # "Local Network" permission prompt and returns no gateways; a delayed retry picks them up
        # once the user has allowed it. Guarded so it fires at most once per autostart.
        self._autostart_retry_done = False

        # "Devices in programming mode" window state (opened from the Diagnostics menu).
        self._progmode_open = False
        self._progmode_scanning = False
        self._progmode_scanned = (
            False  # a scan has completed at least once (empty vs. not-yet-run)
        )
        self._progmode_results: list[ScannedDevice] = []
        self._progmode_error: str | None = (
            None  # last scan's failure, shown in the window
        )

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
        return self._connection_type == ConnectionType.ROUTING

    @property
    def scanning(self) -> bool:
        """Whether a gateway discovery scan is currently running."""
        return self._scanning

    @property
    def gateways(self) -> list[GatewayDescriptor]:
        """The gateways found by the most recent scan."""
        return list(self._gateways)

    def connect_to_gateway_by_ip(self, ip: str) -> bool:
        """Connect to a previously discovered gateway by IP. Returns ``False`` if none matches."""
        gateway = next((g for g in self._gateways if g.ip_addr == ip), None)
        if gateway is None:
            return False
        self.connect_to_gateway(gateway)
        return True

    def configure(
        self, controller_ip: str, multicast_group: str, routing: bool
    ) -> None:
        """Apply and persist connection settings from the options dialog (takes effect on next
        connect)."""
        self._controller_ip = controller_ip.strip() or self._controller_ip
        self._multicast_group = multicast_group.strip() or self._multicast_group
        self._connection_type = (
            ConnectionType.ROUTING if routing else ConnectionType.TUNNELING
        )
        self._save_current_settings()
        self._log.info(
            "connection settings saved",
            controller_ip=self._controller_ip,
            routing=routing,
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
        self._api.connection.use_serial_number = bool(
            data.get("serial_number_downloads", True)
        )

    def _save_current_settings(self) -> None:
        save_settings(
            _SETTINGS,
            {
                "controller_ip": self._controller_ip,
                "multicast_group": self._multicast_group,
                "connection_type": self._connection_type.value,
                "preferred_gateway_ip": self._preferred_gateway_ip,
                "serial_number_downloads": self._api.connection.use_serial_number,
            },
        )

    def connect(self) -> None:
        """Manual IP connect (tunneling)."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        if self._busmonitor_blocks("connect"):
            return
        self._connection_type = ConnectionType.TUNNELING
        self._selected_gateway = None
        self._session += 1
        self._retry_on_credentials = False
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    def connect_to_gateway(self, gateway: GatewayDescriptor) -> None:
        """Connect to a scanned gateway (tunneling preferred, routing fallback)."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        if self._busmonitor_blocks("connect"):
            return
        if gateway.supports_tunnelling:
            self._connection_type = ConnectionType.TUNNELING
            self._controller_ip = gateway.ip_addr
        else:
            self._connection_type = ConnectionType.ROUTING
            self._multicast_group = gateway.multicast_address or DEFAULT_MCAST_GRP
        self._selected_gateway = gateway
        self._preferred_gateway_ip = gateway.ip_addr
        self._session += 1
        self._retry_on_credentials = False
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    @property
    def _connection_target(self) -> str:
        if self._connection_type == ConnectionType.ROUTING:
            return f"{self._multicast_group} (routing)"
        return self._controller_ip

    async def _connect_async(self, reopen: bool = False) -> None:
        """Open the connection; ``reopen`` replaces a lost or outdated one, so the open
        recordings go on."""
        self._log.info(
            "connecting",
            target=self._connection_target,
            mode=self._connection_type.name.lower(),
        )
        try:
            self._xknx = XKNX()
            description = (
                await self._gateway_description()
                if self._connection_type == ConnectionType.TUNNELING
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
            )
            # Replace the inert default interface so all internal send paths use ours.
            self._xknx.knxip_interface = self._interface
            await self._interface.start()
            # Secured group communication with the project's (or the keyring's) keys.
            self._xknx.cemi_handler.data_secure = await self._on_main_thread(
                self._api.connection.data_secure
            )
            self._data_secure_revision = self._api.project.revision
            self._gateway_info = gateway
            self._config = config
            self._link_up = True
            self._state = ConnectionState.CONNECTED
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
            self._api.connection.interface_max_apdu_length = interface_apdu
            self._api.connection.interface_name = (
                self._gateway_info.name
                if self._gateway_info
                else self._connection_target
            )
            self._api.connection.set_connection(self._xknx, asyncio.get_running_loop())
            self._api.connection.link_info = self._link_info()
            self._api.connection.interface_address = (
                str(gateway.individual_address)
                if gateway is not None and gateway.individual_address is not None
                else None
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
            self._log.info(
                "connected",
                target=self._connection_target,
                mode=config.connection_type.name.lower(),
            )
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
        except Exception as e:
            if isinstance(e, MissingCredentialsError):
                self._awaiting_credentials = e.gateway
                self._retry_on_credentials = True
            self._state = ConnectionState.ERROR
            self._error_message = str(e)
            self._interface = None
            self._gateway_info = None
            self._xknx = None
            self._log.error(
                "connection failed",
                target=self._connection_target,
                mode=self._connection_type.name.lower(),
                error=f"{type(e).__name__}: {e}",
            )

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
        try:
            return await request_description(self._controller_ip)
        except Exception as err:
            self._log.debug(
                "gateway description unavailable",
                ip=self._controller_ip,
                error=f"{type(err).__name__}: {err}",
            )
            return None

    def _connection_config(self, gateway: GatewayDescriptor | None) -> ConnectionConfig:
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
        if gateway is not None and gateway.tunnelling_requires_secure:
            free = [
                str(address)
                for address, slot in gateway.tunnelling_slots.items()
                if slot.usable and slot.authorized and slot.free
            ]
            tunnel = (
                connection.secure_tunnel(str(gateway.individual_address), free)
                if gateway.individual_address is not None
                else None
            )
            if tunnel is None:
                raise MissingCredentialsError(gateway)
            return ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP_SECURE,
                gateway_ip=self._controller_ip,
                individual_address=tunnel.individual_address,
                secure_config=SecureConfig(
                    user_id=tunnel.user_id,
                    user_password=tunnel.password,
                    device_authentication_password=tunnel.authentication,
                ),
                threaded=True,
            )
        return ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip=self._controller_ip,
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
            return
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
        self._data_secure_revision = revision
        self._api.connection.refresh_data_secure()

    def disconnect(self) -> None:
        if self._api.connection.busmonitor_state is BusmonitorState.RUNNING:
            self.stop_busmonitor(restore=False)
            return
        if self._state in (ConnectionState.DISCONNECTED, ConnectionState.DISCONNECTING):
            return
        self._session += 1
        self._retry_on_credentials = False
        self._state = ConnectionState.DISCONNECTING
        self._run_async(self._disconnect_async())

    async def _disconnect_async(self, reopen: bool = False) -> None:
        """Close the connection; ``reopen`` closes it for a new one, so the open recordings go
        on."""
        if self._unregister_link_state is not None:
            self._unregister_link_state()
            self._unregister_link_state = None
        was_connected = self._xknx is not None
        try:
            if self._interface is not None:
                await self._interface.stop()
        finally:
            self._interface = None
            self._gateway_info = None
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
            self._api.connection.dispatch_link_state(True)
        elif state is XknxConnectionState.DISCONNECTED:
            self._link_up = False
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
        if self._open_config(session) is None:
            return True
        gateway = self._selected_gateway
        if self._connection_type == ConnectionType.TUNNELING:
            gateway = await self._gateway_description()
            if gateway is None:
                return False
        try:
            config = await self._on_main_thread(
                lambda: self._connection_config(gateway)
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
        """While the connection is lost, check whether the interface now requires another one.
        The connection reconnects by itself with the configuration it has; one the interface no
        longer accepts is replaced."""
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
        return LinkInfo(
            name=name or "IP Tunneling",
            host=self._controller_ip,
            port=self._gateway_port(),
            connector="IpTunneling",
            protocol="Udp",
        )

    def _gateway_port(self) -> int:
        gateway = self._selected_gateway
        if gateway is not None and gateway.ip_addr == self._controller_ip:
            return gateway.port
        return DEFAULT_GATEWAY_PORT

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
        service.set_busmonitor_state(BusmonitorState.STARTING)
        self._run_async(self._start_busmonitor_async())

    async def _start_busmonitor_async(self) -> None:
        service = self._api.connection
        gateway_ip = self._controller_ip
        port = self._gateway_port()
        link = self._link_info()
        self._log.info("starting bus monitor", target=gateway_ip, port=port)
        if self._interface is not None or self._xknx is not None:
            await self._disconnect_async()
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
                target=gateway_ip,
                error=f"{type(err).__name__}: {err}",
            )
            service.set_busmonitor_state(BusmonitorState.OFF, error=reason)
            await self._restore_connection()
            return
        self._busmonitor = monitor
        service.busmonitor_link = link
        service.set_busmonitor_state(BusmonitorState.RUNNING)
        self._log.info("bus monitor running", target=gateway_ip)

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
        """Discover gateways at startup and connect to the preferred (last-used) or first one."""
        if self._state != ConnectionState.DISCONNECTED or self.busmonitor_active:
            return
        self._log.info("auto-discovering gateways")
        self.scan(auto_connect=True)

    async def _scan_async(self, auto_connect: bool = False) -> None:
        try:
            xknx = XKNX()
            scanner = GatewayScanner(xknx, timeout_in_seconds=3.0)
            self._gateways = await scanner.scan()
            self._log.info("gateway scan complete", found=len(self._gateways))
            for g in self._gateways:
                self._log.debug(
                    "gateway found",
                    name=g.name,
                    ip=g.ip_addr,
                    knx_address=str(g.individual_address or ""),
                )
            if auto_connect and not self._gateways:
                self._log.warning("no gateways found on the network")
                if (
                    not self._autostart_retry_done
                    and self._state == ConnectionState.DISCONNECTED
                ):
                    # macOS: the first discovery only triggers the "Local Network" permission prompt
                    # and returns nothing. Retry once after a delay so a granted permission takes
                    # effect without restarting the app.
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
                self.connect_to_gateway(target)
        except Exception as e:
            self._log.error("gateway scan failed", error=f"{type(e).__name__}: {e}")
        finally:
            self._scanning = False

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

    def render_status_indicator(self) -> None:
        self._refresh_data_secure()
        self._connect_with_credentials()
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
                imgui.ImVec4(0.95, 0.6, 0.15, 1.0),
                S.STATUS_BUSMONITOR.format(ip=self._controller_ip),
            )
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.STATUS_BUSMONITOR_HINT)
        elif self._state == ConnectionState.CONNECTED:
            pulse = 0.5 + 0.5 * math.sin(imgui.get_time() * 3.0)
            alpha = 0.4 + 0.6 * pulse
            draw_list.add_circle_filled(center, px(4), color_u32(0.2, 0.8, 0.3, alpha))
            draw_list.add_circle_filled(
                center, px(4 + pulse * 3), color_u32(0.2, 0.8, 0.3, 0.15 * (1 - pulse))
            )
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            target = self._connection_target
            if self._gateway_info and self._gateway_info.name:
                name = "".join(
                    c for c in self._gateway_info.name if c.isprintable()
                ).strip()
                if name:
                    target = f"{name} @ {target}"
            imgui.text(S.STATUS_CONNECTED.format(ip=target))
        elif self._state == ConnectionState.CONNECTING:
            spin = (imgui.get_time() * 4) % 1.0
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
                imgui.ImVec4(0.8, 0.2, 0.2, 1.0),
                S.STATUS_ERROR.format(error=error_short),
            )
            if self._error_message and imgui.is_item_hovered():
                imgui.set_tooltip(self._error_message)
        else:
            draw_list.add_circle_filled(center, px(4), color_u32(0.5, 0.5, 0.5, 1.0))
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            imgui.text_disabled(S.STATUS_DISCONNECTED)

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
        """The 'Devices in programming mode' window; called from the overlay pass."""
        if not self._progmode_open:
            return
        expanded, open_state = begin_tool_window(
            f"{S.PROGMODE_TITLE}###progmode_window", True, width=30 * 16, height=24 * 16
        )
        self._progmode_open = bool(open_state)
        if expanded:
            self._render_progmode_contents()
        imgui.end()

    def _render_progmode_contents(self) -> None:
        imgui.begin_disabled(self._progmode_scanning)
        if imgui.button(S.PROGMODE_RESCAN):
            self._scan_progmode()
        imgui.end_disabled()
        if self._progmode_scanning:
            imgui.same_line()
            imspinner.spinner_ang(
                "##progmode-spinner",
                px(7),
                px(2),
                color=imgui.ImColor(
                    imgui.get_style_color_vec4(imgui.Col_.tab_selected)
                ),
            )
            imgui.same_line()
            imgui.text_disabled(S.PROGMODE_SEARCHING)
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.PROGMODE_HINT)
        imgui.pop_text_wrap_pos()
        imgui.separator()

        if not self._progmode_results:
            if self._progmode_error is not None:
                imgui.push_text_wrap_pos(0.0)
                imgui.text_colored(
                    imgui.ImVec4(0.95, 0.55, 0.35, 1.0), self._progmode_error
                )
                imgui.pop_text_wrap_pos()
            elif self._progmode_scanned and not self._progmode_scanning:
                imgui.text_disabled(S.PROGMODE_NONE)
            return
        imgui.text_disabled(S.PROGMODE_COUNT.format(count=len(self._progmode_results)))
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
        if not imgui.collapsing_header(f"{header}###progmode_{index}"):
            return
        imgui.indent()
        if unprogrammed:
            imgui.text_colored(
                imgui.ImVec4(0.95, 0.75, 0.35, 1.0), S.PROGMODE_UNPROGRAMMED
            )
        elif device is not None:
            imgui.text_disabled(S.PROGMODE_IN_PROJECT.format(name=device.name))
            imgui.same_line()
            if imgui.small_button(f"{S.PROGMODE_SELECT}##progmode_sel_{index}"):
                self._api.project.selected_device = device
                self._api.project.focus_editor()
        else:
            imgui.text_disabled(S.PROGMODE_NOT_IN_PROJECT)

        overview = found.overview
        if overview is None:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(
                imgui.ImVec4(0.95, 0.55, 0.35, 1.0),
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
            imgui.text_colored(imgui.ImVec4(0.45, 0.8, 0.45, 1.0), S.PROGMODE_STATUS_OK)
        else:
            imgui.text_colored(
                imgui.ImVec4(0.95, 0.55, 0.35, 1.0), overview.error_text or ""
            )
        if overview.programming_mode:
            imgui.same_line()
            imgui.text_colored(imgui.ImVec4(0.95, 0.75, 0.35, 1.0), S.PROGMODE_FLAG_ON)

    def render_menu(self) -> None:
        if imgui.begin_menu(S.MENU_CONNECTION):
            if self.busmonitor_active:
                imgui.text(S.STATUS_BUSMONITOR.format(ip=self._controller_ip))
                imgui.text_disabled(S.STATUS_BUSMONITOR_HINT)
                imgui.separator()
                running = (
                    self._api.connection.busmonitor_state is BusmonitorState.RUNNING
                )
                if imgui.menu_item(S.MENU_STOP_BUSMONITOR, "", False, running)[0]:
                    self.stop_busmonitor()
            elif self._state == ConnectionState.CONNECTED:
                imgui.text(S.STATUS_CONNECTED_TO.format(ip=self._connection_target))
                if self._gateway_info:
                    imgui.separator()
                    imgui.text_disabled(S.GATEWAY_SECTION)
                    imgui.text(
                        "  " + S.GATEWAY_NAME.format(name=self._gateway_info.name)
                    )
                    if self._gateway_info.individual_address:
                        imgui.text(
                            "  "
                            + S.GATEWAY_ADDRESS.format(
                                address=self._gateway_info.individual_address
                            )
                        )
                    imgui.text(
                        "  "
                        + S.GATEWAY_CORE_VERSION.format(
                            version=self._gateway_info.core_version
                        )
                    )
                    services: list[str] = []
                    if self._gateway_info.supports_tunnelling:
                        services.append("Tunneling")
                    if self._gateway_info.supports_tunnelling_tcp:
                        services.append("TCP Tunneling")
                    if self._gateway_info.supports_routing:
                        services.append("Routing")
                    if self._gateway_info.supports_secure:
                        services.append("Secure")
                    if services:
                        imgui.text(
                            "  "
                            + S.GATEWAY_SERVICES.format(services=", ".join(services))
                        )
                imgui.separator()
                imgui.text_disabled(S.SECTION_DIAGNOSTICS)
                if imgui.menu_item(S.MENU_READ_PROGMODE, "", self._progmode_open)[0]:
                    self._open_progmode_window()
                imgui.separator()
                if imgui.menu_item(S.MENU_DISCONNECT, "", False)[0]:
                    self.disconnect()
            elif self._state == ConnectionState.CONNECTING:
                imgui.text_disabled(S.STATUS_CONNECTING)
            elif self._state == ConnectionState.ERROR:
                imgui.text_colored(
                    imgui.ImVec4(0.8, 0.2, 0.2, 1.0), S.STATUS_CONNECTION_FAILED
                )
                if self._error_message:
                    imgui.text_wrapped(self._error_message)
                imgui.separator()
                self._render_gateway_picker(retry_label="Retry")
            else:
                self._render_gateway_picker(retry_label=S.MENU_CONNECT)
            imgui.separator()
            changed, use_serial = imgui.menu_item(
                S.MENU_USE_SERIAL_NUMBER, "", self._api.connection.use_serial_number
            )
            if changed:
                self._api.connection.use_serial_number = use_serial
                self._save_current_settings()
            imgui.end_menu()

    def _render_gateway_picker(self, retry_label: str) -> None:
        if imgui.is_window_appearing() and not self._scanning:
            self.scan()

        imgui.text_disabled(S.SECTION_DISCOVERED)
        if self._scanning:
            imgui.same_line()
            imspinner.spinner_ang(
                "##scan-spinner",
                px(6),
                px(2),
                color=imgui.ImColor(
                    imgui.get_style_color_vec4(imgui.Col_.tab_selected)
                ),
            )
        if self._gateways:
            for gw in self._gateways:
                tags: list[str] = []
                if gw.supports_tunnelling:
                    tags.append("T")
                if gw.supports_routing:
                    tags.append("R")
                if gw.supports_secure:
                    tags.append("S")
                tag_str = "/".join(tags)
                label = f"{gw.name}  ({gw.ip_addr})" + (
                    f"  [{tag_str}]" if tag_str else ""
                )
                is_connected_to = (
                    self._state == ConnectionState.CONNECTED
                    and self._selected_gateway is not None
                    and self._selected_gateway.ip_addr == gw.ip_addr
                    and self._selected_gateway.port == gw.port
                )
                if imgui.menu_item(label, "", is_connected_to)[0]:
                    self.connect_to_gateway(gw)
        elif not self._scanning:
            imgui.text_disabled(S.NO_GATEWAYS_FOUND)

        imgui.separator()
        imgui.text_disabled(S.SECTION_MANUAL)
        imgui.set_next_item_width(px(180))
        _, self._controller_ip = imgui.input_text("IP##manual", self._controller_ip)
        if imgui.menu_item(retry_label, "", False)[0]:
            self.connect()

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
