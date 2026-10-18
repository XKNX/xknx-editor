"""A partial download keeps a load state machine's segment and task events only where it keeps the
Load of that part (issue #98: a Parameters download of a product procedure without AppliesTo sent
the address table's AbsSegment without its Load, and the device answered with the error state)."""

from __future__ import annotations

import pytest

from xknxeditor.download.scope import DownloadScope, control_in_scope
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_max_length_t import LdCtrlMaxLength
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ptr_t import LdCtrlTaskPtr
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

# Index -> object type of a BIM M112 mask (address table, association table, application).
_TYPES = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}


def _segment(lsm: int, address: int, size: int) -> LdCtrlAbsSegment:
    return LdCtrlAbsSegment(
        lsm_idx=lsm,
        seg_type=0,
        address=address,
        size=size,
        access=255,
        mem_type=3,
        seg_flags=128,
    )


# The product procedure of the issue (Theben theRonda S360 KNX, MV-0705): one unscoped
# procedure, no AppliesTo anywhere.
_PROCEDURE = [
    LdCtrlConnect(),
    LdCtrlCompareProp(
        obj_idx=0, prop_id=78, inline_data=bytes.fromhex("0000003F010000000000")
    ),
    LdCtrlUnload(lsm_idx=1),
    LdCtrlUnload(lsm_idx=2),
    LdCtrlUnload(lsm_idx=3),
    LdCtrlLoad(lsm_idx=1),
    _segment(1, 16384, 511),
    LdCtrlTaskSegment(lsm_idx=1, address=16384),
    LdCtrlLoadCompleted(lsm_idx=1),
    LdCtrlLoad(lsm_idx=2),
    _segment(2, 16895, 511),
    LdCtrlTaskSegment(lsm_idx=2, address=16895),
    LdCtrlLoadCompleted(lsm_idx=2),
    LdCtrlLoad(lsm_idx=3),
    _segment(3, 1792, 134),
    _segment(3, 17406, 406),
    LdCtrlTaskSegment(lsm_idx=3, address=17406),
    LdCtrlLoadCompleted(lsm_idx=3),
    LdCtrlRestart(),
    LdCtrlDisconnect(),
]

_NEEDS_LOADING = (LdCtrlAbsSegment, LdCtrlTaskSegment, LdCtrlTaskPtr, LdCtrlMaxLength)


def _resolved(scope: DownloadScope) -> list[object]:
    return [c for c in _PROCEDURE if control_in_scope(c, scope, _TYPES)]


@pytest.mark.parametrize("scope", list(DownloadScope)[:5])
def test_every_segment_event_follows_its_load(scope: DownloadScope) -> None:
    loading: set[int | None] = set()
    for control in _resolved(scope):
        index = getattr(control, "lsm_idx", None)
        if isinstance(control, LdCtrlLoad):
            loading.add(index)
        elif isinstance(control, LdCtrlLoadCompleted | LdCtrlUnload):
            loading.discard(index)
        elif isinstance(control, _NEEDS_LOADING):
            assert index in loading, (
                f"{type(control).__name__} for LSM {index} without its Load"
            )


def test_parameters_touch_only_the_application() -> None:
    lsms = {getattr(c, "lsm_idx", None) for c in _resolved(DownloadScope.PARAMETERS)}
    assert lsms - {None} == {3}


def test_group_addresses_keep_the_table_segments() -> None:
    resolved = _resolved(DownloadScope.GROUP_COMMUNICATION)
    assert [c.lsm_idx for c in resolved if isinstance(c, LdCtrlAbsSegment)] == [
        1,
        2,
        3,
        3,
    ]


@pytest.mark.parametrize(
    ("control", "parameters", "group"),
    [
        (_segment(1, 0x4000, 511), False, True),
        (LdCtrlTaskSegment(lsm_idx=2, address=0x41FF), False, True),
        (LdCtrlTaskPtr(lsm_idx=1, init_ptr=0, save_ptr=0, serial_ptr=0), False, True),
        (LdCtrlMaxLength(lsm_idx=2, size=10), False, True),
        (_segment(3, 0x0700, 134), True, True),
        # A relative write into a table belongs to the group communication download.
        (LdCtrlWriteRelMem(obj_idx=1, offset=0, size=4, verify=False), False, True),
        (LdCtrlWriteRelMem(obj_idx=3, offset=0, size=4, verify=False), True, True),
    ],
)
def test_classified_by_their_target(
    control: object, parameters: bool, group: bool
) -> None:
    assert control_in_scope(control, DownloadScope.PARAMETERS, _TYPES) is parameters
    assert control_in_scope(control, DownloadScope.GROUP_COMMUNICATION, _TYPES) is group
    assert control_in_scope(control, DownloadScope.FULL, _TYPES) is True
