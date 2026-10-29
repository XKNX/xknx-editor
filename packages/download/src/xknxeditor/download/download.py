"""High level entry point for downloading an application into a device.

Assembles the download image and runs the device's Load Procedure over a
point-to-point connection, following KNX Standard v3.0.0, Chapter 3/5/3
"Configuration Procedures": the complete download procedure (section 3.5.2) and
the partial download procedure (section 3.5.3).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, NamedTuple

from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress

from xknxeditor.namespaces.intermediate.ld_ctrl_master_reset_t import LdCtrlMasterReset
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace

from .errors import (
    DownloadError,
    MemoryReadNotAnswered,
    MemoryWriteNotAcknowledged,
    PartialDownloadError,
    UnsupportedProcedureError,
)
from .group_communication import materialize_group_communication_controls
from .image import build_image, with_second_program
from .ip_config import (
    IP_PARAMETER_OBJECT,
    PID_MAC_ADDRESS,
    IpDeviceConfig,
    insert_position,
    with_ip_config_controls,
)
from .ip_config import applies as ip_config_applies
from .ip_secure import IpSecureConfig, ip_secure_controls
from .merge import (
    device_mask_version,
    mask_authorize_levels,
    mask_has_property_services,
    resolve_download_controls,
)
from .procedure import LoadProcedureRunner, memory_services
from .programmer import (
    DEFAULT_MAX_APDU_LENGTH,
    MAX_COMMUNICATION_APDU_LENGTH,
    MEMORY_SERVICE_FALLBACKS,
)
from .resources import property_group_tables
from .scope import DownloadScope, mask_configuration, mask_object_types
from .secure_config import (
    commissioning_controls,
    plain_controls,
    routing_control_controls,
    table_controls,
)
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


def _apdu_settings(
    max_apdu_length: int | None, interface_max_apdu_length: int | None = None
) -> tuple[int, bool]:
    """Resolve the APDU ceiling and whether to negotiate it from the device.

    A negotiated length stays within the interface's maximum APDU length: a frame longer
    than the interface accepts is dropped, even when the device could take it.
    """
    if max_apdu_length is not None:
        return max_apdu_length, False
    ceiling = MAX_COMMUNICATION_APDU_LENGTH
    if interface_max_apdu_length is not None:
        ceiling = max(DEFAULT_MAX_APDU_LENGTH, min(ceiling, interface_max_apdu_length))
    return ceiling, True


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
    # Whether the device uses the Tool Key the download was given, at its end (secure
    # commissioning wrote it, or the device already had it).
    tool_key_loaded: bool = False
    # The device's next Sequence Number Sending at the end of a secured download.
    sequence_number: int | None = None
    # Whether the download ran over a secured connection.
    secured: bool = False
    # The memory-service cap the successful run used: None when the default
    # APDU-derived size worked, or the lowered value a no-ACK fallback settled on. A
    # caller can remember this per application to seed the next download and skip
    # the discovery retry.
    memory_service_count: int | None = None
    # Whether an unload reset the device to its factory state with its individual address
    # (a secure device then uses its FDSK again).
    factory_reset: bool = False


_IP_CONFIG_SCOPES = frozenset({DownloadScope.FULL, DownloadScope.APPLICATION})
_MAC_ADDRESS_LENGTH = 6


def _memory_cap_ladder(seed: int | None) -> tuple[int | None, ...]:
    """Memory-service caps to try in order.

    Without a seed, start from the APDU-derived default (``None``) and fall back to
    progressively smaller caps. With a seed (a previously learned limit), start there
    and only fall back to still-smaller caps, so a known device skips the discovery.
    """
    if seed is None:
        return (None, *MEMORY_SERVICE_FALLBACKS)
    return (seed, *(c for c in MEMORY_SERVICE_FALLBACKS if c < seed))


def _resolve_controls(
    application: Application,
    master: MasterData | None,
    image: DownloadImage,
    scope: DownloadScope,
    ip_config: IpDeviceConfig | None = None,
    security: DeviceSecurity | None = None,
    tool_key: bytes | None = None,
    ip_secure: IpSecureConfig | None = None,
    disable_security: bool = False,
    routing_blocking: bool | None = None,
    application2: Application | None = None,
) -> list[object]:
    """Select and materialize the scoped procedure before any device mutation.

    A secured download of a device with secured group addresses writes its security tables
    before the final restart; with ``tool_key`` it starts with the Security Mode and the Tool
    Key, with ``disable_security`` it ends with emptied tables and the Security Mode off (see
    :mod:`.secure_config`). ``application2`` is the product's second application program."""
    controls = _resolve_procedure(application, master, image, scope, application2)
    if (
        ip_config is not None
        and scope in _IP_CONFIG_SCOPES
        and ip_config_applies(application)
    ):
        controls = with_ip_config_controls(application, controls, ip_config)
    if security is None:
        return controls
    if disable_security:
        position = insert_position(application, controls)
        p2p = bool(application.program.max_security_p2_pkey_table_entries)
        return [
            *controls[:position],
            *plain_controls(p2p=p2p),
            *controls[position:],
        ]
    if routing_blocking is not None:
        position = insert_position(application, controls)
        controls = [
            *controls[:position],
            *routing_control_controls(block=routing_blocking),
            *controls[position:],
        ]
    if image.security_tables is not None:
        position = insert_position(application, controls)
        controls = [
            *controls[:position],
            *table_controls(image.security_tables),
            *controls[position:],
        ]
    if ip_secure is not None:
        extra = ip_secure_controls(ip_secure)
        if extra:
            position = insert_position(application, controls)
            controls = [*controls[:position], *extra, *controls[position:]]
    if tool_key is not None:
        controls = [*commissioning_controls(tool_key), *controls]
    return controls


