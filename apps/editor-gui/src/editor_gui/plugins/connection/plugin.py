import asyncio
import contextlib
import math
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from enum import Enum
from typing import TYPE_CHECKING, Any

from imgui_bundle import hello_imgui, imgui, imspinner
from xknx import XKNX
from xknx.exceptions import ConfirmationError
from xknx.io.connection import ConnectionConfig, ConnectionType
from xknx.io.const import DEFAULT_MCAST_GRP
from xknx.io.gateway_scanner import GatewayDescriptor, GatewayScanner
from xknx.io.self_description import request_description

from editor_gui.color import color_u32
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.connection.interface import ObservableKNXIPInterfaceThreaded
from editor_gui.plugins.connection.strings import S
from editor_gui.programming import (
    DEFAULT_INDIVIDUAL_ADDRESS,
    DeviceOverview,
    ScannedDevice,
)
from editor_gui.settings import load_settings, save_settings
from editor_gui.widgets.dpi import label_column_width, px, px_vec2
from xknxeditor.prod.app_id import parse_app_id

if TYPE_CHECKING:
    from xknxeditor.catalog import ProductSummary

_SETTINGS = "connection"

# Delay before the one-shot autostart re-scan (macOS Local Network permission grant window).
_AUTOSTART_RETRY_SECONDS = 30.0


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

        self._gateways: list[GatewayDescriptor] = []
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

    def _save_current_settings(self) -> None:
        save_settings(
            _SETTINGS,
            {
                "controller_ip": self._controller_ip,
                "multicast_group": self._multicast_group,
                "connection_type": self._connection_type.value,
                "preferred_gateway_ip": self._preferred_gateway_ip,
            },
        )

    def connect(self) -> None:
        """Manual IP connect (tunneling)."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        self._connection_type = ConnectionType.TUNNELING
        self._selected_gateway = None
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    def connect_to_gateway(self, gateway: GatewayDescriptor) -> None:
        """Connect to a scanned gateway (tunneling preferred, routing fallback)."""
        if self._state in (ConnectionState.CONNECTING, ConnectionState.CONNECTED):
            return
        if gateway.supports_tunnelling:
            self._connection_type = ConnectionType.TUNNELING
            self._controller_ip = gateway.ip_addr
        else:
            self._connection_type = ConnectionType.ROUTING
            self._multicast_group = gateway.multicast_address or DEFAULT_MCAST_GRP
        self._selected_gateway = gateway
        self._preferred_gateway_ip = gateway.ip_addr
        self._state = ConnectionState.CONNECTING
        self._error_message = None
        self._save_current_settings()
        self._run_async(self._connect_async())

    @property
    def _connection_target(self) -> str:
        if self._connection_type == ConnectionType.ROUTING:
            return f"{self._multicast_group} (routing)"
        return self._controller_ip

    async def _connect_async(self) -> None:
        self._log.info(
            "connecting",
            target=self._connection_target,
            mode=self._connection_type.name.lower(),
        )
        try:
            self._xknx = XKNX()
            if self._connection_type == ConnectionType.ROUTING:
                config = ConnectionConfig(
                    connection_type=ConnectionType.ROUTING,
                    multicast_group=self._multicast_group,
                    threaded=True,
                )
            else:
                config = ConnectionConfig(
                    connection_type=ConnectionType.TUNNELING,
                    gateway_ip=self._controller_ip,
                    threaded=True,
                )
            self._interface = ObservableKNXIPInterfaceThreaded(
                xknx=self._xknx,
                connection_config=config,
                raw_cemi_callback=self._api.connection.dispatch_raw_cemi,
            )
            # Replace the inert default interface so all internal send paths use ours.
            self._xknx.knxip_interface = self._interface
            await self._interface.start()
            if self._selected_gateway is not None:
                self._gateway_info = self._selected_gateway
            elif self._connection_type == ConnectionType.TUNNELING:
                try:
                    self._gateway_info = await request_description(self._controller_ip)
                except Exception as desc_err:
                    self._gateway_info = None
                    self._log.debug(
                        "gateway description unavailable",
                        ip=self._controller_ip,
                        error=f"{type(desc_err).__name__}: {desc_err}",
                    )
            else:
                self._gateway_info = None
            self._state = ConnectionState.CONNECTED
            interface_apdu: int | None = None
            try:
                interface_apdu = await self._interface.read_max_apdu_length()
            except Exception as feature_err:
                self._log.debug(
                    "interface max APDU length unavailable",
                    error=f"{type(feature_err).__name__}: {feature_err}",
                )
            self._api.connection.interface_max_apdu_length = interface_apdu
            self._api.connection.interface_name = (
                self._gateway_info.name
                if self._gateway_info
                else self._connection_target
            )
            self._api.connection.set_connection(self._xknx, asyncio.get_running_loop())
            self._api.connection.dispatch_connected()
            self._log.info("connected", target=self._connection_target)
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

    def disconnect(self) -> None:
        if self._state in (ConnectionState.DISCONNECTED, ConnectionState.DISCONNECTING):
            return
        self._state = ConnectionState.DISCONNECTING
        self._run_async(self._disconnect_async())

    async def _disconnect_async(self) -> None:
        try:
            if self._interface is not None:
                await self._interface.stop()
        finally:
            self._interface = None
            self._gateway_info = None
            self._xknx = None
            self._state = ConnectionState.DISCONNECTED
            self._api.connection.set_connection(None, None)
            self._log.info("disconnected")

    def scan(self, auto_connect: bool = False) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._run_async(self._scan_async(auto_connect))

    def autostart(self) -> None:
        """Discover gateways at startup and connect to the preferred (last-used) or first one."""
        if self._state != ConnectionState.DISCONNECTED:
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
        if self._interface is not None:
            self._run_async(self._disconnect_async())
        if self._async_loop is not None:
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)

    def render_status_indicator(self) -> None:
        draw_list = imgui.get_window_draw_list()
        cursor = imgui.get_cursor_screen_pos()
        text_height = imgui.get_text_line_height()
        center = imgui.ImVec2(cursor.x + px(5), cursor.y + text_height / 2)

        if self._state == ConnectionState.CONNECTED:
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
            imgui.text_disabled("Connecting...")
        elif self._state == ConnectionState.ERROR:
            draw_list.add_circle_filled(center, px(4), color_u32(0.8, 0.2, 0.2, 1.0))
            imgui.dummy(px_vec2(12, 0))
            imgui.same_line()
            error_short = (
                self._error_message[:60] if self._error_message else "Unknown error"
            )
            imgui.text_colored(
                imgui.ImVec4(0.8, 0.2, 0.2, 1.0), f"Error: {error_short}"
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
        imgui.set_next_window_size(
            hello_imgui.em_to_vec2(30.0, 24.0), imgui.Cond_.first_use_ever
        )
        expanded, open_state = imgui.begin(
            f"{S.PROGMODE_TITLE}###progmode_window", True
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
            f"V{overview.application_version}" if overview.application_version else ""
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
            if self._state == ConnectionState.CONNECTED:
                imgui.text(S.STATUS_CONNECTED_TO.format(ip=self._connection_target))
                if self._gateway_info:
                    imgui.separator()
                    imgui.text_disabled("Gateway")
                    imgui.text(f"  Name: {self._gateway_info.name}")
                    if self._gateway_info.individual_address:
                        imgui.text(
                            f"  KNX Address: {self._gateway_info.individual_address}"
                        )
                    imgui.text(f"  Core Version: {self._gateway_info.core_version}")
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
                        imgui.text(f"  Services: {', '.join(services)}")
                imgui.separator()
                imgui.text_disabled(S.SECTION_DIAGNOSTICS)
                if imgui.menu_item(S.MENU_READ_PROGMODE, "", self._progmode_open)[0]:
                    self._open_progmode_window()
                imgui.separator()
                if imgui.menu_item(S.MENU_DISCONNECT, "", False)[0]:
                    self.disconnect()
            elif self._state == ConnectionState.CONNECTING:
                imgui.text_disabled("Connecting...")
            elif self._state == ConnectionState.ERROR:
                imgui.text_colored(
                    imgui.ImVec4(0.8, 0.2, 0.2, 1.0), "Connection failed"
                )
                if self._error_message:
                    imgui.text_wrapped(self._error_message)
                imgui.separator()
                self._render_gateway_picker(retry_label="Retry")
            else:
                self._render_gateway_picker(retry_label=S.MENU_CONNECT)
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
