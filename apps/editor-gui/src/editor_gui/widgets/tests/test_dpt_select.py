from editor_gui.widgets.dpt_select import DptChoice, dpt_choice_label, dpt_choices

_NAMES = {
    "DPT-1": "1-bit",
    "DPST-1-2": "boolean",
    "DPST-1-1": "switch",
    "DPT-14": "4-byte float value",
    "DPST-14-1000": "moment of force",
    "DPT-5": "8-bit unsigned value",
    "DPST-5-1": "percentage (0..100%)",
}


def test_choices_are_main_types_with_their_subtypes_in_number_order() -> None:
    assert dpt_choices(_NAMES) == [
        DptChoice("DPT-1", "1.* 1-bit", False),
        DptChoice("DPST-1-1", "1.001 switch", True),
        DptChoice("DPST-1-2", "1.002 boolean", True),
        DptChoice("DPT-5", "5.* 8-bit unsigned value", False),
        DptChoice("DPST-5-1", "5.001 percentage (0..100%)", True),
        DptChoice("DPT-14", "14.* 4-byte float value", False),
        DptChoice("DPST-14-1000", "14.1000 moment of force", True),
    ]


def test_subtypes_without_a_named_main_type_are_listed() -> None:
    assert dpt_choices({"DPST-7-1": "pulses"}) == [
        DptChoice("DPT-7", "7.*", False),
        DptChoice("DPST-7-1", "7.001 pulses", True),
    ]


def test_labels() -> None:
    assert dpt_choice_label("DPST-1-1", _NAMES) == "1.001 switch"
    assert dpt_choice_label("DPT-14", _NAMES) == "14.* 4-byte float value"
    assert dpt_choice_label("", _NAMES) == "-"
    assert dpt_choice_label(None, _NAMES) == "-"
    assert dpt_choice_label("DPST-99-1", _NAMES) == "99.001"
    assert dpt_choice_label("odd", _NAMES) == "odd"