def _resolve_procedure(
    application: Application,
    master: MasterData | None,
    image: DownloadImage,
    scope: DownloadScope,
    application2: Application | None = None,
) -> list[object]:
    master_raw = master.raw if master is not None else None
    if scope in (DownloadScope.UNLOAD, DownloadScope.UNLOAD_ALL):
        from xknxeditor.namespaces.intermediate.procedure_type_t import ProcedureType

        if unloads_by_master_reset(application):
            # A factory reset, with the individual address for an unload of everything.
            return [
                LdCtrlMasterReset(
                    erase_code=_FACTORY_RESET
                    if scope is DownloadScope.UNLOAD_ALL
                    else _FACTORY_RESET_KEEP_ADDRESS,
                    channel_number=0,
                )
            ]
        # The mask's Unload procedure unloads the second program too.
        return resolve_download_controls(
            application,
            master_raw,
            procedure_type=ProcedureType.UNLOAD,
            scope=scope,
            parameter_values=image.procedure_parameters,
        )
    base = resolve_download_controls(
        application,
        master_raw,
        scope=scope,
        parameter_values=image.procedure_parameters,
        application2=application2,
    )
    return materialize_group_communication_controls(
        image,
        base,
        _object_types(application, master),
        scope,
        property_tables=property_group_tables(device_mask_version(application)),
    )


_PARTIAL_SCOPES = frozenset(
    {
        DownloadScope.PARAMETERS,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    }
)


class _ScopeCheck(NamedTuple):
    """The scope :func:`_partial_scope` chose, and whether the device has the
    application loaded for a full procedure (see ``LoadProcedureRunner``)."""

    scope: DownloadScope
    application_loaded: bool = False


async def _partial_scope(
    xknx: XKNX,
    address: IndividualAddress,
    security: DeviceSecurity | None,
    application: Application,
    master: MasterData | None,
    scope: DownloadScope,
    confirm_full_download: Callable[[str], Awaitable[bool]] | None,
    application2: Application | None = None,
) -> _ScopeCheck:
    """The scope to download after checking the device's application (and the product's second
    application program ``application2``)."""
    from .partial import (
        DifferentProgramLoaded,
        FullDownloadDeclined,
        partial_download_mismatch,
    )
    from .programmer import DeviceProgrammer
    from .resources import bundled_mask_resources, mask_resources

    options = application.program.static.options
    mapped = bundled_mask_resources(device_mask_version(application))
    # BIM M112 product procedures reload by unloading/allocating absolute
    # segments. A "partial" still runs those AbsSegments; skipping Unload of
    # LSM 3 leaves a half-mapped application. They keep the full load, without
    # the code seed the device already holds.
    memory_mapped = mapped is not None and mapped.memory_mapped_lsm() is not None
    prefer = (
        scope in (DownloadScope.FULL, DownloadScope.APPLICATION)
        and options is not None
        and options.prefer_partial_download_if_application_loaded
    )
    if scope not in _PARTIAL_SCOPES and not prefer:
        return _ScopeCheck(scope)
    resources = mask_resources(master, device_mask_version(application))
    manager = _connection_manager(xknx, address, security)
    try:
        programmer = DeviceProgrammer(await manager.open())
        memory_services(application).apply(programmer)
        reason = await partial_download_mismatch(
            programmer, application, resources, application2
        )
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
        return _ScopeCheck(scope)
    if reason is None:
        logger.info(
            "download: trying a partial download because of "
            "PreferPartialDownloadIfApplicationLoaded"
        )
        if memory_mapped:
            return _ScopeCheck(scope, application_loaded=True)
        return _ScopeCheck(DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION)
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
    return _ScopeCheck(scope)


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
        mask_object_types(master.raw, device_mask_version(application))
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
    return mask_authorize_levels(master.raw, device_mask_version(application))


