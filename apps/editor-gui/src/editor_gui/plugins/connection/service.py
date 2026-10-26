from __future__ import annotations

import asyncio
import functools
import threading
import time
import traceback
from collections.abc import Awaitable, Callable, Collection, Coroutine
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, cast

from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.exceptions import ManagementConnectionError
from xknx.management.procedures.device.dm_restart_r_co import dm_restart
from xknx.management.procedures.network.nm_individual_address_read import (
    nm_individual_address_read,
)
from xknx.management.procedures.network.nm_individual_address_serial_number_write import (
    nm_individual_address_serial_number_write,
)
from xknx.telegram import GroupAddress, Telegram
from xknx.telegram.apci import GroupValueRead, GroupValueWrite

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.secure.data_secure import DataSecure

    from editor_gui.device import Device
    from editor_gui.net import TelegramSource
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.keyring.service import KeyringService
    from xknxeditor.catalog.core.hardware import FactoryData
    from xknxeditor.download.data_secure import DeviceSecurity
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.ip_config import IpDeviceConfig
    from xknxeditor.download.ip_secure import IpSecureConfig
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.prod import Application, MasterData
    from xknxeditor.proj.core.service import DeviceKeys
    from xknxeditor.recover import AppId


def _exc_traceback(exc: BaseException) -> str:
    """The full formatted traceback of ``exc`` (including any chained cause). Logged on a failed
    bus operation so an intermittent, hard-to-reproduce error (e.g. a cross-thread DB race) keeps
    its stack instead of only a one-line message."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


@dataclass(frozen=True, slots=True)
class LinkInfo:
    """The connection the bus is reached through, as recordings describe it."""

    name: str
    host: str
    port: int
    # "IpTunneling" or "IpRouting".
    connector: str
    # "Udp" or "Tcp"; empty for routing.
    protocol: str = ""
    medium: str = "Tp"

    @property
    def options(self) -> str:
        """The connection's option string, e.g. for a recording's start entry."""
        if self.connector == "IpRouting":
            return (
                f"Type=IpRouting;MulticastAddress={self.host};"
                f'Name="{self.name}";AutoReconnect=True'
            )
        return (
            f"Type={self.connector};HostAddress={self.host};ProtocolType={self.protocol};"
            f'Name="{self.name}";AutoReconnect=True'
        )

    @property
    def endpoint_name(self) -> str:
        """The name with the endpoint, e.g. ``IP Tunneling (192.168.1.10:3671 Udp)``."""
        suffix = f" {self.protocol}" if self.protocol else ""
        return f"{self.name} ({self.host}:{self.port}{suffix})"


class DeviceCertificateRequired(Exception):
    """A secure download needs the device's FDSK: it still uses its factory key, which the
    project does not know (no device certificate for its serial number)."""


class OperationNotStarted(Exception):
    """An operation that a connection was opened for did not start (the reason is the one it
    gave itself, e.g. on its queue entry)."""


class BusmonitorState(Enum):
    OFF = "off"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class OperationConnector(Protocol):
    """Opens a connection for online operations while none is open (the connection plugin):
    through the backbone's Bus Connection, else the one the user selects."""

    def open(self) -> Future[None]:
        """A connection for one more operation: done once it is open, canceled when the user
        canceled the selection. Each call is balanced by ``release``."""
        ...

    def release(self) -> None:
        """An operation has ended; the connection closes after the last one."""
        ...

    def opened_for_operations(self) -> bool:
        """Whether the open connection was opened for operations: one started now uses it
        too, so it stays open until that one has ended."""
        ...

    def submit(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the main thread."""
        ...


def _connects[F: Callable[..., Any]](op: str) -> Callable[[F], F]:
    """An online operation: while no connection is open it first connects through the
    ``OperationConnector`` (when there is one), then runs; its future is the operation's."""

    def decorate(method: F) -> F:
        @functools.wraps(method)
        def run(self: ConnectionService, *args: Any, **kwargs: Any) -> Any:
            connector = self.operation_connector
            if (
                connector is not None
                and not self.busmonitor_active
                and (self.xknx is None or connector.opened_for_operations())
            ):
                return self.connect_for_operation(
                    op, lambda: method(self, *args, **kwargs)
                )
            return method(self, *args, **kwargs)

        return cast(F, run)

    return decorate


@dataclass(frozen=True)
class SecureTunnel:
    """The credentials of a KNX IP Secure tunnel: its user id, tunnelling address, password and
    the interface's device authentication code."""

    user_id: int
    individual_address: str
    password: str
    authentication: str | None = None


class ConnectionService:
    def __init__(self) -> None:
        self._log: Logger
        self._raw_cemi_listeners: list[Callable[[bytes, TelegramSource], None]] = []
        self._connected_listeners: list[Callable[[], None]] = []
        self._disconnected_listeners: list[Callable[[], None]] = []
        self._sent_cemi_listeners: list[Callable[[bytes], None]] = []
        self._link_state_listeners: list[Callable[[bool], None]] = []
        self._busmonitor_listeners: list[Callable[[bytes], None]] = []
        self._busmonitor_state_listeners: list[Callable[[BusmonitorState], None]] = []
        # The connection the bus is reached through, set by the connection plugin.
        self.link_info: LinkInfo | None = None
        # Bus monitor mode, driven by the connection plugin through these callbacks.
        self._busmonitor_start: Callable[[], None] | None = None
        self._busmonitor_stop: Callable[[], None] | None = None
        self._busmonitor_supported: Callable[[], bool] | None = None
        self._busmonitor_state = BusmonitorState.OFF
        self.busmonitor_error: str | None = None
        # Details of the last failed start, for the log and tooltips.
        self.busmonitor_error_detail: str | None = None
        # Link info of the running bus monitor connection.
        self.busmonitor_link: LinkInfo | None = None
        self._xknx: XKNX | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # The task behind each future of run_async while it runs (cancel_operation).
        self._operation_tasks: dict[Future[Any], list[asyncio.Task[Any]]] = {}
        # Global KNX master data (mask-version default procedures), injected at
        # startup. Required to resolve an UNLOAD scope and default/merged procedures.
        self.master: MasterData | None = None
        # Maximum APDU length the connected interface reports, if it does.
        self.interface_max_apdu_length: int | None = None
        # Display name of the connected interface.
        self.interface_name: str | None = None
        # The individual address of the interface the connection runs through.
        self.interface_address: str | None = None
        self._reopen: Callable[[], None] | None = None
        # Finds a KNXnet/IP device on the network by its serial number (see find_device_ip).
        self._find_device_ip: Callable[[bytes], Future[str | None]] | None = None
        # Keyring service (KNX Data Secure), injected at startup. Used to look up a device's tool
        # key so a point-to-point download/test is secured when the device was commissioned secure.
        self.keyring: KeyringService | None = None
        # The device's tool keys from the open project; they take precedence over the keyring.
        self.project_keys: Callable[[Device], DeviceKeys | None] | None = None
        # The open project's KNX IP Secure tunnels of an interface (by its individual address),
        # its routing backbone ``(key, latency ms)`` and its Data Secure tables ``(group keys,
        # senders with their last valid Sequence Number)``; they take precedence over the keyring.
        self.project_tunnels: Callable[[str], list[SecureTunnel]] | None = None
        self.project_backbone: Callable[[], tuple[bytes, int] | None] | None = None
        self._group_keys: dict[int, bytes] = {}
        self.project_data_secure: (
            Callable[[], tuple[dict[int, bytes], dict[int, int]] | None] | None
        ) = None
        # Download individual addresses by serial number when the device's is known.
        self.use_serial_number = True
        # Current point-to-point bus operation, so the UI can show a
        # "programming/testing in progress" indicator. Set/cleared from worker threads.
        self._busy_lock = threading.Lock()
        self._busy: tuple[str, str] | None = None
        self._busy_progress: tuple[int, int] | None = None
        self._program_notice: tuple[bool, float] | None = None
        # Timestamp of the last connection-requiring call made while disconnected,
        # so the status bar can show a brief "no connection" notice.
        self._not_connected_notice: float | None = None
        # Opens a connection for an operation while none is open (set by the connection plugin),
        # and the operation behind each future connect_for_operation returned, for its cancel.
        self.operation_connector: OperationConnector | None = None
        self._connected_operations: dict[Future[Any], Future[Any] | None] = {}

    @property
    def busy_operation(self) -> tuple[str, str] | None:
        """``(kind, address)`` of the running bus op (``kind`` is ``program``/``test``), or ``None``."""
        with self._busy_lock:
            return self._busy

    @property
    def busy_progress(self) -> tuple[int, int] | None:
        """``(done, total)`` load-control progress of the running download, or ``None``."""
        with self._busy_lock:
            return self._busy_progress

    def _set_busy(self, kind: str, address: str) -> None:
        with self._busy_lock:
            self._busy = (kind, address)
            self._busy_progress = None

    def begin_operation(self, kind: str, address: str) -> bool:
        """Acquire the exclusive point-to-point operation slot.

        Only one bus operation (program / test / recover) may use the single
        KNXnet/IP tunnel connection at a time. Returns ``False`` if one is already
        running (the caller must not start), otherwise marks the bus busy and
        returns ``True``. Release with :meth:`end_operation`."""
        with self._busy_lock:
            if self._busy is not None:
                return False
            self._busy = (kind, address)
            self._busy_progress = None
            return True

    def end_operation(self) -> None:
        """Release the operation slot acquired by :meth:`begin_operation`."""
        self._clear_busy()

    def _clear_busy(self) -> None:
        with self._busy_lock:
            self._busy = None
            self._busy_progress = None

    def _on_program_progress(self, done: int, total: int) -> None:
        with self._busy_lock:
            self._busy_progress = (done, total)

    def set_logger(self, log: Logger) -> None:
        self._log = log

    def not_connected(self, op: str) -> bool:
        """Guard for every connection-requiring call: log + a status-bar notice if no KNX link.

        Returns ``True`` when there is no connection (the caller must abort)."""
        if self._xknx is not None:
            return False
        if self.busmonitor_active:
            self._log.error(
                f"{op} failed: the bus monitor is running, nothing can be sent until it is "
                "stopped"
            )
        else:
            self._log.error(f"{op} failed: no KNX connection")
        with self._busy_lock:
            self._not_connected_notice = time.monotonic()
        return True

    def can_reach_bus(self) -> bool:
        """Whether an online operation can run: a connection is open, or one can be opened for
        it (see ``OperationConnector``) while the bus monitor does not hold the interface."""
        if self.xknx is not None:
            return True
        return self.operation_connector is not None and not self.busmonitor_active

    def waiting_for_connection(self, future: Future[Any] | None) -> bool:
        """Whether the operation behind ``future`` (from ``connect_for_operation``) still waits
        for its connection to open."""
        return (
            future is not None
            and future in self._connected_operations
            and self._connected_operations[future] is None
        )

    def connect_for_operation(
        self, op: str, start: Callable[[], Future[Any] | None]
    ) -> Future[Any] | None:
        """Run ``start`` (an online operation) once a connection for it is open, see
        ``OperationConnector``; the connection closes after the last operation using it. The
        future returned ends as the operation's; it is canceled when the user canceled the
        selection of a connection. Without a connector the operation is refused (None)."""
        connector = self.operation_connector
        if connector is None:
            self.not_connected(op)
            return None
        result: Future[Any] = Future()
        self._connected_operations[result] = None
        result.add_done_callback(lambda f: self._connected_operations.pop(f, None))

        def begin() -> None:
            # On the main thread, where the connection is opened.
            opening = connector.open()
            opening.add_done_callback(lambda _f: connector.submit(lambda: run(opening)))

        def run(opening: Future[None]) -> None:
            if result.done():  # canceled while it connected
                connector.release()
                return
            if opening.cancelled():
                connector.release()
                result.cancel()
                return
            error = opening.exception()
            if error is not None:
                connector.release()
                self.not_connected(op)
                result.set_exception(error)
                return
            try:
                operation = start()
            except BaseException as exc:
                connector.release()
                result.set_exception(exc)
                return
            if operation is None:
                connector.release()
                result.set_exception(OperationNotStarted(f"{op} could not start"))
                return
            self._connected_operations[result] = operation

            def ended(done: Future[Any]) -> None:
                # The operation's outcome first: a queue that starts the next operation then
                # finds the connection still open.
                if result.done():
                    pass
                elif done.cancelled():
                    result.cancel()
                elif (failure := done.exception()) is not None:
                    result.set_exception(failure)
                else:
                    result.set_result(done.result())
                connector.submit(connector.release)

            operation.add_done_callback(ended)

        connector.submit(begin)
        return result

    def not_connected_notice(self, max_age: float = 6.0) -> bool:
        """Whether a recent call was refused for lack of a connection (for the status bar)."""
        with self._busy_lock:
            stamp = self._not_connected_notice
        return (
            stamp is not None
            and self._xknx is None
            and time.monotonic() - stamp <= max_age
        )

    def add_raw_cemi_listener(
        self, callback: Callable[[bytes, TelegramSource], None]
    ) -> None:
        self._raw_cemi_listeners.append(callback)

    def add_connected_listener(self, callback: Callable[[], None]) -> None:
        self._connected_listeners.append(callback)

    def add_disconnected_listener(self, callback: Callable[[], None]) -> None:
        """Called when the connection is closed on purpose (not when it is lost)."""
        self._disconnected_listeners.append(callback)

    def add_sent_cemi_listener(self, callback: Callable[[bytes], None]) -> None:
        """Called with every cEMI frame sent through the connection (L_Data.req)."""
        self._sent_cemi_listeners.append(callback)

    def add_link_state_listener(self, callback: Callable[[bool], None]) -> None:
        """Called when an open connection is lost (``False``) or re-established (``True``)."""
        self._link_state_listeners.append(callback)

    def add_busmonitor_listener(self, callback: Callable[[bytes], None]) -> None:
        """Called with every cEMI frame (L_Busmon.ind) of the bus monitor connection."""
        self._busmonitor_listeners.append(callback)

    def add_busmonitor_state_listener(
        self, callback: Callable[[BusmonitorState], None]
    ) -> None:
        self._busmonitor_state_listeners.append(callback)

    def dispatch_disconnected(self) -> None:
        for cb in self._disconnected_listeners:
            cb()

    def dispatch_sent_cemi(self, raw_cemi: bytes) -> None:
        for cb in self._sent_cemi_listeners:
            cb(raw_cemi)

    def dispatch_link_state(self, established: bool) -> None:
        for cb in self._link_state_listeners:
            cb(established)

    def dispatch_busmonitor_cemi(self, raw_cemi: bytes) -> None:
        for cb in self._busmonitor_listeners:
            cb(raw_cemi)

    def set_reopen_controller(self, reopen: Callable[[], None]) -> None:
        """How the connection is opened anew when its interface requires another one."""
        self._reopen = reopen

    def set_device_ip_finder(self, find: Callable[[bytes], Future[str | None]]) -> None:
        """How a KNXnet/IP device is found on the network by its serial number."""
        self._find_device_ip = find

    def find_device_ip(self, serial: bytes) -> Future[str | None] | None:
        """Search the network for the KNXnet/IP device with ``serial`` (also without a bus
        connection): a future of its IP address, None when it does not answer. None when
        there is no way to search."""
        if self._find_device_ip is None:
            return None
        return self._find_device_ip(serial)

    # --- bus monitor mode -----------------------------------------------------

    def set_busmonitor_controller(
        self,
        start: Callable[[], None],
        stop: Callable[[], None],
        supported: Callable[[], bool],
    ) -> None:
        self._busmonitor_start = start
        self._busmonitor_stop = stop
        self._busmonitor_supported = supported

    @property
    def busmonitor_state(self) -> BusmonitorState:
        return self._busmonitor_state

    @property
    def busmonitor_active(self) -> bool:
        """Whether the bus monitor connection is running, being set up or being closed."""
        return self._busmonitor_state is not BusmonitorState.OFF

    def busmonitor_supported(self) -> bool:
        """Whether the configured connection type can run a bus monitor (tunnelling)."""
        return self._busmonitor_supported is not None and self._busmonitor_supported()

    def set_busmonitor_state(
        self, state: BusmonitorState, error: str | None = None
    ) -> None:
        """Set by the connection plugin; ``error`` is the reason a start failed."""
        self._busmonitor_state = state
        if error is not None or state is BusmonitorState.STARTING:
            self.busmonitor_error = error
        for cb in self._busmonitor_state_listeners:
            cb(state)

    def start_busmonitor(self) -> None:
        """Close the normal connection and connect in bus monitor mode."""
        if self._busmonitor_start is not None and not self.busmonitor_active:
            self._busmonitor_start()

    def stop_busmonitor(self) -> None:
        """Close the bus monitor connection and restore the normal connection."""
        if self._busmonitor_stop is not None and self.busmonitor_active:
            self._busmonitor_stop()

    def dispatch_raw_cemi(self, raw_cemi: bytes) -> None:
        from editor_gui.net import TelegramSource

        self._dispatch_cemi(raw_cemi, TelegramSource.CONNECTION)

    def dispatch_proxy_cemi(self, raw_cemi: bytes) -> None:
        from editor_gui.net import TelegramSource

        self._dispatch_cemi(raw_cemi, TelegramSource.PROXY)

    def dispatch_virtual_cemi(self, raw_cemi: bytes) -> None:
        from editor_gui.net import TelegramSource

        self._dispatch_cemi(raw_cemi, TelegramSource.VIRTUAL)

    def _dispatch_cemi(self, raw_cemi: bytes, source: TelegramSource) -> None:
        for cb in self._raw_cemi_listeners:
            cb(raw_cemi, source)

    def dispatch_connected(self) -> None:
        for cb in self._connected_listeners:
            cb()

    def set_connection(
        self,
        xknx: XKNX | None,
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        self._xknx = xknx
        self._loop = loop
        if xknx is None:
            self.interface_max_apdu_length = None
            self.interface_name = None
            self.interface_address = None
            self.link_info = None

    @property
    def xknx(self) -> XKNX | None:
        return self._xknx

    @property
    def _connected_xknx(self) -> XKNX:
        """The connection, for the calls made after ``not_connected`` returned ``False``."""
        return cast("XKNX", self._xknx)

    def connection_label(self, *, with_address: bool) -> str | None:
        """The connected interface's name, optionally prefixed with our individual address."""
        xknx = self._xknx
        if xknx is None:
            return None
        name = self.interface_name or ""
        if not with_address:
            return name or None
        return f"{xknx.current_address} {name}".strip()

    def send_cemi(self, raw_cemi: bytes) -> Future[Any] | None:
        if self.not_connected("send_cemi"):
            return None
        self._log.debug("send_cemi", hex=raw_cemi.hex(" "))
        try:
            cemi = CEMIFrame.from_knx(raw_cemi)
        except Exception as e:
            # Unparseable frame cannot be re-serialized by xknx; drop rather than crash.
            self._log.error(
                "send_cemi: could not parse CEMI, not delivered",
                error=str(e),
                hex=raw_cemi.hex(" "),
            )
            return None
        reencoded = cemi.to_knx()
        if reencoded != raw_cemi:
            self._log.error(
                "send_cemi: cemi round-trip mismatch, delivering reencoded form",
                original=raw_cemi.hex(" "),
                reencoded=reencoded.hex(" "),
            )
        future = self.run_async(self._connected_xknx.knxip_interface.send_cemi(cemi))
        if future is not None:
            future.add_done_callback(self._log_send_cemi_result)
        return future

    def send_group_telegram(
        self, address: str, payload: GroupValueRead | GroupValueWrite
    ) -> Future[Any] | None:
        """Send a group read or write to ``address`` (e.g. ``1/2/3``)."""
        telegram = Telegram(destination_address=GroupAddress(address), payload=payload)
        frame = CEMIFrame(
            code=CEMIMessageCode.L_DATA_REQ,
            data=CEMILData.init_from_telegram(telegram),
        )
        return self.send_cemi(frame.to_knx())

    def _log_send_cemi_result(self, future: Future[Any]) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error("send_cemi failed", error=str(exc))
        else:
            self._log.debug("send_cemi ok")

    def send_group_value(
        self, address: str, payload: GroupValueRead | GroupValueWrite
    ) -> Future[Any] | None:
        """Read or write the value of the group address ``address`` (its Read and Write commands):
        a group telegram, through a connection opened for it while none is open."""
        return self._send_group_value(address, payload)

    @_connects("send_group_value")
    def _send_group_value(
        self, address: str, payload: GroupValueRead | GroupValueWrite
    ) -> Future[Any] | None:
        return self.send_group_telegram(address, payload)

    @_connects("read_programming_mode_devices")
    def read_programming_mode_devices(self, timeout: float = 3.0) -> Future[Any] | None:
        if self.not_connected("read_programming_mode_devices"):
            return None
        return self.run_async(
            nm_individual_address_read(self._connected_xknx, timeout=timeout)
        )

    @_connects("scan_programming_mode")
    def scan_programming_mode(self, timeout: float = 3.0) -> Future[Any] | None:
        """Find devices in programming mode and read each one's dossier off the bus.

        Holds the exclusive bus slot for the whole scan (broadcast + per-device point-to-point
        reads) and releases it in the done-callback. The result is a list of
        :class:`~editor_gui.programming.ScannedDevice`; the caller attaches its own callback to
        consume it."""
        if self.not_connected("scan_programming_mode"):
            return None
        from editor_gui.programming import scan_programming_mode_devices

        if not self.begin_operation("scan_progmode", "broadcast"):
            self._log.warning("A bus operation is already running")
            return None
        self._log.info("Scanning for devices in programming mode")
        future = self.run_async(
            scan_programming_mode_devices(self._connected_xknx, timeout=timeout)
        )
        if future is not None:
            future.add_done_callback(self._log_progmode_scan_result)
        else:
            self._clear_busy()
        return future

    def _log_progmode_scan_result(self, future: Future[Any]) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error("Programming-mode scan failed", error=str(exc))
            return
        results = future.result()
        self._log.info(
            "Devices in programming mode",
            count=len(results),
            addresses=[r.address for r in results],
        )

    @_connects("assign_individual_address_by_serial")
    def assign_individual_address_by_serial(
        self, serial: bytes, address: str
    ) -> Future[Any] | None:
        if self.not_connected("assign_individual_address_by_serial"):
            return None
        self._log.debug(
            "Assigning individual address by serial",
            address=address,
            serial=serial.hex(),
        )
        return self.run_async(
            nm_individual_address_serial_number_write(
                self._connected_xknx, serial, address
            )
        )

    @_connects("assign_individual_address")
    def assign_individual_address(self, address: str) -> Future[Any] | None:
        if self.not_connected("assign_individual_address"):
            return None
        self._log.debug("Assigning individual address", address=address)
        # Write the address to the device in programming mode without restarting it. xknx's
        # nm_individual_address_write restarts the device here, which leaves it briefly unreachable
        # and races a following download; program_individual_address writes directly and leaves the
        # device running and in programming mode.
        from xknxeditor.download.commissioning import program_individual_address

        return self.run_async(program_individual_address(self._connected_xknx, address))

    def assign_individual_address_for_device(
        self, device: Device
    ) -> Future[Any] | None:
        if not device.individual_address:
            self._log.warning(
                "Device has no individual address assigned", device=device.name
            )
            return None
        return self.assign_individual_address(device.individual_address)

    @_connects("restart_device")
    def restart_device(self, device: Device) -> Future[Any] | None:
        """Restart (reboot) a device over the bus via A_Restart (non-destructive)."""
        if self.not_connected("restart_device"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        if not self.begin_operation("restart", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        self._log.info("Restarting device", device=device.name)
        security = self._security_for(device)
        if security is not None:
            # A device commissioned securely only takes a restart secured with its Tool Key.
            from xknx.telegram.apci import Restart

            future = self.run_async(
                self._send_secured(device.individual_address, Restart(), security)
            )
        else:
            future = self.run_async(
                dm_restart(self._connected_xknx, device.individual_address)
            )
        if future is not None:
            future.add_done_callback(
                functools.partial(
                    self._log_restart_result, address=device.individual_address
                )
            )
        else:
            self._clear_busy()
        return future

    def _log_restart_result(
        self, future: Future[Any], address: str | None = None
    ) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error("Restart failed", address=address, error=str(exc))
            return
        self._log.info("Device restarted", address=address)

    @_connects("master_reset_device")
    def master_reset_device(
        self, device: Device, erase_code: int
    ) -> Future[Any] | None:
        """DESTRUCTIVE: master-reset a device over the bus via A_Restart_Master_Reset. ``erase_code``
        follows the KNX spec (2=FactoryReset, 3=ResetIA, 4=ResetAP, 5=ResetParam, 6=ResetLinks,
        7=FactoryResetWithoutIA, 1=ConfirmedRestart). The device must be re-commissioned afterwards.
        Fire-and-forget (like Basic Restart): the device drops the connection while resetting."""
        if self.not_connected("master_reset_device"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        if not self.begin_operation("reset", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        self._log.warning(
            "Master reset (destructive)", device=device.name, erase_code=erase_code
        )
        security = self._security_for(device)
        if security is not None:
            # Secured with the Tool Key the device holds (3/5/1 6.3.10): it rejects a plain one.
            from xknx.telegram.apci import RestartMasterReset

            future = self.run_async(
                self._send_secured(
                    device.individual_address,
                    RestartMasterReset(erase_code=erase_code),
                    security,
                )
            )
        else:
            future = self.run_async(
                self._do_master_reset(device.individual_address, erase_code)
            )
        if future is not None:
            future.add_done_callback(
                functools.partial(
                    self._log_reset_result, address=device.individual_address
                )
            )
        else:
            self._clear_busy()
        return future

    async def _send_secured(
        self, address: str, payload: Any, security: DeviceSecurity
    ) -> None:
        """Send a restart ``payload`` over a connection secured with the device's Tool Key; the
        device drops the connection while restarting, so the closing is not checked."""
        import contextlib

        from xknx.telegram.address import IndividualAddress

        from xknxeditor.download.session import management_session

        assert self._xknx is not None
        manager = management_session(self._xknx, IndividualAddress(address), security)
        connection = await manager.open()
        try:
            await connection.send_data(payload, wait_for_ack=False)
        finally:
            with contextlib.suppress(Exception):
                await manager.close()

    async def _do_master_reset(self, address: str, erase_code: int) -> None:
        import contextlib

        from xknx.telegram.address import IndividualAddress
        from xknx.telegram.apci import RestartMasterReset

        # A master reset makes the device drop the link immediately, so the closing disconnect
        # normally fails - suppress it so a successful reset is not reported as an error. A failure
        # of the send itself still propagates (real error).
        target = IndividualAddress(address)
        conn = await self._connected_xknx.management.connect(target)
        try:
            await conn.send_data(
                RestartMasterReset(erase_code=erase_code), wait_for_ack=False
            )
        finally:
            with contextlib.suppress(Exception):
                await self._connected_xknx.management.disconnect(target)

    def _log_reset_result(
        self, future: Future[Any], address: str | None = None
    ) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error("Master reset failed", address=address, error=str(exc))
            return
        self._log.info("Master reset sent", address=address)

    def security_for(self, device: Device) -> DeviceSecurity | None:
        return self._security_for(device)

    def _security_for(self, device: Device) -> DeviceSecurity | None:
        """KNX Data Secure tool key for ``device``, or ``None`` (program in the clear). A non-``None``
        result secures every management APDU of the download.

        The project's keys come first: the tool key the device holds (``LoadedToolKey``). A device
        whose tool key was never loaded into it still has its factory key; loading the tool key
        (secure commissioning) is not supported, so it is programmed in the clear. Without project
        keys for the device, the tool key comes from the loaded keyring."""
        keys = self.project_keys(device) if self.project_keys is not None else None
        if keys is not None:
            return self._project_security(device, device.individual_address, keys)
        if self.keyring is None:
            return None
        security = self.keyring.device_security(device.individual_address)
        # Fail loud, not open: if a keyring is loaded but has no tool key for this device, we are
        # about to program it in the CLEAR. That is correct for a non-secure device, but if the
        # device was commissioned secure it is a misconfiguration (wrong/incomplete keyring), so
        # warn rather than silently downgrade. (No keyring loaded at all = plaintext is expected.)
        if security is None and self.keyring.is_loaded():
            self._log.warning(
                "programming in the clear: loaded keyring has no tool key for this device; "
                "if it was commissioned secure, load the matching keyring first",
                device=device.name,
                address=device.individual_address,
            )
        return security

    def secure_tunnel(
        self, gateway_address: str, free: Collection[str] = ()
    ) -> SecureTunnel | None:
        """The credentials of a KNX IP Secure tunnel of the interface at ``gateway_address``: from
        the project, else from the loaded keyring; one on a ``free`` tunnelling address first."""
        tunnels = (
            self.project_tunnels(gateway_address)
            if self.project_tunnels is not None
            else []
        )
        if not tunnels and self.keyring is not None:
            secrets = self.keyring.ip_secrets(gateway_address)
            if secrets is not None:
                tunnels = [
                    SecureTunnel(
                        user_id, address, password, secrets.device_authentication_code
                    )
                    for user_id, address, password in secrets.tunnels
                ]
        if not tunnels:
            return None
        return next((t for t in tunnels if t.individual_address in free), tunnels[0])

    def secure_backbone(self) -> tuple[bytes, int] | None:
        """The KNX IP Secure routing backbone ``(key, latency ms)``: the project's, else the
        loaded keyring's."""
        backbone = (
            self.project_backbone() if self.project_backbone is not None else None
        )
        if backbone is None and self.keyring is not None:
            key = self.keyring.backbone_key()
            if key is not None:
                backbone = (key, 2000)
        return backbone

    def data_secure(self) -> DataSecure | None:
        """KNX Data Secure for the group communication of the open connection: the keys of the
        secured group addresses and their senders, from the project, else from the loaded
        keyring; ``None`` without any."""
        from xknx.secure.data_secure import DataSecure
        from xknx.telegram import GroupAddress, IndividualAddress

        tables = (
            self.project_data_secure() if self.project_data_secure is not None else None
        )
        if not tables and self.keyring is not None:
            tables = self.keyring.data_secure_tables()
        if not tables or not tables[0]:
            self._group_keys = {}
            return None
        group_keys, senders = tables
        self._group_keys = dict(group_keys)
        return DataSecure(
            group_key_table={GroupAddress(raw): key for raw, key in group_keys.items()},
            individual_address_table={
                IndividualAddress(raw): sequence for raw, sequence in senders.items()
            },
        )

    def secure_group_keys(self) -> dict[int, bytes]:
        """The group keys the connection's Data Secure tables were last built with (raw group
        address -> key), e.g. to show secured telegrams decrypted."""
        return self._group_keys

    def refresh_data_secure(self) -> None:
        """Give the open connection the current Data Secure tables (not while a bus operation
        runs: a secured download hooks into the same place)."""
        if self._xknx is None or self.busy_operation is not None:
            return
        self._xknx.cemi_handler.data_secure = self.data_secure()

    @_connects("program_device")
    def program_device(
        self,
        device: Device,
        scope: DownloadScope | None = None,
        group_communication: GroupCommunication | None = None,
        *,
        without_plugin: bool = False,
        current_address: str | None = None,
        waiting: Callable[[], None] | None = None,
        serial_number: bytes | None = None,
        confirm_full_download: Callable[[str], Awaitable[bool]] | None = None,
        ip_config: IpDeviceConfig | None = None,
        tool_key: bytes | None = None,
        ip_secure: IpSecureConfig | None = None,
        disable_security: bool = False,
        routing_blocking: bool | None = None,
    ) -> Future[Any] | None:
        """Download the device's configured application/parameters onto the bus.

        ``scope`` selects a full or partial download (defaults to full).
        ``group_communication`` supplies the address/association tables.
        A device whose hardware needs the manufacturer's plugin is only programmed with
        ``without_plugin`` (the user confirmed it).
        An individual address scope downloads only the address: to the device in programming mode
        (``waiting`` is called while none is), to the device with ``serial_number``, or for an
        overwrite to the device at ``current_address``. A full download of a device that does not
        answer yet gets its address by ``serial_number`` when given. ``tool_key`` commissions the
        device securely; ``disable_security`` switches it to plain communication with the key it
        holds.
        """
        if self.not_connected("program_device"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        if scope is not None and scope.individual_address_only:
            from xknxeditor.download.scope import DownloadScope

            overwrite = scope is DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS
            return self._download_individual_address(
                device,
                current_address if overwrite else None,
                overwrite,
                waiting,
                None if overwrite else serial_number,
            )
        if device.download_plugin_required:
            if not without_plugin:
                self._log.error(
                    "Device can only be programmed with the manufacturer's plugin",
                    device=device.name,
                )
                return None
            self._log.warning(
                "Programming without the manufacturer's plugin, as confirmed",
                device=device.name,
            )
        from editor_gui.memory_cap_cache import learned_memory_cap
        from editor_gui.programming import download_device
        from xknxeditor.download.scope import DownloadScope

        scope = scope or DownloadScope.FULL
        # Before the bus slot is taken: a missing device certificate is reported to the caller.
        security = self._download_security(device, tool_key)
        if not self.begin_operation("program", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        app_id = getattr(getattr(device, "app", None), "id", None)
        seed_cap = learned_memory_cap(app_id)
        if seed_cap is not None:
            self._log.info(
                "Using learned memory-service cap",
                device=device.name,
                cap=seed_cap,
            )
        self._log.info(
            "Programming device",
            device=device.name,
            address=device.individual_address,
            scope=scope.name,
            secure=security is not None,
        )
        future = self.run_async(
            download_device(
                self._connected_xknx,
                device,
                scope,
                group_communication,
                master=self.master,
                progress=self._on_program_progress,
                security=security,
                serial_number=serial_number,
                confirm_full_download=confirm_full_download,
                ip_config=ip_config,
                tool_key=tool_key if security is not None else None,
                ip_secure=ip_secure if security is not None else None,
                disable_security=disable_security and security is not None,
                routing_blocking=routing_blocking if security is not None else None,
                memory_service_count=seed_cap,
                interface_max_apdu_length=self.interface_max_apdu_length,
                factory_image=_factory_image(device),
            )
        )
        if future is not None:
            future.add_done_callback(self._log_program_result)
            future.add_done_callback(
                functools.partial(self._remember_memory_cap, application_id=app_id)
            )
            if device.individual_address == self.interface_address:
                future.add_done_callback(self._reopen_after_interface_download)
        else:
            self._clear_busy()
        return future

    def _remember_memory_cap(
        self, future: Future[Any], *, application_id: str | None
    ) -> None:
        """Persist the memory-service cap a successful download settled on, so the next
        download of the same application seeds it and skips the discovery retry."""
        if future.cancelled() or future.exception() is not None:
            return
        result = future.result()
        cap = getattr(result, "memory_service_count", None)
        if cap is None or application_id is None:
            return
        from editor_gui.memory_cap_cache import learned_memory_cap, remember_memory_cap

        if learned_memory_cap(application_id) == cap:
            return
        remember_memory_cap(application_id, cap)
        self._log.info(
            "Learned memory-service cap; later downloads of this application skip the "
            "discovery retry",
            application=application_id,
            cap=cap,
        )

    def _reopen_after_interface_download(self, future: Future[Any]) -> None:
        """The interface the connection runs through was downloaded: the services it secures and
        its tunnels may have changed, so the connection follows what it requires now."""
        if future.cancelled() or future.exception() is not None:
            return
        if self._reopen is not None:
            self._reopen()

    def _download_individual_address(
        self,
        device: Device,
        current_address: str | None,
        overwrite: bool,
        waiting: Callable[[], None] | None,
        serial_number: bytes | None,
    ) -> Future[Any] | None:
        from xknxeditor.download.commissioning import download_individual_address

        if overwrite and not current_address:
            self._log.error(
                "The current individual address is missing", device=device.name
            )
            return None
        if not self.begin_operation("program", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        security = self._security_for(device)
        current_security = (
            self._current_address_security(device, current_address, security)
            if current_address
            else None
        )
        self._log.info(
            "Downloading individual address",
            device=device.name,
            address=device.individual_address,
            current=current_address,
            serial=serial_number.hex().upper() if serial_number else None,
            secure=(current_security or security) is not None,
        )
        future = self.run_async(
            download_individual_address(
                self._connected_xknx,
                device.individual_address,
                current_address=current_address,
                security=security,
                current_security=current_security,
                master=self.master,
                waiting=waiting,
                serial_number=serial_number,
            )
        )
        if future is not None:
            future.add_done_callback(self._log_program_result)
        else:
            self._clear_busy()
        return future

    def _download_security(
        self, device: Device, tool_key: bytes | None
    ) -> DeviceSecurity | None:
        """The security of a download that gives the device its Tool Key ``tool_key``: the
        session starts with the key the device uses (its loaded Tool Key, else its FDSK, else a
        loaded keyring's key for it) and falls back to the others. Raises
        :class:`DeviceCertificateRequired` when none is known."""
        if tool_key is None:
            return self._security_for(device)
        if not device.individual_address:
            return None
        keys = self.project_keys(device) if self.project_keys is not None else None
        loaded = keys.loaded_tool_key if keys is not None else None
        fdsk = keys.fdsk if keys is not None else None
        ring = (
            self.keyring.device_security(device.individual_address)
            if self.keyring is not None
            else None
        )
        keyring_key = ring.tool_key if ring is not None else None
        current = loaded or fdsk or keyring_key
        if current is None:
            raise DeviceCertificateRequired(device.individual_address)
        from xknx.telegram import IndividualAddress

        from xknxeditor.download.data_secure import DeviceSecurity

        alternatives = [
            key for key in (tool_key, fdsk, loaded, keyring_key) if key is not None
        ]
        return DeviceSecurity(
            IndividualAddress(device.individual_address), current, alternatives
        )

    def _project_security(
        self, device: Device, address: str, keys: DeviceKeys
    ) -> DeviceSecurity | None:
        """The project's tool key of ``device`` for a connection to ``address``: the key the
        device holds (``LoadedToolKey``), with its FDSK as the alternative (after a master reset).
        A device that never got a Tool Key is reached in the clear."""
        key = keys.loaded_tool_key
        if key is None or not address:
            return None
        from xknx.exceptions import CouldNotParseAddress
        from xknx.telegram import IndividualAddress

        from xknxeditor.download.data_secure import DeviceSecurity

        alternatives = [keys.fdsk] if keys.fdsk is not None else []
        try:
            return DeviceSecurity(IndividualAddress(address), key, alternatives)
        except CouldNotParseAddress:
            return None

    def _current_address_security(
        self, device: Device, current_address: str, security: DeviceSecurity | None
    ) -> DeviceSecurity | None:
        """Tool key for the device at its current address: the project's, else from the keyring,
        else the one for its configured address."""
        keys = self.project_keys(device) if self.project_keys is not None else None
        if keys is not None:
            return (
                self._project_security(device, current_address, keys)
                if security is not None
                else None
            )
        if self.keyring is not None:
            found = self.keyring.device_security(current_address)
            if found is not None:
                return found
        if security is None:
            return None
        from xknx.telegram import IndividualAddress

        from xknxeditor.download.data_secure import DeviceSecurity

        return DeviceSecurity(IndividualAddress(current_address), security.tool_key)

    def _log_program_result(self, future: Future[Any]) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            from xknxeditor.download.errors import PartialDownloadError

            if isinstance(exc, PartialDownloadError):
                # Report the actual failure (the chained cause), then make the
                # recovery step explicit. The wrapper alone would hide the root
                # cause, which is what the user needs to see.
                cause = exc.__cause__ or exc
                self._log.error(
                    "Programming failed",
                    error=str(cause),
                    traceback=_exc_traceback(exc),
                )
                self._log.error(
                    "The device may now be in an inconsistent, partially loaded "
                    "state. Re-run programming (full download) once the cause above "
                    "is resolved to restore a working state."
                )
            else:
                self._log.error(
                    "Programming failed", error=str(exc), traceback=_exc_traceback(exc)
                )
                if isinstance(exc, ManagementConnectionError):
                    # A full download of a virgin device first writes its address to the single
                    # device in programming mode; nothing answering here usually means the device
                    # is not in (or more than one device is in) programming mode.
                    self._log.error(
                        "No device responded at the address — for a new device put exactly "
                        "one device into programming mode; otherwise check it is powered and "
                        "reachable from this interface (line/coupler)"
                    )
            self._set_program_notice(False)
        else:
            self._log.info("Programming complete")
            self._set_program_notice(True)

    def _set_program_notice(self, ok: bool) -> None:
        with self._busy_lock:
            self._program_notice = (ok, time.monotonic())

    def program_notice(self, max_age: float = 6.0) -> bool | None:
        """Recent programming outcome (``True`` ok, ``False`` failed) within ``max_age`` s, else ``None``."""
        with self._busy_lock:
            notice = self._program_notice
        if notice is None or time.monotonic() - notice[1] > max_age:
            return None
        return notice[0]

    @_connects("evaluate_device")
    def evaluate_device(
        self,
        device: Device,
        scope: DownloadScope | None = None,
        group_communication: GroupCommunication | None = None,
        ip_config: IpDeviceConfig | None = None,
    ) -> Future[Any] | None:
        """Dry run: read the device and log what programming it would change."""
        if self.not_connected("evaluate_device"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        from editor_gui.programming import eval_device
        from xknxeditor.download.scope import DownloadScope

        scope = scope or DownloadScope.FULL
        if scope.individual_address_only:
            self._log.warning(
                "An address download cannot be tested", device=device.name
            )
            return None
        if not self.begin_operation("test", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        security = self._security_for(device)
        self._log.info(
            "Testing device before programming",
            device=device.name,
            scope=scope.name,
            secure=security is not None,
        )
        future = self.run_async(
            eval_device(
                self._connected_xknx,
                device,
                scope,
                group_communication,
                self.master,
                security=security,
                ip_config=ip_config,
                interface_max_apdu_length=self.interface_max_apdu_length,
                factory_image=_factory_image(device),
            )
        )
        if future is not None:
            future.add_done_callback(
                functools.partial(
                    self._log_evaluate_result, address=device.individual_address
                )
            )
        else:
            self._clear_busy()
        return future

    def _log_evaluate_result(
        self, future: Future[Any], address: str | None = None
    ) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error(
                "Evaluation failed",
                address=address,
                error=str(exc),
                traceback=_exc_traceback(exc),
            )
            if isinstance(exc, ManagementConnectionError):
                # A dry run still reads the live device over a point-to-point connection.
                # No ACK means nothing answered at that individual address on the bus.
                self._log.error(
                    "Device did not respond on the bus — check it is powered and "
                    "reachable from this interface (line/coupler) at the address",
                    address=address,
                )
            return
        report = future.result()
        self._log.info(
            "Pre-flight result",
            changed_bytes=report.total_changed_bytes,
            segments=len(report.changed_segments),
            properties=len(report.changed_properties),
        )
        for line in report.summary().splitlines():
            self._log.info(line.strip())

    @_connects("device_info")
    def device_info(
        self,
        device: Device,
        *,
        group_communication: bool,
        resolve_application: Callable[[AppId], Application | None],
    ) -> Future[Any] | None:
        """Read a device's identity, load states and, if asked, its group communication tables,
        secured with the device's tool key when the loaded keyring has one. The future's result is
        an :class:`~editor_gui.device_info.DeviceInfo`."""
        if self.not_connected("device_info"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        from editor_gui.device_info import read_device_info

        if not self.begin_operation("device_info", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        self._log.info("Reading device info", device=device.name)
        future = self.run_async(
            read_device_info(
                self._connected_xknx,
                device.individual_address,
                resolve_application,
                group_communication=group_communication,
                security=self._security_for(device),
                interface_max_apdu=self.interface_max_apdu_length,
            )
        )
        if future is None:
            self._clear_busy()
            return None
        future.add_done_callback(
            functools.partial(self._end_device_info, address=device.individual_address)
        )
        return future

    def _end_device_info(self, future: Future[Any], address: str) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is None:
            return
        self._log.error("Reading device info failed", address=address, error=str(exc))
        if isinstance(exc, ManagementConnectionError):
            self._log.error(
                "Device did not respond on the bus — check it is powered and "
                "reachable from this interface (line/coupler) at the address",
                address=address,
            )

    @_connects("dali_commissioning")
    def run_dali(
        self,
        device: Device,
        op: Callable[[Any], Coroutine[Any, Any, Any]],
        *,
        channel: int = 0,
    ) -> Future[Any] | None:
        """Run one MDT-DALI commissioning coroutine against ``device`` over the bus.

        ``op`` receives an :class:`~xknxeditor.dali.MdtDaliCommissioner` and returns a coroutine
        (e.g. ``lambda c: c.scan()``). Acquires the exclusive bus slot, uses the device's Tool-Key
        security if a keyring is loaded, and releases the slot in the done-callback. The caller
        attaches its own callback to consume the result.
        """
        if self.not_connected("dali_commissioning"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        from editor_gui.programming_dali import (
            dali_object_index_base,
            run_dali_operation,
        )

        if not self.begin_operation("dali", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        security = self._security_for(device)
        app_id = device.app.id if device.app is not None else None
        object_index_base = dali_object_index_base(app_id)
        future = self.run_async(
            run_dali_operation(
                self._connected_xknx,
                device.individual_address,
                security,
                channel,
                op,
                object_index_base=object_index_base,
                app_id=app_id,
            )
        )
        if future is not None:
            future.add_done_callback(
                functools.partial(
                    self._log_dali_result, address=device.individual_address
                )
            )
        else:
            self._clear_busy()
        return future

    def _log_dali_result(self, future: Future[Any], address: str | None = None) -> None:
        self._clear_busy()
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error(
                "DALI commissioning failed", address=address, error=str(exc)
            )
            return
        # Defensive against future helpers that keep returning False instead of raising:
        # treat a boolean result as a verification signal so the user is not told the run
        # succeeded when it did not. (``commission()`` now raises
        # ``DaliCommissioningError``; this guard catches any other callable that still
        # returns a bool.)
        try:
            result = future.result()
        except Exception as e:
            self._log.error("DALI commissioning failed", address=address, error=str(e))
            return
        if result is False:
            self._log.error(
                "DALI commissioning failed", address=address, error="verify mismatch"
            )
            return
        self._log.info("DALI commissioning done", address=address)

    @_connects("siemens_dali_commissioning")
    def run_siemens_dali(
        self,
        device: Device,
        op: Callable[[Any], Coroutine[Any, Any, Any]],
    ) -> Future[Any] | None:
        """Run one Siemens N141/03 DALI operation (currently read-only scan).

        ``op`` receives a raw :class:`~xknxeditor.dali.connection.Programmer` and returns a
        coroutine (e.g. ``lambda p: scan_n141_03(p, line=0)``). Shares the exclusive bus slot with
        the other point-to-point operations.
        """
        if self.not_connected("siemens_dali_commissioning"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        from editor_gui.programming_dali import run_siemens_dali_operation

        if not self.begin_operation("siemens_dali", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        security = self._security_for(device)
        future = self.run_async(
            run_siemens_dali_operation(
                self._connected_xknx,
                device.individual_address,
                security,
                op,
            )
        )
        if future is None:
            # ``run_async`` returning None means no event loop is running; release the slot
            # we just acquired so the next attempt can start.
            self._clear_busy()
            return None
        future.add_done_callback(
            functools.partial(self._log_dali_result, address=device.individual_address)
        )
        return future

    @_connects("siemens_dca_program")
    def run_siemens_dca_program(
        self,
        device: Device,
        blob_xml: bytes,
    ) -> Future[int] | None:
        """Push the Siemens N141/03 DCA ``DaliDevice`` property-image onto the device.

        Returns a Future with the number of property writes that succeeded. The DCA
        property-table is derived from the project's BLOB_PARA; the port lives in
        :mod:`editor_gui.plugin_blob_readers.siemens_dali.download`.
        """
        if self.not_connected("siemens_dca_program"):
            return None
        if not device.individual_address:
            self._log.warning("Device has no individual address", device=device.name)
            return None
        if not self.begin_operation("siemens_dca", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        security = self._security_for(device)
        from editor_gui.plugin_blob_readers.siemens_dali.download import (
            program_dalidevice,
        )
        from editor_gui.programming_dali import run_siemens_dali_operation

        async def _runner(programmer: Any) -> int:
            return await program_dalidevice(programmer, blob_xml)

        future = self.run_async(
            run_siemens_dali_operation(
                self._connected_xknx,
                device.individual_address,
                security,
                _runner,
            )
        )
        if future is None:
            self._clear_busy()
            return None
        future.add_done_callback(
            functools.partial(self._log_dali_result, address=device.individual_address)
        )
        return future

    def run_async(self, coro: Coroutine[Any, Any, Any]) -> Future[Any] | None:
        if self._loop is None:
            coro.close()
            return None
        # The task the coroutine runs in, for cancel_operation.
        started: list[asyncio.Task[Any]] = []

        async def tracked() -> Any:
            task = asyncio.current_task()
            if task is not None:
                started.append(task)
            return await coro

        future = asyncio.run_coroutine_threadsafe(tracked(), self._loop)
        self._operation_tasks[future] = started
        future.add_done_callback(self._forget_operation_task)
        return future

    def _forget_operation_task(self, future: Future[Any]) -> None:
        self._operation_tasks.pop(future, None)

    def cancel_operation(self, future: Future[Any]) -> None:
        """Stop the operation behind ``future`` (from ``run_async``) at its next await. Its task
        is canceled on the loop rather than the future itself: the future ends only once the
        operation has unwound and closed its connection, and its done callbacks free the bus
        only then."""
        if future in self._connected_operations:
            # Connected for it: cancel the operation, or the wait for its connection.
            operation = self._connected_operations[future]
            if operation is None:
                future.cancel()
            else:
                self.cancel_operation(operation)
            return
        started = self._operation_tasks.get(future)
        if started and self._loop is not None:
            self._loop.call_soon_threadsafe(started[0].cancel)
        else:
            future.cancel()


def _factory_image(device: Device) -> FactoryData | None:
    """The factory image of ``device``'s product, read here (not on the bus thread)."""
    return device.factory_image() if device.factory_image is not None else None
