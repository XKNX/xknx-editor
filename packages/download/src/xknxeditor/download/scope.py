"""Classify load controls by interface-object type without changing wire addressing."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.ld_ctrl_base_t import LdCtrlBase
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

from .errors import UnsupportedProcedureError

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.hawk_configuration_data_t import (
        HawkConfigurationData,
    )
    from xknxeditor.namespaces.intermediate.master_data_t import MasterData

GROUP_COMMUNICATION_OBJECTS = frozenset({1, 2, 9})
_DEVICE_OBJECT = 0
_ROUTER_OBJECT = 6


class DownloadScope(Enum):
    """The loadable parts to program; unload scopes select the Unload procedure."""

    FULL = "full"
    PARAMETERS = "par"
    GROUP_COMMUNICATION = "grp"
    PARAMETERS_AND_GROUP_COMMUNICATION = "par,grp"
    APPLICATION = "ap1"
    UNLOAD = "unload"
    UNLOAD_ALL = "unload_all"
    INDIVIDUAL_ADDRESS = "ia"
    OVERWRITE_INDIVIDUAL_ADDRESS = "ia_overwrite"

    @property
    def individual_address_only(self) -> bool:
        """Only the individual address is downloaded (see ``download_individual_address``)."""
        return self in (
            DownloadScope.INDIVIDUAL_ADDRESS,
            DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS,
        )


def mask_configuration(
    master_data: MasterData | None, mask_version_id: str
) -> HawkConfigurationData | None:
    """Use the current mask configuration for both procedures and object identity."""
    if master_data is not None and master_data.mask_versions is not None:
        for mask in master_data.mask_versions.mask_version:
            if mask.id == mask_version_id:
                for configuration in mask.hawk_configuration_data:
                    if configuration.legacy_version is None:
                        return configuration
    return None


def mask_object_types(
    master_data: MasterData | None, mask_version_id: str
) -> dict[int, int]:
    """Read the mask's explicit index-to-type mapping; never guess conventional indices."""
    configuration = mask_configuration(master_data, mask_version_id)
    result: dict[int, int] = {}
    if configuration is not None and configuration.interface_objects is not None:
        for item in configuration.interface_objects.interface_object:
            if item.index is not None:
                if item.index in result and result[item.index] != item.object_type:
                    raise UnsupportedProcedureError(
                        f"conflicting object types for index {item.index}"
                    )
                result[item.index] = item.object_type
    return result


def target_object_type(control: object, object_types: Mapping[int, int]) -> int | None:
    """Resolve classification only; the original control still chooses the wire service.

    An indexed target with missing metadata is unsafe to classify and fails closed.
    The device object at index zero is the only fixed identity used without metadata.
    """
    if type(control).__name__ == "LdCtrlClearLcfilterTable":
        return _ROUTER_OBJECT
    # Match the execution resolver's precedence (obj_idx, obj_type, lsm_idx).
    index = getattr(control, "obj_idx", None)
    if index is None:
        obj_type = getattr(control, "obj_type", None)
        if obj_type is not None:
            return obj_type
        index = getattr(control, "lsm_idx", None)
    if index is None:
        return None
    if index == 0:
        return _DEVICE_OBJECT
    if index not in object_types:
        raise UnsupportedProcedureError(
            f"cannot resolve interface object index {index} to a type for "
            f"{type(control).__name__}; mask InterfaceObjects metadata is required"
        )
    return object_types[index]


def control_in_scope(
    control: object,
    scope: DownloadScope,
    object_types: Mapping[int, int] | None = None,
    *,
    has_application_program2: bool = False,
) -> bool:
    """Apply each automatic control's partial-load rule; explicit flags take priority."""
    if not control_applies(control, scope):
        return False
    if scope in (
        DownloadScope.FULL,
        DownloadScope.APPLICATION,
        DownloadScope.UNLOAD,
        DownloadScope.UNLOAD_ALL,
    ):
        return True
    if isinstance(control, LdCtrlBase) and control.applies_to.value != "auto":
        return True
    # see .references/ets_map.md
    if not isinstance(
        control,
        (
            LdCtrlUnload,
            LdCtrlLoad,
            LdCtrlLoadCompleted,
            LdCtrlRelSegment,
            LdCtrlWriteProp,
        ),
    ):
        return True
    target = target_object_type(control, object_types or {})
    group = scope in (
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    )
    parameters = scope in (
        DownloadScope.PARAMETERS,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    )
    if isinstance(control, LdCtrlUnload):
        if target in (1, 2):
            return group
        return target not in (3, 4, 9)
    if isinstance(control, (LdCtrlLoad, LdCtrlLoadCompleted, LdCtrlRelSegment)):
        if target in GROUP_COMMUNICATION_OBJECTS:
            return group
        if target == 4:
            return has_application_program2 and (
                parameters or isinstance(control, LdCtrlRelSegment)
            )
    if isinstance(control, LdCtrlWriteProp) and (
        (target in (1, 2) and control.prop_id == 23)
        or (target == 9 and control.prop_id in (51, 52))
    ):
        return group
    return True


def control_applies(control: object, scope: DownloadScope) -> bool:
    """Evaluate AppliesTo flags before merge/materialization."""
    value = getattr(control, "applies_to", "all")
    value = getattr(value, "value", value)
    if value in ("all", "auto") or scope in (
        DownloadScope.UNLOAD,
        DownloadScope.UNLOAD_ALL,
    ):
        return True
    full = scope in (DownloadScope.FULL, DownloadScope.APPLICATION)
    flags = str(value).split(",")
    if full:
        return "full" in flags
    if value == "par,grp":
        return scope is DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION
    return bool(set(scope.value.split(",")) & set(flags))
