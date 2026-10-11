"""Program a configured device onto the bus, or preview what a download would change.

Bridges a GUI :class:`~editor_gui.device.Device` (which holds a live evaluator with the
current parameter state) to the ``xknxeditor.download`` package: the download image is
built directly from the device's evaluator, so the bytes reflect exactly what the
editor shows.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from editor_gui.device import address_order

# Import from the concrete modules: the package also has a ``preflight`` submodule,
# so ``from xknxeditor.download import preflight`` is ambiguous to type checkers.
from xknxeditor.download.download import download, preflight
from xknxeditor.download.image import build_image
from xknxeditor.download.scope import DownloadScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from xknx import XKNX

    from editor_gui.device import Device
    from xknxeditor.download.data_secure import DeviceSecurity
    from xknxeditor.download.image import DownloadImage, GroupCommunication
    from xknxeditor.download.preflight import PreflightReport
    from xknxeditor.prod import MasterData


class DeviceProgrammingError(RuntimeError):
    """A device cannot be programmed as configured (no app, no address, ...)."""


def _image_for(
    device: Device, group_communication: GroupCommunication | None = None
) -> DownloadImage:
    ui = device.dynamic_ui
    if ui is None:
        raise DeviceProgrammingError("device has no dynamic application to program")
    return build_image(device.app, ui=ui, group_communication=group_communication)


def _address(device: Device) -> str:
    if not device.individual_address:
        raise DeviceProgrammingError("device has no individual address")
    return device.individual_address


# Standard Device Object (interface object 0) property ids used for the diagnosis readout.
# PID_ERROR_CODE / PID_PROGMODE are the two the KNX standard makes universally readable without
# any application knowledge (KNX 3/5/1, 3/7/3). PID_ERROR_CODE carries a DPT_ErrorClass_System
# (DPT 20.011) value; PID_PROGMODE bit 0 is the programming-mode LED state.
_PID_ERROR_CODE = 28
_PID_PROGMODE = 54

# DPT_ErrorClass_System (20.011), values 0-18 per the KNX standard (03_07_02 Datapoint Types).
_ERROR_CLASS: dict[int, str] = {
    0: "no fault",
    1: "general device fault",
    2: "communication fault",
    3: "configuration fault",
    4: "hardware fault",
    5: "software fault",
    6: "insufficient non-volatile memory",
    7: "insufficient volatile memory",
    8: "memory allocation with size 0",
    9: "CRC error",
    10: "watchdog reset detected",
    11: "invalid opcode detected",
    12: "general protection fault",
    13: "maximal table length exceeded",
    14: "undefined load command received",
    15: "group address table is not sorted",
    16: "invalid connection number (TSAP)",
    17: "invalid group object number (ASAP)",
    18: "group object type exceeds PID_MAX_APDU_LENGTH - 2",
}


def error_class_text(code: int | None) -> str | None:
    """Human-readable label for a DPT_ErrorClass_System code, or the raw code if unknown."""
    if code is None:
        return None
    return _ERROR_CLASS.get(code, f"error code {code}")


@dataclass
class DeviceOverview:
    """What a device reports about itself over the bus: its mask version, application id
    (manufacturer/number/version), the descriptive Device-Object dossier and a light diagnosis
    (error class + programming mode). This is the *actual programmed* state read live, as opposed
    to the project's planned configuration. Read-only."""

    mask_version: int | None = None
    manufacturer: str | None = None
    application_number: int | None = None
    application_version: int | None = None
    serial_number: str | None = None
    order_info: str | None = None
    hardware_type: str | None = None
    error_code: int | None = None
    programming_mode: bool | None = None

    @property
    def error_text(self) -> str | None:
        return error_class_text(self.error_code)


async def read_device_overview(xknx: XKNX, address: str) -> DeviceOverview:
    """Read a device's general info over a single point-to-point connection (read-only): mask
    version, application id and the Device-Object dossier (serial number, order info, hardware
    type). Composes the ``xknxeditor.recover`` primitives; the connection is always closed again."""
    from xknx.exceptions import XKNXException
    from xknx.telegram import IndividualAddress

    from xknxeditor.download import DeviceProgrammer
    from xknxeditor.download.errors import DownloadError
    from xknxeditor.recover import read_application_id, read_dossier

    async def _prop(programmer: DeviceProgrammer, pid: int) -> bytes:
        # Best-effort: a device that does not expose the property answers empty, not an error.
        try:
            return await programmer.read_property(0, pid)
        except (DownloadError, XKNXException):
            return b""

    target = IndividualAddress(address)
    connection = await xknx.management.connect(target)
    try:
        programmer = DeviceProgrammer(connection)
        mask = await programmer.read_device_descriptor()
        app = await read_application_id(programmer)
        dossier = await read_dossier(programmer)
        error_raw = await _prop(programmer, _PID_ERROR_CODE)
        progmode_raw = await _prop(programmer, _PID_PROGMODE)
    finally:
        with contextlib.suppress(Exception):
            await xknx.management.disconnect(target)

    if app is not None:
        manufacturer = app.manufacturer_id
    elif dossier.manufacturer_id is not None:
        manufacturer = f"M-{dossier.manufacturer_id:04X}"
    else:
        manufacturer = None
    return DeviceOverview(
        mask_version=mask,
        manufacturer=manufacturer,
        application_number=app.application_number if app is not None else None,
        application_version=app.application_version if app is not None else None,
        serial_number=dossier.serial_number,
        order_info=dossier.order_info,
        hardware_type=dossier.hardware_type,
        error_code=error_raw[0] if error_raw else None,
        programming_mode=bool(progmode_raw[0] & 0x01) if progmode_raw else None,
    )


