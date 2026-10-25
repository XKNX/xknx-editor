"""GUI project facade: lazy device view over ProjectService with selection and pub/sub."""

import datetime
import hashlib
import io
import os
import platform
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from editor_gui.concurrency import (
    io_guarded,
    revision_cached,
    revision_cached_by_args,
)
from editor_gui.device import (
    ComObject,
    Device,
    UnloadedDevice,
    address_order,
    device_label,
)
from editor_gui.device_info import serial_to_project
from editor_gui.device_plugins import ConfigurationApp, configuration_apps
from editor_gui.device_snapshot import (
    SnapshotState,
    decode_snapshot,
    encode_snapshot,
    snapshot_key,
    snapshot_state,
)
from editor_gui.dpt import dpt_from_token, lookup_or_make_dpt
from editor_gui.plugins.project.element_status import status_label, usage_label
from editor_gui.plugins.project.medium_text import medium_error_text
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.history import HistoryEntry
from editor_gui.settings import config_dir
from xknxeditor.catalog.core.hardware import FactoryData
from xknxeditor.namespaces.intermediate import (
    ComObjectInstanceRef,
    ParameterInstanceRef,
)
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.prod import Application
from xknxeditor.prod.gc_pause import gc_paused
from xknxeditor.prod.parser_v2.calculation import VALIDATION_FAILED
from xknxeditor.proj import (
    AdditionalAddressError,
    ProjectStorageError,
    ensure_sqlite_writable,
)
from xknxeditor.proj import ProjectService as _ProjectService
from xknxeditor.proj import import_ga_export as _import_ga_export
from xknxeditor.proj import import_knxproj as _import_knxproj
from xknxeditor.proj import is_ga_export as _is_ga_export
from xknxeditor.proj import write_ga_export as _write_ga_export
from xknxeditor.proj.core.addressing import (
    GroupAddressStyle,
    format_ga,
    parse_ga,
    parse_ia,
)
from xknxeditor.proj.core.dpt import normalize_datapoint_type
from xknxeditor.proj.core.ga_export import write_ga_csv as _write_ga_csv
from xknxeditor.proj.core.identity import qualified_com_object_ref
from xknxeditor.proj.core.ip_secure import PASSWORD_ACCESS_TYPES
from xknxeditor.proj.core.medium import (
    IP_ROUTER_MASKS,
    MEDIUM_RF,
    MEDIUM_TP,
    MediumError,
    device_fits,
    medium_name,
)
from xknxeditor.proj.core.project_secrets import check_project_password
from xknxeditor.proj.core.rich_text import parse_rich_text, plain_text
from xknxeditor.proj.core.service import DEFAULT_MULTICAST_ADDRESS

if TYPE_CHECKING:
    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.catalog.service import CatalogService
    from editor_gui.plugins.connection.service import SecureTunnel
    from editor_gui.plugins.keyring.service import KeyringIpSecrets, KeyringService
    from xknxeditor.catalog import ProductDeletion, ProductSummary, ProgramSummary
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.ip_config import IpDeviceConfig
    from xknxeditor.download.ip_secure import IpSecureConfig
    from xknxeditor.download.secure_config import GroupSecurity
    from xknxeditor.prod.parser_v2.calculation import ChangeSet
    from xknxeditor.proj.core.compact import CompactResult
    from xknxeditor.proj.core.fdsk import Fdsk
    from xknxeditor.proj.core.import_notes import ImportLoss
    from xknxeditor.proj.core.product_prune import ProgressCallback
    from xknxeditor.proj.core.service import (
        AdditionalAddressInfo,
        DeviceInfo,
        DeviceKeys,
        DeviceLinkInfo,
        ForcedSecurity,
        FunctionInfo,
        GroupRangeInfo,
        IpSecrets,
        SecureCommissioningImpact,
        SpaceDeviceInfo,
        SpaceInfo,
    )
    from xknxeditor.proj.core.service import (
        GroupAddressInfo as CoreGroupAddressInfo,
    )

_INSTALLATION = 0

ParamMode = Literal["edit", "transfer", "raw"]
"""How a parameter change is applied: ``edit`` runs calculations and validations, ``transfer``
calculations only, ``raw`` stores the values as they are."""


class BuildCancelled(Exception):
    """A device build stopped on request (see ``ProjectService.build_cancelled``)."""


@dataclass(frozen=True)
class UpdateApplicationResult:
    """Outcome of an application update: the version updated to and how many parameter/
    com-object rows carried over vs were dropped as incompatible."""

    new_version: int
    kept: int
    dropped: int


# Which commissioning "loaded" flags a successful download of each scope sets (keyed by
# DownloadScope.value). FULL/UNLOAD set all true/false; partial scopes set only their part. Flags
# not listed are left unchanged.
_ALL_LOADED = (
    "individual_address_loaded",
    "application_program_loaded",
    "communication_part_loaded",
    "medium_config_loaded",
    "parameters_loaded",
)
_COMMISSIONING_BY_SCOPE: dict[str, dict[str, bool]] = {
    "full": dict.fromkeys(_ALL_LOADED, True),
    "unload": dict.fromkeys(_ALL_LOADED, False),
    "ap1": {  # APPLICATION: parameters + application + group communication (not the address)
        "application_program_loaded": True,
        "communication_part_loaded": True,
        "medium_config_loaded": True,
        "parameters_loaded": True,
    },
    "par": {"parameters_loaded": True},  # PARAMETERS
    "grp": {"communication_part_loaded": True},  # GROUP_COMMUNICATION
    "ia": {"individual_address_loaded": True},  # INDIVIDUAL_ADDRESS
    "ia_overwrite": {"individual_address_loaded": True},  # OVERWRITE_INDIVIDUAL_ADDRESS
}


_FLAG_COLUMNS: dict[str, str] = {
    "communication": "communication_flag",
    "read": "read_flag",
    "write": "write_flag",
    "transmit": "transmit_flag",
    "update": "update_flag",
    "read_on_init": "read_on_init_flag",
}


def _parse_individual_address(text: str) -> int | None:
    """Parse ``area.line.device`` into a raw 16-bit individual address."""
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        area, line, device = (int(p) for p in parts)
    except ValueError:
        return None
    if not (0 <= area <= 0xF and 0 <= line <= 0xF and 0 <= device <= 0xFF):
        return None
    return (area << 12) | (line << 8) | device


def _parse_group_address(text: str) -> int | None:
    """Parse a 3-level (``a/b/c``), 2-level (``a/b``) or free group address into a raw value."""
    parts = text.split("/")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        return (nums[0] << 11) | (nums[1] << 8) | nums[2]
    if len(nums) == 2:
        return (nums[0] << 11) | nums[1]
    if len(nums) == 1:
        return nums[0]
    return None


def _parameter_instance_refs(
    parameters: list[tuple[str, str]] | None,
) -> list[ParameterInstanceRef]:
    """Wrap ``(ref_id, value)`` overrides as parameter instance refs for a fresh ``Device``.

    A device built with these resolves its dynamic UI against the given values, so the
    activated (param-driven) com-object set matches the configuration rather than the raw
    application defaults. Empty when there are no overrides (the default set is kept)."""
    if not parameters:
        return []
    return [
        ParameterInstanceRef(ref_id=ref_id, value=value) for ref_id, value in parameters
    ]


_COLUMN_KEYS: dict[type[Any], tuple[str, ...]] = {}


def _row_values(obj: Any) -> tuple[Any, ...]:
    row_type = cast(type[Any], type(obj))
    keys = _COLUMN_KEYS.get(row_type)
    if keys is None:
        keys = tuple(column.key for column in obj.__table__.columns)
        _COLUMN_KEYS[row_type] = keys
    # The loaded values directly (far cheaper than the mapped attributes), the attributes for
    # a row whose values are not loaded.
    values = obj.__dict__
    try:
        return tuple(values[key] for key in keys)
    except KeyError:
        return tuple(getattr(obj, key) for key in keys)


def _instance_refs(
    row: Any, app_program_id: str
) -> tuple[list[ParameterInstanceRef], list[Any], list[ComObjectInstanceRef]]:
    """The parameter, module and com-object instance refs a device of ``row`` is built with."""
    from xknxeditor.namespaces.intermediate import ParameterInstanceRef as _PIR
    from xknxeditor.namespaces.intermediate.module_instance_t import (
        ModuleInstance as _MI,
    )

    pirs = [_PIR(ref_id=p.ref_id, value=p.value) for p in row.parameters]
    mis = [_MI(id=mi.instance_id, ref_id=mi.ref_id) for mi in row.module_instances]
    # Every persisted ComObject row is an instantiated object (that is what pins the device's
    # object set against the parser's channel over-activation). Seed one instance ref per row
    # — with its flag overrides where present, else a bare ref — so objects added by a
    # function/mode re-instantiation (default, i.e. all-None, flags) are also counted as
    # instantiated and thus shown/encoded rather than pruned away.
    coirs = [
        _co_instance_ref_from_row(
            co_row, ref_id=_qualified_com_object_ref(co_row, app_program_id)
        )
        or ComObjectInstanceRef(
            ref_id=_qualified_com_object_ref(co_row, app_program_id)
        )
        for co_row in row.com_objects
    ]
    return pirs, mis, coirs


def _module_instance_changes(
    device: Device, app_program_id: str
) -> tuple[list[tuple[str, str]], list[str]]:
    """The device's module instances its saved list lacks (created by a parameter change), and the
    saved ones it no longer has (ended by one), as saved. Saved ids may be relative to the
    application (as an import keeps them), so both are compared qualified."""

    def qualified(element_id: str) -> str:
        if element_id.startswith(f"{app_program_id}_"):
            return element_id
        return f"{app_program_id}_{element_id}"

    current = device.get_module_instances()
    saved = {qualified(mi.id): mi.id for mi in device.module_instances if mi.id}
    active = {qualified(instance_id) for instance_id, _ in current}
    new = [(i, r) for i, r in current if qualified(i) not in saved]
    ended = [saved_id for key, saved_id in saved.items() if key not in active]
    return new, ended


def _device_source_key(row: Any) -> tuple[Any, ...]:
    """Everything a device is built from: its program, parameters, module instances, com objects."""
    return (
        row.hardware2program_ref_id,
        getattr(row, "application_program_ref_id", None),
        tuple(_row_values(p) for p in row.parameters),
        tuple(_row_values(mi) for mi in row.module_instances),
        tuple(_row_values(co) for co in row.com_objects),
    )


def _qualified_com_object_ref(co_row: Any, app_program_id: str) -> str:
    """The app-prefixed, per-instance-qualified com-object ref that the dynamic UI emits.

    Thin adapter over :func:`xknxeditor.proj.core.identity.qualified_com_object_ref` for callers that
    hold a persisted-row object (ORM ``ComObject`` or a stored-row namespace) rather than the raw
    strings. The single source of truth for the mapping lives in the proj layer so display,
    reconciliation, undo, and application upgrades all resolve instance identity identically.
    """
    return qualified_com_object_ref(
        co_row.ref_id, getattr(co_row, "instance_ref_id", "") or "", app_program_id
    )


@dataclass(frozen=True)
class _ComObjectOverrides:
    """A device's stored com-object row: its id and per-instance overrides."""

    ref: str
    db_id: int
    text: str | None
    function_text: str | None
    description: str | None
    datapoint_type: str | None
    priority: str | None


@dataclass(frozen=True)
class _DeviceInputs:
    """What a device is evaluated from besides its application, read from the project."""

    node_id: int
    name: str
    product_name: str
    description: str
    individual_address: str
    parameter_instance_refs: list[ParameterInstanceRef]
    module_instances: list[Any]
    com_object_instance_refs: list[ComObjectInstanceRef]
    download_plugin_required: bool
    configuration_app: str | None
    com_objects: tuple[_ComObjectOverrides, ...]
    # Raw plug-in blobs attached to the device row (e.g. the Siemens N141/03 DCA
    # ``BLOB_PARA``). Carried through so :func:`_compute_device` can hand the live
    # SQLAlchemy-mapped rows to the ``Device`` without re-querying.
    binary_data: tuple[Any, ...] = ()
    # Gives the product's factory image (see Device.factory_image).
    factory_image: Callable[[], FactoryData | None] | None = None


def _compute_device(inputs: _DeviceInputs, app: Application) -> Device:
    """The device evaluated from ``app``; reads nothing from the project, so it runs on any
    thread."""
    device = Device(
        node_id=inputs.node_id,
        name=inputs.name,
        product_name=inputs.product_name,
        app=app,
        individual_address=inputs.individual_address,
        description=inputs.description,
        parameter_instance_refs=inputs.parameter_instance_refs,
        module_instances=inputs.module_instances,
        com_object_instance_refs=inputs.com_object_instance_refs,
        binary_data=list(inputs.binary_data),
    )
    device.download_plugin_required = inputs.download_plugin_required
    device.factory_image = inputs.factory_image
    device.configuration_app = inputs.configuration_app
    for row in inputs.com_objects:
        co = device.find_com_object(row.ref)
        if co is not None:
            co.db_id = row.db_id
            # Carry the stored per-instance overrides so get_visible_com_objects prefers them
            # over the app default, and reflect them on the display object right away for the
            # panels that read device.com_objects directly.
            co.text_override = row.text
            co.function_text_override = row.function_text
            co.description = row.description or ""
            co.datapoint_type_override = row.datapoint_type
            if row.text:
                co.name = row.text
            if row.function_text:
                co.function_text = row.function_text
            if row.datapoint_type:
                co.dpt = dpt_from_token(row.datapoint_type)
            if row.priority:
                co.priority = row.priority
    # Warm the lightweight views the panels read for every device, then drop the heavy
    # per-device DynamicUI evaluator (~15 MB each) — it is rebuilt lazily only when a device
    # is inspected or edited. This keeps memory bounded to the active device.
    device.get_visible_com_objects()
    device.release_dynamic_ui()
    return device


@dataclass
class _Recheck:
    """A device opened from a snapshot that other code made, to be evaluated again."""

    device: Device
    inputs: _DeviceInputs
    # What the device was built from, its application source and the snapshot's data.
    content: tuple[Any, ...]
    source: str
    data: str
    generation: int


def _co_instance_ref_from_row(
    row: Any, ref_id: str | None = None
) -> ComObjectInstanceRef | None:
    def _e(v: bool | None) -> Enable | None:
        return None if v is None else (Enable.ENABLED if v else Enable.DISABLED)

    if all(
        getattr(row, col) is None
        for col in (
            "communication_flag",
            "read_flag",
            "write_flag",
            "transmit_flag",
            "update_flag",
            "read_on_init_flag",
        )
    ):
        return None
    return ComObjectInstanceRef(
        ref_id=ref_id if ref_id is not None else row.ref_id,
        communication_flag=_e(row.communication_flag),
        read_flag=_e(row.read_flag),
        write_flag=_e(row.write_flag),
        transmit_flag=_e(row.transmit_flag),
        update_flag=_e(row.update_flag),
        read_on_init_flag=_e(row.read_on_init_flag),
    )


def _same_file(a: Path, b: Path) -> bool:
    """Whether ``a`` and ``b`` name the same file (also in another spelling or case)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a.resolve(strict=False) == b.resolve(strict=False)


def _rejection_text(exc: ValueError) -> str:
    message = str(exc)
    return S.VALIDATION_FAILED if message == VALIDATION_FAILED else message


def additional_address_error_text(exc: AdditionalAddressError) -> str:
    """The message shown for a rejected additional individual address."""
    if exc.kind == AdditionalAddressError.NOT_ENOUGH:
        return S.ADDITIONAL_ADDRESSES_NOT_ENOUGH
    if exc.kind == AdditionalAddressError.OTHER_LINE:
        return S.ADDITIONAL_ADDRESS_OTHER_LINE.format(line=exc.address)
    if exc.kind == AdditionalAddressError.IN_USE:
        return S.ADDITIONAL_ADDRESS_IN_USE.format(address=exc.address)
    return S.ADDITIONAL_ADDRESS_INVALID


def _history_device_id(data: dict[str, Any]) -> int | None:
    """The device id an undone/redone event touched — for a ``SyncDeviceComObjects`` (``device_id``)
    or a ``Composite`` (from the first sub-event that carries one), so only that device is rebuilt."""
    if "device_id" in data:
        try:
            return int(data["device_id"])
        except (TypeError, ValueError):
            return None
    for sub in data.get("events", []):
        node_id = _history_device_id(sub.get("data", {}))
        if node_id is not None:
            return node_id
    return None


# Events that, wrapped in a composite with the additional addresses they assign, still need the
# full rebuild they get on their own.
_STRUCTURAL_EVENTS = frozenset(
    ("AddDevice", "ChangeDeviceApplication", "MoveDevice", "UpdateDeviceApplication")
)


def _changes_structure(data: dict[str, Any]) -> bool:
    """Whether a composite event holds an event that changes the device structure."""
    return any(sub.get("type") in _STRUCTURAL_EVENTS for sub in data.get("events", []))


def _quoted(data: dict[str, Any], key: str) -> str:
    return repr(data.get(key, ""))


# Labels of the events without their own text; each takes the event data.
_HISTORY_LABELS: dict[str, Callable[[dict[str, Any]], str]] = {
    "AddDevice": lambda d: S.HISTORY_EV_ADD_DEVICE.format(name=_quoted(d, "name")),
    "UpsertDeviceBinaryData": lambda d: S.HISTORY_EV_STORE_PLUGIN_DATA.format(
        name=_quoted(d, "name")
    ),
    "SetParameter": lambda d: S.HISTORY_EV_SET_PARAMETER.format(
        ref=d.get("ref_id", ""), value=_quoted(d, "value")
    ),
    "CreateArea": lambda d: S.HISTORY_EV_CREATE_AREA.format(address=d.get("address")),
    "CreateLine": lambda d: S.HISTORY_EV_CREATE_LINE.format(address=d.get("address")),
    "CreateSegment": lambda d: S.HISTORY_EV_ADD_SEGMENT,
    "CreateGroupAddress": lambda d: S.HISTORY_EV_CREATE_GA.format(
        address=d.get("address")
    ),
    "ImportGroupAddresses": lambda d: str(d.get("label") or S.HISTORY_EV_IMPORT_GAS),
    "LinkComObject": lambda d: S.HISTORY_EV_LINK,
    "UnlinkComObject": lambda d: S.HISTORY_EV_UNLINK,
    "RenameArea": lambda d: S.HISTORY_EV_RENAME_AREA.format(name=_quoted(d, "name")),
    "SetProjectName": lambda d: S.HISTORY_EV_RENAME_PROJECT.format(
        name=_quoted(d, "name")
    ),
    "RenameLine": lambda d: S.HISTORY_EV_RENAME_LINE.format(name=_quoted(d, "name")),
    "SetDeviceName": lambda d: S.HISTORY_EV_RENAME_DEVICE.format(
        name=_quoted(d, "name")
    ),
    "MoveDevice": lambda d: S.HISTORY_EV_MOVE_DEVICE,
    "SetDeviceAdditionalAddresses": lambda d: str(
        d.get("label") or S.HISTORY_ADDITIONAL_ADDRESSES
    ),
    "SetComObjectFlag": lambda d: S.HISTORY_EV_SET_FLAG,
    "SetComObjectDatapointType": lambda d: S.HISTORY_EV_SET_DPT,
    "SetComObjectPriority": lambda d: S.HISTORY_EV_SET_PRIORITY,
    "SetComObjectDescription": lambda d: S.HISTORY_EV_SET_DESCRIPTION,
    "SetComObjectSending": lambda d: S.HISTORY_EV_SET_SENDING,
    "SetGroupAddressDatapointType": lambda d: S.HISTORY_EV_SET_DPT,
    "AddInstallation": lambda d: S.HISTORY_EV_ADD_INSTALLATION,
    "RemoveDevice": lambda d: S.HISTORY_EV_REMOVE_DEVICE,
    "RemoveArea": lambda d: S.HISTORY_EV_REMOVE_AREA,
    "RemoveLine": lambda d: S.HISTORY_EV_REMOVE_LINE,
    "RemoveSegment": lambda d: S.HISTORY_EV_REMOVE_SEGMENT,
    "RemoveGroupAddress": lambda d: S.HISTORY_EV_REMOVE_GA,
    "RemoveFunction": lambda d: S.HISTORY_EV_REMOVE_FUNCTION,
    "RemoveFunctionGroupAddress": lambda d: S.HISTORY_EV_REMOVE_FUNCTION_GA,
    "RemoveSpace": lambda d: S.HISTORY_EV_REMOVE_SPACE,
    "RemoveGroupRange": lambda d: S.HISTORY_EV_REMOVE_GROUP_RANGE,
    "UpdateDeviceApplication": lambda d: S.HISTORY_EV_UPDATE_APP,
    "ChangeDeviceApplication": lambda d: S.HISTORY_EV_CHANGE_APP,
    "SyncDeviceComObjects": lambda d: S.HISTORY_EV_SYNC_COM_OBJECTS,
    "SyncDeviceModuleInstances": lambda d: S.HISTORY_EV_SYNC_MODULES,
    "SetGroupAddressSecurity": lambda d: S.HISTORY_EV_SET_GA_SECURITY,
    "SetGroupRangeSecurity": lambda d: S.HISTORY_EV_SET_GROUP_SECURITY,
    "SetGroupAddressCentral": lambda d: (
        S.HISTORY_EV_SET_GA_CENTRAL
        if d.get("central")
        else S.HISTORY_EV_UNSET_GA_CENTRAL
    ),
    "SetDeviceSecureCommissioning": lambda d: S.HISTORY_EV_SET_SECURE_COMMISSIONING,
    "RenameGroupAddress": lambda d: S.HISTORY_EV_RENAME_GA.format(
        name=_quoted(d, "name")
    ),
    "SetGroupAddressAddress": lambda d: S.HISTORY_EV_SET_GA_ADDRESS,
    "SetDeviceCertificates": lambda d: S.HISTORY_EV_SET_CERTIFICATES,
    "SetDeviceDescription": lambda d: S.HISTORY_EV_SET_DEVICE_DESCRIPTION,
    "SetTopologyDescription": lambda d: (
        S.HISTORY_EV_SET_AREA_DESCRIPTION
        if d.get("kind") == "area"
        else S.HISTORY_EV_SET_LINE_DESCRIPTION
    ),
    "SetTopologyAddress": lambda d: (
        S.HISTORY_EV_SET_AREA_ADDRESS
        if d.get("kind") == "area"
        else S.HISTORY_EV_SET_LINE_ADDRESS
    ),
    "SetGroupDescription": lambda d: (
        S.HISTORY_EV_SET_GA_DESCRIPTION
        if d.get("kind") == "group_address"
        else S.HISTORY_EV_SET_GROUP_RANGE_DESCRIPTION
    ),
    "SetGroupRangeAddress": lambda d: S.HISTORY_EV_SET_GROUP_RANGE_ADDRESS,
    "SetComment": lambda d: (
        S.HISTORY_EV_SET_INSTALLATION_HINTS
        if d.get("field") == "installation_hints"
        else S.HISTORY_EV_SET_COMMENT
    ),
    "SetCompletionStatus": lambda d: S.HISTORY_EV_SET_STATUS.format(
        status=status_label(str(d.get("status", "")))
    ),
    "SetSpaceUsage": lambda d: S.HISTORY_EV_SET_SPACE_USE,
    "SetSpaceDefaultLine": lambda d: S.HISTORY_EV_SET_CURRENT_LINE,
    "SetInstallationAttribute": lambda d: S.HISTORY_EV_SET_IP_ROUTING,
    "SetSegmentBusAccess": lambda d: S.HISTORY_EV_SET_BUS_CONNECTION,
    "SetSegmentDomainAddress": lambda d: S.HISTORY_EV_SET_DOMAIN_ADDRESS,
    "RenameSegment": lambda d: S.HISTORY_EV_RENAME_SEGMENT.format(
        name=_quoted(d, "name")
    ),
    "SetSegmentMedium": lambda d: S.HISTORY_EV_SET_MEDIUM.format(
        medium=medium_name(str(d.get("medium_type", "")))
    ),
    "SetFunctionDescription": lambda d: S.HISTORY_EV_SET_FUNCTION_DESCRIPTION,
    "CreateFunction": lambda d: S.HISTORY_EV_CREATE_FUNCTION.format(
        name=_quoted(d, "name")
    ),
    "CreateFunctionWithGroupAddresses": lambda d: S.HISTORY_EV_CREATE_FUNCTION.format(
        name=_quoted(d, "name")
    ),
    "RenameFunction": lambda d: S.HISTORY_EV_RENAME_FUNCTION.format(
        name=_quoted(d, "name")
    ),
    "SetFunctionType": lambda d: S.HISTORY_EV_SET_FUNCTION_TYPE,
    "AddFunctionGroupAddress": lambda d: S.HISTORY_EV_ADD_FUNCTION_GA,
    "CreateSpace": lambda d: S.HISTORY_EV_CREATE_SPACE.format(name=_quoted(d, "name")),
    "RenameSpace": lambda d: S.HISTORY_EV_RENAME_SPACE.format(name=_quoted(d, "name")),
    "SetSpaceNumber": lambda d: S.HISTORY_EV_SET_SPACE_NUMBER,
    "SetSpaceDescription": lambda d: S.HISTORY_EV_SET_SPACE_DESCRIPTION,
    "SetSpaceType": lambda d: S.HISTORY_EV_SET_SPACE_TYPE,
    "MoveSpace": lambda d: S.HISTORY_EV_MOVE_SPACE,
    "SetDeviceSpace": lambda d: S.HISTORY_EV_SET_DEVICE_SPACE,
    "SetDeviceCommissioning": lambda d: S.HISTORY_EV_SET_COMMISSIONING,
    "CreateGroupRange": lambda d: S.HISTORY_EV_CREATE_GROUP_RANGE.format(
        name=_quoted(d, "name")
    ),
    "RenameGroupRange": lambda d: S.HISTORY_EV_RENAME_GROUP_RANGE.format(
        name=_quoted(d, "name")
    ),
}


def _history_label(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "Composite":
        if data.get("label"):
            return str(data["label"])
        # A composite is a parameter change plus the com-object re-instantiation it triggered; label
        # it by its first sub-event (the SetParameter) so the history reads naturally.
        for sub in data.get("events", []):
            if sub.get("data"):
                return _history_label(str(sub.get("type", "")), sub["data"])
        return S.HISTORY_EV_CHANGE
    label = _HISTORY_LABELS.get(event_type)
    if label is not None:
        return label(data)
    if event_type.startswith("Remove"):
        return S.HISTORY_EV_REMOVE
    return event_type


@dataclass(frozen=True)
class ElementDetails:
    """What the details of a selected area, line, segment, group range, group address or
    building function show. ``title_kind`` names it in the tab ("area", "line", "segment",
    "main_group", "middle_group", "group_range", "group_address", "function"); a line's address
    shows after its area's (``prefix`` "1."), a middle group's after its main group's ("1/").
    ``status`` is the element's "Status" for the kinds that have one, else None; a segment and a
    function have no address (``has_address``). A line and a segment have a ``medium``, an RF or
    PL one its ``domain_address`` as shown ("" for none). A group address and a main or middle
    group have their own ``security`` ("Auto", "On" or "Off"), with the group above them that
    forces one (``forced_by``); a group address is ``central`` or not."""

    kind: str
    element_id: int
    title_kind: str
    name: str
    description: str
    prefix: str
    address: str
    editable: bool = True
    status: str | None = None
    has_address: bool = True
    medium: str | None = None
    domain_address: str = ""
    security: str | None = None
    forced_by: "ForcedSecurity | None" = None
    central: bool | None = None
    # A group address's data point type ("DPST-16-1", "" for none); None for other elements.
    datapoint_type: str | None = None
    # The backbone's IP routing: network latency (ms), multicast address, its security ("Auto",
    # "On" or "Off"), and its bus connection: the ``Parameter`` of its ``<BusAccess>`` ("" for
    # "Automatic") and as shown.
    latency: int | None = None
    multicast_address: str = ""
    backbone_security: str | None = None
    bus_access: str = ""
    bus_connection: str = ""


def format_bus_access(bus_access: dict[str, str] | None) -> str:
    """A bus connection kept with a segment, as shown: its name and where it connects to
    ("IP Tunneling (192.168.16.103:3671 Tcp)"); "" for none."""
    if not bus_access:
        return ""
    fields: dict[str, str] = {}
    for part in bus_access.get("Parameter", "").split(";"):
        key, _, value = part.partition("=")
        if key.strip():
            fields[key.strip()] = value.strip().strip('"')
    name = fields.get("Name") or bus_access.get("Name", "") or fields.get("Type", "")
    host = fields.get("HostAddress", "")
    if not host:
        return name
    where = f"{host}:{fields.get('Port') or 3671}"
    protocol = fields.get("ProtocolType", "")
    return f"{name} ({where} {protocol})".replace(" )", ")")


def interface_bus_access(name: str, host: str, port: int) -> str:
    """The ``Parameter`` of the ``<BusAccess>`` of a KNXnet/IP interface tunnelled to over UDP."""
    where = host if port == 3671 else f"{host};Port={port}"
    return f'Type=IpTunneling;HostAddress={where};ProtocolType=Udp;Name="{name}"'


def line_display_name(area_number: int, line: "_Line") -> str:
    """A line's name; a backbone or main line without one is called after its medium ("IP
    backbone line", "TP main line")."""
    if line.name or line.line_number != 0:
        return line.name
    text = S.BACKBONE_LINE_NAME if area_number == 0 else S.MAIN_LINE_NAME
    return text.format(medium=medium_name(line.medium_type)).strip()


