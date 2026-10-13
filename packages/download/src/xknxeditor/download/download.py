"""High level entry point for downloading an application into a device.

Assembles the download image and runs the device's Load Procedure over a
point-to-point connection, following KNX Standard v3.0.0, Chapter 3/5/3
"Configuration Procedures": the complete download procedure (section 3.5.2) and
the partial download procedure (section 3.5.3).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress

from .errors import DownloadError, PartialDownloadError, UnsupportedProcedureError
from .group_communication import materialize_group_communication_controls
from .image import build_image
from .ip_config import (
    IP_PARAMETER_OBJECT,
    PID_MAC_ADDRESS,
    IpDeviceConfig,
    with_ip_config_controls,
)
from .ip_config import applies as ip_config_applies
from .merge import mask_authorize_levels, resolve_download_controls
from .procedure import LoadProcedureRunner
from .programmer import MAX_COMMUNICATION_APDU_LENGTH
from .scope import DownloadScope, mask_object_types
from .session import apdu_overhead, management_session

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.address import IndividualAddressableType

    from xknxeditor.prod import Application, MasterData

    from .data_secure import DeviceSecurity
    from .image import DownloadImage, GroupCommunication
    from .preflight import PreflightReport
    from .programmer import ConnectionManager
    from .project_data import SeedDevice


def _apdu_settings(max_apdu_length: int | None) -> tuple[int, bool]:
    """Resolve the APDU ceiling and whether to negotiate it from the device."""
    if max_apdu_length is None:
        return MAX_COMMUNICATION_APDU_LENGTH, True
    return max_apdu_length, False


def _connection_manager(
    xknx: XKNX, address: IndividualAddress, security: DeviceSecurity | None
) -> ConnectionManager:
    """Return a plain or a Tool-Key secured connection manager for ``address``."""
    return management_session(xknx, address, security)


@dataclass(frozen=True)
class DownloadResult:
    """What a download read from the device."""

    # MAC address of an IP device whose IP settings were written.
    mac_address: bytes | None = None


_IP_CONFIG_SCOPES = frozenset({DownloadScope.FULL, DownloadScope.APPLICATION})
_MAC_ADDRESS_LENGTH = 6


def _resolve_controls(
    application: Application,
    master: MasterData | None,
    image: DownloadImage,
    scope: DownloadScope,
    ip_config: IpDeviceConfig | None = None,
) -> list[object]:
    """Select and materialize the scoped procedure before any device mutation."""
    controls = _resolve_procedure(application, master, image, scope)
    if (
        ip_config is not None
        and scope in _IP_CONFIG_SCOPES
        and ip_config_applies(application)
    ):
        controls = with_ip_config_controls(application, controls, ip_config)
    return controls


def _resolve_procedure(
    application: Application,
    master: MasterData | None,
    image: DownloadImage,
    scope: DownloadScope,
) -> list[object]:
    master_raw = master.raw if master is not None else None
    if scope in (DownloadScope.UNLOAD, DownloadScope.UNLOAD_ALL):
        from xknxeditor.namespaces.intermediate.procedure_type_t import ProcedureType

        return resolve_download_controls(
            application, master_raw, procedure_type=ProcedureType.UNLOAD
        )
    base = resolve_download_controls(application, master_raw, scope=scope)
    return materialize_group_communication_controls(
        image, base, _object_types(application, master), scope
    )


_PARTIAL_SCOPES = frozenset(
    {
        DownloadScope.PARAMETERS,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    }
)


async def _partial_scope(
    xknx: XKNX,
    address: IndividualAddress,
    security: DeviceSecurity | None,
    application: Application,
    master: MasterData | None,
    scope: DownloadScope,
    confirm_full_download: Callable[[str], Awaitable[bool]] | None,
) -> DownloadScope:
    """The scope to download after checking the device's application."""
    from .partial import (
        DifferentProgramLoaded,
        FullDownloadDeclined,
        partial_download_mismatch,
    )
    from .programmer import DeviceProgrammer
    from .resources import mask_resources

    options = application.program.static.options
    prefer = (
        scope in (DownloadScope.FULL, DownloadScope.APPLICATION)
        and options is not None
        and options.prefer_partial_download_if_application_loaded
    )
    if scope not in _PARTIAL_SCOPES and not prefer:
        return scope
    resources = mask_resources(master, application.program.mask_version)
    manager = _connection_manager(xknx, address, security)
    try:
        programmer = DeviceProgrammer(await manager.open())
        reason = await partial_download_mismatch(programmer, application, resources)
    except ManagementConnectionError as exc:
        if not prefer:
            raise
        reason = f"device not reachable for a partial download: {exc}"
    finally:
        await manager.close()
    if scope in _PARTIAL_SCOPES:
        if reason is not None:
            logger.warning("download: %s", reason)
            raise DifferentProgramLoaded(reason)
        return scope
    if reason is None:
        logger.info(
            "download: trying a partial download because of "
            "PreferPartialDownloadIfApplicationLoaded"
        )
        return DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION
    logger.info("download: %s", reason)
    if (
        options is not None
        and options.prompt_before_full_download
        and confirm_full_download is not None
        and not await confirm_full_download(reason)
    ):
        raise FullDownloadDeclined(
            "Aborting on user request (PromptBeforeFullDownload)"
        )
    return scope


