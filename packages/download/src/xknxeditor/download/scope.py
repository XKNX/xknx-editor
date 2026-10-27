"""Classify load controls by interface-object type without changing wire addressing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_base_t import LdCtrlBase
from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
    LdCtrlControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_rel_mem_t import (
    LdCtrlLoadImageRelMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
    LdCtrlSetControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl1_t import LdCtrlTaskCtrl1
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl2_t import LdCtrlTaskCtrl2
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ptr_t import LdCtrlTaskPtr
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

from .errors import UnsupportedProcedureError

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.hawk_configuration_data_t import (
        HawkConfigurationData,
    )
    from xknxeditor.namespaces.intermediate.master_data_t import MasterData

GROUP_COMMUNICATION_OBJECTS = frozenset({1, 2, 9})
_APPLICATION_OBJECT = 3
_SECOND_PROGRAM_OBJECT = 4
# Load events a load state machine accepts only while loading: they run where the Load of their
# loadable part runs, or the device answers them with the error state.
_LOADING_EVENTS = (
    LdCtrlLoad,
    LdCtrlLoadCompleted,
    LdCtrlRelSegment,
    LdCtrlAbsSegment,
    LdCtrlTaskSegment,
)
# A partial download sets up no task: the device keeps the one its last full download set.
_TASK_SETUP = (LdCtrlTaskPtr, LdCtrlTaskCtrl1, LdCtrlTaskCtrl2)
# Memory type of an EEPROM segment (both low bits set).
_EEPROM = 0x03
# Writes into a loadable part's relative memory: a group communication table's belong to its
# download.
_RELATIVE_WRITES = (LdCtrlWriteRelMem, LdCtrlLoadImageRelMem)
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


_PARTIAL_SCOPES = frozenset(
    {
        DownloadScope.PARAMETERS,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    }
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

    Returns ``None`` for an index the mask does not list (an application object, or any
    object of a mask without interface object metadata, such as the System 300 masks) so
    the scope rules treat it as non-system and keep the control as it is. The device
    object at index zero is the only fixed identity used without metadata.
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
    # An index the mask does not list has no type: a partial download keeps the control
    # as it is (the automatic transformation needs the type to change it).
    return object_types.get(index)


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
    if isinstance(control, _TASK_SETUP):
        return False
    if not isinstance(
        control, (LdCtrlUnload, LdCtrlWriteProp, *_LOADING_EVENTS, *_RELATIVE_WRITES)
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
    if isinstance(control, _LOADING_EVENTS):
        if target in GROUP_COMMUNICATION_OBJECTS:
            return group
        if target == _SECOND_PROGRAM_OBJECT and not has_application_program2:
            return False
        if isinstance(control, LdCtrlRelSegment):
            return True
        if target in (_APPLICATION_OBJECT, _SECOND_PROGRAM_OBJECT) and isinstance(
            control, (LdCtrlAbsSegment, LdCtrlTaskSegment)
        ):
            # A loaded program is not allocated again: its segments become memory writes
            # (see scoped_controls), its task stays as it is.
            return False
        if target == _SECOND_PROGRAM_OBJECT:
            return parameters
    if isinstance(control, _RELATIVE_WRITES) and target in GROUP_COMMUNICATION_OBJECTS:
        return group
    if isinstance(control, LdCtrlWriteProp) and (
        (target in (1, 2) and control.prop_id == 23)
        or (target == 9 and control.prop_id in (51, 52))
    ):
        return group
    return True


def scoped_controls(
    controls: Sequence[object],
    scope: DownloadScope,
    object_types: Mapping[int, int] | None = None,
    *,
    has_application_program2: bool = False,
) -> list[object]:
    """The controls a download of ``scope`` runs, in procedure order.

    As :func:`control_in_scope`, except that a partial download writes a loaded program's
    EEPROM segments instead of allocating them again: an automatic ``AbsSegment`` of the
    application (or of the second program, in a parameters download) becomes a verified
    memory write of the same range. A segment of another memory type (RAM) is left out, as
    are all of them while the procedure has switched segment writes off.
    """
    result: list[object] = []
    segment_write = True
    for control in controls:
        if (
            isinstance(control, LdCtrlSetControlVariable)
            and control.name is LdCtrlControlVariable.ENABLE_SEGMENT_WRITE
            and control.applies_to is LdCtrlProcType.AUTO
            and control_applies(control, scope)
        ):
            segment_write = control.value
        if control_in_scope(
            control,
            scope,
            object_types,
            has_application_program2=has_application_program2,
        ):
            result.append(control)
        elif segment_write and _program_segment(
            control, scope, object_types, has_application_program2
        ):
            assert isinstance(control, LdCtrlAbsSegment)
            if control.mem_type & _EEPROM == _EEPROM:
                result.append(
                    LdCtrlWriteMem(
                        address=control.address,
                        size=control.size,
                        verify=True,
                        applies_to=LdCtrlProcType.ALL,
                    )
                )
    return result


def _program_segment(
    control: object,
    scope: DownloadScope,
    object_types: Mapping[int, int] | None,
    has_application_program2: bool,
) -> bool:
    """Whether ``control`` is an automatic ``AbsSegment`` of a program that a partial
    download of ``scope`` writes (see :func:`scoped_controls`)."""
    if (
        not isinstance(control, LdCtrlAbsSegment)
        or control.applies_to is not LdCtrlProcType.AUTO
        or not control_applies(control, scope)
        or scope not in _PARTIAL_SCOPES
    ):
        return False
    target = target_object_type(control, object_types or {})
    if target == _APPLICATION_OBJECT:
        return True
    return (
        target == _SECOND_PROGRAM_OBJECT
        and has_application_program2
        and scope
        in (DownloadScope.PARAMETERS, DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION)
    )


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
