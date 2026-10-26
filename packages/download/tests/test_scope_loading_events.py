"""A partial download keeps a load state machine's segment and task events only where it keeps the
Load of that part (issue #98: a Parameters download of a product procedure without AppliesTo sent
the address table's AbsSegment without its Load, and the device answered with the error state).

A loaded program is not allocated again (issue #150: the Theben DM 4-2 T answered the second
allocation of its application segment with the error state): its EEPROM segments become memory
writes, its RAM segments, task segment and task controls are left out."""

from __future__ import annotations

import pytest

from xknxeditor.download.scope import DownloadScope, control_in_scope, scoped_controls
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
    LdCtrlControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_max_length_t import LdCtrlMaxLength
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
    LdCtrlSetControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl1_t import LdCtrlTaskCtrl1
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl2_t import LdCtrlTaskCtrl2
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ptr_t import LdCtrlTaskPtr
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

# Index -> object type of a BIM M112 mask (address table, association table, application).
_TYPES = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
_PARTIAL = (
    DownloadScope.PARAMETERS,
    DownloadScope.GROUP_COMMUNICATION,
    DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
)
_EEPROM = 3
_RAM = 2


def _segment(
    lsm: int, address: int, size: int, mem_type: int = _EEPROM
) -> LdCtrlAbsSegment:
    return LdCtrlAbsSegment(
        lsm_idx=lsm,
        seg_type=0,
        address=address,
        size=size,
        access=255,
        mem_type=mem_type,
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

# The procedure of issue #150 (Theben DM 4-2 T, MV-0705): a RAM segment, task control 1, and
# an LSM 5 task segment and load after the restart.
_DM_PROCEDURE = [
    LdCtrlConnect(),
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
    _segment(3, 7168, 1310, _RAM),
    _segment(3, 17406, 1118),
    _segment(3, 18640, 1581),
    LdCtrlTaskSegment(lsm_idx=3, address=18514),
    LdCtrlTaskCtrl1(lsm_idx=3, address=18425, count=1),
    LdCtrlLoadCompleted(lsm_idx=3),
    LdCtrlRestart(),
    LdCtrlTaskSegment(lsm_idx=5, address=17406),
    LdCtrlLoad(lsm_idx=5),
    LdCtrlDisconnect(),
]

_NEEDS_LOADING = (LdCtrlAbsSegment, LdCtrlTaskSegment)


def _resolved(
    scope: DownloadScope, procedure: list[object] | None = None
) -> list[object]:
    return scoped_controls(
        _PROCEDURE if procedure is None else procedure, scope, _TYPES
    )


def _describe(control: object) -> str:
    name = type(control).__name__.removeprefix("LdCtrl")
    if isinstance(control, LdCtrlWriteMem):
        return f"{name}({control.address},{control.size},verify={control.verify})"
    index = getattr(control, "lsm_idx", None)
    return name if index is None else f"{name}({index})"


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
    assert [c.lsm_idx for c in resolved if isinstance(c, LdCtrlAbsSegment)] == [1, 2]


@pytest.mark.parametrize("scope", _PARTIAL)
def test_a_loaded_application_is_written_not_allocated(scope: DownloadScope) -> None:
    """The application keeps its Load and LoadCompleted; its EEPROM segments become verified
    memory writes, its RAM segment, task segment and task control are left out. After the
    restart the LSM 5 controls stay (the runner leaves out what this mask cannot take)."""
    application = [
        _describe(c)
        for c in _resolved(scope, _DM_PROCEDURE)
        if getattr(c, "lsm_idx", 3) in (3, 5)
        and not isinstance(c, LdCtrlConnect | LdCtrlDisconnect)
    ]
    assert application == [
        "Load(3)",
        "WriteMem(17406,1118,verify=True)",
        "WriteMem(18640,1581,verify=True)",
        "LoadCompleted(3)",
        "Restart",
        "TaskSegment(5)",
        "Load(5)",
    ]


@pytest.mark.parametrize("scope", [DownloadScope.FULL, DownloadScope.APPLICATION])
def test_a_full_download_allocates_the_application(scope: DownloadScope) -> None:
    assert _resolved(scope, _DM_PROCEDURE) == _DM_PROCEDURE


def test_the_written_segments_keep_out_of_automatic_filtering() -> None:
    writes = [
        c
        for c in _resolved(DownloadScope.PARAMETERS, _DM_PROCEDURE)
        if isinstance(c, LdCtrlWriteMem)
    ]
    assert writes
    assert all(c.applies_to is LdCtrlProcType.ALL for c in writes)


def test_segment_writes_switched_off_leave_the_segments_out() -> None:
    procedure = [
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_SEGMENT_WRITE, value=False
        ),
        LdCtrlLoad(lsm_idx=3),
        _segment(3, 17406, 1118),
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_SEGMENT_WRITE, value=True
        ),
        _segment(3, 18640, 1581),
        LdCtrlLoadCompleted(lsm_idx=3),
    ]
    writes = [
        _describe(c)
        for c in _resolved(DownloadScope.PARAMETERS, procedure)
        if isinstance(c, LdCtrlWriteMem)
    ]
    assert writes == ["WriteMem(18640,1581,verify=True)"]


def test_a_second_program_is_written_only_by_a_parameters_download() -> None:
    procedure = [
        LdCtrlLoad(lsm_idx=4),
        _segment(4, 0x6000, 64),
        LdCtrlLoadCompleted(lsm_idx=4),
    ]

    def resolved(scope: DownloadScope, second: bool) -> list[str]:
        return [
            _describe(c)
            for c in scoped_controls(
                procedure, scope, _TYPES, has_application_program2=second
            )
        ]

    assert resolved(DownloadScope.PARAMETERS, True) == [
        "Load(4)",
        "WriteMem(24576,64,verify=True)",
        "LoadCompleted(4)",
    ]
    assert resolved(DownloadScope.GROUP_COMMUNICATION, True) == []
    assert resolved(DownloadScope.PARAMETERS, False) == []


def test_an_explicit_segment_is_kept_as_it_is() -> None:
    segment = _segment(3, 17406, 1118)
    segment.applies_to = LdCtrlProcType.PAR
    assert scoped_controls([segment], DownloadScope.PARAMETERS, _TYPES) == [segment]


@pytest.mark.parametrize(
    ("control", "parameters", "group"),
    [
        (_segment(1, 0x4000, 511), False, True),
        (LdCtrlTaskSegment(lsm_idx=2, address=0x41FF), False, True),
        # Task controls set up a task: a partial download leaves them out, also for a table.
        (LdCtrlTaskPtr(lsm_idx=1, init_ptr=0, save_ptr=0, serial_ptr=0), False, False),
        (LdCtrlTaskCtrl1(lsm_idx=3, address=0x47F9, count=1), False, False),
        (
            LdCtrlTaskCtrl2(lsm_idx=3, callback=0, address=0, seg0=0, seg1=0),
            False,
            False,
        ),
        (LdCtrlTaskSegment(lsm_idx=3, address=0x4852), False, False),
        # Written instead of allocated (scoped_controls).
        (_segment(3, 0x0700, 134), False, False),
        (LdCtrlRelSegment(lsm_idx=3, size=16, mode=0, fill=0), True, True),
        (LdCtrlMaxLength(lsm_idx=2, size=10), True, True),
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