_TABLE_SCOPES = frozenset(
    {
        DownloadScope.FULL,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    }
)


async def _device_association_format(
    xknx: XKNX,
    address: IndividualAddress,
    security: DeviceSecurity | None,
    image: DownloadImage,
    scope: DownloadScope,
) -> DownloadImage:
    """``image`` with its association table in the format the device reports."""
    if scope not in _TABLE_SCOPES or not image.has_association_table:
        return image
    from .programmer import DeviceProgrammer

    manager = _connection_manager(xknx, address, security)
    try:
        connection = await manager.open()
        wide = await DeviceProgrammer(connection).read_association_table_wide()
    except (ManagementConnectionError, DownloadError) as exc:
        logger.debug("download: association table format not readable: %s", exc)
        return image
    finally:
        await manager.close()
    if wide is None:
        return image
    logger.debug("download: device association table format %d", 1 if wide else 0)
    return image.with_association_table_format(wide)


def _object_types(
    application: Application, master: MasterData | None
) -> dict[int, int]:
    return (
        mask_object_types(master.raw, application.program.mask_version)
        if master is not None
        else {}
    )


def _apdu_overhead(security: DeviceSecurity | None) -> int:
    """Wire APDU overhead a secure session adds around each plaintext APDU."""
    return apdu_overhead(security)


def _authorize_levels(
    application: Application,
    master: MasterData | None,
    security: DeviceSecurity | None,
) -> int:
    """Access-protection levels to authorize for, or 0 to skip A_Authorize.

    A secure session authenticates with its tool key, so plaintext A_Authorize
    is not used there; otherwise the mask version's ``AuthorizeLevels`` (from the
    master data) decides. Unknown (no master data) means 0: no authorize.
    """
    if security is not None:
        return 0
    if master is None:
        return 0
    return mask_authorize_levels(master.raw, application.program.mask_version)


def _reject_address_scope(scope: DownloadScope) -> None:
    if scope.individual_address_only:
        raise UnsupportedProcedureError(
            f"scope {scope.name} is downloaded by download_individual_address"
        )


async def _device_present(
    xknx: XKNX, address: IndividualAddress, security: DeviceSecurity | None
) -> bool:
    """Whether a device already answers at ``address``.

    Opens a point-to-point connection (secured when ``security`` is set) and reads
    the device descriptor. Any connection or verification failure means nothing is
    reachable there, so a full download may assign the address instead of failing.
    """
    from .errors import DownloadError
    from .programmer import DeviceProgrammer

    manager = _connection_manager(xknx, address, security)
    try:
        connection = await manager.open()
        await DeviceProgrammer(connection).read_device_descriptor()
        return True
    except (ManagementConnectionError, DownloadError):
        return False
    finally:
        await manager.close()