def _stored_kind(kind: str) -> str:
    """The kind the project stores an element's status, comment and description under: the
    backbone is area 0."""
    return "area" if kind == "backbone" else kind


def format_domain_address(medium: str, address: int | None) -> str:
    """A domain address as it is shown: an RF one as ``0001:23456789`` (its 48 bits in hex), a
    PL one in decimal; "" for none."""
    if address is None:
        return ""
    if medium == MEDIUM_RF:
        return f"{address >> 32:04X}:{address & 0xFFFFFFFF:08X}"
    return str(address)


def _addresses_in(node: "GroupRangeInfo") -> "list[CoreGroupAddressInfo]":
    """The group addresses of a group and of every group below it."""
    found = list(node.group_addresses)
    for child in node.children:
        found.extend(_addresses_in(child))
    return found


def _find_range(
    nodes: "list[GroupRangeInfo]", range_id: int, depth: int
) -> "tuple[GroupRangeInfo, int] | None":
    for node in nodes:
        if node.id == range_id:
            return node, depth
        found = _find_range(node.children, range_id, depth + 1)
        if found is not None:
            return found
    return None


@dataclass
class _Area:
    id: int
    area_number: int
    name: str
    description: str = ""


def _line_of(line: Any) -> "_Line":
    """A line of the topology (a ``Line`` row) with its medium and its sub-segments."""
    ordered = sorted(line.segments, key=lambda s: (s.number, s.id))
    return _Line(
        id=line.id,
        area_id=line.area_id,
        line_number=line.address,
        name=line.name,
        description=line.description,
        medium_type=ordered[0].medium_type if ordered else MEDIUM_TP,
        domain_address=ordered[0].domain_address if ordered else None,
        bus_access=ordered[0].bus_access if ordered else None,
        segments=[
            _Segment(
                id=s.id,
                line_id=line.id,
                number=s.number,
                name=s.name,
                medium_type=s.medium_type,
                description=s.description,
                domain_address=s.domain_address,
            )
            for s in ordered[1:]
        ],
        main_segment_id=ordered[0].id if ordered else None,
    )


@dataclass
class _Segment:
    id: int
    line_id: int
    number: int
    name: str
    medium_type: str
    description: str = ""
    domain_address: int | None = None


@dataclass
class _Line:
    id: int
    area_id: int
    line_number: int
    name: str
    description: str = ""
    # Its main segment's medium, domain address and bus connection, the line's.
    medium_type: str = MEDIUM_TP
    domain_address: int | None = None
    bus_access: dict[str, str] | None = None
    # Its sub-segments, in their order (the main segment is not among them).
    segments: list[_Segment] = field(default_factory=list[_Segment])
    main_segment_id: int | None = None


@dataclass
class GroupAddressInfo:
    id: int
    address: str
    name: str
    datapoint_type: str | None = None
    description: str = ""
    comment: str = ""
    data_secure: bool = False
    raw: int = 0  # raw 16-bit group-address value (style-independent; matches xknx GroupAddress.raw)
    # Its own Security mode (None = default, Auto), the group above it that forces one, and
    # whether it is marked central.
    security_mode: str | None = None
    forced_by: "ForcedSecurity | None" = None
    central: bool = False


@dataclass
class ProjectInfo:
    id: str
    name: str
    group_address_style: str
    guid: str
    created_by: str
    last_modified: str
    schema_version: str
    tool_version: str
    # The installation's own name (an ETS project can hold several named installations); captured
    # from the raw XML on import. Empty for a single, unnamed installation.
    installation_name: str = ""
    # Protection artifacts carried over from the imported .knxproj (shown as presence/size,
    # not raw dumps): the source project id the certificate is bound to, the signed master data,
    # the ".validation" file and the "<pid>.certificate".
    original_project_id: str = ""
    master_data_size: int = 0
    validation_size: int = 0
    certificate_size: int = 0
    # The project is password protected: its exports carry the project folder encrypted.
    password_protected: bool = False


@dataclass
class ProjectTrace:
    """One project-log entry. ``comment`` is verbatim from the source (it is encrypted on disk);
    ``comment_plain`` is the decrypted text when a trace key is installed, else ``None``."""

    date: str
    user_name: str
    comment: str
    comment_plain: str | None = None


@dataclass
class Assignment:
    id: int
    com_object_id: int
    group_address_id: int
    is_sending: bool


@dataclass
class DeviceConfigClipboard:
    """A copied device configuration for pasting onto another device of the same application.

    ``params`` are (ref_id, value) parameter overrides. ``links`` are
    (com_object_number, com_object_size, group_address_id, is_sending) tuples, re-attached to a
    target's com-objects matched by number (guarded by object size)."""

    app_id: str
    source_label: str
    params: list[tuple[str, str]]
    links: list[tuple[int, str, int, bool]]


@dataclass(frozen=True)
class DeviceProduct:
    """Display fields of a device's product, stored on the device like an imported one has them."""

    product_name: str = ""
    hardware_name: str = ""
    order_number: str = ""
    manufacturer_name: str = ""

    @classmethod
    def of(cls, product: "ProductSummary") -> "DeviceProduct":
        return cls(
            product_name=product.name or "",
            hardware_name=product.hardware_name or "",
            order_number=product.order_number or "",
            manufacturer_name=product.manufacturer_name or "",
        )


def _security_role_masks(device: Device) -> dict[str, int]:
    """The device application's security roles: ``SecurityRole @Id`` -> ``@Mask``."""
    if device.app is None:
        return {}
    roles = device.app.program.static.security_roles
    if roles is None:
        return {}
    return {role.id: role.mask for role in roles.security_role}


@dataclass(frozen=True)
class IpSecureInterface:
    """A bus interface of a KNX IP Secure device as the settings show it: its ``@RefId``, kind
    (``"Tunneling"``/``"Routing"``), user id, tunnelling address (``None`` for routing) and
    password."""

    ref_id: str
    access: str
    user_id: int
    address: str | None
    password: str


@dataclass(frozen=True)
class IpSecureSettings:
    """The KNX IP Secure settings of a device: its authentication code, its commissioning
    password, whether its tunnels are secured and its bus interfaces."""

    authentication_code: str
    commissioning_password: str
    secure_tunnelling: bool
    interfaces: list[IpSecureInterface]


def ip_secure_users(
    project: "IpSecrets",
    keyring: "KeyringIpSecrets | None",
    tunnelling_interfaces: dict[str, int],
    tunnelling_octets: list[int],
) -> tuple[str | None, dict[int, str], list[tuple[int, int]]]:
    """``(device authentication code, password by user id, tunnelling users)`` of an IP device,
    from the project's passwords, else the keyring's.

    The project's bus interfaces are users 2, 3, … in the device's order (user 1 is the
    management user), the order exported projects number them in: a routing bus interface with a
    password takes a user id as well, so the tunnels then start at user 3. A tunnelling one
    (``tunnelling_interfaces``: ``@RefId`` -> ``@AddressIndex``) is that user's tunnelling
    address ``AddressIndex`` (the device's additional address of that number). A keyring tunnel
    names its user and its address, found among the device's tunnelling addresses
    (``tunnelling_octets``, the last octet of each, in order). Without a management password no
    password is returned: the hashes are written as one table, which would lock the management
    access out."""
    from xknx.exceptions import CouldNotParseAddress
    from xknx.telegram import IndividualAddress

    from xknxeditor.download.ip_secure import MANAGEMENT_USER_ID

    authentication = project.device_authentication_code or (
        keyring.device_authentication_code if keyring else None
    )
    management = project.management_password or (
        keyring.management_password if keyring else None
    )
    passwords: dict[int, str] = {}
    tunnelling: list[tuple[int, int]] = []
    if not management:
        return authentication, passwords, tunnelling
    passwords[MANAGEMENT_USER_ID] = management
    for position, bus_interface in enumerate(project.bus_interfaces, start=1):
        user_id = MANAGEMENT_USER_ID + position
        if not bus_interface.password:
            continue
        passwords[user_id] = bus_interface.password
        index = tunnelling_interfaces.get(bus_interface.ref_id)
        if index is not None:
            tunnelling.append((user_id, index))
    for user_id, address, password in keyring.tunnels if keyring else []:
        if user_id in passwords:
            continue
        try:
            octet = IndividualAddress(address).raw & 0xFF
        except CouldNotParseAddress:
            continue
        if octet in tunnelling_octets:
            passwords[user_id] = password
            tunnelling.append((user_id, tunnelling_octets.index(octet) + 1))
    return authentication, passwords, tunnelling


def _secured_families(
    kinds: set[str],
    project: "IpSecrets",
    passwords: dict[int, str],
    backbone: bytes | None,
) -> dict[int, bool]:
    """Which KNXnet/IP service families a secure download makes require security: device
    management and tunnelling once their passwords are written (the tunnels as their bus
    interfaces say, ``@IsSecureEnabled``), routing with the backbone key. A family the
    application has no bus interface for is left alone, as is one without its passwords."""
    from xknxeditor.download.ip_secure import (
        SERVICE_FAMILY_DEVICE_MANAGEMENT,
        SERVICE_FAMILY_ROUTING,
        SERVICE_FAMILY_TUNNELLING,
    )

    families: dict[int, bool] = {}
    if passwords:
        families[SERVICE_FAMILY_DEVICE_MANAGEMENT] = True
        if "Tunneling" in kinds:
            families[SERVICE_FAMILY_TUNNELLING] = all(
                bus_interface.secure for bus_interface in project.bus_interfaces
            )
    if "Routing" in kinds and backbone is not None:
        families[SERVICE_FAMILY_ROUTING] = True
    return families


def _bus_interfaces(device: Device) -> list[tuple[str, str, int]]:
    """The application's bus interfaces as ``(@Id, @AccessType, @AddressIndex)``, in order."""
    return _app_bus_interfaces(device.app) if device.app is not None else []


def _app_bus_interfaces(app: Application) -> list[tuple[str, str, int]]:
    interfaces = app.program.static.bus_interfaces
    if interfaces is None:
        return []
    return [
        (
            bus_interface.id,
            str(getattr(bus_interface.access_type, "value", "")),
            bus_interface.address_index,
        )
        for bus_interface in interfaces.bus_interface
    ]


def _tunnelling_interfaces(device: Device) -> dict[str, int]:
    """The application's tunnelling bus interfaces: ``@Id`` -> ``@AddressIndex``."""
    return {
        ref: index
        for ref, access, index in _bus_interfaces(device)
        if access == "Tunneling"
    }