def _property_services(application: Application, master: MasterData | None) -> bool:
    """BCU1 masks have no property services. Unknown masks keep the probes."""
    program = getattr(application, "program", None)
    mask_version = getattr(program, "mask_version", None)
    if not isinstance(mask_version, str):
        return True
    raw = master.raw if master is not None else None
    return mask_has_property_services(raw, mask_version)


def _with_mask_memory(
    image: DownloadImage, application: Application, master: MasterData | None
) -> DownloadImage:
    """``image`` with the memory segments of the device's mask from the master data."""
    if master is None or getattr(image, "mask_memory", True):
        return image
    configuration = mask_configuration(master.raw, device_mask_version(application))
    segments = configuration.memory_segments if configuration is not None else None
    ranges = tuple(
        (segment.location.start_address, segment.length)
        for segment in (segments.memory_segment if segments is not None else ())
        if segment.location is not None
        and segment.location.address_space is ResourceAddrSpace.STANDARD_MEMORY
        and segment.location.start_address is not None
        and segment.length
    )
    return replace(image, mask_memory=ranges) if ranges else image


# Erase codes of a Master Reset: the factory state, with and without the individual address.
_FACTORY_RESET = 2
_FACTORY_RESET_KEEP_ADDRESS = 7
# The mask of a device that only takes secured management.
_LOCKED_SECURE_MASK = "MV-FFFF"


def unloads_by_master_reset(application: Application) -> bool:
    """Whether an unload resets the device to its factory state (a Master Reset) instead of
    running the mask's Unload procedure: a device with KNX Data Secure, which has to give up its
    Tool Key."""
    program = application.program
    return bool(getattr(program, "is_secure_enabled", False)) or (
        device_mask_version(application) == _LOCKED_SECURE_MASK
    )


