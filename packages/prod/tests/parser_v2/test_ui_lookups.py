"""What a UI looks up again and again in its evaluated state (the active parameter refs, the
scope of a module instance's ref) follows each change of the state, and decoding memory and
properties together gives what decoding each gives."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0005-01-0000"
FIXTURES = Path(__file__).parents[1] / "fixtures"
LAYOUT = f"{APP}_P-1_R-1"
# A parameter of the module instance the layout 2 makes (the fixture's default).
INSTANCE_REF = f"{APP}_MD-1_M-9_MI-1_P-1_R-1"


def _ui() -> DynamicUI:
    xml = (FIXTURES / "union_late_assign_application.xml").read_bytes()
    return DynamicUI(parse_application_xml(xml, "M-00FA")[0].program)


def test_the_scope_of_a_ref_follows_its_instance() -> None:
    ui = _ui()
    ui.ui()
    assert ui.local_ref_id(INSTANCE_REF) == f"{APP}_MD-1_P-1_R-1"
    assert ui.local_ref_id(INSTANCE_REF) == f"{APP}_MD-1_P-1_R-1"
    # Layout 0 ends the instance: its refs are in no scope any more.
    ui.set_parameter_ref(LAYOUT, "0")
    ui.ui()
    assert ui.local_ref_id(INSTANCE_REF) == INSTANCE_REF


def test_the_active_refs_follow_a_change() -> None:
    ui = _ui()
    before = ui.active_parameter_ref_ids()
    assert INSTANCE_REF in before
    assert ui.active_parameter_ref_ids() is before
    ui.set_parameter_ref(LAYOUT, "0")
    after = ui.active_parameter_ref_ids()
    assert INSTANCE_REF not in after
    assert f"{APP}_MD-2_M-16_MI-1_P-1_R-1" in after


def test_a_simulation_leaves_the_lookups_as_they_were() -> None:
    ui = _ui()
    before = ui.active_parameter_ref_ids()
    local = ui.local_ref_id(INSTANCE_REF)
    with ui.simulation():
        ui.set_parameter_ref(LAYOUT, "0")
        assert INSTANCE_REF not in ui.active_parameter_ref_ids()
        assert ui.local_ref_id(INSTANCE_REF) == INSTANCE_REF
    assert ui.active_parameter_ref_ids() == before
    assert ui.local_ref_id(INSTANCE_REF) == local


def test_memory_and_properties_decode_together_as_each_does() -> None:
    ui = _ui()
    segments = ui.encode_to_memory()
    properties = ui.encode_to_properties()
    together = ui.decode_stored_parameters(segments, properties)
    expected = dict(ui.decode_memory_parameters(segments))
    if properties:
        expected.update(ui.decode_property_parameters(properties))
    assert together == expected
    assert list(together) == list(expected)
    assert ui.decode_stored_parameters(segments, {}) == ui.decode_memory_parameters(
        segments
    )