def _int_or(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _float_or(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


class ProjectService:
    def __init__(self, catalog: "CatalogService") -> None:
        self._catalog = catalog
        # Share the catalog's re-entrant lock: a background import holds it while writing both stores.
        self._io_lock = catalog.io_lock
        self._svc = _ProjectService()
        self._pid: str | None = None
        # Bumped on every new/open/close so a long-running bus operation can tell its originating
        # project from whatever is open when its deferred writes finally run (see persist_script_changes).
        self._generation = 0
        # ``_path`` is the user-facing "home" (may be on a network share). ``_working_path`` is the
        # local file the SQLite engine actually uses; equal to ``_path`` for a normal local project.
        # They differ only in the SMB/network fallback: the engine works on a local mirror and the
        # file is written back to ``_path`` on close/switch/exit (see _teardown_current).
        self._path: Path | None = None
        self._working_path: Path | None = None
        self._log: Logger
        self._listeners: dict[str, list[Callable[..., Any]]] = {}
        self._app_cache: dict[str, Application] = {}
        self._plugin_cache: dict[str, bool] = {}
        self._configuration_app_cache: tuple[str, list[ConfigurationApp]] | None = None
        # Signature of the non-reverted events at open — the baseline the read-only pre-flight
        # compares against, so opening a previously-edited project is not itself "modified".
        self._history_baseline: frozenset[tuple[int, str, str]] = frozenset()
        # Optional (i_done, total) callback invoked while (re)building the device views — lets the
        # GUI show a determinate "opening project" progress bar sized to the project's device count.
        self.build_progress: Callable[[int, int, str], None] | None = None
        # Optional check between devices while (re)building them: ``True`` stops the build with
        # BuildCancelled (see refresh_catalog_resolution).
        self.build_cancelled: Callable[[], bool] | None = None
        self._devices_cache: list[Device] | None = None
        self._unloaded_devices: list[UnloadedDevice] = []
        # Per device, the stored data it was last built from (see _device_source_key).
        self._build_keys: dict[int, tuple[Any, ...]] = {}
        # The stored device snapshots (device id -> key, data), read once per open, and the ones
        # made since, to be stored.
        self._snapshots: dict[int, tuple[str, str]] | None = None
        self._new_snapshots: dict[int, tuple[str, str]] = {}
        # What each application id is read from (see CatalogService.application_source).
        self._source_cache: dict[str, str | None] = {}
        # Applications loading in the background, and those that failed to load (their devices
        # are then built without a snapshot, which shows them as not loadable).
        self._loading_lock = threading.Lock()
        self._loading: set[str] = set()
        self._failed_loads: set[str] = set()
        self._handled_failures: set[str] = set()
        # Bumped whenever the project changes (_reset); a background load started for an earlier
        # project ignores its result so it cannot contaminate the current project's state.
        self._load_generation = 0
        # Devices opened from a snapshot of other code (another editor version), evaluated again in
        # the background: queued, and evaluated for the main thread to take over; the generation
        # whose devices the background thread evaluates (-1 while none).
        self._recheck_lock = threading.Lock()
        self._recheck_queue: dict[int, _Recheck] = {}
        self._rechecked: list[tuple[_Recheck, Device]] = []
        self._recheck_generation = -1
        self._areas_cache: list[_Area] | None = None
        self._lines_cache: dict[int, list[_Line]] | None = None
        self._ga_cache: list[GroupAddressInfo] | None = None
        # Per-frame tree reads for visible panels (Buildings, "Without space", GA view) are cached by
        # revision via the @revision_cached decorator, keyed by method name in this dict, so a static
        # panel does not re-query SQLite and rebuild the ORM tree every frame (that kept the app off
        # idling and churned the GC). Cleared on _reset so one project's cache is never reused for the
        # next. Adding a new per-frame read is now just the decorator, no bespoke cache fields.
        self._revision_cache: dict[str, tuple[int, object]] = {}
        # Each lazy cache tracks the project version it was built at, independently. A single shared
        # counter is wrong: after an edit only the first cache read would rebuild (and stamp the
        # shared version), leaving the others returning stale data until the next edit.
        self._devices_cache_version: int = -1
        self._topology_cache_version: int = -1
        self._ga_cache_version: int = -1
        self._version: int = 0
        # node id -> ((node id, revision), its KNX IP Secure settings); see ip_secure_settings.
        self._ip_secure_cache: dict[
            int, tuple[tuple[int, int], IpSecureSettings | None]
        ] = {}
        self._selected_node_id: int | None = None
        # A space selected in the Buildings tree: its details replace the device's until a device
        # is selected again.
        self._selected_space_id: int | None = None
        # An area or a line selected in the Devices tree, or a group range or group address in the
        # Group Addresses tree: (kind, id). Its details replace the device's until a device or a
        # space is selected.
        self._selected_element: tuple[str, int] | None = None
        # The element the Comment panel shows: (kind, id) of what was selected last in a tree; the
        # kinds are those of the project's comments (device, space, area, line, group_address, ...).
        self._comment_target: tuple[str, int] | None = None
        # (kind, id, field) -> (revision, stored comment or None without such an element).
        self._comment_cache: dict[tuple[str, int, str], tuple[int, str | None]] = {}
        # (kind, id) -> (revision, stored status or None without such an element).
        self._status_cache: dict[tuple[str, int], tuple[int, str | None]] = {}
        # Multi-selection: the full set of selected device node ids (the primary above is
        # the last-clicked one, kept for all single-device consumers). Empty = single/none.
        self._selected_node_ids: set[int] = set()
        # Recently-viewed devices whose heavy DynamicUI we keep resident (MRU first), so toggling
        # between a few devices is instant instead of re-parsing each time. Older ones are released.
        self._dynui_lru: list[int] = []
        self._dynui_keep = 3
        # Cross-panel "please select this group address" request (consumed by the GA panel).
        self._requested_ga_id: int | None = None
        # Cross-panel "please bring the editor tab to front" request (consumed by the editor panel).
        self._focus_editor_requested = False
        # Cross-panel "please bring the Group Addresses tab to front" request.
        self._focus_group_addresses_requested = False
        # Re-instantiate a device's persisted com-objects on a function/mode parameter change, scoped
        # to exactly the objects that parameter controls (its ChooseWhenNode branches). Only the
        # objects the parameter governs are added/removed; channels and globals stay as configured.
        # (An earlier blanket before/after diff of the whole unpruned set was destructive — it
        # over-activated all channels and removed real objects; the scoped diff in
        # _reconcile_com_objects avoids that.)
        self._co_reconcile_enabled = True

    def set_logger(self, log: "Logger") -> None:
        self._log = log

    def subscribe(self, event: str, handler: Callable[..., Any]) -> Callable[[], None]:
        self._listeners.setdefault(event, []).append(handler)
        return lambda: self._listeners[event].remove(handler)

    def _emit(self, event: str, *args: Any) -> None:
        for handler in list(self._listeners.get(event, [])):
            handler(*args)

    @property
    def loading(self) -> bool:
        """A background open or import holds the project; reads return placeholders meanwhile."""
        if not self._io_lock.acquire(blocking=False):
            return True
        self._io_lock.release()
        return False

    @property
    def is_open(self) -> bool:
        return self._pid is not None

    @property
    def pid(self) -> str | None:
        """The open project's id (``P-XXXX``), or ``None`` when no project is open."""
        return self._pid

    @property
    def generation(self) -> int:
        """Identity of the currently open project. Changes on every new/open/close; capture it when
        starting a deferred/background operation and pass it back so a stale write is dropped."""
        return self._generation

    @property
    def path(self) -> Path | None:
        """The user-facing project location (the "home"; may be on a network share). Use this for
        display, recents and dialogs. To READ the live SQLite file, use :attr:`working_path`."""
        return self._path

    @property
    def working_path(self) -> Path | None:
        """The local SQLite file the engine uses — equal to :attr:`path` for a local project, or a
        local mirror when the home is on a network share. Consumers that OPEN/READ the db file
        (export) must use this, not :attr:`path`."""
        return self._working_path

    @property
    def mirroring_active(self) -> bool:
        """True when the open project is a network home backed by a local working mirror."""
        return self._working_path is not None and self._working_path != self._path

    @property
    def mirror_home(self) -> Path | None:
        """The network home a mirror is written back to, or ``None`` when not mirroring."""
        return self._path if self.mirroring_active else None

    def location_needs_local_copy(self, path: Path) -> bool:
        """Whether ``path`` is a location SQLite can't run on (network share) and would be mirrored.

        The GUI calls this before open/import to ask the user for consent; the mirroring itself
        happens inside open/import."""
        return self._needs_mirror(path if path.suffix else path.with_suffix(".xknx"))

    def can_open(self, path: Path) -> bool:
        """Whether opening ``path`` can succeed — the home file exists, OR a local mirror exists for
        it (a mirrored project whose home was not yet written back, e.g. after a crash)."""
        home = path if path.suffix else path.with_suffix(".xknx")
        return home.is_file() or self._mirror_path(home).exists()

    # Filesystem types that cannot host a reliable live SQLite db (locking is unreliable → hangs or
    # corruption), even when a quick write probe happens to succeed.
    _NETWORK_FS = frozenset(
        {
            "smbfs",
            "cifs",
            "smb3",
            "nfs",
            "nfs4",
            "afpfs",
            "webdav",
            "fusefs.sshfs",
            "osxfuse",
            "macfuse",
            "ftp",
        }
    )

    def _needs_mirror(self, home: Path) -> bool:
        """True when ``home`` can't host a live SQLite db → work on a local mirror.

        Two independent signals, OR-ed: (1) the location is a network filesystem (SMB/NFS/…), which
        is unreliable for SQLite even when a write probe passes — this is the important one, because
        some SMB mounts accept the probe's create+write but then HANG on real locking; (2) the write
        probe itself fails (read-only dir, or a mount that rejects writes outright)."""
        net = self._is_network_fs(home)
        probe_fail = False
        if not net:
            try:
                ensure_sqlite_writable(home)
            except ProjectStorageError:
                probe_fail = True
        result = net or probe_fail
        log = getattr(self, "_log", None)
        if log is not None:
            log.debug(
                "mirror decision",
                home=str(home),
                network_fs=net,
                probe_failed=probe_fail,
                needs_mirror=result,
            )
        return result

    @classmethod
    def _is_network_fs(cls, home: Path) -> bool:
        """Whether ``home`` lives on a network filesystem. Best-effort per OS; on any error returns
        False (falls back to the write probe)."""
        try:
            target = home if home.exists() else home.parent
            target = target.resolve(strict=False)
        except OSError:
            return False
        system = platform.system()
        try:
            if system == "Windows":
                return cls._is_network_fs_windows(target)
            if system == "Darwin":
                return cls._is_network_fs_darwin(target)
            return cls._is_network_fs_linux(target)
        except Exception:
            return False  # detection is best-effort; the write probe is the fallback

    @staticmethod
    def _is_network_fs_windows(target: Path) -> bool:
        import ctypes

        if str(target).startswith("\\\\"):  # UNC path (\\server\share)
            return True
        drive = os.path.splitdrive(str(target))[0]
        if not drive:
            return False
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == DRIVE_REMOTE  # type: ignore[attr-defined]

    @classmethod
    def _is_network_fs_darwin(cls, target: Path) -> bool:
        # Parse `mount`: lines look like "//user@host/share on /Volumes/backup (smbfs, nodev, ...)".
        out = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=5
        ).stdout
        best_mp = ""
        best_type = ""
        s = str(target)
        for line in out.splitlines():
            if " on " not in line or "(" not in line:
                continue
            mp = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
            fstype = line.rsplit("(", 1)[1].split(",", 1)[0].strip()
            if (s == mp or s.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(
                best_mp
            ):
                best_mp, best_type = mp, fstype
        return best_type in cls._NETWORK_FS

    @classmethod
    def _is_network_fs_linux(cls, target: Path) -> bool:
        # /proc/self/mountinfo: fstype is after " - "; mountpoint is field 5 (0-based 4).
        best_mp = ""
        best_type = ""
        s = str(target)
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                fields = left.split()
                if len(fields) < 5 or not right:
                    continue
                mp = fields[4]
                fstype = right.split(maxsplit=1)[0]
                if (s == mp or s.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(
                    best_mp
                ):
                    best_mp, best_type = mp, fstype
        return best_type in cls._NETWORK_FS

    @staticmethod
    def _mirror_dir() -> Path:
        d = config_dir() / "mirrors"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _mirror_path(self, home: Path) -> Path:
        """Deterministic local mirror path for a network ``home`` (full resolved path → no collision
        between same-named files on different shares)."""
        key = hashlib.sha1(str(home.resolve(strict=False)).encode("utf-8")).hexdigest()
        return self._mirror_dir() / f"{key}.xknx"

    @staticmethod
    def _copy_atomic(src: Path, dst: Path) -> None:
        """Copy ``src`` onto ``dst`` without ever truncating ``dst`` in place: write a sibling temp
        then ``os.replace`` (atomic same-dir rename), so an interrupted copy can't leave a torn
        file. Drops a stale ``dst-journal`` so the copied db is never paired with a foreign journal.
        Used for both seeding a mirror (home→mirror) and writing back (working→home)."""
        tmp = dst.with_name(f"{dst.name}.writeback-{os.getpid()}.tmp")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        finally:
            Path(tmp).unlink(missing_ok=True)
        Path(f"{dst}-journal").unlink(missing_ok=True)

    def _teardown_current(self) -> None:
        """End the active project: close the engine, then (if mirroring) write the local working
        file back to the network home. The single place project teardown happens, so every switch
        (open/new/import) and app exit writes the mirror back. A write-back failure (share gone) is
        logged and KEEPS the local mirror — never loses data."""
        if self._pid is None:
            return
        working, home = self._working_path, self._path
        self._log.debug(
            "teardown: closing project",
            home=str(home) if home else None,
            working=str(working) if working else None,
            mirroring=working is not None and home is not None and working != home,
        )
        self._svc.close(self._pid)
        if working is not None and home is not None and working != home:
            self._log.info("writing project back to network location", home=str(home))
            try:
                self._copy_atomic(working, home)
                self._log.info("wrote project back to network location", home=str(home))
            except OSError as e:
                self._log.error(
                    "could not write the project back to its network location; your changes are "
                    "kept in the local copy",
                    home=str(home),
                    working=str(working),
                    error=f"{type(e).__name__}: {e}",
                )
        self._pid = None
        self._path = None
        self._working_path = None
        self._generation += 1
        self._reset()

    @staticmethod
    def _remove_project_file(path: Path) -> None:
        """Delete a project's SQLite file and its DELETE-mode ``-journal`` sidecar, so a fresh create
        never seeds into a leftover database (a stale sidecar over a new db also corrupts it)."""
        path.unlink(missing_ok=True)
        Path(f"{path}-journal").unlink(missing_ok=True)

    def new(
        self,
        path: Path,
        *,
        name: str = "New project",
        group_address_style: GroupAddressStyle = GroupAddressStyle.THREE_LEVEL,
        building_name: str | None = None,
    ) -> None:
        if not path.suffix:
            path = path.with_suffix(".xknx")
        with self._io_lock:
            self._teardown_current()  # close + write back any previous project first
            working = self._mirror_path(path) if self._needs_mirror(path) else path
            # New project = a fresh file. The save dialog lets the user pick an existing path to
            # overwrite; without clearing the WORKING db, create()'s seed collides with the old
            # project's rows ("UNIQUE constraint failed: installations.index").
            self._remove_project_file(working)
            self._pid = self._svc.create(
                working,
                name=name,
                group_address_style=group_address_style,
                building_name=building_name,
            )
            self._generation += 1
            self._path = path
            self._working_path = working
            self._reset()
        if self.mirroring_active:
            self._log.info(
                "network location: working on a local copy",
                home=str(path),
                working=str(working),
            )
        self._log.info("project created", path=str(path))

    def save_as(self, new_path: Path) -> Path | None:
        """Save the open project to ``new_path`` and continue there ("Save as").

        The project persists to its .xknx file continuously (event-sourced), so this snapshots the
        current file to the chosen location and re-opens it there — how an auto-named "untitled"
        project (created when the Welcome screen is closed) gets a real, user-chosen home. Returns the
        final path (``.xknx`` suffix ensured), or ``None`` when no project is open."""
        if self._pid is None or self._working_path is None:
            return None
        if not new_path.suffix:
            new_path = new_path.with_suffix(".xknx")
        password = self.project_password
        home = self._path
        if home is not None and _same_file(new_path, home):
            # Its own file: already saved, as every change is.
            return new_path
        with self._io_lock:
            # Snapshot the live (working) file BEFORE teardown; teardown closes the engine (releases
            # the file/journal) and writes the OLD home back. The live data is in the working file,
            # not necessarily the home (which is only current after a write-back).
            src = self._working_path
            self._teardown_current()
            working = (
                self._mirror_path(new_path)
                if self._needs_mirror(new_path)
                else new_path
            )
            # Replace any stale destination db/mirror so the fresh copy wins (a leftover mirror for
            # this home must not shadow the just-saved data). Copied beside it first, so a failed
            # copy leaves both files as they were.
            try:
                self._copy_atomic(src, working)
            except OSError:
                if home is not None:
                    self.open(home, password)
                raise
        self.open(new_path, password)
        self._log.info("project saved as", path=str(new_path))
        return new_path

    def open(self, path: Path, password: str | None = None) -> None:
        """Open the project at ``path``; a password-protected one needs its ``password`` (raises
        ``ProjectPasswordRequired``/``InvalidProjectPassword``, with the open project kept)."""
        # Hold the shared lock for the whole open (incl. the device-view build) so per-frame UI reads
        # on other threads bail to empty placeholders instead of racing it — lets a background open
        # run behind a progress spinner. Re-entrant, so our own nested reads still work.
        if not path.suffix:
            path = path.with_suffix(".xknx")
        self._log.debug("project open requested", path=str(path))
        with self._io_lock:
            self._log.debug("opening project", path=str(path))
            # Tear down (and write back) the previous project BEFORE opening the new one. Two
            # different files can carry the same project-id (P-XXXX); the core keys engines by id,
            # so opening-before-closing could collide and then close the just-opened project. The
            # trade-off — a corrupt target leaves nothing open — is acceptable for this fallback.
            working = self._mirror_path(path) if self._needs_mirror(path) else path
            if working.is_file():
                # Check the password before closing the current project, so a wrong one keeps it.
                check_project_password(working, password)
            self._teardown_current()
            # First open of a network home: seed the local mirror from it. If the home is missing
            # but a mirror exists (crash before write-back), reuse the mirror as-is (never lose the
            # unsynced local edits) — that is why we only seed when the mirror is absent.
            if working != path and not working.exists() and path.is_file():
                self._log.info(
                    "seeding local copy from network home",
                    home=str(path),
                    working=str(working),
                )
                self._copy_atomic(path, working)
            with gc_paused():
                new_pid = self._svc.open(working, password)
                self._pid = new_pid
                self._generation += 1
                self._path = path
                self._working_path = working
                self._reset()
                self._history_baseline = self._history_key()
                if self.mirroring_active:
                    self._log.info(
                        "network location: working on a local copy",
                        home=str(path),
                        working=str(working),
                    )
                self._log.info(
                    "project opened",
                    path=str(path),
                    devices=len(self.devices),
                    group_range_roots=len(self.get_group_range_tree()),
                )

    def import_knxproj(
        self,
        source: Path,
        dest: Path,
        *,
        password: str | None = None,
        project_password: str | None = None,
    ) -> None:
        """Import a ``.knxproj`` into a new project at ``dest`` and open it.

        A ``.knxproj`` bundles the (unencrypted) manufacturer/application data for the products it
        uses, so we also ingest it into the catalog — without it the device view cannot resolve the
        applications and would skip every imported device.

        The parse-and-write goes into a sibling temp file the live service never references, so the
        whole schema build/DDL happens off to the side; only once it is complete do we close any
        engine on ``dest``, atomically ``os.replace`` the temp into place, and open it. This is what
        keeps a re-import safe: we never rewrite the file under an open SQLite connection, and the UI
        keeps reading the previous project (stable, no schema mutation) until the swap."""
        if not dest.suffix:
            dest = dest.with_suffix(".xknx")
        self._log.debug(
            "knxproj import requested",
            source=str(source),
            dest=str(dest),
            encrypted=password is not None,
        )
        # When ``dest`` is a network share, SQLite can't run there, so import into a local mirror and
        # write it back to ``dest`` on close (see open/_teardown_current). ``ensure_sqlite_writable``
        # still guards via make_engine if even the local mirror dir were unusable (never, in
        # practice — it is config_dir()).
        working = self._mirror_path(dest) if self._needs_mirror(dest) else dest
        # Hold the shared lock for the whole import so per-frame UI reads on other threads bail to
        # empty placeholders (see editor_gui.concurrency) instead of racing these writes. This method is
        # meant to run on a worker thread; the lock is re-entrant, so our own nested reads still work.
        with self._io_lock:
            self._log.debug(
                "importing knxproj",
                source=str(source),
                dest=str(dest),
                encrypted=password is not None,
            )
            tmp = working.with_name(f"{working.name}.import-{os.getpid()}.tmp")
            try:
                # Parse + write into a sibling temp of the WORKING file (never next to a network
                # dest). On a wrong password this raises before writing, so the currently-open
                # project is left untouched.
                _import_knxproj(
                    source, tmp, password=password, project_password=project_password
                )
                self._log.debug(
                    "knxproj parsed",
                    source=str(source),
                    temp=str(tmp),
                )
                # Enrich the catalog from the embedded product data only AFTER a successful parse
                # (the password is validated above), so a wrong password / failed import never
                # mutates the catalog. Best effort: topology and group addresses load regardless, and
                # any parser bug on odd product data must not block the project import.
                try:
                    added = self._catalog.import_project_products(source)
                    self._log.info("catalog updated from knxproj", added=len(added))
                except Exception as e:
                    self._log.warning(
                        "could not populate catalog from knxproj",
                        source=str(source),
                        error=f"{type(e).__name__}: {e}",
                    )
                # If we are overwriting the working file the live service currently has open, tear it
                # down first so no engine holds its inode when we replace it (old-inode/new-file +
                # pooled-connection hazard). A different working file needs no early close — open()
                # tears down the previous project after the swap.
                if (
                    self._working_path is not None
                    and self._working_path.resolve() == working.resolve()
                ):
                    self._teardown_current()
                os.replace(tmp, working)
                self._log.debug("knxproj installed", working=str(working))
            finally:
                # Clean up the temp file if it survived a failure (a successful replace consumed it).
                Path(tmp).unlink(missing_ok=True)
            # The new project is protected with the archive's password, else the one given for it.
            self.open(dest, project_password or password)
        self._log.info("project imported", source=str(source), path=str(dest))

    @staticmethod
    def is_ga_export(path: Path) -> bool:
        """Whether ``path`` is an ETS group-address export (routed to import_ga_export)."""
        return _is_ga_export(path)

    def import_ga_export(self, source: Path, dest: Path) -> None:
        """Import a GA export (``ga-export/01`` XML) into a new project at ``dest`` and open it.

        A GA export carries only the group-address tree (no devices, topology or product
        data), so there is no catalog ingest and no password - unlike import_knxproj. The
        build goes into a sibling temp file and is atomically swapped into place, so the
        currently-open project keeps reading until the import is complete."""
        if not dest.suffix:
            dest = dest.with_suffix(".xknx")
        working = self._mirror_path(dest) if self._needs_mirror(dest) else dest
        with self._io_lock:
            self._log.debug("importing ga export", source=str(source), dest=str(dest))
            tmp = working.with_name(f"{working.name}.import-{os.getpid()}.tmp")
            try:
                # Name the project after the user-chosen dest, not the temp file we build into.
                _import_ga_export(source, tmp, project_name=dest.stem)
                if (
                    self._working_path is not None
                    and self._working_path.resolve() == working.resolve()
                ):
                    self._teardown_current()
                os.replace(tmp, working)
                # Drop a stale DELETE-mode journal left by a prior db at this path, so SQLite can't
                # replay a foreign journal into the freshly imported file on open (see _copy_atomic).
                Path(f"{working}-journal").unlink(missing_ok=True)
            finally:
                Path(tmp).unlink(missing_ok=True)
            self.open(dest)
        self._log.info("ga export imported", source=str(source), path=str(dest))

    def export_ga_export(self, dest: Path) -> int:
        """Write the open project's group-address tree to ``dest`` as a ga-export .xml.

        Reads the working copy (current on disk thanks to the per-change commits), so no
        save step is needed first. Returns the number of addresses written."""
        if not dest.suffix:
            dest = dest.with_suffix(".xml")
        source = self._working_path
        if source is None:
            raise RuntimeError("no project open")
        with self._io_lock:
            count = _write_ga_export(source, dest)
        self._log.info("ga export written", path=str(dest), addresses=count)
        return count

    def export_ga_csv(
        self, dest: Path, *, layout: str, separator: str, header: bool
    ) -> int:
        """Write the open project's group-address tree to ``dest`` as CSV (see
        :func:`write_ga_csv`). Returns the number of addresses written."""
        if not dest.suffix:
            dest = dest.with_suffix(".csv")
        source = self._working_path
        if source is None:
            raise RuntimeError("no project open")
        with self._io_lock:
            count = _write_ga_csv(
                source, dest, layout=layout, separator=separator, header=header
            )
        self._log.info("ga csv written", path=str(dest), addresses=count)
        return count

    def close(self) -> None:
        if self._pid is not None:
            with self._io_lock:
                # Teardown writes the local working file back to a network home (if mirroring).
                self._teardown_current()
                self._history_baseline = frozenset()
                self._log.info("project closed")

    def _reset(self) -> None:
        self._devices_cache = None
        self._build_keys.clear()
        self._areas_cache = None
        self._lines_cache = None
        self._ga_cache = None
        self._revision_cache.clear()
        self._devices_cache_version = -1
        self._topology_cache_version = -1
        self._ga_cache_version = -1
        self._version = 0
        self._ip_secure_cache.clear()
        self._selected_node_id = None
        self._selected_space_id = None
        self._comment_target = None
        self._selected_element = None
        self._comment_cache.clear()
        self._status_cache.clear()
        self._selected_node_ids = set()
        self._dynui_lru.clear()
        self._app_cache.clear()
        self._snapshots = None
        self._new_snapshots.clear()
        self._source_cache.clear()
        with self._loading_lock:
            self._loading.clear()
            self._failed_loads.clear()
            self._load_generation += 1
        with self._recheck_lock:
            self._recheck_queue.clear()
            self._rechecked.clear()
        self._handled_failures.clear()

    def _bump(self, *, structural: bool = True) -> None:
        """Advance the revision so views refresh. ``structural=False`` for edits that don't change
        device structure (group-address/link changes): the expensive device cache — each device
        rebuild re-parses its application's dynamic UI — is kept valid instead of being discarded,
        so linking a group address stays instant on large projects."""
        # Only a cache that is current stays current: one outdated by an earlier structural change
        # (a device added and then placed in a room) must still be rebuilt.
        current = (
            self._devices_cache is not None
            and self._devices_cache_version == self._version
        )
        self._version += 1
        if not structural and current:
            self._devices_cache_version = self._version

    def _refresh_device(self, node_id: int) -> None:
        """Rebuild a single device's cached view in place (its com-objects changed), instead of
        discarding the whole device cache. A full structural rebuild re-evaluates every device's
        DynamicUI (``_build_device`` warms it per device), which is very slow on large projects; a
        function/mode change only affects the one edited device, so refresh just that entry. Caller
        bumps the revision."""
        if self._pid is None or self._devices_cache is None:
            return
        row = next((r for r in self._svc.devices(self._pid) if r.id == node_id), None)
        if row is None:
            return
        rebuilt = self._build_device(row)
        self._store_snapshots()
        if rebuilt is None:
            return
        for i, d in enumerate(self._devices_cache):
            if d.node_id == node_id:
                self._devices_cache[i] = rebuilt
                return

    @property
    def revision(self) -> int:
        """Monotonic counter bumped on every edit/undo/redo — for cheap change detection."""
        return self._version

    def _resolve_app(
        self, program_ref: str | None, application_ref: str | None = None
    ) -> Application | None:
        if program_ref is not None:
            cached = self._app_cache.get(program_ref)
            if cached is not None:
                return cached
            # Through the hardware program itself: a program loads its application whether or not a
            # catalog item lists it.
            app_id = self._catalog.get_program_application_id(program_ref)
            if app_id is not None:
                app = self._catalog.get_application(app_id)
                if app is not None:
                    self._app_cache[program_ref] = app
                    return app
                # The program is known but its application could not be loaded (app XML missing from
                # the stored archive, or a parser failure) - see the catalog logs for the cause.
                self._log.debug(
                    "resolve app: application not loadable",
                    program=program_ref,
                    app=app_id,
                )
            else:
                self._log.debug(
                    "resolve app: hardware program not in catalog",
                    program=program_ref,
                    hint=self._catalog.program_resolution_hint(program_ref),
                )
        # Fallback: resolve by the device's exact application-program ref. ETS resolves a device via
        # Hardware2ProgramRef -> ApplicationProgramRef; when the hardware program is not in the
        # catalog (old / reissued ids), the application itself may still be present under its own id.
        if application_ref is not None:
            cached = self._app_cache.get(application_ref)
            if cached is not None:
                return cached
            app = self._catalog.get_application(application_ref)
            if app is not None:
                self._app_cache[application_ref] = app
                self._log.debug(
                    "resolve app: resolved via application ref fallback",
                    program=program_ref,
                    application=application_ref,
                )
                return app
        return None

    def _configuration_apps(self) -> list[ConfigurationApp]:
        """The device configuration apps among the open project's kept files."""
        if self._pid is None:
            return []
        if (
            self._configuration_app_cache is None
            or self._configuration_app_cache[0] != self._pid
        ):
            blob = self._svc.project(self._pid).imported_project_files
            files: dict[str, bytes] = {}
            if blob:
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    files = {
                        name: archive.read(name)
                        for name in archive.namelist()
                        if name.lower().endswith(".etsapp")
                    }
            self._configuration_app_cache = (self._pid, configuration_apps(files))
        return self._configuration_app_cache[1]

    def _requires_download_plugin(self, program_ref: str | None) -> bool:
        if program_ref is None:
            return False
        cached = self._plugin_cache.get(program_ref)
        if cached is None:
            cached = self._catalog.requires_download_plugin(program_ref)
            self._plugin_cache[program_ref] = cached
        return cached

    def _factory_image_source(
        self, program_ref: str | None
    ) -> Callable[[], FactoryData | None] | None:
        """Gives the factory image of the product ``program_ref`` loads, read when needed."""
        if not program_ref:
            return None
        catalog = self._catalog
        return lambda: catalog.factory_image(program_ref)

    def _build_device(self, row: Any) -> Device | None:
        """The device of ``row``: from its snapshot when nothing it was derived from changed, else
        evaluated from its application (and a new snapshot made)."""
        content = _device_source_key(row)
        restored = self._restore_device(row, content)
        if restored is not None:
            self._build_keys[row.id] = content
            return restored
        device = self._evaluate_device(row)
        if device is not None:
            self._build_keys[row.id] = content
        if device is not None and device.app is not None:
            source = self._application_source(device.app.id)
            if source is not None:
                self._new_snapshots[row.id] = (
                    snapshot_key(content, source),
                    encode_snapshot(
                        device.app, device.com_objects, device.get_visible_com_objects()
                    ),
                )
        return device

    def _application_source(self, application_id: str) -> str | None:
        if application_id not in self._source_cache:
            self._source_cache[application_id] = self._catalog.application_source(
                application_id
            )
        return self._source_cache[application_id]

    def _stored_snapshot(self, device_id: int) -> tuple[str, str] | None:
        if self._pid is None:
            return None
        if self._snapshots is None:
            self._snapshots = self._svc.device_snapshots(self._pid)
        return self._snapshots.get(device_id)

    def _store_snapshots(self) -> None:
        """Store the snapshots made since the last call."""
        if self._pid is None or not self._new_snapshots:
            return
        snapshots, self._new_snapshots = self._new_snapshots, {}
        try:
            self._svc.store_device_snapshots(self._pid, snapshots)
        except Exception as e:
            # Only the next open is slower without them.
            self._log.warning(
                "device snapshots not stored", error=f"{type(e).__name__}: {e}"
            )
            return
        if self._snapshots is not None:
            self._snapshots.update(snapshots)

    def _restore_device(self, row: Any, content: tuple[Any, ...]) -> Device | None:
        """The device of ``row`` from its stored snapshot, without loading its application;
        ``None`` when it has none that matches what the device is now built from."""
        stored = self._stored_snapshot(row.id)
        program_ref = row.hardware2program_ref_id
        if stored is None or program_ref is None:
            return None
        cached = self._app_cache.get(program_ref)
        cache_key = program_ref
        app_id = (
            cached.id
            if cached is not None
            else self._catalog.get_program_application_id(program_ref)
        )
        if app_id is None:
            app_id = getattr(row, "application_program_ref_id", None)
            if app_id is None:
                return None
            cache_key = app_id
            cached = self._app_cache.get(app_id)
        with self._loading_lock:
            if app_id in self._failed_loads:
                return None
        source = self._application_source(app_id)
        if source is None:
            return None
        state = snapshot_state(stored[0], content, source)
        if state is SnapshotState.OUTDATED:
            return None
        snapshot = decode_snapshot(stored[1])
        if snapshot is None:
            return None
        app = cached
        if app is None:
            app = self._catalog.unloaded_application(
                app_id,
                snapshot.application_name,
                snapshot.application_version,
                snapshot.manufacturer_id,
            )
            self._app_cache[cache_key] = app
        assert self._pid is not None
        try:
            pirs, mis, coirs = _instance_refs(row, app.id)
            device = Device(
                node_id=row.id,
                name=row.name,
                product_name=row.product_name or "",
                app=app,
                individual_address=self._svc.individual_address(self._pid, row.id)
                or "",
                description=row.description,
                com_objects=snapshot.com_objects,
                parameter_instance_refs=pirs,
                module_instances=mis,
                com_object_instance_refs=coirs,
                binary_data=list(getattr(row, "binary_data", []) or []),
                visible=snapshot.visible,
            )
        except (IndexError, ValueError) as e:
            self._log.debug("device snapshot unusable", device_id=row.id, error=str(e))
            return None
        device.download_plugin_required = self._requires_download_plugin(program_ref)
        device.factory_image = self._factory_image_source(program_ref)
        device.configuration_app = next(
            (a.name for a in self._configuration_apps() if a.applies_to(app.id)),
            None,
        )
        if state is SnapshotState.OTHER_CODE:
            with self._recheck_lock:
                self._recheck_queue[row.id] = _Recheck(
                    device=device,
                    inputs=self._device_inputs(row, app.id),
                    content=content,
                    source=source,
                    data=stored[1],
                    generation=self._load_generation,
                )
        return device

    def _recheck(self) -> None:
        """Evaluate the devices opened from a snapshot of other code again, in the background."""
        with self._recheck_lock:
            generation = self._load_generation
            if not self._recheck_queue or self._recheck_generation == generation:
                return
            self._recheck_generation = generation
        threading.Thread(
            target=self._recheck_devices,
            args=(generation,),
            name="snapshot-recheck",
            daemon=True,
        ).start()

    def _recheck_devices(self, generation: int) -> None:
        """Evaluate the queued devices of project ``generation`` one after the other, the selected
        device first; the main thread takes the results over (see take_rechecked_devices)."""
        while True:
            with self._recheck_lock:
                if generation != self._load_generation or not self._recheck_queue:
                    if self._recheck_generation == generation:
                        self._recheck_generation = -1
                    return
                selected = self._selected_node_id
                if selected is not None and selected in self._recheck_queue:
                    job = self._recheck_queue.pop(selected)
                else:
                    job = self._recheck_queue.pop(next(iter(self._recheck_queue)))
            app = job.device.app
            assert app is not None
            device: Device | None
            try:
                # As the device build: the collector would rescan the many new objects over and over.
                with gc_paused():
                    device = _compute_device(job.inputs, app)
            except Exception as e:
                # Kept as opened from its snapshot; the next open tries again.
                self._log.warning(
                    "device not evaluated again",
                    device_id=job.inputs.node_id,
                    app=app.id,
                    error=f"{type(e).__name__}: {e}",
                )
                device = None
            with self._recheck_lock:
                if generation == self._load_generation and device is not None:
                    self._rechecked.append((job, device))

    @io_guarded(lambda: None)
    def take_rechecked_devices(self) -> None:
        """Take over the devices evaluated again in the background (main thread, every frame): a
        device whose group objects came out different replaces the one opened from its snapshot,
        and every one gets a snapshot of this code."""
        if not self._rechecked:
            return
        with self._recheck_lock:
            results, self._rechecked = self._rechecked, []
        replaced = False
        for job, device in results:
            node_id = job.inputs.node_id
            cache = self._devices_cache
            if (
                job.generation != self._load_generation
                or cache is None
                or job.device.edited_in_place
                or self._build_keys.get(node_id) != job.content
            ):
                # Changed meanwhile, and evaluated again by that change.
                continue
            index = next((i for i, d in enumerate(cache) if d is job.device), None)
            if index is None:
                continue
            assert device.app is not None
            data = encode_snapshot(
                device.app, device.com_objects, device.get_visible_com_objects()
            )
            self._new_snapshots[node_id] = (snapshot_key(job.content, job.source), data)
            if data != job.data:
                self._log.info(
                    "device group objects changed by the new version",
                    device_id=node_id,
                    address=device.individual_address,
                )
                cache[index] = device
                replaced = True
        self._store_snapshots()
        if replaced:
            self._bump(structural=False)

    def _evaluate_device(self, row: Any) -> Device | None:
        try:
            app = self._resolve_app(
                row.hardware2program_ref_id,
                getattr(row, "application_program_ref_id", None),
            )
        except Exception as e:
            # A broken/unsupported .knxprod (bad application XML) must not abort loading the whole
            # project — report it in the log and skip just this device.
            self._log.error(
                "failed to load device application (broken product data?)",
                device_id=row.id,
                name=row.name,
                program=row.hardware2program_ref_id,
                error=f"{type(e).__name__}: {e}",
            )
            return None
        if app is None:
            if self._without_application(row.hardware2program_ref_id):
                return self._build_device_without_application(row)
            self._log.warning(
                "skipping device: application not found",
                device_id=row.id,
                program=row.hardware2program_ref_id,
            )
            return None
        assert self._pid is not None
        try:
            # A legacy/static application (no <Dynamic> section) resolves fine but has no editable
            # parameter UI - the device shows up, just with an empty parameter tree (issue #51).
            # Accessing app.program here also forces the lazy parse; a failure (e.g. a shared
            # application whose archive is missing) is caught below and reported as this one device
            # being unloadable, not propagated so it aborts the whole project build.
            if getattr(app.program, "dynamic", None) is None:
                self._log.debug(
                    "device application has no dynamic parameter UI (legacy/static application)",
                    device_id=row.id,
                    app=app.id,
                )
            device = _compute_device(self._device_inputs(row, app.program.id), app)
            self._build_keys[row.id] = _device_source_key(row)
            return device
        except Exception as e:
            # Building the device (evaluating its dynamic UI from the .knxprod) failed — log the
            # product/device and skip it rather than crashing the editor.
            self._log.error(
                "failed to build device from product data",
                device_id=row.id,
                name=row.name,
                program=row.hardware2program_ref_id,
                error=f"{type(e).__name__}: {e}",
            )
            return None

    def _device_inputs(self, row: Any, app_program_id: str) -> _DeviceInputs:
        """What the device of ``row`` is evaluated from besides its application."""
        assert self._pid is not None
        pirs, mis, coirs = _instance_refs(row, app_program_id)
        return _DeviceInputs(
            node_id=row.id,
            name=row.name,
            product_name=row.product_name or "",
            description=row.description,
            individual_address=self._svc.individual_address(self._pid, row.id) or "",
            parameter_instance_refs=pirs,
            module_instances=mis,
            com_object_instance_refs=coirs,
            download_plugin_required=self._requires_download_plugin(
                row.hardware2program_ref_id
            ),
            binary_data=tuple(getattr(row, "binary_data", []) or []),
            factory_image=self._factory_image_source(row.hardware2program_ref_id),
            configuration_app=next(
                (
                    a.name
                    for a in self._configuration_apps()
                    if a.applies_to(app_program_id)
                ),
                None,
            ),
            com_objects=tuple(
                _ComObjectOverrides(
                    ref=_qualified_com_object_ref(co_row, app_program_id),
                    db_id=co_row.id,
                    text=co_row.text_override,
                    function_text=co_row.function_text_override,
                    description=co_row.description_override,
                    datapoint_type=co_row.datapoint_type_override,
                    priority=co_row.priority,
                )
                for co_row in row.com_objects
            ),
        )

    def _without_application(self, program_ref: str | None) -> bool:
        """Whether the product of ``program_ref`` has no application program at all."""
        return program_ref is not None and self._catalog.is_program_without_application(
            program_ref
        )

    def _build_device_without_application(self, row: Any) -> Device:
        """A device of a product without an application program (e.g. a power supply): no
        parameters and no group objects, only its name, address and description."""
        assert self._pid is not None
        device = Device(
            node_id=row.id,
            name=row.name,
            product_name=row.product_name or "",
            app=None,
            individual_address=self._svc.individual_address(self._pid, row.id) or "",
            description=row.description,
        )
        self._build_keys[row.id] = _device_source_key(row)
        return device

    def _reuse_device(self, row: Any, previous: dict[int, Device]) -> Device | None:
        """The already built device for ``row`` if it was not changed in place and nothing it was
        built from has changed since.

        Only its name, description and individual address are refreshed then. Rebuilding every
        device after e.g. an address change would load every application again."""
        old = previous.get(row.id)
        if (
            old is None
            or old.edited_in_place
            or old.app is not self._app_cache.get(row.hardware2program_ref_id or "")
            or self._build_keys.get(row.id) != _device_source_key(row)
        ):
            return None
        assert self._pid is not None
        old.name = row.name
        old.product_name = row.product_name or ""
        old.description = row.description
        old.individual_address = self._svc.individual_address(self._pid, row.id) or ""
        return old

    @property
    @io_guarded(list)
    def devices(self) -> list[Device]:
        if self._devices_cache is None or self._devices_cache_version != self._version:
            previous = {d.node_id: d for d in self._devices_cache or []}
            devices: list[Device] = []
            unloaded: list[UnloadedDevice] = []
            if self._pid is not None:
                # Grouped by program, so the devices to evaluate need each application loaded once
                # (only the most recently used ones stay in memory).
                rows = sorted(
                    self._svc.devices(self._pid, with_contents=True),
                    key=lambda r: (r.hardware2program_ref_id or "", r.id),
                )
                total = len(rows)
                report = self.build_progress
                cancelled = self.build_cancelled
                started = time.monotonic()
                self._log.debug("building devices", total=total)
                # Building creates millions of long-lived objects; the cyclic garbage collector would
                # rescan all of them over and over, which dominates the load time.
                with gc_paused():
                    for i, row in enumerate(rows, start=1):
                        if cancelled is not None and cancelled():
                            self._log.info("device build cancelled", built=i - 1)
                            raise BuildCancelled
                        device = self._reuse_device(
                            row, previous
                        ) or self._build_device(row)
                        if device is None:
                            unloaded.append(
                                UnloadedDevice(
                                    node_id=row.id,
                                    name=row.name or "",
                                    product_name=row.product_name or "",
                                    individual_address=self._svc.individual_address(
                                        self._pid, row.id
                                    )
                                    or "",
                                    program_ref=row.hardware2program_ref_id,
                                    description=row.description or "",
                                )
                            )
                        else:
                            devices.append(device)
                            # Verbose per-device trace: the build is otherwise a silent gap
                            # between "opening project" and "project opened".
                            self._log.debug(
                                "built device",
                                index=i,
                                total=total,
                                device_id=row.id,
                                address=device.individual_address,
                                name=device.name,
                            )
                        if report is not None:
                            label = (
                                device_label(device)
                                if device is not None
                                else (row.name or "")
                            )
                            report(i, total, label)
                self._store_snapshots()
                self._recheck()
                self._log.info(
                    "devices built",
                    built=len(devices),
                    skipped=total - len(devices),
                    seconds=round(time.monotonic() - started, 2),
                )
            devices.sort(key=lambda d: (address_order(d.individual_address), d.node_id))
            unloaded.sort(
                key=lambda d: (address_order(d.individual_address), d.node_id)
            )
            self._devices_cache = devices
            self._unloaded_devices = unloaded
            self._devices_cache_version = self._version
        return self._devices_cache

    @property
    def unloaded_devices(self) -> list[UnloadedDevice]:
        """Project devices whose application could not be loaded, shown without parameters."""
        _ = self.devices
        return self._unloaded_devices

    def application_ready(self, device: Device) -> bool:
        """Whether ``device``'s application is loaded. If not, it is loaded in the background and
        this answers ``False`` until it is; a device restored from its snapshot loads it only
        when it is used."""
        app = device.app
        if app is None or app.loaded or device.evaluated:
            return True
        with self._loading_lock:
            failed = app.id in self._failed_loads
            if not failed and app.id not in self._loading:
                self._loading.add(app.id)
                generation = self._load_generation
                threading.Thread(
                    target=self._load_application,
                    args=(app, generation),
                    name="application-load",
                    daemon=True,
                ).start()
        if failed and app.id not in self._handled_failures:
            # Build its devices again from their application, which reports them as not
            # loadable, as a project open without snapshots does. Drop every cached wrapper for
            # this application id, not just this instance: two hardware programs can resolve to the
            # same application through separate wrappers, and the sibling would otherwise stay
            # cached and never rebuild (stuck on "Loading application program…").
            self._handled_failures.add(app.id)
            for key, cached in list(self._app_cache.items()):
                if cached.id == app.id:
                    del self._app_cache[key]
            self._bump()
        return False

    def _load_application(self, app: Application, generation: int) -> None:
        try:
            app.resolved()
        except Exception as e:
            self._log.error(
                "failed to load device application (broken product data?)",
                app=app.id,
                error=f"{type(e).__name__}: {e}",
            )
            with self._loading_lock:
                # A load started for an earlier project must not write into the current one.
                if generation == self._load_generation:
                    self._failed_loads.add(app.id)
        finally:
            with self._loading_lock:
                if generation == self._load_generation:
                    self._loading.discard(app.id)

    def find_device_by_node_id(self, node_id: int) -> Device | None:
        return next((d for d in self.devices if d.node_id == node_id), None)

    def find_device_by_address(self, address: str) -> Device | None:
        return next((d for d in self.devices if d.individual_address == address), None)

    @property
    def selected_device(self) -> Device | None:
        if self._selected_node_id is None:
            return None
        return self.find_device_by_node_id(self._selected_node_id)

    @selected_device.setter
    def selected_device(self, device: Device | None) -> None:
        new_id = device.node_id if device is not None else None
        # Selecting a device (also the one already selected) shows its details again.
        self._selected_space_id = None
        self._comment_target = ("device", new_id) if new_id is not None else None
        self._selected_element = None
        if new_id != self._selected_node_id:
            self._selected_node_id = new_id
            # Any single-device select (command palette, Health/Cockpit navigate) collapses the
            # multi-selection to just this device; set_multi_selection re-widens it afterwards.
            self._selected_node_ids = {new_id} if new_id is not None else set[int]()
            # Keep the few most-recently-viewed devices' DynamicUI resident (instant toggling);
            # release those that fall out of the small LRU (no-op if they have unsaved edits).
            if new_id is not None:
                if new_id in self._dynui_lru:
                    self._dynui_lru.remove(new_id)
                self._dynui_lru.insert(0, new_id)
                for evicted in self._dynui_lru[self._dynui_keep :]:
                    dev = self.find_device_by_node_id(evicted)
                    if dev is not None:
                        dev.release_dynamic_ui()
                del self._dynui_lru[self._dynui_keep :]
            self._emit("device_selected", device)

    @property
    def selected_node_ids(self) -> set[int]:
        """The full multi-selection (device node ids); empty when a single/no device is selected."""
        return set(self._selected_node_ids)

    def set_multi_selection(self, primary_id: int | None, node_ids: list[int]) -> None:
        """Set the selection set and its primary (the device the single-device panels track). The
        primary is forced into the set; an empty selection clears everything."""
        ids = set(node_ids)
        if primary_id is not None and primary_id not in ids:
            primary_id = None
        if primary_id is None and ids:
            primary_id = min(ids)
        # Assign the primary first (the setter collapses _selected_node_ids to {primary}), then
        # widen to the full multi-selection set.
        self.selected_device = (
            self.find_device_by_node_id(primary_id) if primary_id is not None else None
        )
        self._selected_node_ids = ids

    def request_group_address(self, ga_id: int) -> None:
        """Ask the Group Addresses panel to select ``ga_id`` (cross-panel navigation, e.g. Health)."""
        self._requested_ga_id = ga_id

    def take_requested_group_address(self) -> int | None:
        """One-shot: the pending externally-requested group-address selection, then cleared."""
        ga_id = self._requested_ga_id
        self._requested_ga_id = None
        return ga_id

    def focus_editor(self) -> None:
        """Ask the editor tab to come to the front (after selecting a device from another view)."""
        self._focus_editor_requested = True

    def take_focus_editor(self) -> bool:
        """One-shot: whether the editor tab was asked to focus, then cleared."""
        requested = self._focus_editor_requested
        self._focus_editor_requested = False
        return requested

    def focus_group_addresses(self) -> None:
        """Ask the Group Addresses tab to come to the front (e.g. clicking a linked GA)."""
        self._focus_group_addresses_requested = True

    def take_focus_group_addresses(self) -> bool:
        """One-shot: whether the Group Addresses tab was asked to focus, then cleared."""
        requested = self._focus_group_addresses_requested
        self._focus_group_addresses_requested = False
        return requested

    def _installation(self) -> Any:
        assert self._pid is not None
        return self._svc.topology(self._pid, _INSTALLATION)

    def _default_device_segment_id(self) -> int:
        """Segment a new device lands on when no line is chosen: the first TP line (end devices
        belong on a TP line, not the IP backbone/main line). Falls back to the very first segment
        if the project has no TP line (e.g. an IP-only setup)."""
        installation = self._installation()
        for area in installation.areas:
            for line in area.lines:
                for segment in line.segments:
                    if segment.medium_type == MEDIUM_TP:
                        return segment.id
        return installation.areas[0].lines[0].segments[0].id

    def _ensure_topology_cache(self) -> None:
        if (
            self._areas_cache is not None
            and self._topology_cache_version == self._version
        ):
            return
        if self._pid is None:
            self._areas_cache = []
            self._lines_cache = {}
            return
        installation = self._svc.topology(self._pid, _INSTALLATION)
        self._areas_cache = [
            _Area(
                id=a.id, area_number=a.address, name=a.name, description=a.description
            )
            for a in installation.areas
        ]
        self._lines_cache = {
            a.id: [_line_of(ln) for ln in a.lines] for a in installation.areas
        }
        self._topology_cache_version = self._version

    @io_guarded(list)
    def get_areas(self) -> list[_Area]:
        if self._pid is None:
            return []
        self._ensure_topology_cache()
        return self._areas_cache or []

    @io_guarded(list)
    def get_lines(self, area_id: int) -> list[_Line]:
        if self._pid is None:
            return []
        self._ensure_topology_cache()
        return (self._lines_cache or {}).get(area_id, [])

    @property
    @io_guarded(list)
    def group_addresses(self) -> list[GroupAddressInfo]:
        if self._pid is None:
            return []
        if self._ga_cache is not None and self._ga_cache_version == self._version:
            return self._ga_cache
        self._ga_cache = [
            GroupAddressInfo(
                id=g.id,
                address=g.text,
                name=g.name,
                datapoint_type=g.datapoint_type,
                description=g.description,
                comment=plain_text(parse_rich_text(g.comment)),
                data_secure=g.data_secure,
                raw=g.address,
                security_mode=g.security_mode,
                forced_by=g.forced_by,
                central=g.central,
            )
            for g in self._svc.group_addresses(self._pid)
        ]
        self._ga_cache_version = self._version
        return self._ga_cache

    @io_guarded(lambda: None)
    @revision_cached_by_args
    def get_group_address(self, ga_id: int) -> GroupAddressInfo | None:
        if self._pid is None:
            return None
        try:
            g = self._svc.group_address(self._pid, ga_id)
        except KeyError:
            return None
        return GroupAddressInfo(
            id=g.id,
            address=g.text,
            name=g.name,
            datapoint_type=g.datapoint_type,
            description=g.description,
            comment=plain_text(parse_rich_text(g.comment)),
            data_secure=g.data_secure,
            raw=g.address,
            security_mode=g.security_mode,
            forced_by=g.forced_by,
            central=g.central,
        )

    @io_guarded(list)
    @revision_cached_by_args
    def get_assignments_for_ga(self, ga_id: int) -> list[Assignment]:
        if self._pid is None:
            return []
        return [
            Assignment(
                id=link.id,
                com_object_id=link.com_object_id,
                group_address_id=link.group_address_id,
                is_sending=link.is_sending,
            )
            for link in self._svc.group_address_links(self._pid, ga_id)
        ]

    @io_guarded(list)
    @revision_cached
    def device_links(self) -> list["DeviceLinkInfo"]:
        """Every group-address link with its device (``node_id``) and its com-object's flags, read in
        one query: for views over the whole project, where a read per com-object is far too slow."""
        if self._pid is None:
            return []
        return self._svc.device_links(self._pid)

    @io_guarded(list)
    @revision_cached_by_args
    def get_links_for_com_object(self, com_object_db_id: int) -> list[Assignment]:
        """A device com-object's group-address links (the per-com-object direction, for the editor's
        Group Objects view). ``com_object_db_id`` is ``ComObject.db_id``."""
        if self._pid is None:
            return []
        return [
            Assignment(
                id=link.id,
                com_object_id=link.com_object_id,
                group_address_id=link.group_address_id,
                is_sending=link.is_sending,
            )
            for link in self._svc.com_object_links(self._pid, com_object_db_id)
        ]

    def group_address_names(self) -> dict[int, str]:
        """Map of group-address value (16-bit int) -> name, for labelling read-out addresses."""
        out: dict[int, str] = {}
        for ga in self.group_addresses:
            address = _parse_group_address(ga.address)
            if address is not None and ga.name:
                out[address] = ga.name
        return out

    def expected_group_addresses(self, device: Device) -> dict[int, set[int]]:
        """The project's planned group-address values (16-bit ints) per group-object number, for the
        read-out comparison in the device-info view. Mirrors the links used for programming."""
        out: dict[int, set[int]] = {}
        for com_object in device.com_objects:
            if com_object.db_id is None:
                continue
            for assignment in self.get_links_for_com_object(com_object.db_id):
                ga = self.get_group_address(assignment.group_address_id)
                if ga is None:
                    continue
                address = _parse_group_address(ga.address)
                if address is not None:
                    out.setdefault(com_object.number, set()).add(address)
        return out

    def group_communication_for(self, device: Device) -> "GroupCommunication | None":
        """Collect a device's group-address links into a :class:`GroupCommunication` for programming.

        Returns ``None`` when the device has no (parseable) individual address. Used by both the GUI
        download flow and the embedded MCP server so both program identical address/association data.
        """
        from xknxeditor.download import GroupCommunication
        from xknxeditor.download.project_data import GroupObjectLink

        device_address = _parse_individual_address(device.individual_address)
        if device_address is None:
            return None
        links: list[GroupObjectLink] = []
        for com_object in device.com_objects:
            if com_object.db_id is None:
                continue
            for assignment in self.get_links_for_com_object(com_object.db_id):
                ga = self.get_group_address(assignment.group_address_id)
                if ga is None:
                    continue
                address = _parse_group_address(ga.address)
                if address is None:
                    continue
                links.append(
                    GroupObjectLink(
                        com_object_ref_id=com_object.id,
                        group_address=address,
                        sending=assignment.is_sending,
                    )
                )
        # A line/backbone coupler (address x.y.0) also carries a group-address filter table computed
        # from the whole topology (which group addresses cross it). A non-coupler at .0 gets one too,
        # but its load procedure has no Router-object write, so the extra image segment is unused.
        from xknxeditor.download.filter_table import (
            compute_coupler_filter_table,
            is_coupler_address,
        )

        filter_table = None
        if is_coupler_address(device_address):
            # The project's pass-through addresses have to be included, or the computed table
            # blocks addresses it was explicitly configured to always route: group addresses (or
            # whole ranges) flagged Unfiltered, and the line's AdditionalGroupAddresses.
            unfiltered, additional = self._coupler_pass_through(device_address)
            filter_table = compute_coupler_filter_table(
                device_address,
                self._all_device_group_addresses(),
                unfiltered=unfiltered,
                additional=additional,
            )
        return GroupCommunication(
            device_address=device_address,
            links=links,
            filter_table=filter_table,
            security=self._group_security(device),
        )

    def _group_security(self, device: Device) -> "GroupSecurity | None":
        """The keys of the device's secured group addresses and their senders, for a device whose
        application supports KNX Data Secure; ``None`` when none of its group addresses is secured."""
        from xknxeditor.download.secure_config import GroupSecurity

        if self._pid is None or device.app is None:
            return None
        if not device.app.program.is_secure_enabled:
            return None
        keys, senders = self._svc.group_security(
            self._pid,
            device.node_id,
            self._com_object_can_send,
            self._tunnel_senders(),
        )
        p2p = self._svc.p2p_security(
            self._pid, device.node_id, _security_role_masks(device)
        )
        if not keys and not p2p:
            return None
        return GroupSecurity(keys=keys, senders=senders, p2p=p2p)

    def com_object_requires_security(self, com_object_id: int) -> bool:
        """Whether the application requires KNX Data Secure for a group object (its
        ``SecurityRequired``): it may then only be linked to secured group addresses."""
        from xknxeditor.prod.parser_v2.application_indexer import (
            com_object_security_required,
        )

        for device in self.devices:
            for co in device.com_objects:
                if co.db_id == com_object_id:
                    if device.app is None:
                        return False
                    indexer = device.app.tree_builder().idx
                    return com_object_security_required(indexer, co.id) != "None"
        return False

    def _security_tables_overflow(
        self, com_object_id: int, group_address_id: int
    ) -> str | None:
        """The device (``address name``) whose security tables the link would make larger than
        its application allows (``MaxSecurityGroupKeyTableEntries``,
        ``MaxSecurityIndividualAddressEntries``), or ``None`` when they fit."""
        if self._pid is None:
            return None
        owner = {
            co.db_id: device
            for device in self.devices
            for co in device.com_objects
            if co.db_id is not None
        }
        try:
            links = self._svc.group_address(self._pid, group_address_id).links
        except KeyError:
            return None
        devices = {
            d.node_id: d
            for co_id in [*links, com_object_id]
            if (d := owner.get(co_id)) is not None
            and d.app is not None
            and d.app.program.is_secure_enabled
            and self._svc.secure_commissioning(self._pid, d.node_id)
        }
        if not devices:
            return None
        sizes = self._svc.security_table_sizes(
            self._pid,
            list(devices),
            self._com_object_can_send,
            with_link=(com_object_id, group_address_id),
        )
        for node_id, (keys, senders) in sizes.items():
            device = devices[node_id]
            assert device.app is not None
            program = device.app.program
            key_limit = program.max_security_group_key_table_entries or 0
            sender_limit = program.max_security_individual_address_entries or 0
            if (key_limit and keys > key_limit) or (
                sender_limit and senders > sender_limit
            ):
                return (
                    f"{device.individual_address or '-'} {device.display_name}".strip()
                )
        return None

    def _link_would_unsecure(self, com_object_id: int, group_address_id: int) -> bool:
        """Whether linking the group object would put a group object that requires security on a
        group address in the clear: the object requires it and the group address would not be
        secured, or the object's device is commissioned in the clear and a group object on the
        group address requires it (3/5/3 1.5.3)."""
        if self._pid is None:
            return False
        try:
            ga = self._svc.group_address(self._pid, group_address_id)
        except KeyError:
            return False
        owner = {
            co.db_id: device
            for device in self.devices
            for co in device.com_objects
            if co.db_id is not None
        }
        device = owner.get(com_object_id)
        if device is None:
            return False

        def secure(other: Device) -> bool:
            return bool(self._svc.secure_commissioning(self._pid or "", other.node_id))

        # The mode that counts: a group above the address may force it.
        mode = ga.effective_security_mode
        if self.com_object_requires_security(com_object_id):
            if mode == "Off" or not secure(device):
                return True
            if mode != "On" and not all(
                secure(other)
                for link in ga.links
                if (other := owner.get(link)) is not None
            ):
                return True
        if mode not in ("On", "Off") and not secure(device):
            return any(self.com_object_requires_security(link) for link in ga.links)
        return False

    def _tunnel_senders(self) -> dict[int, list[int]]:
        """The tunnels of the project's KNX IP Secure interfaces by the group addresses assigned
        to them (raw group address -> raw tunnelling addresses): a tool sends on those secured
        group addresses through that tunnel, so the devices on them accept it as a sender."""
        from xknx.telegram import IndividualAddress

        if self._pid is None:
            return {}
        result: dict[int, list[int]] = {}
        for device in self.devices:
            settings = self.ip_secure_settings(device)
            if settings is None:
                continue
            assigned = self._svc.bus_interface_group_addresses(
                self._pid, device.node_id
            )
            for interface in settings.interfaces:
                if interface.address is None:
                    continue
                tunnel = IndividualAddress(interface.address).raw
                for group_address in assigned.get(interface.ref_id, []):
                    result.setdefault(group_address, []).append(tunnel)
        return result

    def _com_object_can_send(self, com_object_id: int) -> bool:
        """Whether a group object can send on its group addresses: it communicates and transmits,
        answers read requests or requests a read on init. An object not in the cached view-models
        counts as one that can."""
        for device in self.devices:
            for co in device.com_objects:
                if co.db_id == com_object_id:
                    flags = co.flags
                    return flags.communication and (
                        flags.transmit or flags.read or flags.read_on_init
                    )
        return True

    def download_tool_key(self, device: Device) -> bytes | None:
        """The Tool Key a download gives the device: its own when its secure commissioning is
        activated, else ``None`` (a download in the clear, or with a keyring's key)."""
        if self._pid is None or device.app is None:
            return None
        if not device.app.program.is_secure_enabled:
            return None
        keys = self.device_keys(device)
        return keys.tool_key if keys is not None else None

    def download_disables_security(self, device: Device) -> bool:
        """Whether a download switches the device to plain communication: its secure
        commissioning is deactivated, but it still holds a Tool Key from an earlier secure
        download, so it is reached with that key once more."""
        if self._pid is None or device.app is None:
            return False
        if not device.app.program.is_secure_enabled:
            return False
        keys = self.device_keys(device)
        return (
            keys is not None
            and keys.tool_key is None
            and keys.loaded_tool_key is not None
        )

    def secure_commissioning(self, device: Device) -> bool | None:
        """Whether the device is commissioned securely; ``None`` when its application does not
        support KNX Data Secure."""
        if self._pid is None or device.app is None:
            return None
        if not device.app.program.is_secure_enabled:
            return None
        return self._svc.secure_commissioning(self._pid, device.node_id)

    @io_guarded(frozenset)
    @revision_cached
    def secure_device_ids(self) -> frozenset[int]:
        """The devices commissioned securely (KNX Data Secure), by node id; read once per change
        for the rows that mark them."""
        if self._pid is None:
            return frozenset()
        return frozenset(self._svc.secure_commissioned_devices(self._pid))

    def secure_commissioning_of(self, node_id: int) -> bool:
        """Whether the device with ``node_id`` is commissioned securely."""
        return self._pid is not None and self._svc.secure_commissioning(
            self._pid, node_id
        )

    def secure_commissioning_impact(
        self, device: Device
    ) -> "SecureCommissioningImpact | None":
        """What deactivating the device's secure commissioning would change."""
        if self._pid is None:
            return None
        return self._svc.secure_commissioning_impact(
            self._pid, device.node_id, self.com_object_requires_security
        )

    def set_secure_commissioning(self, device: Device, activated: bool) -> None:
        """Activate or deactivate the device's secure commissioning (one undo step); activating
        needs a project password (``ProjectPasswordRequired``)."""
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.set_secure_commissioning(
                self._pid,
                device.node_id,
                activated,
                self.com_object_requires_security,
            )
            if activated:
                self._ensure_ip_secrets(
                    device.node_id,
                    device.app,
                    self._svc.device(self._pid, device.node_id).hardware2program_ref_id,
                )
        self._bump(structural=True)

    def ip_secure_device(self, device: Device) -> bool:
        """Whether the device is a KNX IP Secure device: an IP device whose application supports
        KNX Data Secure and has tunnelling or routing bus interfaces."""
        if self._pid is None or device.app is None:
            return False
        try:
            ref = self._svc.device(self._pid, device.node_id).hardware2program_ref_id
        except KeyError:
            return False
        return self._ip_secure_application(device.app, ref)

    def secure_tunnels(self, host_address: str) -> "list[SecureTunnel]":
        """The KNX IP Secure tunnels of the project's interface at ``host_address`` (secure
        commissioning activated), with their passwords."""
        from editor_gui.plugins.connection.service import SecureTunnel

        device = next(
            (d for d in self.devices if d.individual_address == host_address), None
        )
        settings = self.ip_secure_settings(device) if device is not None else None
        if settings is None:
            return []
        return [
            SecureTunnel(
                interface.user_id,
                interface.address,
                interface.password,
                settings.authentication_code or None,
            )
            for interface in settings.interfaces
            if interface.access == "Tunneling"
            and interface.address
            and interface.password
        ]

    def secure_backbone(self) -> tuple[bytes, int] | None:
        """The project's KNX IP Secure routing backbone ``(key, latency ms)``, unless its backbone
        security is switched off."""
        if self._pid is None:
            return None
        installation = self._svc.installation_attributes(self._pid)
        if installation.get("IPRoutingBackboneSecurity") == "Off":
            return None
        key = self._svc.ip_backbone_key(self._pid)
        if key is None:
            return None
        return key, _int_or(installation.get("IPRoutingLatencyTolerance"), 2000)

    def data_secure_tables(self) -> tuple[dict[int, bytes], dict[int, int]] | None:
        """The project's Data Secure tables for a connection: the keys of the secured group
        addresses and every device that can send on them with its last valid Sequence Number."""
        from xknxeditor.proj import ProjectPasswordRequired

        if self._pid is None:
            return None
        with self._io_lock:
            try:
                keys = self._svc.secured_group_keys(self._pid)
            except ProjectPasswordRequired:
                return None
            if not keys:
                return None
            return keys, self._svc.secure_senders(self._pid, self._com_object_can_send)

    def export_keyring(self, path: Path, password: str) -> int:
        """Write the project's key material as a keyring (``.knxkeys``) to ``path``, encrypted and
        signed with ``password``: the backbone key, every device commissioned securely (with the
        Tool Key it holds, else the one it gets), the tunnels of the KNX IP Secure devices (with
        the secured group addresses assigned to them and their senders) and the keys of the
        secured group addresses. Returns how many devices it holds."""
        from xknx.telegram import IndividualAddress

        from xknxeditor.datasecure import (
            KeyringDevice,
            KeyringTunnel,
            create_keyring,
            serialize_keyring,
        )

        if self._pid is None:
            raise ValueError("No project is open.")
        installation = self._svc.installation_attributes(self._pid)
        with self._io_lock:
            group_keys = self._svc.secured_group_keys(self._pid)
        devices: list[KeyringDevice] = []
        tunnels: list[KeyringTunnel] = []
        for device in self.devices:
            address = device.individual_address
            keys = self.device_keys(device)
            tool_key = (keys.loaded_tool_key or keys.tool_key) if keys else None
            if not address or tool_key is None:
                continue
            settings = self.ip_secure_settings(device)
            devices.append(
                KeyringDevice(
                    address,
                    tool_key=tool_key,
                    sequence_number=self._svc.device_sequence_number(
                        self._pid, device.node_id
                    ),
                    management_password=settings.commissioning_password or None
                    if settings
                    else None,
                    authentication=settings.authentication_code or None
                    if settings
                    else None,
                )
            )
            if settings is None:
                continue
            assigned = self._svc.bus_interface_group_addresses(
                self._pid, device.node_id
            )
            for interface in settings.interfaces:
                if interface.access != "Tunneling" or not interface.address:
                    continue
                if not interface.password:
                    continue
                tunnels.append(
                    KeyringTunnel(
                        host=address,
                        individual_address=interface.address,
                        user_id=interface.user_id,
                        password=interface.password,
                        authentication=settings.authentication_code or None,
                        group_addresses={
                            ga: [
                                str(IndividualAddress(raw))
                                for raw in self._svc.group_address_senders(
                                    self._pid, ga, self._com_object_can_send
                                )
                            ]
                            for ga in assigned.get(interface.ref_id, [])
                            if ga in group_keys
                        },
                    )
                )
        created = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S")
        keyring = create_keyring(
            self._project_name_for_keyring(),
            password,
            created,
            backbone_key=self._svc.ip_backbone_key(self._pid),
            multicast_address=installation.get("IPRoutingMulticastAddress")
            or "224.0.23.12",
            latency=_int_or(installation.get("IPRoutingLatencyTolerance"), 2000),
            tunnels=tunnels,
            devices=devices,
            group_keys=group_keys,
        )
        path.write_bytes(serialize_keyring(keyring))
        self._log.info("keyring exported", devices=len(devices), tunnels=len(tunnels))
        return len(devices)

    def _project_name_for_keyring(self) -> str:
        info = self.get_project_metadata()
        return (info.name if info is not None else "") or "Project"

    def ip_secure_settings(self, device: Device) -> IpSecureSettings | None:
        """The device's KNX IP Secure settings, for a KNX IP Secure device commissioned securely;
        ``None`` otherwise. Cached per project revision (the settings panel asks every frame)."""
        key = (device.node_id, self._version)
        cached = self._ip_secure_cache.get(device.node_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        settings = self._read_ip_secure_settings(device)
        self._ip_secure_cache[device.node_id] = (key, settings)
        return settings

    def _read_ip_secure_settings(self, device: Device) -> IpSecureSettings | None:
        # The project's secure commissioning first: it rules out nearly every device without
        # loading its application program.
        if self._pid is None or not self._svc.secure_commissioning(
            self._pid, device.node_id
        ):
            return None
        if not self.ip_secure_device(device):
            return None
        secrets = self._svc.ip_secrets(self._pid, device.node_id)
        kinds = {ref: (access, index) for ref, access, index in _bus_interfaces(device)}
        octets = self._svc.device_additional_addresses(self._pid, device.node_id)
        prefix = device.individual_address.rsplit(".", 1)[0]
        interfaces: list[IpSecureInterface] = []
        for position, bus_interface in enumerate(secrets.bus_interfaces, start=1):
            access, index = kinds.get(bus_interface.ref_id, ("", 0))
            if access not in PASSWORD_ACCESS_TYPES:
                continue
            address = None
            if access == "Tunneling" and 0 < index <= len(octets) and prefix:
                address = f"{prefix}.{octets[index - 1]}"
            interfaces.append(
                IpSecureInterface(
                    ref_id=bus_interface.ref_id,
                    access=access,
                    user_id=1 + position,
                    address=address,
                    password=bus_interface.password or "",
                )
            )
        return IpSecureSettings(
            authentication_code=secrets.device_authentication_code or "",
            commissioning_password=secrets.management_password or "",
            secure_tunnelling=all(b.secure for b in secrets.bus_interfaces),
            interfaces=interfaces,
        )

    def set_ip_secure_settings(
        self,
        device: Device,
        *,
        authentication_code: str | None = None,
        commissioning_password: str | None = None,
        interface_password: tuple[str, str] | None = None,
        secure_tunnelling: bool | None = None,
    ) -> None:
        """Change one of the device's KNX IP Secure settings (see the core ``set_ip_secrets``);
        ``ValueError`` for a password the device cannot take."""
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.set_ip_secrets(
                self._pid,
                device.node_id,
                authentication_code=authentication_code,
                management_password=commissioning_password,
                bus_interface_passwords=dict([interface_password])
                if interface_password
                else None,
                secure_tunnelling=secure_tunnelling,
            )
        self._bump(structural=False)

    def _ip_secure_application(
        self, app: Application, hardware_ref: str | None
    ) -> bool:
        # The hardware first: the catalog tells it without loading the application program.
        if not hardware_ref or not self._catalog.hardware_is_ip_enabled(hardware_ref):
            return False
        return app.program.is_secure_enabled and any(
            access in PASSWORD_ACCESS_TYPES
            for _ref, access, _index in _app_bus_interfaces(app)
        )

    def _ensure_ip_secrets(
        self, node_id: int, app: Application | None, hardware_ref: str | None
    ) -> None:
        """Give a KNX IP Secure device its passwords when it has none yet (the caller holds the
        IO lock)."""
        if self._pid is None or app is None:
            return
        if not self._ip_secure_application(app, hardware_ref):
            return
        kinds = _app_bus_interfaces(app)
        refs = [ref for ref, access, _index in kinds if access in PASSWORD_ACCESS_TYPES]
        self._svc.ensure_ip_secrets(self._pid, node_id, refs)
        # A secure IP router routes with the installation's backbone key.
        if any(access == "Routing" for _ref, access, _index in kinds):
            self._svc.ensure_backbone_key(self._pid)

    def backbone_security(self) -> str | None:
        """The installation's KNX IP Secure routing setting (``"On"``/``"Off"``, ``None`` =
        Auto); see the core ``backbone_security``."""
        return self._svc.backbone_security(self._pid) if self._pid is not None else None

    def set_backbone_security(self, mode: str | None) -> None:
        """Set the installation's KNX IP Secure routing; On needs a project password
        (``ProjectPasswordRequired``) for its key."""
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.set_backbone_security(self._pid, mode)
        self._bump(structural=False)

    def device_certificate_list(self) -> list[tuple[bytes, str]]:
        """The project's device certificates as ``(serial number, the device with it)``, the
        device as ``address name`` or empty when no device has its serial number."""
        if self._pid is None:
            return []
        owners = {
            serial: f"{d.individual_address or '-'} {d.display_name}".strip()
            for d in self.devices
            if (serial := self._device_serial(d.node_id)) is not None
        }
        return [
            (fdsk.serial_number, owners.get(fdsk.serial_number, ""))
            for fdsk in self._svc.device_certificates(self._pid).values()
        ]

    def _device_serial(self, node_id: int) -> bytes | None:
        from editor_gui.device_info import serial_from_project

        info = self.get_device_info(node_id)
        return serial_from_project(info.serial_number) if info is not None else None

    def remove_device_certificate(self, serial_number: bytes) -> None:
        """Remove a device certificate from the project (one undo step)."""
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.remove_device_certificate(self._pid, serial_number)
        self._bump(structural=False)

    def security_proxy_coupler(self, device: Device) -> bool:
        """Whether the device is a coupler with the security extensions (its hardware program's
        ``CouplerCapabilities`` has ``SecurityProxy``) commissioned securely."""
        if self._pid is None or device.app is None:
            return False
        try:
            ref = self._svc.device(self._pid, device.node_id).hardware2program_ref_id
        except KeyError:
            return False
        if not ref or "SecurityProxy" not in self._catalog.coupler_capabilities(ref):
            return False
        return self._svc.secure_commissioning(self._pid, device.node_id)

    def unicast_broadcast_blocking(self, device: Device) -> str | None:
        """The coupler's blocking setting (``"On"``/``"Off"``, ``None`` = Auto)."""
        if self._pid is None:
            return None
        return self._svc.unicast_broadcast_blocking(self._pid, device.node_id)

    def set_unicast_broadcast_blocking(self, device: Device, mode: str | None) -> None:
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.set_unicast_broadcast_blocking(self._pid, device.node_id, mode)
        self._bump(structural=False)

    def persist_dali_backup(
        self,
        device: Device,
        name: str,
        data: bytes,
        *,
        label: str | None = None,
    ) -> None:
        """Persist a DALI DCA backup blob into the device's ``BinaryData`` as one undo step.

        ``name`` is the BinaryData entry name (e.g. ``DaliGC16-Backup-Store``); ``data`` the
        raw UTF-16 XML. Refreshes the device so a later read sees the new blob, then bumps the
        revision.
        """
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.upsert_device_binary_data(
                self._pid, device.node_id, name, data, label=label
            )
        self._refresh_device(device.node_id)
        self._bump(structural=False)

    def device_binary_data(self, device: Device, name: str) -> bytes | None:
        """The raw bytes of the device's ``BinaryData`` entry named ``name``, or ``None``."""
        for row in device.binary_data:
            if getattr(row, "name", None) == name:
                return bytes(getattr(row, "data", b"") or b"")
        return None

    def download_routing_blocking(self, device: Device) -> bool | None:
        """Whether a download blocks point-to-point and broadcast messages on both sides of the
        device, a coupler with the security extensions; ``None`` for any other device. Auto
        blocks them on a coupler used as a Security Proxy (KNX AN192 3.5.2.2); the project never
        sets one up to translate between secured and plain lines, so Auto routes them."""
        if not self.security_proxy_coupler(device):
            return None
        return self.unicast_broadcast_blocking(device) == "On"

    def record_factory_reset(self, device: Device) -> None:
        """A factory reset (with the individual address) gave the device its FDSK back: it no
        longer holds a Tool Key, so its next secure download needs its device certificate."""
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.set_tool_key_unloaded(self._pid, device.node_id)
        self._bump(structural=False)

    def record_secure_download(self, device: Device, result: object) -> None:
        """Record what a secured download left in the device (its ``DownloadResult``): the Tool
        Key it uses now and its next Sequence Number Sending."""
        if self._pid is None:
            return
        if getattr(result, "tool_key_loaded", False):
            self.set_tool_key_loaded(device)
        sequence = getattr(result, "sequence_number", None)
        if isinstance(sequence, int):
            with self._io_lock:
                self._svc.set_device_sequence_number(
                    self._pid, device.node_id, sequence
                )
            self._bump(structural=False)

    def set_tool_key_loaded(self, device: Device) -> None:
        """Record that the device uses its Tool Key after a secure download."""
        if self._pid is None:
            return
        with self._io_lock:
            self._svc.set_tool_key_loaded(self._pid, device.node_id)
        self._bump(structural=False)

    def add_device_certificate(self, code: str) -> "Fdsk":
        """Add the device certificate (FDSK label code) to the project; see the core method."""
        if self._pid is None:
            raise ValueError("no project is open")
        with self._io_lock:
            fdsk = self._svc.add_device_certificate(self._pid, code)
        self._bump(structural=False)
        self._log.info("device certificate added")
        return fdsk

    def _coupler_pass_through(
        self, coupler_address: int
    ) -> tuple[list[int], list[int]]:
        """``(unfiltered, additional)`` pass-through addresses for a coupler; empty when unknown."""
        if self._pid is None:
            return [], []
        return self._svc.coupler_pass_through(self._pid, coupler_address)

    def _all_device_group_addresses(self) -> dict[int, set[int]]:
        """Map every device's raw individual address to the raw group addresses it links (send or
        receive). The coupler filter-table computation uses this to decide which addresses cross a
        coupler (linked both behind it and in front of it)."""
        result: dict[int, set[int]] = {}
        for dev in self.devices:
            ia = _parse_individual_address(dev.individual_address)
            if ia is None:
                continue
            gas: set[int] = set()
            for co in dev.com_objects:
                if co.db_id is None:
                    continue
                for assignment in self.get_links_for_com_object(co.db_id):
                    ga = self.get_group_address(assignment.group_address_id)
                    if ga is None:
                        continue
                    raw = _parse_group_address(ga.address)
                    if raw is not None:
                        gas.add(raw)
            result[ia] = gas
        return result

    @io_guarded(list)
    @revision_cached
    def get_group_range_tree(self) -> list["GroupRangeInfo"]:
        """The named group-address range tree (roots → children → GAs) for the GA view."""
        if self._pid is None:
            return []
        return self._svc.group_ranges(self._pid, _INSTALLATION)

    @io_guarded(list)
    @revision_cached
    def get_space_tree(self) -> list["SpaceInfo"]:
        """The building/location tree (spaces → devices/functions) for the Buildings view."""
        if self._pid is None:
            return []
        return self._svc.space_tree(self._pid, _INSTALLATION)

    @io_guarded(lambda: None)
    @revision_cached
    def get_project_metadata(self) -> ProjectInfo | None:
        """Project-level metadata (name, author, tool/schema version, …) from the imported project."""
        if self._pid is None:
            return None
        p = self._svc.project(self._pid)
        installations = self._svc.installations(self._pid)
        return ProjectInfo(
            id=p.id,
            name=p.name,
            group_address_style=p.group_address_style,
            guid=p.guid,
            created_by=p.created_by,
            last_modified=p.last_modified,
            schema_version=p.schema_version,
            tool_version=p.tool_version,
            installation_name=installations[0].name if installations else "",
            original_project_id=p.original_project_id,
            master_data_size=len(p.knx_master_xml) if p.knx_master_xml else 0,
            validation_size=len(p.knx_validation),
            certificate_size=len(p.knx_certificate),
            password_protected=self._svc.is_password_protected(self._pid),
        )

    def set_project_name(self, name: str) -> None:
        """Rename the project (one undo step); an empty name keeps the current one."""
        name = name.strip()
        if self._pid is None or not name or name == self._svc.project(self._pid).name:
            return
        self._svc.set_project_name(self._pid, name)
        self._bump(structural=False)

    def set_project_password(self, password: str | None) -> None:
        """Set or change the project password, or remove it with ``None``. The project's secrets
        are encrypted again with it, so this is not an undo step. A project with KNX Secure data
        keeps its password (see :meth:`project_needs_password`)."""
        if self._pid is None:
            return
        if not password and self.project_needs_password():
            raise ValueError("a project with KNX Secure data needs a password")
        with self._io_lock:
            self._svc.set_project_password(self._pid, password or None)
        self._bump(structural=False)
        self._log.info(
            "project password set" if password else "project password removed"
        )

    @property
    def project_password(self) -> str | None:
        """The password of the open project, ``None`` when it is not protected."""
        return self._svc.project_password(self._pid) if self._pid is not None else None

    def needs_password_setup(self) -> bool:
        """Whether the open project holds KNX Secure data but no password protects it yet."""
        return self._pid is not None and self._svc.needs_password(self._pid)

    def reveal(self, data: bytes) -> bytes:
        """Serialized project XML with the project's encrypted secrets in the clear."""
        return self._svc.reveal(self._pid, data) if self._pid is not None else data

    @io_guarded(lambda: False)
    @revision_cached
    def project_needs_password(self) -> bool:
        """Whether the project holds KNX Secure data, whose keys an export protects only with the
        project password."""
        return self._pid is not None and self._svc.has_secure_data(self._pid)

    def device_keys(self, device: Device) -> "DeviceKeys | None":
        """The device's KNX Data Secure tool keys from the project, or ``None`` when it has none."""
        if self._pid is None:
            return None
        return self._svc.device_keys(self._pid, device.node_id)

    def device_certificate_state(self, device: Device) -> tuple[bytes | None, bool]:
        """The serial number the project holds the device's certificate for (``None`` without
        one) and whether the device uses its own Tool Key (the certificate is then needed again
        only after a factory reset). Cached by revision: the device settings read it every
        frame."""
        if self._pid is None:
            return None, False
        cache_key = f"device_certificate_state:{device.node_id}"
        hit = self._revision_cache.get(cache_key)
        if hit is not None and hit[0] == self.revision:
            return cast("tuple[bytes | None, bool]", hit[1])
        keys = self.device_keys(device)
        serial = (
            self._device_serial(device.node_id)
            if keys is not None and keys.fdsk is not None
            else None
        )
        state = (serial, keys is not None and keys.loaded_tool_key is not None)
        self._revision_cache[cache_key] = (self.revision, state)
        return state

    @io_guarded(lambda: None)
    def has_project_log(self) -> bool | None:
        """Whether the open project has entries in its log (only an imported project has, the
        editor writes none); None while that cannot be read (an import is running)."""
        return bool(self.get_project_traces())

    def get_project_traces(self) -> list[ProjectTrace]:
        """The project log (ProjectInformation/ProjectTraces), in document order.

        Comments are stored verbatim (they are encrypted on disk); ``comment_plain`` is filled with
        the decrypted text when a trace key has been installed (see :mod:`editor_gui.trace_key`).
        Cached by revision and installed key: the Project Log reads it every frame.
        """
        from xknxeditor.proj import current_marker, decrypt_comment

        if self._pid is None:
            return []
        cache_key = f"project_traces:{current_marker()}"
        hit = self._revision_cache.get(cache_key)
        if hit is not None and hit[0] == self.revision:
            return cast("list[ProjectTrace]", hit[1])
        traces = [
            ProjectTrace(
                date=date,
                user_name=user_name,
                comment=comment,
                comment_plain=decrypt_comment(comment),
            )
            for date, user_name, comment in self._svc.project_traces(self._pid)
        ]
        self._revision_cache[cache_key] = (self.revision, traces)
        return traces

    def get_import_notes(self) -> list["ImportLoss"]:
        """Lossy-import notes recorded when the current project was built from a ``.knxproj``
        (data ETS/xknxproject carried that our store cannot fully represent). Empty when the
        project was not imported or nothing was dropped."""
        if self._pid is None:
            return []
        from xknxeditor.proj.core.import_notes import loads

        p = self._svc.project(self._pid)
        return loads(p.import_notes)

    def next_free_group_address(self) -> str | None:
        """The next unused group address, style-formatted (e.g. ``"0/0/2"``); None if no project."""
        if self._pid is None:
            return None
        from xknxeditor.proj.core.addressing import format_ga

        value = self._svc.next_free_group_address(self._pid, _INSTALLATION)
        return format_ga(value, self.group_address_style)

    @io_guarded(lambda: None)
    def get_device_info(self, node_id: int) -> "DeviceInfo | None":
        """Descriptive device metadata (manufacturer/order number/hardware/description) from the
        imported project, independent of catalog resolution.

        Cached by revision per node id. The Configure panel reads this every frame; an uncached read
        there is a 60 Hz SQLite query that both churns the CPU and, when a background writer (import,
        download) holds the database lock, can raise ``database is locked`` straight out of the render
        loop. @revision_cached only memoises zero-arg reads, so this keys the shared revision cache by
        node id itself; the dict is cleared on project open/close like the other per-frame caches."""
        if self._pid is None:
            return None
        cache_key = f"get_device_info:{node_id}"
        hit = self._revision_cache.get(cache_key)
        if hit is not None and hit[0] == self.revision:
            return cast("DeviceInfo | None", hit[1])
        try:
            info = self._svc.device(self._pid, node_id)
        except KeyError:
            info = None
        self._revision_cache[cache_key] = (self.revision, info)
        return info

    @io_guarded(lambda: None)
    def ip_download_config(self, device: Device) -> "IpDeviceConfig | None":
        """The project's IP settings a full download writes to ``device``, or ``None`` when its
        hardware is not an IP device."""
        from xknx.telegram import IndividualAddress

        from xknxeditor.download.ip_config import (
            DEFAULT_ROUTING_MULTICAST_ADDRESS,
            IpDeviceConfig,
        )

        if self._pid is None or not device.individual_address:
            return None
        row = next(
            (r for r in self._svc.devices(self._pid) if r.id == device.node_id), None
        )
        if (
            row is None
            or not row.hardware2program_ref_id
            or not self._catalog.hardware_is_ip_enabled(row.hardware2program_ref_id)
        ):
            return None
        ip = row.ip_config or {}
        installation = row.segment.line.area.installation.extra_attributes or {}
        return IpDeviceConfig(
            individual_address=IndividualAddress(device.individual_address).raw,
            friendly_name=row.name,
            routing_multicast_address=installation.get("IPRoutingMulticastAddress")
            or DEFAULT_ROUTING_MULTICAST_ADDRESS,
            fixed_address=ip.get("Assign") == "Fixed",
            ip_address=ip.get("IPAddress"),
            subnet_mask=ip.get("SubnetMask"),
            default_gateway=ip.get("DefaultGateway"),
            additional_addresses=tuple(
                a.address for a in row.additional_addresses if a.address is not None
            ),
        )

    def ip_secure_config(
        self, device: Device, keyring: "KeyringService | None" = None
    ) -> "IpSecureConfig | None":
        """The KNX IP Secure settings a secured download writes to ``device``, a secure-enabled IP
        device: the installation's routing backbone key, and the device authentication code, the
        management password and the tunnelling users' passwords from the project (else from the
        loaded ``keyring``). The password hashes are only written with the management password
        known, so the device's management access is never replaced by an unknown one. ``None``
        when the device is not such a device or nothing is known for it."""
        from xknxeditor.download.ip_secure import IpSecureConfig

        if (
            self._pid is None
            or device.app is None
            or not device.app.program.is_secure_enabled
        ):
            return None
        row = next(
            (r for r in self._svc.devices(self._pid) if r.id == device.node_id), None
        )
        if (
            row is None
            or not row.hardware2program_ref_id
            or not self._catalog.hardware_is_ip_enabled(row.hardware2program_ref_id)
        ):
            return None
        installation = row.segment.line.area.installation.extra_attributes or {}
        kinds = {access for _ref, access, _index in _bus_interfaces(device)}
        # The backbone key is the routing one: only a device that routes takes it, and not while
        # the installation's backbone security is switched off.
        backbone = (
            self._svc.ip_backbone_key(self._pid)
            if "Routing" in kinds
            and installation.get("IPRoutingBackboneSecurity") != "Off"
            else None
        )
        project = self._svc.ip_secrets(self._pid, device.node_id)
        ring = (
            keyring.ip_secrets(device.individual_address)
            if keyring is not None
            else None
        )
        authentication, passwords, tunnelling = ip_secure_users(
            project,
            ring,
            _tunnelling_interfaces(device),
            [a.address for a in row.additional_addresses if a.address is not None],
        )
        if backbone is None and authentication is None and not passwords:
            return None
        tolerance = _int_or(installation.get("IPRoutingLatencyTolerance"), 2000)
        fraction = _float_or(installation.get("IPSyncLatencyFraction"), 0.1)
        return IpSecureConfig(
            backbone_key=backbone,
            device_authentication_passphrase=authentication,
            user_passwords=passwords,
            tunnelling_users=tunnelling,
            secured_families=_secured_families(kinds, project, passwords, backbone),
            multicast_latency_tolerance_ms=tolerance,
            # PID_SYNC_LATENCY_FRACTION is a DPT 5.001 scaling: 0..255 for 0..100 %.
            sync_latency_fraction=max(0, min(255, round(fraction * 255))),
        )

    def record_ip_secure_download(
        self, device: Device, config: "IpSecureConfig"
    ) -> None:
        """Record the KNX IP Secure settings a secure download wrote (``config``)."""
        from xknxeditor.download.ip_secure import (
            MANAGEMENT_USER_ID,
            device_authentication_code,
            password_hash,
        )

        if self._pid is None:
            return
        management = config.user_passwords.get(MANAGEMENT_USER_ID)
        authentication = config.device_authentication_passphrase
        with self._io_lock:
            self._svc.set_ip_secure_loaded(
                self._pid,
                device.node_id,
                backbone_key=config.backbone_key,
                authentication_code_hash=device_authentication_code(authentication)
                if authentication is not None
                else None,
                management_password_hash=password_hash(management)
                if management is not None
                else None,
            )
        self._bump(structural=False)

    def record_download(
        self,
        node_id: int,
        scope_value: str,
        serial: bytes | None = None,
        mac_address: bytes | None = None,
    ) -> None:
        """Record a successful download of ``scope_value`` (a DownloadScope value): its loaded
        flags, the serial number and MAC address read and, except for an unload, the time."""
        flags = _COMMISSIONING_BY_SCOPE.get(scope_value)
        if flags is None:
            return
        now = (
            None
            if scope_value == "unload"
            else datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")
        )
        self.set_device_commissioning(
            node_id,
            serial_number=serial_to_project(serial) if serial else None,
            last_download=now,
            mac_address=":".join(f"{b:02X}" for b in mac_address)
            if mac_address
            else None,
            **flags,
        )

    def set_device_commissioning(
        self,
        node_id: int,
        *,
        serial_number: str | None = None,
        last_download: str | None = None,
        individual_address_loaded: bool | None = None,
        application_program_loaded: bool | None = None,
        communication_part_loaded: bool | None = None,
        medium_config_loaded: bool | None = None,
        parameters_loaded: bool | None = None,
        mac_address: str | None = None,
    ) -> None:
        """Record a device's commissioning state (loaded ticks / serial / last download / MAC
        address).

        Called after programming a device. Non-structural: the device's parameter/com-object UI is
        unchanged, so the expensive device cache is preserved (only the revision bumps)."""
        if self._pid is None:
            return
        self._svc.set_device_commissioning(
            self._pid,
            node_id,
            serial_number=serial_number,
            last_download=last_download,
            individual_address_loaded=individual_address_loaded,
            application_program_loaded=application_program_loaded,
            communication_part_loaded=communication_part_loaded,
            medium_config_loaded=medium_config_loaded,
            parameters_loaded=parameters_loaded,
            mac_address=mac_address,
        )
        self._bump(structural=False)

    @io_guarded(set)
    def program_refs(self) -> set[str]:
        """Distinct hardware-program refs (fallback product refs) across all project devices.

        Used to collect the manufacturer archives a ``.knxproj`` export needs to bundle.
        """
        if self._pid is None:
            return set()
        refs: set[str] = set()
        for row in self._svc.devices(self._pid):
            ref = row.hardware2program_ref_id or row.product_ref_id
            if ref:
                refs.add(ref)
        return refs

    @io_guarded(dict)
    def module_arguments(self) -> dict[int, dict[str, list[tuple[str, str]]]]:
        """The module instance arguments of the project's devices, by device id, for the
        ``.knxproj`` export of instances that have none stored (devices added in the editor)."""
        result: dict[int, dict[str, list[tuple[str, str]]]] = {}
        for device in self.devices:
            if device.module_instances:
                result[device.node_id] = device.get_module_instance_arguments()
        return result

    @io_guarded(list)
    def missing_program_refs(self) -> list[str]:
        """Hardware-program refs of devices whose application is NOT in the local catalog.

        These are the devices dropped from the view with "application not found"; the refs can be
        fetched from the online catalog (they are a prefix of the online catalog item id).
        Guarded because it is polled every frame and also read from the fetch worker."""
        if self._pid is None:
            return []
        missing: list[str] = []
        for row in self._svc.devices(self._pid):
            ref = row.hardware2program_ref_id
            if (
                ref
                and ref not in missing
                and self._resolve_app(ref, row.application_program_ref_id) is None
                and not self._without_application(ref)
            ):
                missing.append(ref)
        return missing

    def refresh_catalog_resolution(
        self,
        *,
        rebuild: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> bool:
        """Drop the cached catalog lookups and invalidate the device view — call after new .knxprod
        products were imported (so unresolved apps now resolve) or after a UI-language change (so
        device labels re-parse in the new language).

        With ``rebuild=True`` the device views are rebuilt *now*, while the lock is held — use this
        from a worker thread (behind the progress modal) so the slow re-parse doesn't freeze the UI
        thread on the next frame. Holds the shared lock across clear + rebuild so per-frame UI reads
        (which acquire it non-blocking and bail otherwise) never see a half-cleared cache or trigger
        the rebuild themselves. The lock is re-entrant, so the ``devices`` read below still works.

        ``cancelled`` is asked between devices; once it answers ``True`` the rebuild stops and the
        lookups are back as before, so the next device read reuses every device built before.
        Returns whether the rebuild was not cancelled."""
        with self._io_lock:
            kept_apps, kept_sources = dict(self._app_cache), dict(self._source_cache)
            self._app_cache.clear()
            self._source_cache.clear()
            with self._loading_lock:
                self._failed_loads.clear()
            self._handled_failures.clear()
            self._bump()
            if rebuild:
                self.build_cancelled = cancelled
                try:
                    # Rebuild under the held lock instead of lazily on the UI thread.
                    _ = self.devices
                except BuildCancelled:
                    self._app_cache.clear()
                    self._app_cache.update(kept_apps)
                    self._source_cache.clear()
                    self._source_cache.update(kept_sources)
                    # Snapshots of the devices built so far are of the abandoned rebuild.
                    self._new_snapshots.clear()
                    self._bump()
                    return False
                finally:
                    self.build_cancelled = None
        return True

    def forget_deleted_product(
        self, deletion: "ProductDeletion", *, rebuild: bool = False
    ) -> None:
        """Drop the cached catalog lookups that went through the hardware programs or applications
        of a product deleted from the catalog, and invalidate the device view. Only devices built
        from a dropped lookup are rebuilt; ``rebuild`` as in :meth:`refresh_catalog_resolution`."""
        programs = set(deletion.removed_programs)
        apps = {a for a in deletion.program_applications.values() if a is not None}
        with self._io_lock:
            for key, app in list(self._app_cache.items()):
                if key in programs or key in apps or app.id in apps:
                    del self._app_cache[key]
            for ref in programs:
                self._plugin_cache.pop(ref, None)
            self._source_cache.clear()
            self._bump()
            if rebuild:
                _ = self.devices

    @io_guarded(int)
    def count_devices_losing_product(self, deletion: "ProductDeletion") -> int:
        """How many devices of the open project could no longer be loaded once ``deletion`` is
        applied: they use a removed hardware program or application and nothing else resolves."""
        if self._pid is None:
            return 0
        programs = set(deletion.removed_programs)
        apps = set(deletion.removed_applications)
        count = 0
        for row in self._svc.devices(self._pid):
            program = row.hardware2program_ref_id
            app_ref = row.application_program_ref_id
            if program not in programs and app_ref not in apps:
                continue
            if (
                program is not None
                and program not in programs
                and self._catalog.get_program_application_id(program) is not None
            ):
                continue
            if (
                app_ref is not None
                and app_ref not in apps
                and self._catalog.has_application(app_ref)
            ):
                continue
            count += 1
        return count

    # --- compact project ---------------------------------------------------

    def analyse_compaction(
        self, progress: "ProgressCallback | None" = None
    ) -> "CompactResult | None":
        """What "Compact Project" would remove and the estimated file size (no changes)."""
        if self._pid is None:
            return None
        with self._io_lock:
            return self._svc.analyse_compaction(self._pid, progress)

    def compact(
        self,
        progress: "ProgressCallback | None" = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> "CompactResult | None":
        """Remove stored product data no device uses and compact the project file.

        Not undoable. Holds the shared lock for the whole operation; raises
        :class:`~xknxeditor.proj.CompactCancelled` when ``cancelled`` is set before the write."""
        if self._pid is None:
            return None
        with self._io_lock:
            result = self._svc.compact(self._pid, progress, cancelled)
            self._revision_cache.clear()
            self._bump(structural=False)
        self._log.info(
            "project compacted",
            removed_applications=len(result.report.application_programs),
            removed_manufacturers=len(result.report.manufacturers),
            size_before=result.file_size_before,
            size_after=result.file_size_after,
        )
        return result

    # --- application update ("Update Application Program") -------------

    def _device_program(self, device: Device) -> str | None:
        if self._pid is None:
            return None
        return self._svc.device(self._pid, device.node_id).hardware2program_ref_id

    @io_guarded(list)
    def application_change_targets(self, device: Device) -> "list[ProgramSummary]":
        """The other application programs of the device's hardware in the local catalog, for
        "Change Application Program": every program that loads an application and is not
        unregistered, older or newer."""
        current = self._device_program(device)
        if current is None or "_HP-" not in current:
            return []
        hardware_id = current.split("_HP-", 1)[0]
        return [
            program
            for program in self._catalog.hardware_programs(hardware_id)
            if program.id != current
            and program.application_id is not None
            and program.application_version is not None
            and program.registration_status != "Unregistered"
        ]

    @io_guarded(lambda: None)
    def local_update_target(self, device: Device) -> "ProgramSummary | None":
        """The local program "Update Application Program Version" switches to: the highest
        version of the same application whose ``ReplacesVersions`` names the current version."""
        if device.app is None:
            return None
        current = device.app.program
        candidates = [
            program
            for program in self.application_change_targets(device)
            if program.application_number == current.application_number
            and current.application_version
            in self._catalog.replaces_versions(program.id)
        ]
        return max(
            candidates,
            key=lambda program: program.application_version or 0,
            default=None,
        )

    @io_guarded(lambda: None)
    def newer_application_version(self, device: Device) -> int | None:
        """The application version "Update" switches the device to, or ``None``."""
        target = self.local_update_target(device)
        return target.application_version if target is not None else None

    def update_application(self, device: Device) -> UpdateApplicationResult | None:
        """See :meth:`_update_application`; holds the IO lock so it can run on a worker."""
        with self._io_lock:
            return self._update_application(device)

    def _update_application(self, device: Device) -> UpdateApplicationResult | None:
        """Update ``device`` to the local application version that replaces its own ("Update
        Application Program Version"). Parameter values are carried over by ParameterRef number,
        group objects of the same size by ComObjectRef number with their flags, priority,
        datapoint type and links. Returns the outcome, or ``None`` when there is no such version
        or it cannot be resolved."""
        from editor_gui.plugins.project.application_update import (
            carry_com_objects,
            carry_parameters,
        )
        from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

        if self._pid is None or device.app is None:
            return None
        target = self.local_update_target(device)
        if (
            target is None
            or target.application_id is None
            or target.application_version is None
        ):
            return None
        new_app = self._catalog.get_application(target.application_id)
        row = next(
            (r for r in self._svc.devices(self._pid) if r.id == device.node_id), None
        )
        if new_app is None or row is None:
            return None
        parameters = carry_parameters(
            [(p.ref_id, p.value) for p in row.parameters],
            device.app.id,
            new_app.id,
            ApplicationIndexer(new_app.program),
        )
        module_instances, new_objects = self._initial_rows(
            device.name, new_app, parameters.values
        )
        objects = carry_com_objects(
            row.com_objects,
            {co.id: co.object_size for co in device.com_objects},
            device.app.id,
            [(co.id, co.object_size) for co in new_objects],
            new_app.id,
        )
        self._svc.change_device_application(
            self._pid,
            device.node_id,
            hardware2program_ref_id=target.id,
            new_app_id=new_app.id,
            com_objects=[(co.id, None) for co in new_objects],
            module_instances=module_instances or None,
            parameters=parameters.values,
            com_object_data=objects.data,
            additional_addresses=new_app.program.additional_addresses_count or 0,
        )
        self._app_cache[target.id] = new_app
        self._bump()
        updated = self.find_device_by_node_id(device.node_id)
        if updated is not None:
            self.recalculate_params(
                updated.node_id, [p.ref_id for p in updated.parameter_instance_refs]
            )
        kept = len(parameters.values) + objects.kept
        dropped = parameters.dropped + objects.dropped
        self._log.info(
            "application updated",
            device=device.node_id,
            to_version=target.application_version,
            kept=kept,
            dropped=dropped,
        )
        return UpdateApplicationResult(
            new_version=target.application_version, kept=kept, dropped=dropped
        )

    def change_application(self, device: Device, program: "ProgramSummary") -> bool:
        """See :meth:`_change_application`; holds the IO lock so it can run on a worker."""
        with self._io_lock:
            return self._change_application(device, program)

    def _change_application(self, device: Device, program: "ProgramSummary") -> bool:
        """Switch ``device`` to another application program of its hardware, starting from that
        application's defaults ("Change Application Program"). Returns whether it changed."""
        if self._pid is None or device.app is None or program.application_id is None:
            return False
        new_app = self._catalog.get_application(program.application_id)
        if new_app is None:
            self._log.warning(
                "change application: application not resolvable",
                device=device.node_id,
                application=program.application_id,
            )
            return False
        module_instances, com_objects = self._initial_rows(device.name, new_app, [])
        self._svc.change_device_application(
            self._pid,
            device.node_id,
            hardware2program_ref_id=program.id,
            new_app_id=new_app.id,
            com_objects=[(co.id, None) for co in com_objects],
            module_instances=module_instances or None,
            additional_addresses=new_app.program.additional_addresses_count or 0,
        )
        self._app_cache[program.id] = new_app
        self._bump()
        self._log.info(
            "application changed",
            device=device.node_id,
            old=device.app.id,
            new=new_app.id,
        )
        return True

    def find_or_create_segment_for_address(self, individual_address: str) -> int | None:
        """Return the segment id for an address' area/line, creating them if absent.

        A fresh project only has area/line ``0.0``; recovering a device at e.g.
        ``1.1.5`` needs that area and line to exist first, otherwise setting the
        address silently fails. Parses ``area.line.device`` and ensures the area
        and line (which comes with a segment) exist, returning that segment's id."""
        if self._pid is None:
            return None
        try:
            area_num, line_num, _device = (
                int(part) for part in individual_address.split(".")
            )
        except ValueError:
            return None
        topo = self._svc.topology(self._pid, _INSTALLATION)
        area = next((a for a in topo.areas if a.address == area_num), None)
        if area is None:
            self._svc.create_area(self._pid, _INSTALLATION, area_num, "")
            topo = self._svc.topology(self._pid, _INSTALLATION)
            area = next((a for a in topo.areas if a.address == area_num), None)
        if area is None:
            return None
        line = next((line for line in area.lines if line.address == line_num), None)
        if line is None:
            self._svc.create_line(self._pid, area.id, line_num, "")
            topo = self._svc.topology(self._pid, _INSTALLATION)
            area = next((a for a in topo.areas if a.address == area_num), None)
            line = (
                next((line for line in area.lines if line.address == line_num), None)
                if area is not None
                else None
            )
        if line is None or not line.segments:
            return None
        self._bump()
        return line.segments[0].id

    def _initial_rows(
        self, name: str, app: Application, parameters: list[tuple[str, str]] | None
    ) -> tuple[list[tuple[str, str]], list[ComObject]]:
        """The module instances and active group objects of a new device of ``app`` with
        ``parameters``."""
        pirs = _parameter_instance_refs(parameters)
        # A module-scoped parameter override (…_M-100_MI-1_P-…) only routes into its module scope
        # once that scope has been materialized — building with parameter_instance_refs alone leaves
        # module channels on their raw application default, so the override is silently ignored and
        # the persisted com-object set is stale (its refs no longer exist once the override applies,
        # so _build_device prunes those channels on reload). Discover the module instances from a
        # first build, then rebuild with them so overrides take effect and the persisted set is
        # symmetric with _build_device. Non-module devices see an empty list and skip the second pass.
        probe = Device(
            node_id=0,
            name=name,
            app=app,
            individual_address="",
            parameter_instance_refs=pirs,
        )
        module_instances = probe.get_module_instances()
        if module_instances and parameters:
            from xknxeditor.namespaces.intermediate.module_instance_t import (
                ModuleInstance as _MI,
            )

            # Drop the probe's heavy DynamicUI before building the second one, so peak memory holds
            # one evaluator, not two.
            probe.release_dynamic_ui()
            init_device = Device(
                node_id=0,
                name=name,
                app=app,
                individual_address="",
                parameter_instance_refs=pirs,
                module_instances=[
                    _MI(id=instance_id, ref_id=ref_id)
                    for instance_id, ref_id in module_instances
                ],
            )
            module_instances = init_device.get_module_instances()
        else:
            init_device = probe
        com_objects = list(init_device.com_objects)
        init_device.release_dynamic_ui()
        return module_instances, com_objects

    def add_device(
        self,
        product_ref_id: str,
        hardware2program_ref_id: str | None,
        name: str,
        app: Application,
        *,
        segment_id: int | None = None,
        address: int | None = None,
        parameters: list[tuple[str, str]] | None = None,
        product: DeviceProduct | None = None,
        secure_commissioning: bool | None = None,
        with_additional_addresses: bool = True,
        space_id: int | None = None,
    ) -> int | None:
        """Add a device. A device that supports KNX Data Secure is commissioned securely by
        default when the project has a password (``secure_commissioning`` ``None``); ``True``
        activates it (the project needs a password, ``ProjectPasswordRequired`` otherwise),
        ``False`` adds it commissioned in the clear. The device gets the additional individual
        addresses its application has (its tunnelling addresses), the highest free ones of the
        line, unless ``with_additional_addresses`` is off; when the line has too few free, it is
        added without them. A device added to the space ``space_id`` goes on the space's Current
        Line when no ``segment_id`` is given, in the same undo step. Raises ``MediumError`` when
        the device's hardware does not support the medium of the segment."""
        if self._pid is None:
            return None
        if not app.program.is_secure_enabled:
            secure_commissioning = False
        elif secure_commissioning is None:
            secure_commissioning = self.project_password is not None
        product = product or DeviceProduct()
        if segment_id is None and space_id is not None:
            segment_id = self._svc.current_line_segment(self._pid, space_id)
        if segment_id is None:
            segment_id = self._default_device_segment_id()
        self.check_device_medium(
            hardware2program_ref_id,
            segment_id,
            name or product.product_name or app.name,
        )
        if address is None:
            # Assign the next free individual address on the target line (standard behaviour), instead of
            # leaving the device address-less. Editable afterwards in Configure. If the line is full,
            # fall back to no address rather than failing the add.
            try:
                address = self._svc.next_free_individual_address_for_segment(
                    self._pid, segment_id
                )
            except ValueError:
                address = None
        module_instances, init_objects = self._initial_rows(name, app, parameters)
        com_objects: list[tuple[str, str | None]] = [
            (co.id, None) for co in init_objects
        ]
        pid = self._pid

        def add(additional: int) -> int:
            return self._svc.add_device(
                pid,
                segment_id,
                product_ref_id,
                name=name,
                address=address,
                hardware2program_ref_id=hardware2program_ref_id,
                application_program_ref_id=app.id,
                parameters=parameters or None,
                com_objects=com_objects,
                module_instances=module_instances if module_instances else None,
                product_name=product.product_name,
                hardware_name=product.hardware_name,
                order_number=product.order_number,
                manufacturer_name=product.manufacturer_name,
                secure_commissioning=secure_commissioning,
                additional_addresses=additional,
                space_id=space_id,
            )

        additional = (
            app.program.additional_addresses_count or 0
            if with_additional_addresses
            else 0
        )
        try:
            device_id = add(additional)
        except AdditionalAddressError as exc:
            self._log.warning(
                "device added without its additional addresses",
                count=additional,
                error=str(exc),
            )
            device_id = add(0)
        if secure_commissioning:
            self._ensure_ip_secrets(device_id, app, hardware2program_ref_id)
        if hardware2program_ref_id is not None:
            self._app_cache[hardware2program_ref_id] = app
        self._bump()
        return device_id

    def set_param(self, device: Device, param_id: str, value: str) -> None:
        """A user edit of one parameter; raises ``ValueError`` when it is rejected."""
        if self._pid is None:
            return
        self._log.debug(
            "param clicked",
            device=device.node_id,
            param=param_id,
            old=device.get_param_value(param_id),
            new=value,
        )
        try:
            self.edit_params(device, [(param_id, value)], mode="edit")
        except ValueError as exc:
            device.param_errors[param_id] = _rejection_text(exc)
            device.param_inputs[param_id] = value
            raise
        device.param_errors.pop(param_id, None)
        device.param_inputs.pop(param_id, None)
        self._retry_rejected_inputs(device)
        self._log_param_tree(device, param_id)

    def _retry_rejected_inputs(self, device: Device) -> None:
        """Submit the inputs still shown in rejected fields again, as after every edit."""
        for ref_id, value in list(device.param_inputs.items()):
            try:
                self.edit_params(device, [(ref_id, value)], mode="edit")
            except ValueError as exc:
                device.param_errors[ref_id] = _rejection_text(exc)
                continue
            device.param_errors.pop(ref_id, None)
            device.param_inputs.pop(ref_id, None)

    def param_error(self, node_id: int, param_id: str) -> str | None:
        """The message of the last rejected edit of a parameter, until it is edited again."""
        device = self.find_device_by_node_id(node_id)
        return None if device is None else device.param_errors.get(param_id)

    def edit_params(
        self,
        device: Device,
        edits: list[tuple[str, str]],
        *,
        mode: ParamMode = "edit",
        label: str | None = None,
        skip_invalid: bool = False,
    ) -> "ChangeSet":
        """Apply parameter edits to a live device and persist them, including calculated values
        and the com-object changes they cause, as one undo step."""
        if self._pid is None:
            return {}
        old_active = (
            device.active_parameter_driven_com_object_ref_ids()
            if self._co_reconcile_enabled
            else set[str]()
        )
        if mode == "raw":
            changes: ChangeSet = {
                ref_id: (device.get_param_value(ref_id), value)
                for ref_id, value in edits
            }
            device.apply_param_values(dict(edits))
        else:
            changes = device.change_param_values(
                edits, validate=mode == "edit", skip_invalid=skip_invalid
            )
        self._persist_param_changes(device, changes, old_active, label=label)
        return changes

    def recalculate_params(self, node_id: int, ref_ids: list[str]) -> "ChangeSet":
        """Run and persist the calculations depending on ``ref_ids``."""
        device = self.find_device_by_node_id(node_id)
        if self._pid is None or device is None:
            return {}
        old_active = (
            device.active_parameter_driven_com_object_ref_ids()
            if self._co_reconcile_enabled
            else set[str]()
        )
        changes = device.recalculate_params(ref_ids)
        self._persist_param_changes(device, changes, old_active)
        return changes

    def parameter_driven_com_objects(self, device: Device) -> set[str]:
        """The active com-object set a later :meth:`commit_param_changes` diffs against."""
        if not self._co_reconcile_enabled:
            return set[str]()
        return device.active_parameter_driven_com_object_ref_ids()

    def persist_script_changes(
        self,
        node_id: int,
        changes: "ChangeSet",
        label: str | None,
        *,
        generation: int | None = None,
    ) -> None:
        """Store values a running parameter script already applied to its locked device.

        ``generation`` is the project identity captured when the operation started; a mismatch means
        the project was closed or replaced while the operation ran, so the write is dropped rather
        than applied to the wrong project."""
        if generation is not None and generation != self._generation:
            self._log.warning(
                "dropping script changes for a project that is no longer open",
                node_id=node_id,
            )
            return
        values = [(ref, new) for ref, (_, new) in changes.items() if new is not None]
        if self._pid is None or not values:
            return
        self._svc.set_parameters(self._pid, node_id, values, label=label)
        self._bump(structural=False)

    def finish_script_changes(
        self, node_id: int, old_active: set[str], *, generation: int | None = None
    ) -> None:
        """After a parameter script: reconcile com-objects and rebuild the device from the project."""
        if generation is not None and generation != self._generation:
            return
        device = self.find_device_by_node_id(node_id)
        if self._pid is None or device is None or device.app is None:
            return
        target = self._com_object_target(device, old_active)
        if target is not None:
            self._svc.set_parameters(
                self._pid,
                node_id,
                [],
                com_object_target=[(r, None) for r in sorted(target)],
                app_program_id=device.app.program.id,
            )
        self._refresh_device(node_id)
        self._bump(structural=False)

    def commit_param_changes(
        self,
        device: Device,
        changes: "ChangeSet",
        old_active: set[str],
        *,
        label: str | None = None,
    ) -> None:
        """Persist changes already applied to the live device as one undo step."""
        self._persist_param_changes(device, changes, old_active, label=label)

    def _persist_param_changes(
        self,
        device: Device,
        changes: "ChangeSet",
        old_active: set[str],
        *,
        label: str | None = None,
    ) -> None:
        device.last_lost_links = []
        values = [(ref, new) for ref, (_, new) in changes.items() if new is not None]
        if self._pid is None or not values or device.app is None:
            return
        target = self._com_object_target(device, old_active)
        if target is not None:
            device.last_lost_links = self._removed_links(device, target)
        new_instances, ended_instances = _module_instance_changes(
            device, device.app.program.id
        )
        self._svc.set_parameters(
            self._pid,
            device.node_id,
            values,
            com_object_target=None
            if target is None
            else [(r, None) for r in sorted(target)],
            app_program_id=device.app.program.id,
            label=label,
            module_instances=new_instances,
            removed_module_instances=ended_instances,
        )
        # Rebuild the device so that subsequent reads of ``parameter_instance_refs`` and
        # ``module_instances`` reflect the just-committed state. Without this, panels that
        # drop their caches but re-read the Device's ORM collections (e.g. the Plugin
        # Config view) would see stale values until the next structural change.
        if values or target is not None or new_instances or ended_instances:
            self._refresh_device(device.node_id)
        self._bump(structural=False)

    def _removed_links(self, device: Device, target: set[str]) -> list[dict[str, Any]]:
        """Links the reconcile is about to drop: group-address links of the com-objects present now
        but not in ``target``. Captured before ``set_parameters`` deletes them, so a caller can report
        what a function/mode change cost. The removed set is small (only the deactivated objects)."""
        removed_ids = {co.id for co in device.com_objects} - target
        lost: list[dict[str, Any]] = []
        for co in device.com_objects:
            if co.id not in removed_ids or co.db_id is None:
                continue
            for a in self.get_links_for_com_object(co.db_id):
                g = self.get_group_address(a.group_address_id)
                lost.append(
                    {
                        "com_object_ref_id": co.id,
                        "group_address_id": a.group_address_id,
                        "address": g.address if g else None,
                    }
                )
        return lost

    def _log_param_tree(self, device: Device, param_id: str) -> None:
        """Debug: dump the (pruned) parameter tree the device now renders — total parameter count and
        the top-level sections with their parameter counts — so a parameter edit's effect on the tree
        (e.g. a section appearing/disappearing) is visible in the log."""
        from xknxeditor.prod.parser_v2.ui import (
            UiComObject,
            UiParameter,
            UiParameterBlock,
            UiTab,
        )

        def count(nodes: list[Any]) -> tuple[int, int]:
            params = cos = 0
            stack: list[Any] = list(nodes)
            while stack:
                n = stack.pop()
                if isinstance(n, UiParameter):
                    params += 1
                elif isinstance(n, UiComObject):
                    cos += 1
                elif isinstance(n, (UiTab, UiParameterBlock)):
                    stack.extend(n.children)
            return params, cos

        ui = device.get_ui()
        total_params, total_cos = count(list(ui))
        sections: list[str] = []
        for node in ui:
            children: list[Any] = (
                list(node.children) if isinstance(node, UiTab) else [node]
            )
            for child in children:
                if isinstance(child, (UiTab, UiParameterBlock)):
                    # Match the renderer, which shows text first (parameter_widgets.py) — so the log
                    # reflects the actual section header the user sees.
                    name = getattr(child, "text", None) or getattr(child, "name", None)
                    if name:
                        p, _ = count([child])
                        sections.append(f"{name}({p})")
        self._log.debug(
            "param tree",
            device=device.node_id,
            param=param_id,
            params=total_params,
            com_objects=total_cos,
            sections=sections[:40],
        )

    def _com_object_target(
        self, device: Device, old_active: set[str]
    ) -> set[str] | None:
        """The com-object set after a parameter change, or ``None`` when it is unchanged.

        Reconcile on the DELTA of the parameter-driven active set (chain-AND) across the change —
        never the absolute set: ADD objects that became active (``(new_active - old_active) -
        current``), REMOVE objects that became inactive (``(old_active - new_active) & current``).
        Using the delta cancels any pre-existing mismatch between the configured set and the parser's
        derivation, so an unrelated edit touches nothing."""
        if not self._co_reconcile_enabled:
            return None
        new_active = device.active_parameter_driven_com_object_ref_ids()
        current = {co.id for co in device.com_objects}
        add = (new_active - old_active) - current
        remove = (old_active - new_active) & current
        target = (current - remove) | add
        if target == current:
            return None
        self._log.info(
            "sync com-objects",
            device=device.node_id,
            added=sorted(add),
            removed=sorted(remove),
            target=len(target),
        )
        return target

    def set_param_on_matching(self, device: Device, param_id: str, value: str) -> int:
        """Set a parameter on every device running the same application (multi-fill).

        Same-application devices share parameter ref-ids (they come from the one application
        program), so the same ``param_id`` applies. Returns how many devices were changed."""
        if self._pid is None:
            return 0
        app_id = getattr(device.app, "id", None)
        targets = [
            d.node_id for d in self.devices if getattr(d.app, "id", None) == app_id
        ]
        return self.set_param_on_selected(targets, param_id, value)

    def set_param_on_selected(
        self, node_ids: list[int], param_id: str, value: str
    ) -> int:
        """Set a parameter on each of the given devices (multi-device edit over a chosen
        subset). Same as :meth:`set_param` per device; returns how many were changed."""
        if self._pid is None:
            return 0
        count = 0
        for nid in node_ids:
            d = self.find_device_by_node_id(nid)
            if d is None:
                continue
            try:
                self.set_param(d, param_id, value)
            except (KeyError, ValueError) as exc:
                self._log.warning(
                    "multi-edit parameter rejected",
                    device=nid,
                    param=param_id,
                    error=str(exc),
                )
                continue
            count += 1
        return count

    def set_device_name(self, node_id: int, old_name: str, new_name: str) -> None:
        if self._pid is None or old_name == new_name:
            return
        self._svc.set_device_name(self._pid, node_id, new_name)
        # In place: update the live device's name and bump non-structurally (a rename changes no
        # device structure/topology), instead of rebuilding every device's dynamic UI.
        dev = self.find_device_by_node_id(node_id)
        if dev is not None:
            dev.name = new_name
        self._bump(structural=False)

    def set_device_description(
        self, node_id: int, old_description: str, new_description: str
    ) -> None:
        if self._pid is None or old_description == new_description:
            return
        self._svc.set_device_description(self._pid, node_id, new_description)
        # In place, like a rename: description is metadata only, no structure/topology change.
        dev = self.find_device_by_node_id(node_id)
        if dev is not None:
            dev.description = new_description
        self._bump(structural=False)

    def remove_device(self, node_id: int) -> None:
        """Delete a device (and its com-objects/links) from the project."""
        if self._pid is None:
            return
        self._svc.remove_device(self._pid, node_id)
        self._bump()

    def clone_device(
        self,
        node_id: int,
        count: int = 1,
        *,
        include_params: bool = True,
        include_links: bool = False,
    ) -> list[int]:
        """Create ``count`` copies of a device.

        Copies keep the source product/application; parameter values are carried over when
        ``include_params`` (default), and group-address links when ``include_links``. The individual
        address is left unset (the copies land in the first segment) so the user assigns fresh
        addresses. Returns the new device node ids."""
        if self._pid is None:
            return []
        row = next((r for r in self._svc.devices(self._pid) if r.id == node_id), None)
        if row is None:
            return []
        app = self._resolve_app(
            row.hardware2program_ref_id, row.application_program_ref_id
        )
        if app is None:
            self._log.warning("cannot clone: application not resolved", device=node_id)
            return []
        params = (
            [(p.ref_id, p.value) for p in row.parameters] if include_params else None
        )
        source = self.find_device_by_node_id(node_id) if include_links else None
        source_links = self._capture_links(source) if source is not None else []
        secure = self._svc.secure_commissioning(self._pid, node_id)
        created: list[int] = []
        for i in range(max(1, count)):
            suffix = " (copy)" if count == 1 else f" (copy {i + 1})"
            new_id = self.add_device(
                row.product_ref_id,
                row.hardware2program_ref_id,
                f"{row.name}{suffix}" if row.name else "",
                app,
                parameters=params,
                product=DeviceProduct(
                    product_name=row.product_name,
                    hardware_name=row.hardware_name,
                    order_number=row.order_number,
                    manufacturer_name=row.manufacturer_name,
                ),
                secure_commissioning=secure,
            )
            if new_id is not None:
                if source_links:
                    new_dev = self.find_device_by_node_id(new_id)
                    if new_dev is not None:
                        self._apply_links(new_dev, source_links, replace_matching=False)
                created.append(new_id)
        self._log.info("device cloned", source=node_id, copies=len(created))
        return created

    def copy_device_config(self, node_id: int) -> DeviceConfigClipboard | None:
        """Snapshot a device's parameter values and group-address links for pasting onto another
        device of the same application."""
        if self._pid is None:
            return None
        device = self.find_device_by_node_id(node_id)
        if device is None:
            return None
        app_id = getattr(device.app, "id", None)
        if not app_id:
            return None
        row = next((r for r in self._svc.devices(self._pid) if r.id == node_id), None)
        params = [(p.ref_id, p.value) for p in row.parameters] if row else []
        return DeviceConfigClipboard(
            app_id=app_id,
            source_label=device.name or getattr(device.app, "name", "") or "",
            params=params,
            links=self._capture_links(device),
        )

    def paste_device_config(
        self,
        node_id: int,
        clip: DeviceConfigClipboard,
        *,
        include_params: bool = True,
        include_links: bool = True,
    ) -> tuple[bool, int, int]:
        """Apply a copied configuration onto a device running the same application. Returns
        (ok, params_changed, links_mapped); ``ok`` is False when the target's application differs."""
        if self._pid is None:
            return (False, 0, 0)
        device = self.find_device_by_node_id(node_id)
        if device is None or getattr(device.app, "id", None) != clip.app_id:
            return (False, 0, 0)
        params_changed = (
            self._apply_params(node_id, clip.params) if include_params else 0
        )
        links_mapped = 0
        if include_links:
            device = self.find_device_by_node_id(node_id)
            if device is not None:
                links_mapped = self._apply_links(
                    device, clip.links, replace_matching=True
                )
        self._bump(structural=False)
        self._log.info(
            "paste device config",
            target=node_id,
            params=params_changed,
            links=links_mapped,
        )
        return (True, params_changed, links_mapped)

    def _capture_links(self, device: Device) -> list[tuple[int, str, int, bool]]:
        """Snapshot a device's group-address links as
        (com_object_number, com_object_size, group_address_id, is_sending)."""
        out: list[tuple[int, str, int, bool]] = []
        for co in device.get_visible_com_objects():
            if co.db_id is None:
                continue
            for link in self.get_links_for_com_object(co.db_id):
                out.append(
                    (co.number, co.object_size, link.group_address_id, link.is_sending)
                )
        return out

    def _apply_links(
        self,
        device: Device,
        links: list[tuple[int, str, int, bool]],
        *,
        replace_matching: bool,
    ) -> int:
        """Attach captured links to a device's com-objects, matched by number (guarded by object
        size). When ``replace_matching``, a matched com-object's existing links are removed first.
        Returns how many com-objects were (re)linked."""
        by_number = {
            co.number: co
            for co in device.get_visible_com_objects()
            if co.db_id is not None
        }
        grouped: dict[int, tuple[str, list[tuple[int, bool]]]] = {}
        for number, size, ga_id, is_sending in links:
            grouped.setdefault(number, (size, []))[1].append((ga_id, is_sending))
        mapped = 0
        for number, (size, gas) in grouped.items():
            nco = by_number.get(number)
            if nco is None or nco.db_id is None:
                continue
            if size and nco.object_size and size != nco.object_size:
                continue
            if replace_matching:
                for assignment in self.get_links_for_com_object(nco.db_id):
                    self.unlink_com_object_from_ga(assignment.id)
            for ga_id, is_sending in gas:
                self.link_com_object_to_ga(nco.db_id, ga_id, is_sending=is_sending)
            mapped += 1
        return mapped

    def _apply_params(self, node_id: int, params: list[tuple[str, str]]) -> int:
        """Transfer (ref_id, value) overrides to a single device: calculations run, validations do
        not, and values the device rejects are skipped. Returns the number of values changed."""
        device = self.find_device_by_node_id(node_id)
        if device is None:
            return 0
        edits = [
            (ref, value)
            for ref, value in params
            if device.get_param_value(ref) != value
        ]
        changes = self.edit_params(device, edits, mode="transfer", skip_invalid=True)
        return sum(1 for ref, _ in edits if ref in changes)

    def set_device_individual_address(
        self, node_id: int, old_address: str, new_address: str
    ) -> bool:
        """Persist a new individual address. Returns ``True`` on success, ``False`` when rejected
        (e.g. the address is already used) so the caller can avoid mutating its live device object
        with an address the project never accepted. Raises ``MediumError`` when the address
        moves the device to a line whose medium its hardware does not support."""
        if self._pid is None or old_address == new_address:
            return False
        self._check_line_medium(node_id, new_address)
        try:
            self._svc.set_individual_address(self._pid, node_id, new_address)
        except (KeyError, ValueError) as e:
            self._log.warning(
                "could not set individual address", address=new_address, error=str(e)
            )
            return False
        self._bump()
        return True

    def next_free_device_number(self, area_number: int, line_number: int) -> int | None:
        """The lowest device number of the line ``area_number.line_number`` that no device and no
        additional address uses; ``None`` when the line does not exist or is full."""
        if self._pid is None:
            return None
        line = next(
            (
                line
                for area in self._svc.topology(self._pid, _INSTALLATION).areas
                if area.address == area_number
                for line in area.lines
                if line.address == line_number
            ),
            None,
        )
        if line is None:
            return None
        try:
            return self._svc.next_free_individual_address(self._pid, line.id)
        except ValueError:
            return None

    def additional_address_count(self, device: Device) -> int:
        """How many additional individual addresses the device's application has (its tunnelling
        addresses); 0 while its application is not loaded."""
        if device.app is None:
            return 0
        return device.app.program.additional_addresses_count or 0

    def additional_addresses(self, node_id: int) -> "list[AdditionalAddressInfo]":
        """The device's additional individual addresses, every entry in order."""
        info = self.get_device_info(node_id)
        return list(info.additional_addresses) if info is not None else []

    def assign_additional_addresses(
        self, device: Device, count: int | None = None
    ) -> "list[AdditionalAddressInfo]":
        """Give the device's additional addresses without one an address, adding entries up to
        ``count`` (the application's number by default): the highest free device numbers of its
        line. Undoable. Raises ``AdditionalAddressError`` when the line has too few free."""
        if self._pid is None:
            return []
        entries = self._svc.assign_additional_addresses(
            self._pid,
            device.node_id,
            self.additional_address_count(device) if count is None else count,
            label=S.HISTORY_ASSIGN_ADDITIONAL_ADDRESSES,
        )
        self._bump(structural=False)
        return entries

    def set_additional_address(
        self, node_id: int, index: int, address: str | None
    ) -> None:
        """Set the address of the device's additional address ``index`` (1-based): a device number
        or a full ``a.l.d`` in its line, empty or ``None`` to clear it. Undoable. Raises
        ``AdditionalAddressError`` when the address is invalid, outside the line or in use."""
        if self._pid is None:
            return
        new = self._svc.additional_address_text(self._pid, node_id, address) or ""
        old = next(
            (e.address for e in self.additional_addresses(node_id) if e.index == index),
            None,
        )
        if (old or "") == new:
            return
        self._svc.set_additional_address(
            self._pid,
            node_id,
            index,
            address,
            label=S.HISTORY_ADDITIONAL_ADDRESS_CHANGED.format(
                self._device_history_name(node_id), old or "", new
            ),
        )
        self._bump(structural=False)

    def set_additional_address_name(self, node_id: int, index: int, name: str) -> None:
        """Set the name of the device's additional address ``index`` (1-based). Undoable."""
        if self._pid is None:
            return
        self._svc.set_additional_address_name(
            self._pid, node_id, index, name, label=S.HISTORY_ADDITIONAL_ADDRESS_NAMES
        )
        self._bump(structural=False)

    def set_additional_address_description(
        self, node_id: int, index: int, description: str
    ) -> None:
        """Set the description of the device's additional address ``index`` (1-based).
        Undoable."""
        if self._pid is None:
            return
        self._svc.set_additional_address_description(
            self._pid,
            node_id,
            index,
            description,
            label=S.HISTORY_ADDITIONAL_ADDRESS_DESCRIPTIONS,
        )
        self._bump(structural=False)

    def _device_history_name(self, node_id: int) -> str:
        """The device's name as the history names it: its own, else its default one."""
        device = self.find_device_by_node_id(node_id)
        if device is not None:
            return device.display_name
        info = self.get_device_info(node_id)
        return info.name if info is not None else ""

    def set_flag(self, device: Device, co_id: str, flag_name: str, value: bool) -> None:
        if self._pid is None:
            return
        co = device.find_com_object(co_id)
        column = _FLAG_COLUMNS.get(flag_name)
        if co is None or co.db_id is None or column is None:
            return
        self._svc.set_com_object_flag(self._pid, co.db_id, column, value)
        # In place instead of a full rebuild (which would reset the Configure panel's tree/edit
        # state): reflect on the live com-object and push the com-object's full override set into the
        # live dynamic UI — exactly what a rebuild reconstructs from the DB — then bump non-structurally
        # so revision-keyed panels (Health/Cockpit) refresh.
        setattr(co.flags, flag_name, value)
        device.edited_in_place = True
        row = self._find_com_object_row(co.db_id)
        if row is not None:
            coir = _co_instance_ref_from_row(row, ref_id=co_id) or ComObjectInstanceRef(
                ref_id=co_id
            )
            device.set_com_obj_instance_ref(co_id, coir)
        self._bump(structural=False)

    def set_com_object_datapoint_type(
        self, device: Device, co_id: str, value: str | None
    ) -> None:
        """Pin (or clear with an empty/``None`` ``value``) the per-instance DatapointType of a group
        object. ``value`` is a dotted code (``5.001``) or ETS token (``DPST-5-1``); clearing inherits
        the application/product default again."""
        if self._pid is None:
            return
        co = device.find_com_object(co_id)
        if co is None or co.db_id is None:
            return
        token = normalize_datapoint_type(value) if value else None
        self._svc.set_com_object_datapoint_type(self._pid, co.db_id, token)
        # Reflect on the live com-object. With a value, update the override and the resolved display
        # DPT in place (non-structural bump keeps the Configure tree/edit state). On a clear, the
        # default DPT is only known after a rebuild, so bump structurally to pull it from the catalog.
        co.datapoint_type_override = token
        if value:
            co.dpt = lookup_or_make_dpt(value)
        device.edited_in_place = True
        self._bump(structural=token is None)

    def set_com_object_priority(
        self, device: Device, co_id: str, value: str | None
    ) -> None:
        """Set (or clear with ``None``, inherit the default) the per-instance Priority of a group
        object."""
        if self._pid is None:
            return
        co = device.find_com_object(co_id)
        if co is None or co.db_id is None:
            return
        self._svc.set_com_object_priority(self._pid, co.db_id, value)
        # Set: reflect in place (non-structural). Clear: the default is only known after a rebuild.
        if value:
            co.priority = value
        device.edited_in_place = True
        self._bump(structural=value is None)

    def set_com_object_description(
        self, device: Device, co_id: str, value: str | None
    ) -> None:
        """Set (or clear with an empty/``None`` value) the per-instance Description of a group
        object. The description is shown as a tooltip; it has no application default to fall back to,
        so a clear just empties it."""
        if self._pid is None:
            return
        co = device.find_com_object(co_id)
        if co is None or co.db_id is None:
            return
        normalized = value or None
        self._svc.set_com_object_description(self._pid, co.db_id, normalized)
        co.description = normalized or ""
        device.edited_in_place = True
        self._bump(structural=False)

    def _find_com_object_row(self, co_db_id: int) -> Any | None:
        """The core ComObject ORM row for ``co_db_id`` (to rebuild its instance-ref overrides)."""
        if self._pid is None:
            return None
        for d in self._svc.devices(self._pid):
            for c in d.com_objects:
                if c.id == co_db_id:
                    return c
        return None

    def create_area(self, area_number: int, name: str = "") -> int | None:
        if self._pid is None:
            return None
        area_id = self._svc.create_area(self._pid, _INSTALLATION, area_number, name)
        self._bump()
        return area_id

    def remove_area(self, area_id: int, area_number: int = 0, name: str = "") -> None:
        if self._pid is None:
            return
        self._svc.remove_area(self._pid, area_id)
        self._bump()

    def rename_area(self, area_id: int, old_name: str, new_name: str) -> None:
        if self._pid is None or old_name == new_name:
            return
        self._svc.rename_area(self._pid, area_id, new_name)
        self._bump()

    def create_line(self, area_id: int, line_number: int, name: str = "") -> int | None:
        if self._pid is None:
            return None
        line_id = self._svc.create_line(self._pid, area_id, line_number, name)
        self._bump()
        return line_id

    def remove_line(
        self, line_id: int, area_id: int = 0, line_number: int = 0, name: str = ""
    ) -> None:
        if self._pid is None:
            return
        self._svc.remove_line(self._pid, line_id)
        self._bump()

    def rename_line(self, line_id: int, old_name: str, new_name: str) -> None:
        if self._pid is None or old_name == new_name:
            return
        self._svc.rename_line(self._pid, line_id, new_name)
        self._bump()

    @io_guarded(dict)
    @revision_cached
    def device_segments(self) -> dict[int, int]:
        """Every device's segment: ``node id -> segment id``."""
        if self._pid is None:
            return {}
        return self._svc.device_segments(self._pid)

    def find_line(self, line_id: int) -> _Line | None:
        """The line ``line_id`` with its medium and its sub-segments."""
        found = self.find_topology("line", line_id)
        return found if isinstance(found, _Line) else None

    def find_segment(self, segment_id: int) -> _Segment | None:
        """The sub-segment ``segment_id`` (a line's main segment is the line itself)."""
        self._ensure_topology_cache()
        return next(
            (
                segment
                for lines in (self._lines_cache or {}).values()
                for line in lines
                for segment in line.segments
                if segment.id == segment_id
            ),
            None,
        )

    def add_segment(self, line_id: int, name: str | None = None) -> int | None:
        """A new sub-segment of the line, with the medium a line gives a new one; undoable.
        Raises ``MediumError`` when the line allows none."""
        if self._pid is None:
            return None
        segment_id = self._svc.add_segment(
            self._pid,
            line_id,
            name=S.SEGMENT_DEFAULT_NAME if name is None else name,
        )
        self._bump()
        return segment_id

    def remove_segment(self, segment_id: int) -> None:
        """Remove a sub-segment and its devices; undoable."""
        if self._pid is None:
            return
        self._svc.remove_segment(self._pid, segment_id)
        self._bump()

    def rename_segment(self, segment_id: int, name: str) -> None:
        segment = self.find_segment(segment_id)
        if self._pid is None or segment is None or segment.name == name:
            return
        self._svc.rename_segment(self._pid, segment_id, name)
        self._bump()

    def set_line_medium(self, line_id: int, medium_type: str) -> None:
        """Change a line's medium (its main segment's); undoable. Raises ``MediumError`` when
        the line holds devices or its segments do not allow it."""
        line = self.find_line(line_id)
        if self._pid is None or line is None or line.medium_type == medium_type:
            return
        couplers, ip_routers = self._device_roles()
        self._svc.set_line_medium(
            self._pid,
            line_id,
            medium_type,
            couplers=couplers,
            ip_routers=ip_routers,
            line_names=self._line_names(),
        )
        self._bump()

    def _device_roles(self) -> tuple[set[int], set[int]]:
        """The devices whose hardware is a coupler, and those that are KNXnet/IP routers (by
        their application's mask version), as the catalog knows them."""
        programs = self._svc.device_programs(cast(str, self._pid))
        roles = self._catalog.program_roles(sorted(set(programs.values())))
        couplers = {d for d, p in programs.items() if p in roles and roles[p][0]}
        ip_routers = {
            d
            for d, p in programs.items()
            if p in roles and roles[p][1] in IP_ROUTER_MASKS
        }
        return couplers, ip_routers

    def _line_names(self) -> dict[int, str]:
        """Every line's name as the tree shows it ("IP main line" for an unnamed main line)."""
        return {
            line.id: line_display_name(area.area_number, line)
            for area in self.get_areas()
            for line in self.get_lines(area.id)
        }

    def set_segment_medium(self, segment_id: int, medium_type: str) -> None:
        """Change a sub-segment's medium; undoable. Raises ``MediumError`` when it holds devices
        or its line does not allow it."""
        segment = self.find_segment(segment_id)
        if self._pid is None or segment is None or segment.medium_type == medium_type:
            return
        couplers, ip_routers = self._device_roles()
        self._svc.set_segment_medium(
            self._pid,
            segment_id,
            medium_type,
            couplers=couplers,
            ip_routers=ip_routers,
            line_names=self._line_names(),
        )
        self._bump()

    def segment_medium(self, segment_id: int) -> tuple[str, bool] | None:
        """A segment's medium and whether it is its line's main segment; None for no such
        segment."""
        self._ensure_topology_cache()
        for lines in (self._lines_cache or {}).values():
            for line in lines:
                if line.main_segment_id == segment_id:
                    return line.medium_type, True
                for segment in line.segments:
                    if segment.id == segment_id:
                        return segment.medium_type, False
        return None

    def check_device_medium(
        self, hardware2program_ref_id: str | None, segment_id: int, label: str
    ) -> None:
        """Raise a ``MediumError`` naming the device ``label`` when its hardware does not support
        the medium of the segment. Couplers are not checked, nor a device whose hardware the
        catalog lacks or lists without media."""
        if not hardware2program_ref_id:
            return
        profile = self._catalog.program_media(hardware2program_ref_id)
        target = self.segment_medium(segment_id)
        if profile is None or target is None:
            return
        media, coupler = profile
        medium, main = target
        if coupler or not media or device_fits(medium, media):
            return
        raise MediumError(
            "device_line_medium" if main else "device_segment_medium", device=label
        )

    def _check_line_medium(self, node_id: int, address: str) -> None:
        """Raise a ``MediumError`` when the individual address ``address`` moves the device to
        another line, onto a main segment whose medium its hardware does not support."""
        try:
            area_number, line_number, _device = parse_ia(address)
            placement = self._svc.device_placement(cast(str, self._pid), node_id)
        except (KeyError, ValueError):
            return
        area = next((a for a in self.get_areas() if a.area_number == area_number), None)
        line = next(
            (
                ln
                for ln in (self.get_lines(area.id) if area is not None else [])
                if ln.line_number == line_number
            ),
            None,
        )
        if line is None or line.id == placement.line_id or line.main_segment_id is None:
            return
        device = self.find_device_by_node_id(node_id)
        self.check_device_medium(
            placement.hardware2program_ref_id,
            line.main_segment_id,
            device_label(device) if device is not None else address,
        )

    def move_device_to_segment(self, node_id: int, segment_id: int) -> None:
        """Move a device to a segment: within its line it keeps its address, into another line
        it gets that line's lowest free one. Undoable. Raises ``MediumError`` when the device's
        hardware does not support the segment's medium, ``ValueError`` when the line is full."""
        if self._pid is None:
            return
        placement = self._svc.device_placement(self._pid, node_id)
        if placement.segment_id == segment_id:
            return
        device = self.find_device_by_node_id(node_id)
        label = device_label(device) if device is not None else str(node_id)
        self.check_device_medium(placement.hardware2program_ref_id, segment_id, label)
        target_line = next(
            (
                line
                for lines in (self._lines_cache or {}).values()
                for line in lines
                if line.main_segment_id == segment_id
                or any(s.id == segment_id for s in line.segments)
            ),
            None,
        )
        if target_line is None:
            raise ValueError(f"no segment with id {segment_id}")
        address = (
            placement.address
            if target_line.id == placement.line_id
            else self._svc.next_free_individual_address(self._pid, target_line.id)
        )
        self._svc.move_device(self._pid, node_id, segment_id, address)
        self._bump()

    @property
    @io_guarded(lambda: GroupAddressStyle.THREE_LEVEL)
    def group_address_style(self) -> GroupAddressStyle:
        """The project's group-address style (three-level, two-level, free).

        Read every frame by several panels, so it is IO-guarded: during a background import (which
        rewrites the schema on a worker thread) it returns the default instead of querying the DB
        mid-DDL, which would raise "malformed database schema"."""
        if self._pid is None:
            return GroupAddressStyle.THREE_LEVEL
        return GroupAddressStyle(self._svc.project(self._pid).group_address_style)

    def create_group_address(
        self, address: str | None = None, name: str = ""
    ) -> int | None:
        """Create a group address. ``address`` is a style-formatted string (e.g. ``"1/2/3"``); when
        omitted, the next free address is allocated. Returns ``None`` on an invalid address."""
        if self._pid is None:
            return None
        if address:
            style = GroupAddressStyle(self._svc.project(self._pid).group_address_style)
            try:
                value = parse_ga(address, style)
            except (ValueError, IndexError):
                self._log.warning("invalid group address", address=address)
                return None
        else:
            value = self._svc.next_free_group_address(self._pid, _INSTALLATION)
        ga_id = self._svc.create_group_address(self._pid, _INSTALLATION, value, name)
        self._bump(structural=False)
        return ga_id

    def create_group_address_value(self, value: int, name: str = "") -> int | None:
        """Return the id of the group address with this raw value, creating it if absent.

        Idempotent by value so recovering into a project that already contains a
        group address does not create duplicate rows (as a plain create would)."""
        if self._pid is None:
            return None
        existing = next(
            (
                ga.id
                for ga in self._svc.group_addresses(self._pid)
                if ga.address == value
            ),
            None,
        )
        if existing is not None:
            return existing
        ga_id = self._svc.create_group_address(self._pid, _INSTALLATION, value, name)
        self._bump(structural=False)
        return ga_id

    def rename_group_address(self, ga_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_group_address(self._pid, ga_id, name)
        self._bump(structural=False)

    def set_group_address_address(self, ga_id: int, address: str) -> bool:
        """Renumber a group address (ETS-style change-address): its com-object links move with it.
        ``address`` is a style-formatted string (e.g. ``"1/2/3"``). Returns False on an invalid
        address or when another group address already uses it."""
        if self._pid is None:
            return False
        style = GroupAddressStyle(self._svc.project(self._pid).group_address_style)
        try:
            value = parse_ga(address, style)
        except (ValueError, IndexError):
            self._log.warning("invalid group address", address=address)
            return False
        # parse_ga composes bits leniently (e.g. "1/8/1" silently becomes "1/0/1", "32/0/1"
        # overflows). Reject anything that does not round-trip back to the typed string, so a
        # malformed address is refused rather than quietly stored as a different one.
        if format_ga(value, style) != address.strip():
            self._log.warning("malformed group address", address=address)
            return False
        try:
            self._svc.set_group_address_address(self._pid, ga_id, value)
        except ValueError:
            return False
        self._bump(structural=True)
        return True

    def set_group_address_dpt(self, ga_id: int, dpt: str | None) -> None:
        if self._pid is None:
            return
        self._svc.set_group_address_datapoint_type(self._pid, ga_id, dpt or None)
        self._bump(structural=False)

    def set_element_dpt(self, ga_id: int, dpt: str) -> str | None:
        """Set a group address's data point type ("" for none); the reason it cannot be, or
        None."""
        try:
            self.set_group_address_dpt(ga_id, dpt.strip())
        except ValueError as exc:
            return str(exc)
        return None

    def set_group_address_security(self, ga_id: int, mode: str) -> None:
        """Set a group address's Security mode (``"Auto"``/``"On"``/``"Off"``). ``"On"`` secures it
        (a key is generated, so the project needs a password), ``"Off"`` does not, and ``"Auto"``
        secures it while every linked device is commissioned securely (see the core
        ``ga_security``). Raises ``GroupAddressSecurityError`` for ``"On"`` while a linked device
        is not commissioned securely, or for ``"Off"`` while a linked group object requires
        security."""
        from xknxeditor.proj import GroupAddressSecurityError

        if self._pid is None:
            return
        if mode == "Off":
            try:
                links = self._svc.group_address(self._pid, ga_id).links
            except KeyError:
                links = []
            if any(self.com_object_requires_security(link) for link in links):
                raise GroupAddressSecurityError(
                    "a group object on the group address requires secure communication"
                )
        with self._io_lock:
            self._svc.set_group_address_security(self._pid, ga_id, mode)
        self._bump(structural=False)

    def set_group_range_security(self, range_id: int, mode: str) -> None:
        """Set a main or middle group's Security mode (``"Auto"``/``"On"``/``"Off"``): ``"On"`` and
        ``"Off"`` force it on every group address below it. Raises
        ``GroupAddressSecurityError`` for ``"On"`` while a device linked below it is not
        commissioned securely, or for ``"Off"`` while a group object linked below it requires
        security; ``ProjectPasswordRequired`` when it secures an address without a key."""
        from xknxeditor.proj import GroupAddressSecurityError

        if self._pid is None:
            return
        if mode == "Off":
            found = _find_range(self.get_group_range_tree(), range_id, 0)
            links = (
                [link for ga in _addresses_in(found[0]) for link in ga.links]
                if found is not None
                else []
            )
            if any(self.com_object_requires_security(link) for link in links):
                raise GroupAddressSecurityError(
                    "a group object below the group requires secure communication"
                )
        with self._io_lock:
            self._svc.set_group_range_security(self._pid, range_id, mode)
        self._bump(structural=False)

    def set_group_address_central(self, ga_id: int, central: bool) -> None:
        """Mark a group address as central or not; undoable."""
        if self._pid is None:
            return
        self._svc.set_group_address_central(self._pid, ga_id, central)
        self._bump(structural=False)

    def set_element_security(self, kind: str, element_id: int, mode: str) -> str | None:
        """Set the Security of a group address or a main or middle group; the reason it cannot
        be, or None."""
        from xknxeditor.proj import GroupAddressSecurityError, ProjectPasswordRequired

        try:
            if kind == "group_address":
                self.set_group_address_security(element_id, mode)
            elif kind == "group_range":
                self.set_group_range_security(element_id, mode)
        except ProjectPasswordRequired:
            return S.SECURE_DOWNLOAD_PASSWORD_REQUIRED
        except GroupAddressSecurityError:
            if mode != "On":
                return S.GA_SECURE_REQUIRED
            return (
                S.GA_SECURE_ON_REFUSED
                if kind == "group_address"
                else S.GROUP_SECURE_ON_REFUSED
            )
        return None

    def remove_group_address(
        self, ga_id: int, address: str = "", name: str = ""
    ) -> None:
        if self._pid is None:
            return
        self._svc.remove_group_address(self._pid, ga_id)
        self._bump(structural=False)

    def create_group_range(self, parent_id: int | None, name: str) -> int | None:
        """Create an empty group-range folder (main group when ``parent_id`` is ``None``, else a
        middle group under it). Returns ``None`` when the style has no such folder or it is full."""
        if self._pid is None:
            return None
        rid = self._svc.create_group_range(self._pid, _INSTALLATION, parent_id, name)
        self._bump(structural=False)
        return rid

    def rename_group_range(self, range_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_group_range(self._pid, range_id, name)
        self._bump(structural=False)

    def import_group_addresses(self, path: Path) -> tuple[int, int, int]:
        """Import a group address export (XML or CSV) into the open project as one undo step.

        Returns ``(added, updated, skipped)``. Raises ``ValueError`` for a file that cannot be read
        or uses another address style than the project."""
        from xknxeditor.proj.core.ga_export import read_group_address_file

        if self._pid is None:
            raise ValueError("No project is open.")
        try:
            export = read_group_address_file(path)
        except (OSError, ET.ParseError) as exc:
            raise ValueError(str(exc)) from exc
        event = self._svc.import_group_addresses(
            self._pid,
            _INSTALLATION,
            export,
            label=S.HISTORY_EV_IMPORT_GAS_FROM.format(file=path.name),
        )
        self._bump(structural=False)
        return event.added, event.updated, event.skipped

    def remove_group_range(self, range_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_group_range(self._pid, range_id)
        self._bump(structural=False)

    def link_com_object_to_ga(
        self, com_object_id: int, group_address_id: int, is_sending: bool = False
    ) -> int | None:
        if self._pid is None:
            return None
        from xknxeditor.proj import GroupAddressSecurityError

        if self._link_would_unsecure(com_object_id, group_address_id):
            self._log.error(S.GA_SECURE_REQUIRED)
            return None
        full = self._security_tables_overflow(com_object_id, group_address_id)
        if full is not None:
            self._log.error(S.GA_SECURE_TABLES_FULL.format(device=full))
            return None
        derive = self._dpt_to_derive_on_link(com_object_id, group_address_id)
        try:
            link_id = self._svc.link_com_object(
                self._pid,
                com_object_id,
                group_address_id,
                sending=is_sending,
                derive_datapoint_type=derive,
            )
        except GroupAddressSecurityError:
            self._log.error(S.GA_SECURE_LINK_REFUSED)
            return None
        self._bump(structural=False)
        return link_id

    def _com_object_dpt_token(self, co_db_id: int) -> str | None:
        """The ETS DPT token of a com-object (resolved via the cached device view-models)."""
        for d in self.devices:
            for co in d.com_objects:
                if co.db_id == co_db_id:
                    return co.dpt.token
        return None

    def _dpt_to_derive_on_link(
        self, com_object_id: int, group_address_id: int
    ) -> str | None:
        """DPT token to type the group address with on link, or ``None`` to leave it.

        Only an empty group-address DPT is filled; a differing existing one is kept (and logged),
        never overwritten.
        """
        token = self._com_object_dpt_token(com_object_id)
        if not token:
            return None
        ga = self.get_group_address(group_address_id)
        if ga is None:
            return None
        if not ga.datapoint_type:
            return token
        if ga.datapoint_type != token:
            self._log.warning(
                "linked com-object DPT differs from group address DPT; keeping existing",
                group_address=ga.address,
                existing=ga.datapoint_type,
                com_object=token,
            )
        return None

    def unlink_com_object_from_ga(
        self,
        assignment_id: int,
        com_object_id: int = 0,
        group_address_id: int = 0,
        is_sending: bool = False,
    ) -> None:
        if self._pid is None:
            return
        self._svc.unlink_com_object(self._pid, assignment_id)
        self._bump(structural=False)

    def create_function(
        self, space_id: int, function_type: str, name: str
    ) -> int | None:
        if self._pid is None:
            return None
        fid = self._svc.create_function(self._pid, space_id, function_type, name)
        self._bump(structural=False)
        return fid

    def remove_function(self, function_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_function(self._pid, function_id)
        self._bump(structural=False)

    def rename_function(self, function_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_function(self._pid, function_id, name)
        self._bump(structural=False)

    def set_function_type(self, function_id: int, function_type: str) -> None:
        if self._pid is None:
            return
        self._svc.set_function_type(self._pid, function_id, function_type)
        self._bump(structural=False)

    def add_function_group_address(
        self, function_id: int, group_address_id: int, role: str = ""
    ) -> int | None:
        if self._pid is None:
            return None
        link_id = self._svc.add_function_group_address(
            self._pid, function_id, group_address_id, role
        )
        self._bump(structural=False)
        return link_id

    def remove_function_group_address(self, link_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_function_group_address(self._pid, link_id)
        self._bump(structural=False)

    @io_guarded(list)
    @revision_cached
    def get_unassigned_devices(self) -> "list[SpaceDeviceInfo]":
        # Per-frame read (Spaces panel "Without space" section): guard it like the other tree reads
        # so it bails to an empty list while a background import holds the IO lock and rewrites the
        # schema — an unguarded query races the DDL and SQLite raises "malformed database schema".
        if self._pid is None:
            return []
        return self._svc.unassigned_devices(self._pid, _INSTALLATION)

    def create_space(
        self, parent_id: int | None, space_type: str, name: str, usage: str = ""
    ) -> int | None:
        """A new space; a room with its ``usage`` (a SpaceUsage id, "" for none)."""
        if self._pid is None:
            return None
        sid = self._svc.create_space(
            self._pid,
            _INSTALLATION,
            space_type,
            name,
            parent_id,
            usage=usage,
            usage_text=usage_label(usage) if usage else "",
        )
        self._bump(structural=False)
        return sid

    def rename_space(self, space_id: int, name: str) -> None:
        if self._pid is None:
            return
        self._svc.rename_space(self._pid, space_id, name)
        self._bump(structural=False)

    @property
    def selected_space_id(self) -> int | None:
        """The space selected in the Buildings tree, if a space rather than a device is."""
        return self._selected_space_id

    @selected_space_id.setter
    def selected_space_id(self, space_id: int | None) -> None:
        self._selected_space_id = space_id
        if space_id is not None:
            self._comment_target = ("space", space_id)
            self._selected_element = None

    @property
    def selected_element(self) -> tuple[str, int] | None:
        """``(kind, id)`` of the area, line, group range or group address selected, if one rather
        than a device or a space is."""
        return self._selected_element

    @property
    def selected_topology(self) -> tuple[str, int] | None:
        """The selected element if it is the backbone, an area, a line or a segment."""
        element = self._selected_element
        return (
            element
            if element is not None
            and element[0] in ("backbone", "area", "line", "segment")
            else None
        )

    def select_element(self, kind: str, element_id: int) -> None:
        """An area, line, group range or group address was selected: its details show in place of
        the device's, its comment in the Comment tab. The backbone is area 0 ("backbone")."""
        self._selected_element = (kind, element_id)
        self._selected_space_id = None
        self._comment_target = (_stored_kind(kind), element_id)

    def _main_line(self, area_id: int) -> _Line | None:
        """The main line (address 0) of the area ``area_id``."""
        return next((ln for ln in self.get_lines(area_id) if ln.line_number == 0), None)

    def find_topology(self, kind: str, element_id: int) -> "_Area | _Line | None":
        """The area or line ``element_id``; None when it does not exist (any more)."""
        if kind == "area":
            return next((a for a in self.get_areas() if a.id == element_id), None)
        self._ensure_topology_cache()
        return next(
            (
                ln
                for lines in (self._lines_cache or {}).values()
                for ln in lines
                if ln.id == element_id
            ),
            None,
        )

    def _backbone_details(self, area_id: int) -> "ElementDetails | None":
        """The backbone (area 0 with its line 0.0) and the IP routing of the installation."""
        area = self.find_topology("area", area_id)
        if self._pid is None or not isinstance(area, _Area) or area.area_number != 0:
            return None
        main = self._main_line(area_id)
        attributes = self._svc.installation_attributes(self._pid)
        return ElementDetails(
            "backbone",
            area_id,
            "backbone",
            area.name,
            area.description,
            "",
            "",
            status=self.completion_status("area", area_id),
            has_address=False,
            medium=main.medium_type if main is not None else None,
            latency=_int_or(attributes.get("IPRoutingLatencyTolerance"), 2000),
            multicast_address=attributes.get("IPRoutingMulticastAddress")
            or DEFAULT_MULTICAST_ADDRESS,
            backbone_security=attributes.get("IPRoutingBackboneSecurity") or "Auto",
            bus_access=((main.bus_access or {}).get("Parameter", "") if main else ""),
            bus_connection=format_bus_access(main.bus_access if main else None),
        )

    def set_backbone_bus_connection(self, parameter: str) -> None:
        """Set the backbone's bus connection: the ``Parameter`` of its ``<BusAccess>``, "" for
        "Automatic"; undoable."""
        area = next((a for a in self.get_areas() if a.area_number == 0), None)
        main = self._main_line(area.id) if area is not None else None
        if self._pid is None or main is None or main.main_segment_id is None:
            return
        self._svc.set_segment_bus_access(
            self._pid, main.main_segment_id, parameter or None
        )
        self._bump(structural=False)

    def set_element_backbone_security(self, mode: str) -> str | None:
        """Set the security of the backbone's IP routing ("Auto", "On" or "Off"); the reason it
        cannot be, or None."""
        from xknxeditor.proj import ProjectPasswordRequired

        try:
            self.set_backbone_security(None if mode == "Auto" else mode)
        except ProjectPasswordRequired:
            return S.SECURE_DOWNLOAD_PASSWORD_REQUIRED
        return None

    def set_ip_routing(
        self, *, latency: int | None = None, multicast_address: str | None = None
    ) -> str | None:
        """Set the backbone's network latency (ms) or routing multicast address; the reason it
        cannot be, or None."""
        if self._pid is None:
            return None
        try:
            self._svc.set_ip_routing(
                self._pid, latency=latency, multicast_address=multicast_address
            )
        except ValueError:
            return (
                S.MULTICAST_ADDRESS_INVALID
                if multicast_address is not None
                else S.LATENCY_INVALID
            )
        self._bump(structural=False)
        return None

    def element_details(self, kind: str, element_id: int) -> "ElementDetails | None":
        """What the details of an area, line, segment, group range, group address or building
        function show; None when it does not exist (any more)."""
        if kind == "segment":
            segment = self.find_segment(element_id)
            if segment is None:
                return None
            return ElementDetails(
                kind,
                element_id,
                "segment",
                segment.name,
                segment.description,
                "",
                "",
                status=self.completion_status(kind, element_id),
                has_address=False,
                medium=segment.medium_type,
                domain_address=format_domain_address(
                    segment.medium_type, segment.domain_address
                ),
            )
        if kind == "backbone":
            return self._backbone_details(element_id)
        if kind in ("area", "line"):
            element = self.find_topology(kind, element_id)
            status = self.completion_status(kind, element_id)
            if isinstance(element, _Area):
                main = self._main_line(element.id)
                return ElementDetails(
                    kind,
                    element_id,
                    "area",
                    element.name,
                    element.description,
                    "",
                    str(element.area_number),
                    status=status,
                    medium=main.medium_type if main is not None else None,
                    domain_address=format_domain_address(
                        main.medium_type, main.domain_address
                    )
                    if main is not None
                    else "",
                )
            if isinstance(element, _Line):
                area = self.find_topology("area", element.area_id)
                prefix = f"{area.area_number}." if isinstance(area, _Area) else ""
                return ElementDetails(
                    kind,
                    element_id,
                    "line",
                    element.name,
                    element.description,
                    prefix,
                    str(element.line_number),
                    status=status,
                    medium=element.medium_type,
                    domain_address=format_domain_address(
                        element.medium_type, element.domain_address
                    ),
                )
            return None
        if kind == "function":
            function = self.find_function(element_id)
            if function is None:
                return None
            return ElementDetails(
                kind,
                element_id,
                "function",
                function.name,
                function.description,
                "",
                "",
                status=self.completion_status(kind, element_id),
                has_address=False,
            )
        if kind == "group_address":
            ga = self.get_group_address(element_id)
            if ga is None:
                return None
            return ElementDetails(
                kind,
                element_id,
                "group_address",
                ga.name,
                ga.description,
                "",
                ga.address,
                security=ga.security_mode or "Auto",
                forced_by=ga.forced_by,
                central=ga.central,
                datapoint_type=ga.datapoint_type or "",
            )
        if kind == "group_range":
            found = _find_range(self.get_group_range_tree(), element_id, 0)
            if found is None:
                return None
            node, depth = found
            security = node.security_mode or "Auto"
            if not node.address:  # Free style: groups without a number
                return ElementDetails(
                    kind,
                    element_id,
                    "group_range",
                    node.name,
                    node.description,
                    "",
                    f"{node.range_start}\u2013{node.range_end}",
                    editable=False,
                    security=security,
                    forced_by=node.forced_by,
                )
            prefix, _, number = node.address.rpartition("/")
            return ElementDetails(
                kind,
                element_id,
                "middle_group" if depth else "main_group",
                node.name,
                node.description,
                f"{prefix}/" if prefix else "",
                number,
                security=security,
                forced_by=node.forced_by,
            )
        return None

    def rename_element(self, kind: str, element_id: int, name: str) -> None:
        details = self.element_details(kind, element_id)
        if details is None or details.name == name:
            return
        if kind in ("area", "backbone"):
            self.rename_area(element_id, details.name, name)
        elif kind == "line":
            self.rename_line(element_id, details.name, name)
        elif kind == "segment":
            self.rename_segment(element_id, name)
        elif kind == "group_range":
            self.rename_group_range(element_id, name)
        elif kind == "group_address":
            self.rename_group_address(element_id, name)
        elif kind == "function" and name:
            self.rename_function(element_id, name)

    def set_element_description(
        self, kind: str, element_id: int, description: str
    ) -> None:
        if kind in ("area", "line", "backbone"):
            self.set_topology_description(_stored_kind(kind), element_id, description)
            return
        details = self.element_details(kind, element_id)
        if self._pid is None or details is None or details.description == description:
            return
        if kind == "segment":
            self._svc.set_topology_description(self._pid, kind, element_id, description)
        elif kind == "function":
            self._svc.set_function_description(self._pid, element_id, description)
        else:
            self._svc.set_group_description(self._pid, kind, element_id, description)
        self._bump(structural=False)

    def set_element_medium(
        self, kind: str, element_id: int, medium_type: str
    ) -> str | None:
        """Change the medium of a line or a segment, of an area's main line (the backbone's
        for the backbone); the reason it cannot be, or None."""
        try:
            if kind in ("area", "backbone"):
                main = self._main_line(element_id)
                if main is not None:
                    self.set_line_medium(main.id, medium_type)
            elif kind == "line":
                self.set_line_medium(element_id, medium_type)
            elif kind == "segment":
                self.set_segment_medium(element_id, medium_type)
        except MediumError as error:
            return medium_error_text(error)
        return None

    def set_element_address(self, kind: str, element_id: int, text: str) -> str | None:
        """Give the element another address; the reason it cannot be, or None."""
        if kind in ("area", "line"):
            return self.set_topology_address(kind, element_id, text)
        details = self.element_details(kind, element_id)
        if self._pid is None or details is None or details.address == text.strip():
            return None
        if kind == "group_address":
            ok = self.set_group_address_address(element_id, text.strip())
            return None if ok else S.GA_RENUMBER_FAILED
        middle = details.title_kind == "middle_group"
        invalid = (
            S.GROUP_MIDDLE_NUMBER_INVALID if middle else S.GROUP_MAIN_NUMBER_INVALID
        )
        try:
            number = int(text.strip())
        except ValueError:
            return invalid
        if not 0 <= number <= (7 if middle else 31):
            return invalid
        try:
            self._svc.set_group_range_number(self._pid, element_id, number)
        except ValueError:
            return S.TOPOLOGY_ADDRESS_IN_USE.format(address=f"{details.prefix}{number}")
        self._bump()
        return None

    def set_topology_description(
        self, kind: str, element_id: int, description: str
    ) -> None:
        element = self.find_topology(kind, element_id)
        if self._pid is None or element is None or element.description == description:
            return
        self._svc.set_topology_description(self._pid, kind, element_id, description)
        self._bump(structural=False)

    def set_topology_address(self, kind: str, element_id: int, text: str) -> str | None:
        """Give an area or a line another address (its devices' addresses change with it); the
        reason it cannot be, or None."""
        if self._pid is None:
            return None
        try:
            address = int(text.strip())
        except ValueError:
            return S.TOPOLOGY_ADDRESS_INVALID
        if not 0 <= address <= 15:
            return S.TOPOLOGY_ADDRESS_INVALID
        element = self.find_topology(kind, element_id)
        current = (
            None
            if element is None
            else element.area_number
            if isinstance(element, _Area)
            else element.line_number
        )
        if current is None or current == address:
            return None
        try:
            self._svc.set_topology_address(self._pid, kind, element_id, address)
        except ValueError:
            return S.TOPOLOGY_ADDRESS_IN_USE.format(address=address)
        self._bump()
        return None

    @property
    def comment_target(self) -> tuple[str, int] | None:
        """``(kind, id)`` of the element selected last in a tree, whose comment the Comment panel
        shows; None when nothing is selected."""
        return self._comment_target

    def select_comment_target(self, kind: str, element_id: int) -> None:
        """An element without its own details panel (area, line, group range, group address,
        function) was selected: the Comment panel shows its comment."""
        self._comment_target = (kind, element_id)

    def comment(self, kind: str, element_id: int, field: str = "comment") -> str | None:
        """An element's comment (RTF as stored) or a device's installation hints (``field``
        "installation_hints"); None when the element does not exist (any more)."""
        if self._pid is None:
            return None
        key = (kind, element_id, field)
        cached = self._comment_cache.get(key)
        if cached is not None and cached[0] == self._version:
            return cached[1]
        value = self._svc.comment(self._pid, kind, element_id, field)
        self._comment_cache[key] = (self._version, value)
        return value

    def set_comment(
        self, kind: str, element_id: int, value: str, field: str = "comment"
    ) -> None:
        """Store an element's comment or a device's installation hints (RTF); undoable."""
        if self._pid is None or self.comment(kind, element_id, field) in (None, value):
            return
        self._svc.set_comment(self._pid, kind, element_id, value, field)
        self._bump(structural=False)

    def completion_status(self, kind: str, element_id: int) -> str | None:
        """The "Status" of the project (``kind`` "project"), an area, a line, a device (its
        database id), a space or a function; None when it does not exist (any more). The
        backbone's is area 0's."""
        if self._pid is None:
            return None
        kind = _stored_kind(kind)
        key = (kind, element_id)
        cached = self._status_cache.get(key)
        if cached is not None and cached[0] == self._version:
            return cached[1]
        value = self._svc.completion_status(self._pid, kind, element_id)
        self._status_cache[key] = (self._version, value)
        return value

    def set_completion_status(self, kind: str, element_id: int, status: str) -> None:
        """Set an element's "Status" (one of ``COMPLETION_STATUSES``); undoable."""
        kind = _stored_kind(kind)
        if self._pid is None or self.completion_status(kind, element_id) in (
            None,
            status,
        ):
            return
        self._svc.set_completion_status(self._pid, kind, element_id, status)
        self._bump(structural=False)

    def find_space(self, space_id: int) -> "SpaceInfo | None":
        """The space ``space_id`` of the building tree."""

        def search(spaces: "list[SpaceInfo]") -> "SpaceInfo | None":
            for space in spaces:
                if space.id == space_id:
                    return space
                found = search(space.children)
                if found is not None:
                    return found
            return None

        return search(self.get_space_tree())

    def find_function(self, function_id: int) -> "FunctionInfo | None":
        """The building function ``function_id``."""

        def search(spaces: "list[SpaceInfo]") -> "FunctionInfo | None":
            for space in spaces:
                for function in space.functions:
                    if function.id == function_id:
                        return function
                found = search(space.children)
                if found is not None:
                    return found
            return None

        return search(self.get_space_tree())

    def set_space_number(self, space_id: int, number: str) -> None:
        if self._pid is None:
            return
        self._svc.set_space_number(self._pid, space_id, number)
        self._bump(structural=False)

    def set_space_description(self, space_id: int, description: str) -> None:
        if self._pid is None:
            return
        self._svc.set_space_description(self._pid, space_id, description)
        self._bump(structural=False)

    def set_space_usage(self, space_id: int, usage: str) -> None:
        """Set a room's use: a SpaceUsage id of the master data, "" for none."""
        space = self.find_space(space_id)
        if self._pid is None or space is None or space.usage == usage:
            return
        self._svc.set_space_usage(
            self._pid, space_id, usage, usage_label(usage) if usage else ""
        )
        self._bump(structural=False)

    def current_line(self, space_id: int) -> "tuple[int, SpaceInfo] | None":
        """The Current Line of a space: the line's id and the space that sets it, the space itself
        or the nearest one above it that has one; None when none has."""

        def path(spaces: "list[SpaceInfo]") -> "list[SpaceInfo] | None":
            for space in spaces:
                if space.id == space_id:
                    return [space]
                below = path(space.children)
                if below is not None:
                    return [space, *below]
            return None

        for space in reversed(path(self.get_space_tree()) or []):
            if space.default_line_id is not None:
                return space.default_line_id, space
        return None

    def line_choices(self) -> list[tuple[int, str]]:
        """Every line of the topology as its id and its text ("1.1 Name"), in address order; a
        backbone or main line without a name is called after its medium ("1.0 TP main line")."""
        return [
            (
                line.id,
                f"{area.area_number}.{line.line_number} {line_display_name(area.area_number, line)}".rstrip(),
            )
            for area in sorted(self.get_areas(), key=lambda a: a.area_number)
            for line in sorted(self.get_lines(area.id), key=lambda ln: ln.line_number)
        ]

    def set_space_default_line(self, space_id: int, line_id: int | None) -> None:
        """Set a space's own Current Line, or clear it with ``line_id`` None; undoable."""
        space = self.find_space(space_id)
        if self._pid is None or space is None or space.default_line_id == line_id:
            return
        self._svc.set_space_default_line(self._pid, space_id, line_id)
        self._bump(structural=False)

    def set_space_type(self, space_id: int, space_type: str) -> None:
        if self._pid is None:
            return
        self._svc.set_space_type(self._pid, space_id, space_type)
        self._bump(structural=False)

    def move_space(self, space_id: int, new_parent_id: int | None) -> None:
        if self._pid is None:
            return
        self._svc.move_space(self._pid, space_id, new_parent_id)
        self._bump(structural=False)

    def remove_space(self, space_id: int) -> None:
        if self._pid is None:
            return
        self._svc.remove_space(self._pid, space_id)
        self._bump(structural=False)

    def set_device_space(self, device_id: int, space_id: int | None) -> None:
        if self._pid is None:
            return
        self._svc.set_device_space(self._pid, device_id, space_id)
        self._bump(structural=False)

    def undo(self) -> bool:
        if self._pid is None:
            return False
        peek = self._svc.peek_undo(self._pid)
        result = self._svc.undo(self._pid)
        if result:
            self._refresh_after_history(peek, undo=True)
        return result

    def redo(self) -> bool:
        if self._pid is None:
            return False
        peek = self._svc.peek_redo(self._pid)
        result = self._svc.redo(self._pid)
        if result:
            self._refresh_after_history(peek, undo=False)
        return result

    def _refresh_after_history(
        self, peek: "tuple[str, dict[str, Any]] | None", *, undo: bool
    ) -> None:
        """Refresh views after an undo/redo. A plain parameter change is applied *in place* on the
        affected device (like a live edit); a composite param+com-object change rebuilds only that one
        device (its com-object rows changed in the DB) — both instant, instead of discarding and
        re-parsing every device's dynamic UI. Anything else falls back to a full structural rebuild."""
        if peek is not None and self._devices_cache is not None:
            event_type, data = peek
            if event_type == "SetParameter":
                value = data.get("old_value") if undo else data.get("value")
                device = self.find_device_by_node_id(int(data["device_id"]))
                if device is not None:
                    device.apply_param_values(
                        {str(data["ref_id"]): None if value is None else str(value)}
                    )
                    self._bump(structural=False)
                    return
            elif event_type in (
                "SetDeviceAdditionalAddresses",
                "SetComment",
                "SetCompletionStatus",
                "SetTopologyDescription",
                "SetGroupDescription",
            ):
                # Read from the project on every revision; no device structure changes.
                self._bump(structural=False)
                return
            elif event_type in (
                "Composite",
                "SyncDeviceComObjects",
            ) and not _changes_structure(data):
                node_id = _history_device_id(data)
                if node_id is not None:
                    self._refresh_device(node_id)  # rebuild only this device (not all)
                    self._bump(structural=False)
                    return
        self._bump(structural=True)

    @io_guarded(lambda: False)
    def can_undo(self) -> bool:
        return self._pid is not None and self._svc.can_undo(self._pid)

    @io_guarded(lambda: False)
    def can_redo(self) -> bool:
        return self._pid is not None and self._svc.can_redo(self._pid)

    @property
    @io_guarded(lambda: 0)
    def cursor(self) -> int:
        return self._svc.cursor(self._pid) if self._pid is not None else 0

    def jump_to(self, event_id: int) -> None:
        if self._pid is None:
            return
        self._svc.jump_to(self._pid, event_id)
        self._bump()

    @io_guarded(list)
    @revision_cached
    def history(self) -> list[HistoryEntry]:
        # The History panel reads this every frame. Cache it by revision like the other per-frame
        # panel reads (see get_device_info): an uncached 60 Hz SELECT contends with a background
        # writer's connection — during an export it blocks the render loop (the "not responding"
        # stall) and can raise "database is locked" straight out of the frame.
        if self._pid is None:
            return []
        return [
            HistoryEntry(
                id=entry.id,
                display_text=_history_label(entry.event_type, entry.data),
                reverted=entry.reverted,
            )
            for entry in self._svc.history(self._pid)
        ]

    def _history_key(self) -> frozenset[tuple[int, str, str]]:
        """Signature of the currently-effective (non-reverted) events.

        Includes the event type and a repr of its data, not just the id: a store that deletes
        events on branch (undo + new command) can reuse an id, which an id-only set would miss.

        Commissioning bookkeeping (recorded right after a successful program) is excluded: it
        never affects the generated image, so it must not count as "edited" and lock out the
        read-only pre-flight self-test on a device that was only re-programmed."""
        if self._pid is None:
            return frozenset()
        return frozenset(
            (entry.id, entry.event_type, repr(entry.data))
            for entry in self._svc.history(self._pid)
            if not entry.reverted and entry.event_type != "SetDeviceCommissioning"
        )

    @io_guarded(lambda: False)
    def edited_since_open(self) -> bool:
        """Whether the effective edit set changed since the project was opened.

        Opening a previously-edited project is not "modified"; only edits (or undo/redo) made in
        this session since the open count. Used to gate the read-only pre-flight self-test."""
        return self._history_key() != self._history_baseline
