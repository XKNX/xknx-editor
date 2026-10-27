"""Materialize existing System B table controls in their original load transaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

from .errors import ImageError, UnsupportedProcedureError
from .image import DownloadImage
from .scope import (
    GROUP_COMMUNICATION_OBJECTS,
    DownloadScope,
    control_in_scope,
    scoped_controls,
    target_object_type,
)
from .tables_system300 import (
    ADDRESS_TABLE,
    ASSOCIATION_TABLE,
    GROUP_OBJECT_REFERENCES,
    GROUP_OBJECT_TABLE,
)


def materialize_group_communication_controls(
    image: DownloadImage,
    controls: Sequence[object],
    object_types: Mapping[int, int],
    scope: DownloadScope = DownloadScope.FULL,
    *,
    property_tables: bool = False,
) -> list[object]:
    """Resize table allocations/writes, rejecting incomplete plans before bus access.

    Replace the sizes on existing controls, preserving their transaction order.
    Do not append controls after completion/restart. Copies
    keep the master/application templates reusable for another image or scope.
    With ``property_tables`` (System 300) the tables are written as properties instead:
    see :func:`_property_table_controls`.
    """
    if scope in (DownloadScope.UNLOAD, DownloadScope.UNLOAD_ALL):
        return list(controls)
    selected = scoped_controls(controls, scope, object_types)
    if property_tables:
        return _property_table_controls(image, selected, object_types, scope)
    tables = {
        segment.object_type
        for segment in image.relative_segments
        if segment.object_type in GROUP_COMMUNICATION_OBJECTS
        and control_in_scope(
            LdCtrlLoad(obj_type=segment.object_type), scope, object_types
        )
    }
    for control in selected:
        if isinstance(control, (LdCtrlRelSegment, LdCtrlWriteRelMem)):
            target = target_object_type(control, object_types)
            if target in GROUP_COMMUNICATION_OBJECTS:
                tables.add(target)
    # In a System B plan every touched table needs its complete transaction,
    # including one whose allocation/write was accidentally omitted altogether.
    if tables or 9 in object_types.values():
        for control in selected:
            if isinstance(control, (LdCtrlUnload, LdCtrlLoad, LdCtrlLoadCompleted)):
                target = target_object_type(control, object_types)
                if target in GROUP_COMMUNICATION_OBJECTS:
                    tables.add(target)
    if not tables:
        return selected

    # Per table: unloaded -> loading -> allocated -> written -> completed.
    phases: dict[int, str] = {}
    result: list[object] = []
    for position, control in enumerate(selected, 1):
        if isinstance(control, LdCtrlRestart) and any(
            p != "completed" for p in phases.values()
        ):
            raise UnsupportedProcedureError("restart before table load completion")
        if not isinstance(
            control,
            (
                LdCtrlUnload,
                LdCtrlLoad,
                LdCtrlRelSegment,
                LdCtrlWriteRelMem,
                LdCtrlLoadCompleted,
            ),
        ):
            result.append(control)
            continue
        target = target_object_type(control, object_types)
        if target not in tables:
            result.append(control)
            continue
        segment = image.relative_segment(target)
        if sum(s.object_type == target for s in image.relative_segments) > 1:
            raise ImageError(f"duplicate table images for object type {target}")
        if segment is None or not segment.data:
            raise ImageError(
                f"missing table image for object type {target} at control {position}"
            )
        if (
            getattr(control, "occurrence", 0) != 0
            or sum(t == target for t in object_types.values()) > 1
        ):
            raise UnsupportedProcedureError(
                f"ambiguous table occurrence for object type {target}"
            )
        if segment.mask is not None and (
            len(segment.mask) != len(segment.data) or not all(segment.mask)
        ):
            raise ImageError(f"incomplete table image for object type {target}")
        phase = phases.get(target)
        if isinstance(control, LdCtrlUnload):
            valid = phase in (None, "completed")
            next_phase = "unloaded"
        elif isinstance(control, LdCtrlLoad):
            valid = phase in (None, "unloaded", "completed")
            next_phase = "loading"
        elif isinstance(control, LdCtrlRelSegment):
            valid = phase in ("loading", "allocated")
            next_phase = "allocated"
            control = replace(control, size=len(segment.data))
        elif isinstance(control, LdCtrlWriteRelMem):
            valid = phase == "allocated"
            next_phase = "written"
            if control.offset != 0 or control.inline_data is not None:
                raise UnsupportedProcedureError(
                    f"table type {target} requires one image-backed write at offset 0"
                )
            control = replace(control, size=len(segment.data))
        else:
            valid = phase == "written"
            next_phase = "completed"
        if not valid:
            raise UnsupportedProcedureError(
                f"invalid table lifecycle for object type {target} at control {position}: "
                f"{type(control).__name__} after {phase or 'no load'}"
            )
        phases[target] = next_phase
        result.append(control)
    for target in tables:
        if phases.get(target) != "completed":
            raise UnsupportedProcedureError(
                f"incomplete table lifecycle for object type {target}: missing load/allocation/data/completion"
            )
    return result


_TABLE_PROPERTIES = frozenset(
    {ADDRESS_TABLE, ASSOCIATION_TABLE, GROUP_OBJECT_TABLE, GROUP_OBJECT_REFERENCES}
)
_LOADS_TABLES = frozenset(
    {
        DownloadScope.FULL,
        DownloadScope.APPLICATION,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    }
)
_LOADS_PARAMETERS = frozenset(
    {
        DownloadScope.FULL,
        DownloadScope.APPLICATION,
        DownloadScope.PARAMETERS,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    }
)


def _property_table_controls(
    image: DownloadImage,
    selected: Sequence[object],
    object_types: Mapping[int, int],
    scope: DownloadScope,
) -> list[object]:
    """The product's table writes of a System 300 device with the number of elements to
    write: the group addresses, the links, and the group objects (for ``PID_GRPOBJTABLE`` and
    for ``PID_EXT_GRPOBJREFERENCE``, which only a download with parameters writes). A table
    with nothing to write, or one the scope does not load, is not written."""
    tables = image.property_tables
    result: list[object] = []
    for control in selected:
        if isinstance(control, LdCtrlWriteProp) and control.start_element != 0:
            # The object's type; one addressed by an index the mask does not list is none.
            object_type = (
                control.obj_type
                if control.obj_idx is None
                else object_types.get(control.obj_idx)
            )
            target = (object_type, control.prop_id)
            if target in _TABLE_PROPERTIES:
                count = (
                    tables.element_count(target)  # type: ignore[arg-type]
                    if tables is not None and scope in _LOADS_TABLES
                    else 0
                )
                if target == GROUP_OBJECT_REFERENCES and scope not in _LOADS_PARAMETERS:
                    count = 0
                if count == 0:
                    continue
                control = replace(control, count=count)
        result.append(control)
    return result
