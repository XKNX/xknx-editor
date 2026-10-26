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


def materialize_group_communication_controls(
    image: DownloadImage,
    controls: Sequence[object],
    object_types: Mapping[int, int],
    scope: DownloadScope = DownloadScope.FULL,
) -> list[object]:
    """Resize table allocations/writes, rejecting incomplete plans before bus access.

    Replace the sizes on existing controls, preserving their transaction order.
    Do not append controls after completion/restart. Copies
    keep the master/application templates reusable for another image or scope.
    """
    if scope in (DownloadScope.UNLOAD, DownloadScope.UNLOAD_ALL):
        return list(controls)
    selected = scoped_controls(controls, scope, object_types)
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