async def download(
    xknx: XKNX,
    individual_address: IndividualAddressableType,
    application: Application,
    *,
    master: MasterData | None = None,
    device: SeedDevice | None = None,
    image: DownloadImage | None = None,
    group_communication: GroupCommunication | None = None,
    scope: DownloadScope = DownloadScope.FULL,
    parameter_values: Mapping[str, str] | None = None,
    max_apdu_length: int | None = None,
    expected_descriptor: int | None = None,
    security: DeviceSecurity | None = None,
    commission: bool = False,
    commission_serial_number: bytes | None = None,
    progress: Callable[[int, int], None] | None = None,
    confirm_full_download: Callable[[str], Awaitable[bool]] | None = None,
    ip_config: IpDeviceConfig | None = None,
) -> DownloadResult:
    """Download ``application`` into the device at ``individual_address``.

    Assembles the download image (applying ``parameter_values`` on top of the
    application defaults), resolves the Load Procedure (merging the application's
    fragments into the mask version's default procedure from ``master`` for
    default/merged procedure styles) and runs it. The transport connection is
    opened and closed following the procedure's Connect/Disconnect/Restart
    controls. ``scope`` selects a full or partial download.

    ``max_apdu_length`` defaults to ``None``, which negotiates the length from the
    device (reading its maximum APDU length once, for larger and fewer telegrams);
    pass a fixed integer to override. ``expected_descriptor`` (the device
    descriptor type 0, e.g. ``0x07B0`` for a System B device) is checked against
    the device before any write when given, guarding against programming the wrong
    device.

    Pass a pre-built ``image`` to skip assembly (e.g. one an editor built from its
    live evaluator); otherwise it is built from ``device``/``parameter_values``.
    ``master`` (``registry.master``) is required for applications with a default
    or merged procedure style.

    ``commission`` enables full-download commissioning of a virgin device: for a
    :data:`~.scope.DownloadScope.FULL` download, if no device answers at
    ``individual_address``, the address is written to the single device currently
    in programming mode before the application is downloaded - or, with
    ``commission_serial_number``, to the device with that KNX serial number.
    Without it, the device must already carry ``individual_address`` (a partial
    download always requires that). ``xknx`` has to be started.

    A :data:`~.scope.DownloadScope.UNLOAD_ALL` download runs the Unload procedure
    over the device address and then resets the individual address to the default
    (15.15.255); the single device in programming mode is reset. ``master`` is
    required to resolve the Unload procedure.

    A partial download first checks that the device runs the application and fails with
    :class:`~.partial.DifferentProgramLoaded` otherwise. An application that prefers a
    partial download (``PreferPartialDownloadIfApplicationLoaded``) gets one on a full
    download when that check passes; when it does not, the full download asks
    ``confirm_full_download`` with the reason first if the application wants that
    (``PromptBeforeFullDownload``).
    """
    _reject_address_scope(scope)
    if image is None:
        logger.debug("download: assembling image (scope=%s)", scope.name)
        image = build_image(
            application,
            device=device,
            parameter_values=parameter_values,
            group_communication=group_communication,
        )
    else:
        logger.debug("download: using pre-built image (scope=%s)", scope.name)
    controls = _resolve_controls(application, master, image, scope, ip_config)
    address = IndividualAddress(individual_address)
    if (
        commission
        and scope is DownloadScope.FULL
        and not await _device_present(xknx, address, security)
    ):
        logger.info(
            "download: no device answered at %s; assigning the address to the "
            "device %s",
            address,
            "by serial number"
            if commission_serial_number is not None
            else "in programming mode",
        )
        from .commissioning import program_individual_address

        await program_individual_address(
            xknx, address, serial_number=commission_serial_number
        )
    checked = await _partial_scope(
        xknx, address, security, application, master, scope, confirm_full_download
    )
    if checked is not scope:
        scope = checked
        controls = _resolve_controls(application, master, image, scope, ip_config)
    adapted = await _device_association_format(xknx, address, security, image, scope)
    if adapted is not image:
        image = adapted
        controls = _resolve_controls(application, master, image, scope, ip_config)
    apdu_ceiling, negotiate_apdu = _apdu_settings(max_apdu_length)
    logger.debug(
        "download: %s -> %d load controls, apdu=%s secure=%s",
        address,
        len(controls),
        "negotiate" if negotiate_apdu else apdu_ceiling,
        security is not None,
    )

    manager = _connection_manager(xknx, address, security)
    runner = LoadProcedureRunner(
        application,
        image,
        connection_manager=manager,
        max_apdu_length=apdu_ceiling,
        controls=controls,
        object_types=_object_types(application, master),
        scope=scope,
        expected_descriptor=expected_descriptor,
        negotiate_apdu=negotiate_apdu,
        apdu_overhead=_apdu_overhead(security),
        authorize_levels=_authorize_levels(application, master, security),
    )
    try:
        await runner.run(progress)
    except Exception as exc:
        # A failure after the procedure began writing the device may leave it in a
        # partially loaded, inconsistent state. Flag that for load scopes so the
        # caller can warn the user; an unload is meant to clear the device, so its
        # failures pass through unwrapped. The original failure is kept both as the
        # cause and inside the message so it is never lost from any log sink.
        if runner.state_mutated and scope not in (
            DownloadScope.UNLOAD,
            DownloadScope.UNLOAD_ALL,
        ):
            raise PartialDownloadError(
                f"Downloading {address} failed after it had started writing the "
                f"device, so it may be left in an inconsistent, partially loaded "
                f"state - re-download it to restore a working state. Cause: {exc}"
            ) from exc
        raise
    finally:
        # Ensure the connection is closed even if the procedure omits a trailing
        # Disconnect or fails partway through.
        await manager.close()

    if scope is DownloadScope.UNLOAD_ALL:
        logger.info(
            "download: unload complete; resetting %s to the default address", address
        )
        from .commissioning import reset_individual_address

        await reset_individual_address(xknx)
    mac = runner.captured_property(IP_PARAMETER_OBJECT, PID_MAC_ADDRESS)
    return DownloadResult(
        mac_address=mac if mac is not None and len(mac) == _MAC_ADDRESS_LENGTH else None
    )