@dataclass
class ScannedDevice:
    """One device found in programming mode: its individual address plus the dossier read off the
    bus (``overview``), or ``error`` when that read failed (the device answered the broadcast but a
    point-to-point read did not complete)."""

    address: str
    overview: DeviceOverview | None = None
    error: str | None = None


# Default individual address of a factory-fresh / unprogrammed device.
DEFAULT_INDIVIDUAL_ADDRESS = "15.15.255"


async def scan_programming_mode_devices(
    xknx: XKNX, timeout: float = 3.0
) -> list[ScannedDevice]:
    """Find every device currently in programming mode and read its dossier.

    Broadcasts an individual-address read (all devices in programming mode answer with their
    address), then reads each one's :class:`DeviceOverview` over a point-to-point connection. A
    device that answers the broadcast but fails the follow-up read is still returned, with its
    ``error`` set, so the user sees it was present."""
    from xknx.management.procedures import nm_individual_address_read

    addresses = await nm_individual_address_read(xknx, timeout=timeout)
    seen: set[str] = set()
    results: list[ScannedDevice] = []
    for address in sorted(addresses, key=lambda a: address_order(str(a))):
        text = str(address)
        # A device retransmits its response several times within the timeout window, so the same
        # address can appear more than once; read each device only once.
        if text in seen:
            continue
        seen.add(text)
        try:
            overview = await read_device_overview(xknx, text)
            results.append(ScannedDevice(text, overview=overview))
        except Exception as exc:  # bus timeout / device dropped the link mid-read
            results.append(ScannedDevice(text, error=str(exc)))
    return results


def runtime_managed_addresses(device: Device) -> set[int]:
    """Absolute memory addresses of system parameters the device sets at runtime.

    These are parameters with access "None" (not user configurable), e.g. a
    download-detection byte the application firmware overwrites after a download.
    A pre-flight difference at such an address is expected and benign, so the
    result view can annotate it instead of flagging a real change.
    """
    ui = device.dynamic_ui
    if ui is None:
        return set()
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(device.app.program)
    base_addresses = ui.segment_base_addrs()
    addresses: set[int] = set()
    for segment_id, offset_map in ui.memory_param_map().items():
        base = base_addresses.get(segment_id)
        if base is None:
            continue
        for offset, (parameter_id, _value) in offset_map.items():
            parameter = indexer.parameters.get(parameter_id)
            access = getattr(parameter, "access", None)
            if access is not None and access.name == "NONE":
                addresses.add(base + offset)
    return addresses


def parameter_driven_bits(device: Device) -> dict[int, int]:
    """Absolute memory address -> bitmask of bits an active parameter actually writes.

    A pre-flight rewrites a whole byte as soon as one parameter touches it, so a
    byte can differ from the device in a bit no active parameter drives (the byte is
    written for a neighbour parameter and the shared bit is only set to the segment
    seed / application default). This map lets the result view separate, within a
    changed byte, the bits carrying a real configured value (in the mask) from the
    bits merely reset to the default (not in the mask, so a difference there means
    the device holds an older/stale value programming will normalise).
    """
    ui = device.dynamic_ui
    if ui is None:
        return {}
    base_addresses = ui.segment_base_addrs()
    driven: dict[int, int] = {}
    for segment_id, mask in ui.written_bit_mask().items():
        base = base_addresses.get(segment_id)
        if base is None:
            continue
        for offset, bits in enumerate(mask):
            if bits:
                driven[base + offset] = driven.get(base + offset, 0) | bits
    return driven


async def eval_device(
    xknx: XKNX,
    device: Device,
    scope: DownloadScope = DownloadScope.FULL,
    group_communication: GroupCommunication | None = None,
    master: MasterData | None = None,
    security: DeviceSecurity | None = None,
) -> PreflightReport:
    """Dry run: report what programming ``device`` would change, writing nothing.

    ``master`` supplies the mask version's default procedures; it is required for
    an ``UNLOAD`` scope and for applications with a default/merged procedure style.
    ``security`` (KNX Data Secure tool key) secures the point-to-point reads when the
    device is commissioned secure; ``None`` reads in the clear.
    """
    return await preflight(
        xknx,
        _address(device),
        device.app,
        master=master,
        image=_image_for(device, group_communication),
        scope=scope,
        security=security,
    )


async def download_device(
    xknx: XKNX,
    device: Device,
    scope: DownloadScope = DownloadScope.FULL,
    group_communication: GroupCommunication | None = None,
    master: MasterData | None = None,
    progress: Callable[[int, int], None] | None = None,
    security: DeviceSecurity | None = None,
) -> None:
    """Program ``device``: build its image and run the load procedure on the bus.

    ``scope`` selects a full download or a partial one (parameters only, or group
    communication only), mirroring the standard download options. ``group_communication``
    supplies the address/association tables a full or group download writes. ``master``
    supplies the mask version's default procedures (required for an ``UNLOAD`` scope and
    for default/merged procedure styles). ``progress`` (optional) is called
    ``progress(done, total)`` after each executed load control. ``security`` (KNX Data
    Secure tool key) secures every management APDU when set; ``None`` programs in the clear.

    A full download commissions a virgin device: if nothing answers at the device's
    address, the address is first written to the single device in programming mode.
    """
    await download(
        xknx,
        _address(device),
        device.app,
        master=master,
        image=_image_for(device, group_communication),
        scope=scope,
        commission=True,
        progress=progress,
        security=security,
    )
