from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from xknx.cemi import CEMIFrame
from xknx.exceptions import ManagementConnectionError
from xknx.management.procedures import (
    dm_restart,
    nm_individual_address_read,
    nm_individual_address_serial_number_write,
)

if TYPE_CHECKING:
    from xknx import XKNX

    from editor_gui.device import Device
    from editor_gui.net import TelegramSource
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.keyring.service import KeyringService
    from xknxeditor.download.data_secure import DeviceSecurity
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.prod import Application, MasterData
    from xknxeditor.recover import AppId


class ConnectionService:
    def __init__(self) -> None:
        self._log: Logger
        self._raw_cemi_listeners: list[Callable[[bytes, TelegramSource], None]] = []
        self._connected_listeners: list[Callable[[], None]] = []
        self._xknx: XKNX | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Global KNX master data (mask-version default procedures), injected at
        # startup. Required to resolve an UNLOAD scope and default/merged procedures.
        self.master: MasterData | None = None
        # Maximum APDU length the connected interface reports, if it does.
        self.interface_max_apdu_length: int | None = None
        # Display name of the connected interface.
        self.interface_name: str | None = None
        # Keyring service (KNX Data Secure), injected at startup. Used to look up a device's tool
        # key so a point-to-point download/test is secured when the device was commissioned secure.
        self.keyring: KeyringService | None = None
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
        self._log.error(f"{op} failed: no KNX connection")
        with self._busy_lock:
            self._not_connected_notice = time.monotonic()
        return True

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

    @property
    def xknx(self) -> XKNX | None:
        return self._xknx

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
        future = self.run_async(self._xknx.knxip_interface.send_cemi(cemi))
        if future is not None:
            future.add_done_callback(self._log_send_cemi_result)
        return future

    def _log_send_cemi_result(self, future: Future[Any]) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log.error("send_cemi failed", error=str(exc))
        else:
            self._log.debug("send_cemi ok")

    def read_programming_mode_devices(self, timeout: float = 3.0) -> Future[Any] | None:
        if self.not_connected("read_programming_mode_devices"):
            return None
        return self.run_async(nm_individual_address_read(self._xknx, timeout=timeout))

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
            scan_programming_mode_devices(self._xknx, timeout=timeout)
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
            nm_individual_address_serial_number_write(self._xknx, serial, address)
        )

    def assign_individual_address(self, address: str) -> Future[Any] | None:
        if self.not_connected("assign_individual_address"):
            return None
        self._log.debug("Assigning individual address", address=address)
        # Write the address to the device in programming mode without restarting it. xknx's
        # nm_individual_address_write restarts the device here, which leaves it briefly unreachable
        # and races a following download; program_individual_address writes directly and leaves the
        # device running and in programming mode.
        from xknxeditor.download.commissioning import program_individual_address

        return self.run_async(program_individual_address(self._xknx, address))

    def assign_individual_address_for_device(
        self, device: Device
    ) -> Future[Any] | None:
        if not device.individual_address:
            self._log.warning(
                "Device has no individual address assigned", device=device.name
            )
            return None
        return self.assign_individual_address(device.individual_address)

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
        future = self.run_async(dm_restart(self._xknx, device.individual_address))
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

    async def _do_master_reset(self, address: str, erase_code: int) -> None:
        import contextlib

        from xknx.telegram.address import IndividualAddress
        from xknx.telegram.apci import RestartMasterReset

        # A master reset makes the device drop the link immediately, so the closing disconnect
        # normally fails - suppress it so a successful reset is not reported as an error. A failure
        # of the send itself still propagates (real error).
        target = IndividualAddress(address)
        conn = await self._xknx.management.connect(target)
        try:
            await conn.send_data(
                RestartMasterReset(erase_code=erase_code), wait_for_ack=False
            )
        finally:
            with contextlib.suppress(Exception):
                await self._xknx.management.disconnect(target)

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
        """KNX Data Secure tool key for ``device`` from the loaded keyring, or ``None`` (program in
        the clear). A non-``None`` result secures every management APDU of the download."""
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
    ) -> Future[Any] | None:
        """Download the device's configured application/parameters onto the bus.

        ``scope`` selects a full or partial download (defaults to full).
        ``group_communication`` supplies the address/association tables.
        A device whose hardware needs the manufacturer's plugin is only programmed with
        ``without_plugin`` (the user confirmed it).
        An individual address scope downloads only the address: to the device in programming mode
        (``waiting`` is called while none is), to the device with ``serial_number``, or for an
        overwrite to the device at ``current_address``. A full download of a device that does not
        answer yet gets its address by ``serial_number`` when given.
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
        from editor_gui.programming import download_device
        from xknxeditor.download.scope import DownloadScope

        scope = scope or DownloadScope.FULL
        if not self.begin_operation("program", device.individual_address):
            self._log.warning("A bus operation is already running", device=device.name)
            return None
        security = self._security_for(device)
        self._log.info(
            "Programming device",
            device=device.name,
            address=device.individual_address,
            scope=scope.name,
            secure=security is not None,
        )
        future = self.run_async(
            download_device(
                self._xknx,
                device,
                scope,
                group_communication,
                master=self.master,
                progress=self._on_program_progress,
                security=security,
                serial_number=serial_number,
            )
        )
        if future is not None:
            future.add_done_callback(self._log_program_result)
        else:
            self._clear_busy()
        return future

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
            self._current_address_security(current_address, security)
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
                self._xknx,
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

    def _current_address_security(
        self, current_address: str, security: DeviceSecurity | None
    ) -> DeviceSecurity | None:
        """Tool key for the device at its current address: from the keyring, else the project's."""
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
                self._log.error("Programming failed", error=str(cause))
                self._log.error(
                    "The device may now be in an inconsistent, partially loaded "
                    "state. Re-run programming (full download) once the cause above "
                    "is resolved to restore a working state."
                )
            else:
                self._log.error("Programming failed", error=str(exc))
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

    def evaluate_device(
        self,
        device: Device,
        scope: DownloadScope | None = None,
        group_communication: GroupCommunication | None = None,
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
                self._xknx,
                device,
                scope,
                group_communication,
                self.master,
                security=security,
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
            self._log.error("Evaluation failed", address=address, error=str(exc))
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
                self._xknx,
                device.individual_address,
                resolve_application,
                group_communication=group_communication,
                security=self.keyring.device_security(device.individual_address)
                if self.keyring is not None
                else None,
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
        object_index_base = dali_object_index_base(
            device.app.id if device.app is not None else None
        )
        future = self.run_async(
            run_dali_operation(
                self._xknx,
                device.individual_address,
                security,
                channel,
                op,
                object_index_base=object_index_base,
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
        self._log.info("DALI commissioning done", address=address)

    def run_async(self, coro: Coroutine[Any, Any, Any]) -> Future[Any] | None:
        if self._loop is None:
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)
