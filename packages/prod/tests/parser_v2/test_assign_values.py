"""An Assign that applies writes its value into its target like an edit: the value is part of the
edit's change. When the last Assign on a target stops applying in an edit, the target gets its
default again; a UI seeded from stored values keeps them as they are."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0007-01-0000"
XML = (Path(__file__).parents[1] / "fixtures" / "fan_coil_application.xml").read_bytes()
ENABLED = f"{APP}_P-1_R-1"
TYPE = f"{APP}_P-2_R-2"
MODE = f"{APP}_P-3_R-3"
LOCK = f"{APP}_P-6_R-6"


def _ui(values: dict[str, str] | None = None) -> DynamicUI:
    program = parse_application_xml(XML, "M-00FA")[0].program
    refs = [ParameterInstanceRef(ref_id=r, value=v) for r, v in (values or {}).items()]
    return DynamicUI(program, parameter_instance_refs=refs)


def _objects(ui: DynamicUI) -> list[str]:
    return sorted(
        r.rsplit("_", 2)[-2] for r in ui.active_parameter_driven_com_object_ref_ids()
    )


def _state(ui: DynamicUI) -> tuple[str | None, str | None, list[str]]:
    ui.ui()
    return ui.get_value(TYPE), ui.get_value(MODE), _objects(ui)


def test_the_assign_sets_its_target_while_it_applies() -> None:
    ui = _ui()
    ui.ui()
    assert ui.get_value(TYPE) == "0"
    assert ui.is_parameter_active(TYPE)


def test_the_values_an_assign_writes_are_part_of_the_change() -> None:
    ui = _ui({ENABLED: "1"})
    ui.ui()
    assert ui.edit_parameters([(ENABLED, "0")]) == {
        ENABLED: ("1", "0"),
        TYPE: ("1", "0"),
        # The Assign of Type 1 no longer applies: Mode has its default again.
        MODE: ("2", "1"),
    }
    assert ui.get_value(TYPE) == "0"


def test_a_target_has_its_default_again_when_its_assign_stops_applying() -> None:
    ui = _ui()
    ui.ui()
    changes = ui.edit_parameters([(ENABLED, "1")])
    assert changes == {ENABLED: ("0", "1"), TYPE: ("0", "1"), MODE: ("1", "2")}
    assert _state(ui) == ("1", "2", ["O-1", "O-2", "O-3", "O-4", "O-5", "O-6"])


def test_the_default_replaces_a_value_set_before_the_assign_applied() -> None:
    ui = _ui({ENABLED: "1"})
    ui.ui()
    ui.edit_parameters([(TYPE, "2")])
    ui.edit_parameters([(ENABLED, "0")])
    assert ui.get_value(TYPE) == "0"
    assert ui.edit_parameters([(ENABLED, "1")])[TYPE] == ("0", "1")
    assert ui.get_value(TYPE) == "1"


def test_no_reset_while_another_assign_on_the_target_applies() -> None:
    ui = _ui({LOCK: "1"})
    ui.ui()
    changes = ui.edit_parameters([(ENABLED, "1")])
    assert TYPE not in changes
    assert ui.get_value(TYPE) == "0"
    assert ui.edit_parameters([(LOCK, "0")])[TYPE] == ("0", "1")
    assert ui.get_value(TYPE) == "1"


def test_a_single_edit_resets_like_a_batch() -> None:
    ui = _ui()
    ui.ui()
    changes = ui.edit_parameter(ENABLED, "1")
    assert changes[TYPE] == ("0", "1")
    assert ui.get_value(TYPE) == "1"


def test_a_seeded_ui_keeps_the_stored_values() -> None:
    # The Assign of Enabled 0 does not apply: the stored Type stays, no reset to the default.
    ui = _ui({ENABLED: "1", TYPE: "0"})
    assert _state(ui) == ("0", "1", ["O-1", "O-2", "O-5", "O-6"])


def test_applied_values_are_kept_without_reset() -> None:
    # As an undo restores them: the Assign stops applying, the target keeps what it was given.
    ui = _ui()
    ui.ui()
    ui.apply_parameter_values({ENABLED: "1", TYPE: "0"})
    assert _state(ui) == ("0", "1", ["O-1", "O-2", "O-5", "O-6"])


def test_an_edited_ui_agrees_with_one_seeded_from_its_changes() -> None:
    stored: dict[str, str] = {}
    edited = _ui()
    edited.ui()
    for edit in (
        (ENABLED, "1"),
        (TYPE, "2"),
        (ENABLED, "0"),
        (LOCK, "1"),
        (ENABLED, "1"),
    ):
        changes = edited.edit_parameters([edit])
        stored.update(
            {ref: new for ref, (_, new) in changes.items() if new is not None}
        )
        seeded = _ui(stored)
        assert _state(edited) == _state(seeded)
        assert edited.encode_to_memory() == seeded.encode_to_memory()


def test_a_target_the_edit_sets_keeps_its_value() -> None:
    # As when the settings of another device are copied: the copied value of the target stays.
    ui = _ui()
    ui.ui()
    changes = ui.edit_parameters([(ENABLED, "1"), (TYPE, "2")])
    assert changes[TYPE] == ("0", "2")
    assert _state(ui) == ("2", "2", ["O-1", "O-2", "O-3", "O-4", "O-5", "O-6"])