def _with_second_program(
    image: DownloadImage, application2: Application | None
) -> DownloadImage:
    """``image`` with the segments of the second application program ``application2``, which
    the procedure loads with the application."""
    return image if application2 is None else with_second_program(image, application2)


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
    tool_key: bytes | None = None,
    ip_secure: IpSecureConfig | None = None,
    disable_security: bool = False,
    routing_blocking: bool | None = None,
    memory_service_count: int | None = None,
    interface_max_apdu_length: int | None = None,
    application2: Application | None = None,
) -> DownloadResult:
    """Download ``application`` into the device at ``individual_address``.

    Assembles the download image (applying ``parameter_values`` on top of the
    application defaults), resolves the Load Procedure (merging the application's
    fragments into the mask version's default procedure from ``master`` for
    default/merged procedure styles) and runs it. The transport connection is
    opened and closed following the procedure's Connect/Disconnect/Restart
    controls. ``scope`` selects a full or partial download.

    ``max_apdu_length`` defaults to ``None``, which negotiates the length from the
    device (reading its maximum APDU length once, for larger and fewer telegrams),
    within ``interface_max_apdu_length`` (the connecting interface's maximum) when
    given; pass a fixed integer to override. ``expected_descriptor`` (the device
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
    required to resolve the Unload procedure. A device with KNX Data Secure is reset to
    its factory state instead (a Master Reset; with its individual address for
    ``UNLOAD_ALL``, which the result reports as ``factory_reset``).

    A partial download first checks that the device runs the application and fails with
    :class:`~.partial.DifferentProgramLoaded` otherwise. An application that prefers a
    partial download (``PreferPartialDownloadIfApplicationLoaded``) gets one on a full
    download when that check passes; when it does not, the full download asks
    ``confirm_full_download`` with the reason first if the application wants that
    (``PromptBeforeFullDownload``).

    ``tool_key`` is the device's Tool Key from the project, for a secured download (``security``):
    a device that still uses its FDSK (secure commissioning) or the key the session tried first
    gets it written, after the Security Mode is switched on. ``security`` then carries the key
    the device is expected to use and, as alternatives, the others (e.g. the FDSK). The result
    tells whether the device uses ``tool_key`` at the end, and its next Sequence Number.

    ``disable_security`` switches a device whose secure commissioning was deactivated to plain
    communication, over the secured connection with the key it still holds (``security``).

    ``routing_blocking`` blocks (``True``) or routes (``False``) point-to-point and broadcast
    messages on both sides of a coupler with the security extensions (KNX AN192); ``None``
    leaves them.

    ``application2`` is the product's second application program (the PEI program of a
    Hardware2Program with two ApplicationProgramRefs): a full download loads it with the
    application, a partial one checks that the device runs it.
    """
    if tool_key is not None and security is None:
        raise DownloadError("writing the Tool Key needs a secured download")
    if ip_secure is not None and security is None:
        raise DownloadError("writing IP Secure settings needs a secured download")
    if disable_security and (security is None or tool_key is not None):
        raise DownloadError(
            "switching a device to plain communication needs its key and no new Tool Key"
        )
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
    image = _with_second_program(
        _with_mask_memory(image, application, master), application2
    )
    controls = _resolve_controls(
        application,
        master,
        image,
        scope,
        ip_config,
        security,
        tool_key,
        ip_secure,
        disable_security,
        routing_blocking if security is not None else None,
        application2,
    )
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
        xknx,
        address,
        security,
        application,
        master,
        scope,
        confirm_full_download,
        application2,
    )
    application_loaded = checked.application_loaded
    if checked.scope is not scope:
        scope = checked.scope
        controls = _resolve_controls(
            application,
            master,
            image,
            scope,
            ip_config,
            security,
            tool_key,
            ip_secure,
            disable_security,
            routing_blocking if security is not None else None,
            application2,
        )
    adapted = await _device_association_format(xknx, address, security, image, scope)
    if adapted is not image:
        image = adapted
        controls = _resolve_controls(
            application,
            master,
            image,
            scope,
            ip_config,
            security,
            tool_key,
            ip_secure,
            disable_security,
            routing_blocking if security is not None else None,
            application2,
        )
    apdu_ceiling, negotiate_apdu = _apdu_settings(
        max_apdu_length, interface_max_apdu_length
    )
    logger.debug(
        "download: %s -> %d load controls, apdu=%s secure=%s",
        address,
        len(controls),
        f"negotiate (at most {apdu_ceiling})" if negotiate_apdu else apdu_ceiling,
        security is not None,
    )

    object_types = _object_types(application, master)
    authorize_levels = _authorize_levels(application, master, security)
    # Size memory writes from the negotiated APDU first; if a device leaves a classic
    # memory write unacknowledged (some firmwares advertise more than they accept),
    # retry the whole download with progressively smaller memory-service caps. A caller
    # that already learned a device's limit passes it as ``memory_service_count`` to
    # start there and skip the discovery retry.
    memory_caps = _memory_cap_ladder(memory_service_count)
    if memory_service_count is not None:
        logger.info(
            "download: %s starting at a learned %d-octet memory-service cap",
            address,
            memory_service_count,
        )
    runner: LoadProcedureRunner | None = None
    manager: ConnectionManager | None = None
    used_memory_cap: int | None = None
    for attempt, memory_cap in enumerate(memory_caps):
        manager = _connection_manager(xknx, address, security)
        runner = LoadProcedureRunner(
            application,
            image,
            connection_manager=manager,
            max_apdu_length=apdu_ceiling,
            controls=controls,
            object_types=object_types,
            scope=scope,
            expected_descriptor=expected_descriptor,
            negotiate_apdu=negotiate_apdu,
            apdu_overhead=_apdu_overhead(security),
            authorize_levels=authorize_levels,
            memory_service_count=memory_cap,
            property_services=_property_services(application, master),
            application_loaded=application_loaded,
            application2=application2,
        )
        try:
            await runner.run(progress)
            used_memory_cap = memory_cap
            break
        except (MemoryWriteNotAcknowledged, MemoryReadNotAnswered) as exc:
            next_cap = (
                memory_caps[attempt + 1] if attempt + 1 < len(memory_caps) else None
            )
            if next_cap is not None:
                logger.warning(
                    "download: %s left a long memory service unanswered; retrying with a "
                    "%d-octet memory-service cap. Cause: %s",
                    address,
                    next_cap,
                    exc,
                )
                continue
            raise PartialDownloadError(
                f"Downloading {address} failed after it had started writing the "
                f"device, so it may be left in an inconsistent, partially loaded "
                f"state - re-download it to restore a working state. Cause: {exc}"
            ) from exc
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

    # The loop only reaches here via a successful run (every other path raises).
    assert runner is not None and manager is not None

    if used_memory_cap is not None:
        logger.info(
            "download: %s completed with a %d-octet memory-service cap "
            "(device rejected larger classic memory writes)",
            address,
            used_memory_cap,
        )
    # The factory reset of an unload of everything took the individual address too.
    factory_reset = scope is DownloadScope.UNLOAD_ALL and unloads_by_master_reset(
        application
    )
    if scope is DownloadScope.UNLOAD_ALL and not factory_reset:
        logger.info(
            "download: unload complete; resetting %s to the default address", address
        )
        from .commissioning import reset_individual_address

        await reset_individual_address(xknx)
    mac = runner.captured_property(IP_PARAMETER_OBJECT, PID_MAC_ADDRESS)
    return DownloadResult(
        mac_address=mac
        if mac is not None and len(mac) == _MAC_ADDRESS_LENGTH
        else None,
        tool_key_loaded=tool_key is not None
        and getattr(manager, "active_key", None) == tool_key,
        sequence_number=getattr(manager, "device_sequence_number", None),
        secured=security is not None,
        memory_service_count=used_memory_cap,
        factory_reset=factory_reset,
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
    interface_max_apdu_length: int | None = None,
    application2: Application | None = None,
) -> PreflightReport:
    """Report what :func:`download` would change on the device, changing nothing.

    Assembles the same download image and resolves the same Load Procedure as
    :func:`download` (with the same arguments), then reads the device's current
    bytes at every location a write would target and returns the diff. Nothing is
    written and no load state is changed. Run this before a real download to
    confirm the change set (and to catch a mismatched application via the
    procedure's compare controls). Pass a pre-built ``image`` to skip assembly.
    ``interface_max_apdu_length`` limits the negotiated APDU length as for
    :func:`download`; a long memory read that gets no response is repeated with
    smaller blocks. ``xknx`` has to be started. ``application2`` is the product's second
    application program, as for :func:`download`.
    """
    _reject_address_scope(scope)
    if image is None:
        image = build_image(
            application,
            device=device,
            parameter_values=parameter_values,
            group_communication=group_communication,
        )
    image = _with_second_program(
        _with_mask_memory(image, application, master), application2
    )
    address = IndividualAddress(individual_address)
    application_loaded = False
    if scope not in _PARTIAL_SCOPES:
        # The scope a download would take (PreferPartialDownloadIfApplicationLoaded).
        scope, application_loaded = await _partial_scope(
            xknx, address, security, application, master, scope, None, application2
        )
    image = await _device_association_format(xknx, address, security, image, scope)
    controls = _resolve_controls(
        application, master, image, scope, ip_config, application2=application2
    )
    apdu_ceiling, negotiate_apdu = _apdu_settings(
        max_apdu_length, interface_max_apdu_length
    )
    memory_caps = _memory_cap_ladder(None)
    for attempt, memory_cap in enumerate(memory_caps):
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
            memory_service_count=memory_cap,
            property_services=_property_services(application, master),
            application_loaded=application_loaded,
            application2=application2,
        )
        try:
            return await runner.preflight()
        except MemoryReadNotAnswered as exc:
            if attempt + 1 >= len(memory_caps):
                raise
            logger.warning(
                "preflight: %s left a long memory read unanswered; retrying with a "
                "%d-octet memory-service cap. Cause: %s",
                address,
                memory_caps[attempt + 1],
                exc,
            )
        finally:
            await manager.close()
    raise AssertionError("unreachable")
