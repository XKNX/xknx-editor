"""encode_to_memory regression tests against the real Gira Push-button interface 2-gang comfort
(M-0008_A-7072-21-5CC3-O000A). Verifies that changing a parameter via set_parameter_ref is
reflected in the encoded memory output."""

from pathlib import Path

import pytest

from xknxeditor.namespaces.intermediate.segment_base_t import SegmentBase
from xknxeditor.prod import load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.encode import MemWrite, _write_param, segment_seed

_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "gira_2gang_button_interface.knxprod"
)
_APP_ID = "M-0008_A-7072-21-5CC3-O000A"
_SEG_ID = "M-0008_A-7072-21-5CC3-O000A_RS-04-00000"

# A top-level numeric parameter visible in the default UI state (delay in minutes, default 0).
_DELAY_REF_ID = "M-0008_A-7072-21-5CC3-O000A_P-496_R-106"
# A top-level numeric parameter (delay seconds, default 5).
_DELAY_SEC_REF_ID = "M-0008_A-7072-21-5CC3-O000A_P-505_R-107"
# Checkbox: "Logic functions" (default 0).
_LOGIC_REF_ID = "M-0008_A-7072-21-5CC3-O000A_P-4_R-79"


@pytest.fixture()
def dui() -> DynamicUI:
    app = load(_FIXTURE.read_bytes()).applications[_APP_ID]
    d = app.dynamic_ui()
    assert d is not None
    return d


def test_default_encode_produces_segment(dui: DynamicUI) -> None:
    mem = dui.encode_to_memory()
    assert _SEG_ID in mem
    assert len(mem[_SEG_ID]) > 0


def test_changing_numeric_param_changes_bytes(dui: DynamicUI) -> None:
    mem_before = dui.encode_to_memory()
    dui.set_parameter_ref(_DELAY_REF_ID, "3")
    mem_after = dui.encode_to_memory()

    assert mem_before[_SEG_ID] != mem_after[_SEG_ID], (
        "memory was identical after changing delay-minutes param from 0 to 3"
    )


def test_changed_value_encodes_correctly(dui: DynamicUI) -> None:
    mem_default = dui.encode_to_memory()
    dui.set_parameter_ref(_DELAY_REF_ID, "7")
    mem_changed = dui.encode_to_memory()

    # Find which byte changed and verify it contains the new value
    seg_before = mem_default[_SEG_ID]
    seg_after = mem_changed[_SEG_ID]
    diffs = [
        (i, seg_before[i], seg_after[i])
        for i in range(min(len(seg_before), len(seg_after)))
        if seg_before[i] != seg_after[i]
    ]
    assert len(diffs) > 0, "no bytes changed"
    # At least one changed byte must contain the encoded value 7
    assert any(after == 7 for _, _, after in diffs), f"encoded diffs: {diffs}"


def test_reverting_param_restores_original_bytes(dui: DynamicUI) -> None:
    mem_original = dui.encode_to_memory()
    default_val = "0"

    dui.set_parameter_ref(_DELAY_REF_ID, "5")
    mem_changed = dui.encode_to_memory()
    assert mem_original[_SEG_ID] != mem_changed[_SEG_ID]

    dui.set_parameter_ref(_DELAY_REF_ID, default_val)
    mem_reverted = dui.encode_to_memory()
    assert mem_original[_SEG_ID] == mem_reverted[_SEG_ID]


def test_multiple_param_changes(dui: DynamicUI) -> None:
    mem_before = dui.encode_to_memory()

    dui.set_parameter_ref(_DELAY_REF_ID, "2")
    dui.set_parameter_ref(_DELAY_SEC_REF_ID, "30")

    mem_after = dui.encode_to_memory()
    assert mem_before[_SEG_ID] != mem_after[_SEG_ID]


def test_param_map_value_matches_encoded_byte(dui: DynamicUI) -> None:
    dui.set_parameter_ref(_DELAY_REF_ID, "4")

    mem = dui.encode_to_memory()
    # rebuild param map the same way configure.py does
    pmap = dui.memory_param_map()

    seg = mem[_SEG_ID]
    seg_map = pmap[_SEG_ID]

    # Find the byte(s) that the param map says belong to _DELAY_REF_ID's parameter
    delay_param_id = "M-0008_A-7072-21-5CC3-O000A_P-496"
    matching = {
        offset: val for offset, (pid, val) in seg_map.items() if delay_param_id in pid
    }
    assert matching, "param map has no entry for delay-minutes parameter"

    for offset, val in matching.items():
        assert val == "4", f"param map shows value {val!r} but expected '4'"
        assert seg[offset] == 4, (
            f"encoded byte at offset {offset} is {seg[offset]:#04x}, expected 0x04"
        )


def test_segment_seed_pads_data_to_the_declared_size() -> None:
    # Data may cover only the start of a segment; parameters behind it are still in the segment.
    seg = SegmentBase(id="AS-1", size=6, data=bytes([1, 2]))
    assert segment_seed(seg) == bytearray([1, 2, 0, 0, 0, 0])
    assert segment_seed(SegmentBase(id="AS-2", size=3)) == bytearray(3)
    # Data longer than the declared size is kept whole.
    longer = SegmentBase(id="AS-3", size=1, data=bytes([7, 8]))
    assert segment_seed(longer) == bytearray([7, 8])


def test_parameter_outside_its_segment_is_left_out() -> None:
    # A product can place a parameter past its segment (e.g. the last channel of a module
    # repeat); the device holds only the segment, so it is left out instead of written past it.
    buf = bytearray(4)
    inside = MemWrite("RS-1", 3, 0, "P-1", "PT-1", "7")
    assert _write_param(buf, inside, 8, 7)
    outside = MemWrite("RS-1", 4, 0, "P-2", "PT-1", "1")
    assert not _write_param(buf, outside, 8, 1)
    assert buf == bytearray([0, 0, 0, 7])
