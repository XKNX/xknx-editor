"""Tests for download scope filtering (by target loadable part)."""

from __future__ import annotations

import pytest

from xknxeditor.download.scope import DownloadScope, control_in_scope
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem


def _ctrl(**attrs: int) -> object:
    return LdCtrlLoad(**attrs)


@pytest.mark.parametrize(
    ("control", "scope", "expected"),
    [
        # framing controls (no target object) always run
        (_ctrl(), DownloadScope.PARAMETERS, True),
        (_ctrl(), DownloadScope.GROUP_COMMUNICATION, True),
        # device object (0) is framing (fingerprint compare) -> always
        (_ctrl(lsm_idx=0), DownloadScope.PARAMETERS, True),
        (_ctrl(lsm_idx=0), DownloadScope.GROUP_COMMUNICATION, True),
        # group communication objects: address(1), association(2), group object(9)
        (_ctrl(obj_type=1), DownloadScope.GROUP_COMMUNICATION, True),
        (_ctrl(obj_type=2), DownloadScope.GROUP_COMMUNICATION, True),
        (_ctrl(lsm_idx=4), DownloadScope.GROUP_COMMUNICATION, True),
        (_ctrl(obj_type=1), DownloadScope.PARAMETERS, False),
        # application program object (3) holds parameters
        (_ctrl(obj_type=3), DownloadScope.PARAMETERS, True),
        (_ctrl(lsm_idx=3), DownloadScope.PARAMETERS, True),
        (_ctrl(obj_type=3), DownloadScope.GROUP_COMMUNICATION, True),
        # full download runs everything
        (_ctrl(obj_type=1), DownloadScope.FULL, True),
        (_ctrl(obj_type=3), DownloadScope.FULL, True),
        # ap1 includes all three tables, as in the wire capture.
        (_ctrl(obj_type=3), DownloadScope.APPLICATION, True),
        (_ctrl(lsm_idx=3), DownloadScope.APPLICATION, True),
        (_ctrl(obj_type=9), DownloadScope.APPLICATION, True),
        (_ctrl(obj_type=1), DownloadScope.APPLICATION, True),
        (_ctrl(obj_type=2), DownloadScope.APPLICATION, True),
        (_ctrl(lsm_idx=0), DownloadScope.APPLICATION, True),
        (_ctrl(), DownloadScope.APPLICATION, True),
    ],
)
def test_control_in_scope(
    control: object, scope: DownloadScope, expected: bool
) -> None:
    assert control_in_scope(control, scope, {0: 0, 1: 1, 2: 2, 3: 3, 4: 9}) is expected


def test_coupler_filter_table_scopes_together() -> None:
    # The coupler filter table (Router object type 6) and its clear must scope IDENTICALLY, so a
    # partial download never clears without rewriting or rewrites without clearing.
    from xknxeditor.namespaces.intermediate.ld_ctrl_clear_lcfilter_table_t import (
        LdCtrlClearLcfilterTable,
    )

    clear = LdCtrlClearLcfilterTable(
        use_function_prop=True, applies_to=LdCtrlProcType.FULL_GRP
    )
    write = LdCtrlWriteRelMem(
        obj_type=6, offset=0, size=1, verify=False, applies_to=LdCtrlProcType.FULL_GRP
    )
    for scope, expected in [
        (DownloadScope.FULL, True),
        (DownloadScope.GROUP_COMMUNICATION, True),
        (DownloadScope.PARAMETERS, False),
        (DownloadScope.APPLICATION, True),
    ]:
        assert control_in_scope(clear, scope) is expected
        assert control_in_scope(write, scope) is expected


def test_system_b_index_three_is_group_not_application() -> None:
    mapping = {0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: 4}
    for field in ("lsm_idx",):
        assert control_in_scope(
            _ctrl(**{field: 3}), DownloadScope.GROUP_COMMUNICATION, mapping
        )
        assert not control_in_scope(
            _ctrl(**{field: 3}), DownloadScope.PARAMETERS, mapping
        )
        assert control_in_scope(_ctrl(**{field: 4}), DownloadScope.PARAMETERS, mapping)


def test_unknown_index_fails_closed() -> None:
    from xknxeditor.download.errors import UnsupportedProcedureError

    with pytest.raises(UnsupportedProcedureError, match="InterfaceObjects"):
        control_in_scope(_ctrl(lsm_idx=3), DownloadScope.GROUP_COMMUNICATION)


def test_memory_cap_ladder_seeds_and_falls_back() -> None:
    from xknxeditor.download.download import _memory_cap_ladder

    # No learned limit: start at the APDU-derived default, then fall back.
    assert _memory_cap_ladder(None) == (None, 15, 12)
    # A learned limit starts there and only drops to still-smaller caps.
    assert _memory_cap_ladder(15) == (15, 12)
    assert _memory_cap_ladder(12) == (12,)
    assert _memory_cap_ladder(52) == (52, 15, 12)


def test_app_object_control_classifies_without_mask_type() -> None:
    from xknxeditor.download.errors import UnsupportedProcedureError
    from xknxeditor.download.scope import control_in_scope, target_object_type
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

    control = LdCtrlWriteProp(obj_idx=6, prop_id=58, verify=False)
    system = {1: 1, 2: 2, 3: 3}  # populated mask map; index 6 is an application object
    # Index 6 is absent -> application object -> non-system (None), included in a
    # partial scope instead of aborting the whole download.
    assert target_object_type(control, system) is None
    assert control_in_scope(control, DownloadScope.PARAMETERS, system) is True
    assert control_in_scope(control, DownloadScope.GROUP_COMMUNICATION, system) is True
    # With no mask metadata at all the index stays unclassifiable and fails closed.
    with pytest.raises(UnsupportedProcedureError, match="InterfaceObjects metadata"):
        target_object_type(control, {})