async def preflight(
    xknx: XKNX,
    individual_address: IndividualAddressableType,
    application: Application,
    *,
    master: MasterData | None = None,
    device: SeedDevice | None = None,
    image: DownloadImage | None = None,
    group_communication: GroupCommunication | None = None,
    scope: DownloadScope = DownloadScope.FULL,
    parameter_values: Mapping[str, str] | None = None,
    max_apdu_length: int | None = None,
    expected_descriptor: int | None = None,
    security: DeviceSecurity | None = None,
    ip_config: IpDeviceConfig | None = None,
) -> PreflightReport:
    """Report what :func:`download` would change on the device, changing nothing.

    Assembles the same download image and resolves the same Load Procedure as
    :func:`download` (with the same arguments), then reads the device's current
    bytes at every location a write would target and returns the diff. Nothing is
    written and no load state is changed. Run this before a real download to
    confirm the change set (and to catch a mismatched application via the
    procedure's compare controls). Pass a pre-built ``image`` to skip assembly.
    ``xknx`` has to be started.
    """
    _reject_address_scope(scope)
    if image is None:
        image = build_image(
            application,
            device=device,
            parameter_values=parameter_values,
            group_communication=group_communication,
        )
    address = IndividualAddress(individual_address)
    image = await _device_association_format(xknx, address, security, image, scope)
    controls = _resolve_controls(application, master, image, scope, ip_config)
    apdu_ceiling, negotiate_apdu = _apdu_settings(max_apdu_length)

    manager = _connection_manager(xknx, address, security)
    runner = LoadProcedureRunner(
        application,
        image,
        connection_manager=manager,
        max_apdu_length=apdu_ceiling,
        controls=controls,
        object_types=_object_types(application, master),
        scope=scope,
        expected_descriptor=expected_descriptor,
        negotiate_apdu=negotiate_apdu,
        apdu_overhead=_apdu_overhead(security),
        authorize_levels=_authorize_levels(application, master, security),
    )
    try:
        return await runner.preflight()
    finally:
        await manager.close()
